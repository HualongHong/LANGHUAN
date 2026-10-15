/**
 * @langhuan/dsh-plugin —— LangHuan 套件的 DSH（DeepSeek Harness）原生插件。
 *
 * 职责：把 DSH 设置体系里的运营商/角色/密钥环境变量名配置，序列化落盘为
 * skills/lit-scout-harvester 的 config/providers.json（provider_registry.py
 * 的唯一持久层），并向其他插件暴露 langhuanProviders 服务。
 *
 * 加载契约（DSH 0.1.5-rc.3 验证）：
 * - 命名导出 { apply, Config, name }；cordis loader 取模块命名空间的
 *   `.apply`/`.Config`，校验经 Config['~standard'].validate（Standard Schema v1）。
 * - Config 双路径：宿主能解析 @deepseek-ai/schemastery 时用 canonical
 *   z.object schema（0.2.x 自动加 .volatile() 标记，见 lib/schema.js）；
 *   pnpm link: 本地文件夹安装解析不到宿主依赖时回退零依赖 Standard Schema。
 * - 密钥红线：Config 只存 apiKeyEnv（环境变量名），永不存 key 本体。
 *
 * 版本兼容第一原则：任何设置服务都不得成为硬依赖——apply 同步完成、
 * entry 绝不 pending（0.2.0 Desktop 上 pending 会导致整个 web boot 失败）。
 * 设置扩展点一律 ctx.get() 探测（非严格、立即返回 undefined）：
 * - 0.1.x：settings.installSection 注册命名空间（onChange 重同步）；
 * - 0.2.x：Host 自动把含 volatile 字段的 Config 服务为命名空间
 *   （ns = loader entry id = 'langhuan'），写入经 configEditor 落 profile
 *   patch → fiber 重启/volatile HMR → apply 重跑或 volatile-update 事件
 *   → 两者都接到 sync()；
 * - 都没有：记 info 日志，配置经 cordis.patch.yml 生效。
 */

let z = null;
try {
  z = (await import('@deepseek-ai/schemastery')).default ?? null;
} catch {
  z = null; // link: 本地安装场景：宿主 schema 包不可解析，走 Standard Schema 回退
}

import { buildSchemasteryConfig, buildStandardSchemaConfig } from './schema.js';
import { syncProvidersJson } from './write-providers.js';
import { validateConfig, assertValidConfig, unwrapResolved } from './resolve-config.js';

export const name = 'langhuan';

export const Config = z ? buildSchemasteryConfig(z) : buildStandardSchemaConfig();

/** 非严格服务探测：任何版本 cordis 都可用，缺失返回 undefined，绝不 pending。 */
const probe = (ctx, service) => {
  try {
    return ctx.get(service);
  } catch {
    return undefined;
  }
};

/**
 * DSH 插件入口。
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {any} config 经 Config 解析后的配置（0.2.x 的 volatile 字段是引用）
 */
