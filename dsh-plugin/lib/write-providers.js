/**
 * providers.json 落盘器 —— 把 DSH 插件配置序列化为
 * lit-scout-harvester 的 provider_registry.py 兼容 JSON。
 *
 * 兼容策略（与 Python 侧 save_registry 的输出逐字节同构）：
 * - 字段名 snake_case，条目键序固定为 dataclass 字段序；
 * - JSON.stringify 不转义非 ASCII（等价 Python ensure_ascii=False），indent 2 + 尾换行；
 * - 原子写（临时文件 + rename）；
 * - 合并而非覆盖：目标文件里 Python 侧新增的未知顶层字段、未知条目字段、
 *   以及 DSH 配置中不存在的本地新增条目，全部原样保留。
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { EOL, homedir } from 'node:os';
import { dirname, join } from 'node:path';

import { PROVIDERS_JSON_RELATIVE_PATH } from './presets.js';

/**
 * 展开路径开头的 ~（仅 ~/ 或恰为 ~ 时；其余原样返回）。
 * @param {string} p
 * @returns {string}
 */
export function expandHome(p) {
  if (p === '~') return homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) return join(homedir(), p.slice(2));
  return p;
}

/**
 * camelCase 配置条目 → snake_case providers.json 条目（键序固定）。
 * @param {string} id
 * @param {Record<string, any>} entry
 * @returns {Record<string, any>}
 */
function toSnakeEntry(id, entry) {
  return {
    id,
    operator: entry.operator ?? '',
    type: entry.type ?? 'openai_compatible',
    base_url: entry.baseUrl ?? '',
    api_key_env: entry.apiKeyEnv ?? '',
    models: Array.isArray(entry.models) ? [...entry.models] : [],
    enabled: entry.enabled ?? false,
    supports_logprobs: entry.supportsLogprobs ?? false,
    reserved: entry.reserved ?? false,
    note: entry.note ?? '',
  };
}

/**
 * 组装待写内容（纯函数，便于测试与写前比对）。
 * @param {object} options
 * @param {Record<string, any>} options.providers  DSH 配置 providers（camelCase）
 * @param {string} options.annotationProvider  角色槽（空串 → null）
 * @param {string} options.decisionProvider
 * @param {Record<string, any>} [options.existing]  现有 providers.json 内容（可缺省）
 * @returns {{ data: Record<string, any>, text: string }}
 */
export function buildProvidersDocument({ providers, annotationProvider, decisionProvider, existing }) {
  const prev = existing && typeof existing === 'object' ? existing : {};
  const prevProviders = Array.isArray(prev.providers) ? prev.providers : [];
  const prevById = new Map();
  for (const p of prevProviders) {
    if (p && typeof p === 'object' && typeof p.id === 'string') prevById.set(p.id, p);
  }

  // DSH 配置管辖区：按 DSH dict 键序输出；同 id 的本地未知字段保留在条目前部。
  const managed = Object.entries(providers ?? {}).map(([id, entry]) => {
    const snake = toSnakeEntry(id, entry);
    const preserved = {};
    const previous = prevById.get(id);
    if (previous) {
      for (const [k, v] of Object.entries(previous)) {
        if (!(k in snake)) preserved[k] = v;
      }
    }
    return { ...preserved, ...snake };
  });

  // DSH 配置之外的本地新增条目：原样保留在尾部。
  const managedIds = new Set(Object.keys(providers ?? {}));
  const extras = prevProviders.filter((p) => p && typeof p === 'object' && !managedIds.has(p.id));

  const data = {};
  for (const [k, v] of Object.entries(prev)) {
    if (!['version', 'annotation_provider', 'decision_provider', 'providers'].includes(k)) data[k] = v;
  }
  data.version = '1.0';
  data.annotation_provider = annotationProvider || null;
  data.decision_provider = decisionProvider || null;
  data.providers = [...managed, ...extras];

  // Python save_registry 经文本模式落盘：\n 在 Windows 上被翻译成 \r\n。
  // 逐字节对齐同一平台的行为，避免把整文件换行符刷一遍。
  return { data, text: JSON.stringify(data, null, 2).split('\n').join(EOL) + EOL };
}

/**
 * 读出现有 providers.json（缺失返回 null；坏 JSON 抛 SyntaxError 由调用方决定）。
 * @param {string} targetPath
 * @returns {Record<string, any> | null}
 */
export function readExisting(targetPath) {
  let text;
  try {
    text = readFileSync(targetPath, 'utf8');
  } catch {
    return null;
  }
  return JSON.parse(text);
}

/**
 * 计算 providers.json 目标绝对路径。
 * @param {string} skillsDir
 * @returns {string}
 */
export function resolveProvidersPath(skillsDir) {
  return join(expandHome(skillsDir), ...PROVIDERS_JSON_RELATIVE_PATH.split('/'));
}

/**
 * 同步落盘（原子写；内容未变化时跳过写文件）。
 * @param {string} skillsDir
 * @param {object} configSlice { providers, annotationProvider, decisionProvider }
 * @returns {{ path: string, changed: boolean, data: Record<string, any> }}
 */
export function syncProvidersJson(skillsDir, configSlice) {
  const path = resolveProvidersPath(skillsDir);
  let existing = null;
  try {
    existing = readExisting(path);
  } catch {
    existing = null; // 坏 JSON 按无文件处理：本插件写的是合法出厂结构
  }
  const { data, text } = buildProvidersDocument({ ...configSlice, existing });
  let previousText = null;
  try {
    previousText = readFileSync(path, 'utf8');
  } catch {}
  if (previousText === text) return { path, changed: false, data };
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, text, 'utf8');
  renameSync(tmp, path);
  return { path, changed: true, data };
}
