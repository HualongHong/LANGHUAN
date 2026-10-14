/**
 * 共享配置解析器 —— schemastery 路径与零依赖 Standard Schema 回退路径
 * 共用同一份「填默认值 + 结构校验」语义，保证两条路径对 cordis
 * 暴露的行为完全一致（resolveConfig 只消费 Config["~standard"].validate）。
 */

import { DEFAULT_ANNOTATION_PROVIDER, DEFAULT_DECISION_PROVIDER, DEFAULT_PROVIDERS, DEFAULT_SKILLS_DIR } from './presets.js';

export const PROVIDER_TYPES = /** @type {const} */ ([
  'openai_compatible',
  'openai_compatible_decision',
  'jev_legacy',
]);

const ENTRY_DEFAULTS = {
  operator: '',
  type: 'openai_compatible',
  baseUrl: '',
  apiKeyEnv: '',
  models: /** @type {string[]} */ ([]),
  enabled: false,
  supportsLogprobs: false,
  reserved: false,
  note: '',
};

function clonePresets() {
  return structuredClone(DEFAULT_PROVIDERS);
}

/**
 * 解析原始配置：填默认值并收集结构问题（issues）。
 * @param {unknown} raw
 * @returns {{ value: any, issues: { message: string, path?: (string|number)[] }[] }}
 */
export function resolveConfigValue(raw) {
  const issues = [];
  const input = raw ?? {};

  if (typeof input !== 'object' || Array.isArray(input)) {
    return {
      value: fallbackValue(),
      issues: [{ message: 'config 必须是对象', path: [] }],
    };
  }

  let providers;
  if (input.providers === undefined || input.providers === null) {
    providers = clonePresets();
  } else if (typeof input.providers === 'object' && !Array.isArray(input.providers)) {
    providers = {};
    for (const [id, rawEntry] of Object.entries(input.providers)) {
      if (typeof rawEntry !== 'object' || rawEntry === null || Array.isArray(rawEntry)) {
        issues.push({ message: `provider '${id}' 的条目必须是对象`, path: ['providers', id] });
        continue;
      }
      const entry = { ...ENTRY_DEFAULTS };
      for (const [k, v] of Object.entries(rawEntry)) {
        if (k === 'type') {
          if (PROVIDER_TYPES.includes(v)) entry.type = v;
          else issues.push({ message: `provider '${id}' 的 type 必须是 ${PROVIDER_TYPES.join(' / ')}`, path: ['providers', id, 'type'] });
        } else if (k === 'models') {
          if (Array.isArray(v) && v.every((m) => typeof m === 'string')) entry.models = [...v];
          else issues.push({ message: `provider '${id}' 的 models 必须是字符串数组`, path: ['providers', id, 'models'] });
        } else if (k === 'operator' || k === 'baseUrl' || k === 'apiKeyEnv' || k === 'note') {
          if (typeof v === 'string') entry[k] = v;
          else issues.push({ message: `provider '${id}' 的 ${k} 必须是字符串`, path: ['providers', id, k] });
        } else if (k === 'enabled' || k === 'supportsLogprobs' || k === 'reserved') {
          if (typeof v === 'boolean') entry[k] = v;
          else issues.push({ message: `provider '${id}' 的 ${k} 必须是布尔值`, path: ['providers', id, k] });
        }
        // 未知键：两条路径都忽略（与 schemastery z.object 行为一致）
      }
      providers[id] = entry;
    }
  } else {
    issues.push({ message: 'providers 必须是对象（provider id → 条目）', path: ['providers'] });
    providers = clonePresets();
  }

  const str = (key, fallback) => {
    const v = input[key];
    if (v === undefined || v === null) return fallback;
    if (typeof v === 'string') return v;
    issues.push({ message: `${key} 必须是字符串`, path: [key] });
    return fallback;
  };

  return {
    value: {
      providers,
      annotationProvider: str('annotationProvider', DEFAULT_ANNOTATION_PROVIDER),
      decisionProvider: str('decisionProvider', DEFAULT_DECISION_PROVIDER),
      skillsDir: str('skillsDir', DEFAULT_SKILLS_DIR),
    },
    issues,
  };
}

/** 解析彻底失败时的兜底值（仅用于 issues 非空时占位返回）。 */
function fallbackValue() {
  return {
    providers: clonePresets(),
    annotationProvider: DEFAULT_ANNOTATION_PROVIDER,
    decisionProvider: DEFAULT_DECISION_PROVIDER,
    skillsDir: DEFAULT_SKILLS_DIR,
  };
}

/**
 * 角色/启用约束校验（apply 时执行；schemastery 3.18.2 无 .check()，
 * 故两条路径都在 apply 内做）。返回错误列表（空 = 通过）。
 * @param {any} config 已解析配置
 * @returns {string[]}
 */
export function validateConfig(config) {
  const errors = [];
  const providers = config.providers ?? {};
  for (const [id, entry] of Object.entries(providers)) {
    if (entry.enabled) {
      if (!entry.baseUrl) errors.push(`provider '${id}' 已启用但 baseUrl 为空`);
      if (!entry.apiKeyEnv) errors.push(`provider '${id}' 已启用但 apiKeyEnv 为空`);
    }
  }
  const checkRole = (role, slot, extra) => {
    if (!slot) return;
    const entry = providers[slot];
    if (!entry) {
      errors.push(`角色 ${role} 指向不存在的 provider: ${slot}`);
      return;
    }
    if (!entry.enabled) errors.push(`角色 ${role} 指向的 provider '${slot}' 未启用`);
    if (entry.reserved) errors.push(`角色 ${role} 指向的 provider '${slot}' 是 reserved 预留入口，禁止选用`);
    if (extra) extra(entry);
  };
  checkRole('annotation', config.annotationProvider);
  checkRole('decision', config.decisionProvider, (entry) => {
    if (!entry.supportsLogprobs) {
      errors.push(`决策角色 provider 不支持 logprobs（阶段 B 概率输出必需）: '${config.decisionProvider}'`);
    }
  });
  return errors;
}
