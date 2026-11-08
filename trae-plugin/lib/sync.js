/**
 * @langhuan/trae-plugin —— 同步纯逻辑（不 require vscode，可独立单测）。
 *
 * 职责：把设置卡片读到的原始四项配置，与 plugin-core 出厂预置合并成
 * syncProvidersJson 所需的 configSlice。密钥红线：这里只流转
 * apiKeyEnv（环境变量名），永不接触 key 本体。
 */

'use strict';

/** 结构化克隆（providers 条目均为 JSON 可序列化值）。 */
function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

/**
 * 深合并出厂预置与 langhuan.providers 覆盖表。
 *
 * 合并粒度：逐 provider id、逐字段浅覆盖——覆盖表只写要改的字段，
 * 未提及的出厂字段（enabled / reserved / models / apiKeyEnv ...）原样保留；
 * 覆盖表里的未知 id 原样收录（视为用户自建运营商，缺省字段由
 * plugin-core 的 toSnakeEntry 在落盘时兜底）。defaults 不被修改。
 *
 * @param {Record<string, Record<string, any>>} defaultProviders DEFAULT_PROVIDERS
 * @param {unknown} overrides langhuan.providers 设置值（非对象时忽略）
 * @returns {Record<string, Record<string, any>>}
 */
function mergeProviderOverrides(defaultProviders, overrides) {
  const merged = clone(defaultProviders) ?? {};
  if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) {
    return merged;
  }
  for (const [id, fields] of Object.entries(overrides)) {
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) continue;
    const base = merged[id] && typeof merged[id] === 'object' ? merged[id] : {};
    const entry = { ...base };
    for (const [key, value] of Object.entries(fields)) {
      if (value === undefined) continue;
      entry[key] = clone(value);
    }
    merged[id] = entry;
  }
  return merged;
}

/**
 * 组装 plugin-core 的 configSlice。
 *
 * 角色槽语义与 plugin-core 一致：空串 = 用户显式选择「未配置」，
 * 落盘为 annotation_provider/decision_provider = null（不回落默认）；
 * 仅 undefined/null（设置项缺失）时回落出厂默认。
 *
 * @param {object} raw 设置原始值 { providers, annotationProvider, decisionProvider, skillsDir }
 * @param {object} defaults 出厂默认 { providers, annotationProvider, decisionProvider, skillsDir }
 * @returns {{ providers: Record<string, any>, annotationProvider: string, decisionProvider: string, skillsDir: string }}
 */
function buildConfigSlice(raw, defaults) {
  const rawConfig = raw && typeof raw === 'object' ? raw : {};
  return {
    providers: mergeProviderOverrides(defaults?.providers, rawConfig.providers),
    annotationProvider: rawConfig.annotationProvider ?? defaults?.annotationProvider ?? '',
    decisionProvider: rawConfig.decisionProvider ?? defaults?.decisionProvider ?? '',
    skillsDir: rawConfig.skillsDir ?? defaults?.skillsDir ?? '',
  };
}

module.exports = { buildConfigSlice, mergeProviderOverrides };
