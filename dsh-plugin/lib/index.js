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
 * - 配置更新路径：Host 保存设置 → fiber.update → 插件以新 config 重启，
 *   apply 重新执行并重新落盘，因此无需监听 loader/volatile-update
 *   （该事件在 cordis 4.0.2 / loader 1.0.3 中尚不存在）。
 */

let z = null;
try {
  z = (await import('@deepseek-ai/schemastery')).default ?? null;
} catch {
  z = null; // link: 本地安装场景：宿主 schema 包不可解析，走 Standard Schema 回退
}

import { buildSchemasteryConfig, buildStandardSchemaConfig } from './schema.js';
import { syncProvidersJson } from './write-providers.js';
import { validateConfig } from './resolve-config.js';

export const name = 'langhuan';

export const Config = z ? buildSchemasteryConfig(z) : buildStandardSchemaConfig();

/**
 * DSH 插件入口。
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {any} config 经 Config 解析后的配置
 */
export function apply(ctx, config) {
  for (const error of validateConfig(config)) {
    ctx.logger.error('[langhuan] 配置校验: %s', error);
  }

  let sync;
  try {
    sync = syncProvidersJson(config.skillsDir, {
      providers: config.providers,
      annotationProvider: config.annotationProvider,
      decisionProvider: config.decisionProvider,
    });
  } catch (error) {
    ctx.logger.error('[langhuan] providers.json 落盘失败: %s', error);
    return;
  }
  if (sync.changed) {
    const n = sync.data.providers?.length ?? 0;
    ctx.logger.info(
      '[langhuan] providers.json 已同步 → %s（%d 个运营商；annotation=%s decision=%s）',
      sync.path,
      n,
      sync.data.annotation_provider ?? '（未配置）',
      sync.data.decision_provider ?? '（未配置）',
    );
  } else {
    ctx.logger.debug('[langhuan] providers.json 无变化，跳过写入 (%s)', sync.path);
  }

  // 服务注册点：供其他插件消费当前快照（cordis 服务，随本插件卸载自动注销）。
  ctx.provide('langhuanProviders', Object.freeze({
    path: sync.path,
    snapshot: Object.freeze(sync.data),
  }));
}

export { validateConfig };
