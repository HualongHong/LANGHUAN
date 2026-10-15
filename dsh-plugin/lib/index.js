/**
 * @langhuan/dsh-plugin —— LangHuan 套件的 DSH（DeepSeek Harness）原生插件。
 *
 * 职责：把 DSH 设置体系里的运营商/角色/密钥环境变量名配置，序列化落盘为
 * skills/lit-scout-harvester 的 config/providers.json（provider_registry.py
 * 的唯一持久层），并向其他插件暴露 langhuanProviders 服务。
 *
 * 加载契约（DSH 0.1.5-rc.3 验证）：
 * - 命名导出 { apply, Config, name }；cordis loader 对插件模块取
 *   `default ?? 模块命名空间`，再取 `.apply` 作为回调、`.Config` 作为
 *   运行时 schema（cordis-plugin-loader unwrapExports / Registry.plugin），
 *   校验经 Config['~standard'].validate（Standard Schema v1）。
 * - Config 双路径：宿主能解析 @deepseek-ai/schemastery 时用 canonical
 *   z.object schema；pnpm link: 本地文件夹安装时插件 realpath 不在宿主
 *   node_modules 树内，解析失败则回退零依赖 Standard Schema——两条路径
 *   解析语义由 lib/resolve-config.js 保证一致。
 * - 密钥红线：Config 只存 apiKeyEnv（环境变量名），永不存 key 本体，
 *   与内置 dsh-llm-pi-ai 的 apiKeyEnv + role('credential-ref') 惯例一致。
 * - 配置更新路径（两条，都会触发 providers.json 重同步）：
 *   1) cordis.patch.yml 改动 → fiber 重启 → apply 重跑；
 *   2) 图形设置卡片写入 → settings 命名空间 user section（~/.dsh/settings.yaml）
 *      → installSection 的 onChange → current() 换源重同步（无重启）。
 * - 图形设置卡片（浏览器半侧 lib/client.js，lazy-CJS factory）需要服务端
 *   注册 settings 命名空间 `langhuan`，注册依赖 schemastery schema——仅
 *   canonical 路径注册；回退路径下卡片不渲染，providers.json 同步不受影响。
 */

let z = null;
try {
  z = (await import('@deepseek-ai/schemastery')).default ?? null;
} catch {
  z = null; // link: 本地安装场景：宿主 schema 包不可解析，走 Standard Schema 回退
}

import { buildSchemasteryConfig, buildStandardSchemaConfig } from './schema.js';
import { syncProvidersJson } from './write-providers.js';
import { validateConfig, assertValidConfig } from './resolve-config.js';

export const name = 'langhuan';

export const Config = z ? buildSchemasteryConfig(z) : buildStandardSchemaConfig();

/**
 * DSH 插件入口。
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {any} config 经 Config 解析后的配置
 */
export function apply(ctx, config) {
  // 活的配置源：settings 命名空间接管后经 setSource 换成 provider 解析值。
  let current = () => config;
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

  sync('boot');

  // 服务注册点：供其他插件消费当前快照（getter 形式，随每次同步刷新；
  // cordis 服务，随本插件卸载自动注销）。
  ctx.provide('langhuanProviders', {
    get path() { return service.path; },
    get snapshot() { return service.snapshot; },
  });

  // 图形设置卡片的服务端前提：注册 settings 命名空间（内置 bash-local 同款
  // installSection：base = 组合配置，user section 写 ~/.dsh/settings.yaml，
  // setSource 换源 + onChange 重同步；validate 拒绝非法写入）。
  if (z) {
    ctx.inject(['settings'], (settingsCtx) => {
      try {
        settingsCtx.settings.installSection(ctx, 'langhuan', Config, config, {
          validate: assertValidConfig,
          setSource: (next) => { current = next; },
          onChange: () => sync('settings'),
        });
        ctx.logger.info('[langhuan] settings 命名空间 langhuan 已注册（图形设置卡片可用）');
      } catch (error) {
        ctx.logger.error('[langhuan] settings 命名空间注册失败（图形设置卡片不可用）: %s', error);
      }
    });
  } else {
    ctx.logger.info('[langhuan] 未解析到 schemastery，跳过 settings 命名空间注册（图形设置卡片不可用；providers.json 同步不受影响）');
  }
}

export { validateConfig };
