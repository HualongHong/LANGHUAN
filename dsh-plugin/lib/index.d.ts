import type { Context } from '@deepseek-ai/cordis';
import type { Schema } from '@deepseek-ai/schemastery';

/** Standard Schema v1（@standard-schema/spec）最小形态。 */
export interface StandardSchemaV1<Input = unknown, Output = Input> {
  /** Standard Schema 品牌键。 */
  readonly '~standard': {
    readonly version: 1;
    readonly vendor: string;
    validate(value: unknown): { value: Output; issues?: undefined } | { issues: StandardSchemaIssue[]; value?: undefined };
  };
}

export interface StandardSchemaIssue {
  message: string;
  path?: (string | number)[];
}

/** 运营商接口类型（ssh_tunnel / model_deployment HTTP 层面仍按 openai_compatible 驱动）。 */
export type ProviderType =
  | 'openai_compatible'
  | 'openai_compatible_decision'
  | 'jev_legacy'
  | 'ssh_tunnel'
  | 'model_deployment';

/** providers dict 单条配置（camelCase；落盘时转为 snake_case）。 */
export interface ProviderEntryConfig {
  /** 运营商显示名 */
  operator: string;
  /** 接口类型 */
  type: ProviderType;
  /** OpenAI 兼容端点 base URL */
  baseUrl: string;
  /** 密钥环境变量名（只存名字，不存 key 本体） */
  apiKeyEnv: string;
  /** ssh_tunnel：SSH 服务器地址（仅 type=ssh_tunnel 有意义） */
  sshHost: string;
  /** ssh_tunnel：SSH 端口 */
  sshPort: number;
  /** ssh_tunnel：SSH 登录用户 */
  sshUser: string;
  /** ssh_tunnel：私钥路径（空 = 默认密钥/ssh-agent） */
  sshKeyPath: string;
  /** ssh_tunnel：转发目标地址（远端 OpenAI 端点 host） */
  remoteHost: string;
  /** ssh_tunnel：转发目标端口 */
  remotePort: number;
  /** ssh_tunnel：本地监听端口（baseUrl 指向 127.0.0.1:<localPort>） */
  localPort: number;
  /** 可用模型 id 列表 */
  models: string[];
  /** 启用开关 */
  enabled: boolean;
  /** 是否支持 logprobs（decision 角色要求 true） */
  supportsLogprobs: boolean;
  /** 预留不可用入口（reserved=true 禁止选为角色） */
  reserved: boolean;
  /** 备注 */
  note: string;
}

/** apply 收到的解析后配置。 */
export interface LanghuanConfig {
  /** 模型运营商注册表（provider id → 条目） */
  providers: Record<string, ProviderEntryConfig>;
  /** 标注角色（阶段 A）provider id；空串 = 不写入 */
  annotationProvider: string;
  /** 决策角色（阶段 B）provider id；空串 = 不写入 */
  decisionProvider: string;
  /** LangHuan 技能根目录（支持 ~ 展开） */
  skillsDir: string;
}

/** ctx.provide('langhuanProviders', ...) 暴露的服务快照。 */
export interface LanghuanProvidersService {
  /** providers.json 绝对路径 */
  readonly path: string;
  /** 最近一次落盘内容（snake_case 文档形态） */
  readonly snapshot: Readonly<Record<string, unknown>>;
}

export const name: 'langhuan';

/**
 * 运行时配置 schema。宿主可解析 @deepseek-ai/schemastery 时为
 * Schema<LanghuanConfig>；否则为零依赖 Standard Schema v1 对象
 * （{ '~standard': { version, vendor, validate } }），cordis 经
 * Config['~standard'].validate 消费，语义一致。
 */
export const Config: Schema<LanghuanConfig> | StandardSchemaV1<unknown, LanghuanConfig>;

/** 结构校验（等价 .check()）：角色槽指向、enabled/reserved/logprobs 约束。 */
export function validateConfig(config: LanghuanConfig): string[];

/** DSH 插件入口。 */
export function apply(ctx: Context, config: LanghuanConfig): void;
