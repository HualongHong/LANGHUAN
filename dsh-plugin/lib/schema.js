/**
 * Config schema 构造 —— 宿主可解析 @deepseek-ai/schemastery 时走 canonical
 * 路径（z.object，与内置插件同 idioms）；解析不到（pnpm link: 本地文件夹
 * 安装时插件 realpath 不在宿主 node_modules 树内）由 lib/index.js 回退到
 * 零依赖 Standard Schema，语义由 lib/resolve-config.js 保证一致。
 */

import { DEFAULT_ANNOTATION_PROVIDER, DEFAULT_DECISION_PROVIDER, DEFAULT_PROVIDERS, DEFAULT_SKILLS_DIR } from './presets.js';
import { PROVIDER_TYPES, resolveConfigValue } from './resolve-config.js';

/**
 * @param {any} z @deepseek-ai/schemastery 默认导出
 */
export function buildSchemasteryConfig(z) {
  const providerEntry = z.object({
    operator: z.string().default('').description('运营商显示名'),
    type: z.union([...PROVIDER_TYPES])
      .default('openai_compatible')
      .description('接口类型：openai_compatible / openai_compatible_decision / jev_legacy / ssh_tunnel / model_deployment（后两者 HTTP 层面仍按 openai_compatible 驱动，type 区分仅供隧道生命周期管理与 UI 分组）'),
    baseUrl: z.string().default('').description('OpenAI 兼容端点 base URL'),
    apiKeyEnv: z.string().role('credential-ref').default('')
      .description('密钥环境变量名（只存名字；值维护在中央 langhuan.env，永不入库）'),
    sshHost: z.string().default('').description('ssh_tunnel：SSH 服务器地址'),
    sshPort: z.number().default(22).description('ssh_tunnel：SSH 端口'),
    sshUser: z.string().default('').description('ssh_tunnel：SSH 登录用户'),
    sshKeyPath: z.string().default('').description('ssh_tunnel：私钥路径（空 = 默认密钥/ssh-agent）'),
    remoteHost: z.string().default('').description('ssh_tunnel：转发目标地址（远端 OpenAI 端点 host）'),
    remotePort: z.number().default(0).description('ssh_tunnel：转发目标端口'),
    localPort: z.number().default(0).description('ssh_tunnel：本地监听端口（base_url 指向 127.0.0.1:<localPort>）'),
    models: z.array(z.string()).default([]).description('可用模型 id 列表'),
    enabled: z.boolean().default(false).description('启用开关（reserved 条目即使启用也不可选用）'),
    supportsLogprobs: z.boolean().default(false)
      .description('是否支持 logprobs；decision 角色（阶段 B 概率输出）要求 true'),
    reserved: z.boolean().default(false).description('预留不可用入口：reserved=true 禁止选为角色'),
    note: z.string().default('').description('备注'),
  });

  return z.object({
    providers: z.dict(providerEntry).default(DEFAULT_PROVIDERS)
      .description('模型运营商注册表（以 provider id 为键；出厂为 LangHuan 10 预置）'),
    annotationProvider: z.string().default(DEFAULT_ANNOTATION_PROVIDER)
      .description('标注角色（阶段 A）选用的 provider id；置空表示不写入'),
    decisionProvider: z.string().default(DEFAULT_DECISION_PROVIDER)
      .description('决策角色（阶段 B，要求 supportsLogprobs）选用的 provider id；置空表示不写入'),
    skillsDir: z.string().default(DEFAULT_SKILLS_DIR)
      .description('LangHuan 技能根目录（providers.json 写到其下 langhuan-lit-scout-harvester/config/）'),
  });
}

/**
 * 零依赖 Standard Schema v1 回退（@standard-schema/spec 形态），
 * cordis resolveConfig 消费的是 Config['~standard'].validate。
 */
export function buildStandardSchemaConfig() {
  return {
    '~standard': {
      version: 1,
      vendor: 'langhuan',
      validate(value) {
        const { value: resolved, issues } = resolveConfigValue(value);
        return issues.length ? { issues } : { value: resolved };
      },
    },
  };
}
