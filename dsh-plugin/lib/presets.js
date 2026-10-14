/**
 * LangHuan 预置运营商 —— 与 skills/lit-scout-harvester/config/providers.json
 * 的出厂内容逐字段一致（DSH 侧 camelCase 视图）。
 *
 * 密钥红线：这里只出现环境变量名（apiKeyEnv），永不出现 key 本体。
 */

/** @typedef {{ operator: string, type: string, baseUrl: string, apiKeyEnv: string, models: string[], enabled: boolean, supportsLogprobs: boolean, reserved: boolean, note: string }} ProviderPreset */

/** @type {Record<string, ProviderPreset>} */
export const DEFAULT_PROVIDERS = {
  deepseek: {
    operator: 'DeepSeek',
    type: 'openai_compatible',
    baseUrl: 'https://api.deepseek.com/v1',
    apiKeyEnv: 'DEEPSEEK_API_KEY',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    enabled: true,
    supportsLogprobs: true,
    reserved: false,
    note: '标注 + 决策均可用（logprobs 已验证）',
  },
  minimax: {
    operator: 'MiniMax',
    type: 'openai_compatible',
    baseUrl: 'https://api.minimaxi.com/v1',
    apiKeyEnv: 'MINIMAX_API_KEY',
    models: [],
    enabled: false,
    supportsLogprobs: false,
    reserved: false,
    note: '接入后填实际模型名',
  },
  qwen: {
    operator: '阿里云百炼',
    type: 'openai_compatible',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    apiKeyEnv: 'QWEN_API_KEY',
    models: ['qwen-max', 'qwen-plus'],
    enabled: false,
    supportsLogprobs: false,
    reserved: false,
    note: '阿里云百炼 OpenAI 兼容端点',
  },
  tencent: {
    operator: '腾讯混元',
    type: 'openai_compatible',
    baseUrl: 'https://api.lkeap.cloud.tencent.com/v1',
    apiKeyEnv: 'TENCENT_API_KEY',
    models: ['hunyuan-pro'],
    enabled: false,
    supportsLogprobs: false,
    reserved: true,
    note: '当前不可访问，入口保留',
  },
  jev_original: {
    operator: 'JEV 原厂',
    type: 'jev_legacy',
    baseUrl: 'https://jev.invalid',
    apiKeyEnv: 'JEV_API_KEY',
    models: [],
    enabled: false,
    supportsLogprobs: false,
    reserved: true,
    note: '原厂服务已不可访问，保留入口待恢复',
  },
  shanghai_sc_llm: {
    operator: '上海超算云平台·LLM',
    type: 'openai_compatible',
    baseUrl: 'https://llm.shsc.cn/v1',
    apiKeyEnv: 'SHANGHAI_SC_API_KEY',
    models: [],
    enabled: false,
    supportsLogprobs: false,
    reserved: false,
    note: '基座大模型，接入后填实际地址与模型名',
  },
  shanghai_sc_jev: {
    operator: '上海超算·类JEV决策模型',
    type: 'openai_compatible_decision',
    baseUrl: 'https://llm.shsc.cn/v1',
    apiKeyEnv: 'SHANGHAI_SC_API_KEY',
    models: [],
    enabled: false,
    supportsLogprobs: true,
    reserved: false,
    note: '类 JEV 决策模型：选项内选择 + 概率输出',
  },
};

export const DEFAULT_ANNOTATION_PROVIDER = 'deepseek';
export const DEFAULT_DECISION_PROVIDER = 'deepseek';
export const DEFAULT_SKILLS_DIR = '~/.agents/skills';

/** providers.json 相对 skillsDir 的目标路径片段。 */
export const PROVIDERS_JSON_RELATIVE_PATH = 'langhuan-lit-scout-harvester/config/providers.json';
