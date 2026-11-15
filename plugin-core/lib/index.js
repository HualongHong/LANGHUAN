/**
 * @langhuan/plugin-core —— LangHuan 多宿主插件的共享核心。
 *
 * 与宿主无关的三件套：
 * - presets.js          11 家运营商预置表 + 出厂角色 + skillsDir 约定
 * - resolve-config.js   配置解包（provider $ref / Volatile 引用）+ 校验
 * - write-providers.js  providers.json 原子落盘同步（内容不变跳过写入）
 * - schema-standard.js  零依赖 Standard Schema v1 配置校验器
 *
 * 宿主插件（dsh-plugin / trae-plugin / workbuddy-plugin / kimi-plugin）
 * 只实现各自的"配置从哪来 + 卡片怎么画"，核心语义在此单点维护。
 *
 * dsh-plugin 与 TRAE 扩展均从这里消费同一份实现，避免各宿主的
 * 配置校验与落盘语义漂移。
 */
export {
  DEFAULT_ANNOTATION_PROVIDER,
  DEFAULT_DECISION_PROVIDER,
  DEFAULT_PROVIDERS,
  DEFAULT_SKILLS_DIR,
  PROVIDERS_JSON_RELATIVE_PATH,
} from './presets.js';
export {
  PROVIDER_TYPES,
  assertValidConfig,
  resolveConfigValue,
  unwrapResolved,
  validateConfig,
} from './resolve-config.js';
export {
  buildProvidersDocument,
  resolveProvidersPath,
  syncProvidersJson,
} from './write-providers.js';
export { buildStandardSchemaConfig } from './schema-standard.js';
