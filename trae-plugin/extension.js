/**
 * langhuan-provider-card —— LangHuan 套件的 TRAE/VS Code 扩展。
 *
 * 职责：把设置卡片（contributes.configuration）里的运营商/角色/密钥环境
 * 变量名配置，同步落盘为 langhuan-lit-scout-harvester 的 providers.json
 * （plugin-core 的 syncProvidersJson 是唯一写路径，语义与 DSH 插件一致：
 * boot 同步 + 变更监听重同步）。
 *
 * 加载契约（VS Code 扩展宿主只加载 CJS；plugin-core 是 ESM）：
 * 顶层不静态 import ESM，activate 后按需 dynamic import()，失败记日志返回 null。
 * 同步路径全程 try/catch——任何失败只落 Output Channel，绝不抛给宿主。
 *
 * 密钥红线：配置里只存 apiKeyEnv（环境变量名），永不存 key 本体。
 */

'use strict';

const vscode = require('vscode');

const { buildConfigSlice } = require('./lib/sync');

/** @type {vscode.OutputChannel | null} */
let channel = null;

/** plugin-core 惰性加载承诺（ESM dynamic import 只做一次）。 */
let corePromise = null;

/** 加载共享核心（ESM）；失败记日志、返回 null，绝不抛。 */
function loadCore() {
  corePromise ??= import('@langhuan/plugin-core').catch((error) => {
    log('error', `加载 @langhuan/plugin-core 失败: ${toMessage(error)}`);
    return null;
  });
  return corePromise;
}

function toMessage(error) {
  return error && error.stack ? error.stack : String(error);
}

/** Output Channel 就绪前（极端早败场景）静默丢弃，activate 一定先建 channel。 */
function log(level, message) {
  if (!channel) return;
  if (level === 'error') channel.error(message);
  else if (level === 'warn') channel.warn(message);
  else if (level === 'debug') channel.debug(message);
  else channel.appendLine(message);
}

/**
 * 读设置卡片 → 合并出厂预置 → 校验 → 落盘 providers.json。
 * @param {string} trigger 触发源（boot / config-change），仅用于日志
 */
async function sync(trigger) {
  try {
    const core = await loadCore();
    if (!core) return;

    const config = vscode.workspace.getConfiguration('langhuan');
    const slice = buildConfigSlice(
      {
        providers: config.get('providers'),
        annotationProvider: config.get('annotationProvider'),
        decisionProvider: config.get('decisionProvider'),
        skillsDir: config.get('skillsDir'),
      },
      {
        providers: core.DEFAULT_PROVIDERS,
        annotationProvider: core.DEFAULT_ANNOTATION_PROVIDER,
        decisionProvider: core.DEFAULT_DECISION_PROVIDER,
        skillsDir: core.DEFAULT_SKILLS_DIR,
      },
    );

    const resolved = core.unwrapResolved(slice);
    for (const error of core.validateConfig(resolved)) {
      log('error', `[langhuan] 配置校验 (${trigger}): ${error}`);
    }

    let result;
    try {
      result = core.syncProvidersJson(resolved.skillsDir, {
        providers: resolved.providers,
        annotationProvider: resolved.annotationProvider,
        decisionProvider: resolved.decisionProvider,
      });
    } catch (error) {
      log('error', `[langhuan] providers.json 落盘失败 (${trigger}): ${toMessage(error)}`);
      return;
    }

    if (result.changed) {
      const n = result.data.providers?.length ?? 0;
      log(
        'info',
        `[langhuan] providers.json 已同步 (${trigger}) → ${result.path}` +
          `（${n} 个运营商；annotation=${result.data.annotation_provider ?? '（未配置）'}` +
          ` decision=${result.data.decision_provider ?? '（未配置）'}）`,
      );
    } else {
      log('debug', `[langhuan] providers.json 无变化 (${trigger})，跳过写入 (${result.path})`);
    }
  } catch (error) {
    log('error', `[langhuan] 同步失败 (${trigger}): ${toMessage(error)}`);
  }
}

/** @param {vscode.ExtensionContext} context */
function activate(context) {
  channel = vscode.window.createOutputChannel('LangHuan');
  context.subscriptions.push(channel);

  void sync('boot');

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('langhuan')) void sync('config-change');
    }),
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