export function apply(ctx, config) {
  // 活的配置源：0.1.x settings 接管后经 setSource 换 provider 解析值；
  // 0.2.x volatile HMR 后同一引用的 .get() 返回新值。统一 unwrap 成纯数据。
  let current = () => unwrapResolved(config);
  const service = { path: '', snapshot: null };

  const sync = (trigger) => {
    const resolved = current();
    for (const error of validateConfig(resolved)) {
      ctx.logger.error('[langhuan] 配置校验 (%s): %s', trigger, error);
    }
    let result;
    try {
      result = syncProvidersJson(resolved.skillsDir, {
        providers: resolved.providers,
        annotationProvider: resolved.annotationProvider,
        decisionProvider: resolved.decisionProvider,
      });
    } catch (error) {
      ctx.logger.error('[langhuan] providers.json 落盘失败 (%s): %s', trigger, error);
      return;
    }
    service.path = result.path;
    service.snapshot = result.data;
    if (result.changed) {
      const n = result.data.providers?.length ?? 0;
      ctx.logger.info(
        '[langhuan] providers.json 已同步 (%s) → %s（%d 个运营商；annotation=%s decision=%s）',
        trigger,
        result.path,
        n,
        result.data.annotation_provider ?? '（未配置）',
        result.data.decision_provider ?? '（未配置）',
      );
    } else {
      ctx.logger.debug('[langhuan] providers.json 无变化 (%s)，跳过写入 (%s)', trigger, result.path);
    }
  };

  // boot 同步不依赖任何服务——这是唯一必须成功的写路径。
  sync('boot');

  // 服务注册点：供其他插件消费当前快照（getter 形式，随每次同步刷新）。
  ctx.provide('langhuanProviders', {
    get path() { return service.path; },
    get snapshot() { return service.snapshot; },
  });

  // ── 设置扩展点探测（0.1.x / 0.2.x），任何分支失败都不影响已完成的 boot 同步 ──
  // 为什么不用 ctx.inject(['settings'])：apply 运行时机早于同 reconcile 批次内
  // 的 settings 服务激活，且未来组合可能根本不含 dsh-settings——硬等待会
  // pending（0.2.0 Desktop 上 pending = 整个 web boot 失败）。改为异步重试
  // 探测（setTimeout 链，unref 不挡进程退出），attach 成功或超限后停止。
  const attachSettings = () => {
    const settings = probe(ctx, 'settings');
    if (settings === undefined || settings === null) return 'pending'; // 服务尚未激活
    if (typeof settings.installSection === 'function') {
      // ── 0.1.x（CLI 0.1.5-rc.3 验证）：installSection 注册 settings 命名空间，
      //    user section 写 ~/.dsh/settings.yaml，watched 文档变更经 onChange 重同步。
      try {
        settings.installSection(ctx, 'langhuan', Config, unwrapResolved(config), {
          validate: assertValidConfig,
          setSource: (next) => { current = () => unwrapResolved(next()); },
          onChange: () => sync('settings'),
        });
        ctx.logger.info('[langhuan] settings 命名空间 langhuan 已注册（0.1.x installSection；图形设置卡片可用）');
      } catch (error) {
        ctx.logger.error('[langhuan] 0.1.x settings 命名空间注册失败（图形设置卡片不可用）: %s', error);
      }
      return 'attached';
    }
    if (typeof settings.configure === 'function' && typeof settings.describe === 'function') {
      // ── 0.2.x（Desktop 0.2.0+，asar 实证）：Host 自动把含 volatile 字段的
      //    Config 服务为设置命名空间（ns = entry id），无需注册；volatile 路径的
      //    写入经 HMR 就地更新，监听 volatile-update 重同步；非 volatile 路径
      //    走 fiber 重启 → apply 重跑（boot 分支已覆盖）。
      try {
        ctx.on('loader/volatile-update', () => sync('volatile-update'));
        ctx.logger.info('[langhuan] 检测到 0.2.x 设置服务：Config 由 Host 自动服务为命名空间（entry id = langhuan；loader/volatile-update 已监听）');
      } catch (error) {
        ctx.logger.error('[langhuan] 0.2.x volatile-update 监听注册失败: %s', error);
      }
      return 'attached';
    }
    return 'foreign'; // settings 存在但形态陌生
  };

  let attempts = 0;
  let timer = null;
  const tryAttach = () => {
    const outcome = attachSettings();
    if (outcome === 'attached') return;
    if (++attempts < 25) {
      timer = setTimeout(tryAttach, 200);
      timer.unref?.();
      return;
    }
    ctx.logger.info('[langhuan] 宿主未提供设置扩展点（settings 服务缺失或形态非预期）；配置经 cordis.patch.yml 生效，providers.json 同步不受影响');
  };
  timer = setTimeout(tryAttach, 50);
  timer.unref?.();
  ctx.effect(() => () => clearTimeout(timer), 'langhuan: settings probe');
}

export { validateConfig };
