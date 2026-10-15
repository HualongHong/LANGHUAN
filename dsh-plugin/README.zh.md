# @langhuan/dsh-plugin

LangHuan Suite 的 DSH（DeepSeek Harness）原生插件：把 LangHuan 技能群的**模型运营商注册表**搬进 DSH 的设置体系——在 DSH 插件配置里统一维护运营商入口、标注/决策两个角色槽、以及密钥**环境变量名**，并由插件在启动/配置变更时自动落盘为 lit-scout-harvester 的 `config/providers.json`。

- 包名：`@langhuan/dsh-plugin`（服务端 + 浏览器半侧双 face 插件）
- 运行时形态：手写 ESM JS（`lib/`）+ 手写 lazy-CJS client factory + JSDoc/d.ts 类型声明，零构建链，`pnpm add <本地路径>` 即可被 DSH 加载
- 许可证：MIT

## 它做什么

```
DSH 配置（两路汇入，都会触发重同步）
  ├─ cordis.patch.yml（组合默认值层）
  └─ 图形设置卡片 → settings 命名空间 user section（~/.dsh/settings.yaml）
        │  Config schema（schemastery，camelCase）
        ▼
apply(ctx, config) / onChange(settings) —— 校验角色约束
        │  原子写（临时文件 + rename；字节对齐 Python save_registry 输出）
        ▼
<skillsDir>/langhuan-lit-scout-harvester/config/providers.json
        │  snake_case，provider_registry.py 的唯一持久层
        ▼
Lit-Scout-Harvester 两段式标注（annotation → llm 主段；decision → llm.models.decision）
```

同时向 cordis 提供服务 `langhuanProviders`（`ctx.get('langhuanProviders')` → `{ path, snapshot }`），供其他插件消费当前快照。

设计要点：

- **密钥红线**：Config 只存 `apiKeyEnv`（环境变量**名**），永不存 key 本体——与内置 `dsh-llm-pi-ai` 的 `apiKeyEnv` 惯例一致（`role('credential-ref')`）。key 值维护在中央 `~/.agents/skills/langhuan.env`（见 `scripts/install_plugin.sh`）。
- **单向同步**：DSH 配置是本插件管辖区的事实来源；目标文件里 Python 侧新增的未知顶层字段、未知条目字段、以及 DSH 配置之外的本地新增条目，都会被原样保留（合并而非覆盖）。
- **两条变更路径都会落盘**：① cordis.patch.yml 改动 → 插件重启 → `apply` 重跑；② 图形设置卡片保存 → settings 命名空间 user section（`~/.dsh/settings.yaml`）→ `onChange` 换源重同步（无重启）。内容未变化时跳过写入。
- **校验 fail-soft + 写前强校验**：apply 时角色约束违反记 `ctx.logger.error` 并继续；图形卡片写入经 settings `validate` 钩子在持久化**之前**拒绝（草稿保留、最后有效值不变），错误同样会被 Python 侧 `validate()` 复核。

## 图形设置卡片

插件自带浏览器半侧（`lib/client.js`，DSH 客户端模块系统 lazy-CJS factory）。装好并启动后：**设置 → 插件 → 插件配置**页出现「LangHuan 模型运营商」卡片：

- **运营商入口表格**：启用开关、显示名、baseUrl、模型列表（逗号分隔）、logprobs 开关、备注可编辑；`reserved` 预留条目标注「预留·不可访问（锁定）」且整行禁用；**apiKeyEnv 只读展示**（值永远不进表单），下方提示去 `~/.agents/skills/langhuan.env` 填 key。
- **角色分配下拉**：标注/决策两个角色槽，选项按 Python 侧同语义过滤——未启用/reserved 的条目标注原因并禁用，决策角色额外要求 supportsLogprobs；无效选择即时报错并阻止保存。
- **保存/放弃**：草稿本地暂存，保存走 settings 原子 mutate（revision 围栏）；服务端校验拒绝时保留草稿。复位（reset）等价于 unset，回落到 cordis.patch.yml 组合默认值。

> 截图位：卡片 UI 验证截图待补（`docs/` 下留存后在此引用）。
>
> 前提：图形卡片需要服务端注册 settings 命名空间，注册依赖 schemastery schema——**canonical 安装形态**（tarball/npm，见安装一节）下可用；`pnpm link:` 开发安装形态下 providers.json 同步照常，卡片不渲染。卡片渲染还依赖组合里已含 `@deepseek-ai/dsh-client-ui-settings`（web/desktop 出厂组合均含）。

## Config schema

```ts
interface Config {
  /** 模型运营商注册表，provider id → 条目；出厂默认 = LangHuan 7 预置 */
  providers: Record<string, {
    operator: string;              // 运营商显示名
    type: 'openai_compatible' | 'openai_compatible_decision' | 'jev_legacy';
    baseUrl: string;               // OpenAI 兼容端点
    apiKeyEnv: string;             // 密钥环境变量名（只存名）
    models: string[];              // 模型 id 列表
    enabled: boolean;
    supportsLogprobs: boolean;     // decision 角色要求 true
    reserved: boolean;             // reserved=true 禁止选为角色
    note: string;
  }>;
  annotationProvider: string;      // 标注角色 provider id（空串 = 不写入）
  decisionProvider: string;        // 决策角色 provider id（空串 = 不写入）
  skillsDir: string;               // 默认 ~/.agents/skills（支持 ~ 展开）
}
```

角色约束（apply 时校验，等价 `.check()`——schemastery 3.18.2 尚无该 API）：

- 角色必须指向**存在且 enabled** 的条目；**reserved 预留入口禁选**；
- `decisionProvider` 额外要求 `supportsLogprobs === true`（阶段 B 概率输出必需）。

注意：schemastery dict 的默认值**不做按键合并**——patch 里重写 `providers` 时需完整列出要保留的条目（与内置 llm 插件的 patch 写法相同，见下）。

## 安装

### 方式一：本地路径安装（开发/未发布）

```bash
# 1. 建/选一个非 desktop 的 profile（desktop 由 Electron 托管，CLI 拒绝管理）
dsh --profile langhuan --from-default-profile web --dump-config >/dev/null

# 2. 在 profile 目录内把插件包装进来
#    直接 pnpm add 文件夹 = link: 安装，插件以仓库路径为 realpath 运行；
#    想要宿主 schemastery 的 canonical schema 时改用 tarball（见备注）。
cd ~/.dsh/profiles/langhuan
pnpm add D:/opt/dev/LangHuan/dsh-plugin

# 3. cordis.patch.yml 用 - insert: 追加花名册行（普通 - id: 行只能改已存在的条目）
cat >> cordis.patch.yml <<'EOF'
- insert:
    - id: langhuan
      name: '@langhuan/dsh-plugin'
      config:
        annotationProvider: deepseek
        decisionProvider: deepseek
EOF

# 4. 验证合成
dsh --profile langhuan --dump-config | grep -A6 "langhuan"
```

> 备注（两种本地安装形态）：
> - `pnpm add <文件夹>` → link: 链接安装。此时插件的 schemastery 依赖解析不到宿主页，
>   自动回退到内置的零依赖 Standard Schema（校验/默认值语义一致，行为已验证）。
> - `npm pack` 出 tarball 后 `pnpm add <tarball>` → 实体安装。插件落在 profile
>   node_modules 内，经 DSH 的 profiles 级共享 node_modules 解析到宿主 schemastery，
>   使用 canonical `z.object` schema。推荐正式发布前用 tarball 验证。
>
> 两种形态下 DSH 加载、配置校验、providers.json 落盘行为完全一致。

### 方式二：发布到 npm 之后

```bash
dsh plugin --profile langhuan add @langhuan/dsh-plugin
```

（`dsh plugin` 子命令把参数原样转发给 pnpm；同样需要在 cordis.patch.yml 用 `- insert:` 加花名册行，行格式与方式一相同。）

### 修改运营商配置

编辑 profile 的 `cordis.patch.yml`（或对应设置表单），例如给 minimax 填模型并启用。
注意两层结构：花名册行必须包在 `- insert:` 里；行内 `config.providers` 是
dict 整体替换（默认值不按键合并）。错误与正确写法：

```yaml
# ✗ 错误 1：不用 - insert: 包装 → dump-config 报 "patch: entry "langhuan" not found"
# ✗ 错误 2：providers 只写一个条目 → 其余 6 个预置全部被替换掉
- id: langhuan
  name: '@langhuan/dsh-plugin'
  config:
    providers:
      minimax: { models: [MiniMax-M2], enabled: true }
```

```yaml
# ✓ 正确：- insert: 包装；providers 完整列出要保留的条目（条目内字段可省，schema 填默认）
- insert:
    - id: langhuan
      name: '@langhuan/dsh-plugin'
      config:
        annotationProvider: deepseek
        decisionProvider: deepseek
        providers:
          deepseek:
            operator: DeepSeek
            type: openai_compatible
            baseUrl: https://api.deepseek.com/v1
            apiKeyEnv: DEEPSEEK_API_KEY
            models: [deepseek-chat, deepseek-reasoner]
            enabled: true
            supportsLogprobs: true
            reserved: false
            note: 标注 + 决策均可用（logprobs 已验证）
          minimax:
            baseUrl: https://api.minimaxi.com/v1
            apiKeyEnv: MINIMAX_API_KEY
            models: [MiniMax-M2]
            enabled: true
          qwen: { baseUrl: https://dashscope.aliyuncs.com/compatible-mode/v1, apiKeyEnv: QWEN_API_KEY }
          tencent: { reserved: true }
          jev_original: { reserved: true }
          shanghai_sc_llm: {}
          shanghai_sc_jev: {}
```

保存后 DSH 重载该插件 → `apply` 重跑 → providers.json 更新。

## 与 langhuan.env 的关系

```
┌─────────────────────────── DSH 侧 ───────────────────────────┐
│ @langhuan/dsh-plugin  Config                                 │
│   providers.*.apiKeyEnv = "DEEPSEEK_API_KEY"  ←── 只存环境变量名 │
└──────────────────────────────┬───────────────────────────────┘
                               │ 同步（id/角色/入口/变量名）
                               ▼
        langhuan-lit-scout-harvester/config/providers.json
                               │ 运行时 os.environ[api_key_env] 现取
                               ▼
┌─────────────────────── 密钥值侧（唯一事实来源）──────────────────┐
│ ~/.agents/skills/langhuan.env（install_plugin.sh 安装/分发）      │
│   DEEPSEEK_API_KEY=sk-...                                    │
└───────────────────────────────────────────────────────────────┘
```

- DSH 插件管「**用哪个入口、哪个角色、key 叫什么名字**」；
- `langhuan.env` 管「**key 的值**」——单一事实来源，经硬链接分发给各 `langhuan-*/.env`；
- DSH 会话需要拿到 key 值时，把 `langhuan.env` 导出进进程环境即可（两个体系只经环境变量名耦合）。

## DSH Desktop 接入

见 [INSTALL.zh.md](./INSTALL.zh.md)（desktop profile 被 CLI 保留，必须走文件编辑，步骤单独成文）。

## 实现备注

- 加载契约：命名导出 `{ apply, Config, name }`（cordis loader 取模块命名空间对象的 `.apply`/`.Config`，经 `unwrapExports`）；本仓库以本机 DSH 0.1.5-rc.3 的全局包 lib/ 与官方仓库 cookbook 交叉验证。
- Config 双路径（已在 scratch profile 实测两种安装形态）：宿主可解析 `@deepseek-ai/schemastery` 时用 canonical `z.object` schema；`pnpm link:` 本地文件夹安装时插件 realpath 在仓库树内、解析不到宿主依赖，自动回退零依赖 Standard Schema v1（cordis `resolveConfig` 消费 `Config['~standard'].validate`，两条路径解析语义一致）。DSH 的 profiles 级共享 node_modules（`~/.dsh/profiles/node_modules`）使实体安装（tarball/npm）天然走 canonical 路径。
- 浏览器半侧（`lib/client.js`）：DSH 客户端模块系统的 lazy-CJS factory（`window.__ModuleLoader__.load({id, factory(require)})`，契约对照官方 packages/client/{modules,ui-slots,web} 源码与内置 ui-settings-plugins/client.js）。只 `require` 基座内的 react，故 `dsh.client` 无 `external`；`inject: ["@deepseek-ai/dsh-client-ui-settings"]` 保证 settingsScope 提供方先加载。卡片注册进 `settings.plugin.item`（keyed slot，key = settings 命名空间），由 ui-settings-plugins 的「插件配置」页按「Host 服务的命名空间 ∩ 卡片认领」分发。
- 服务端命名空间注册：`ctx.settings.installSection(ctx, 'langhuan', Config, entry, {validate, setSource, onChange})`（内置 dsh-bash-local 同款；user section 持久化到 `$DSH_HOME/settings.yaml`，watched 文档，写后 setSource 换源 + onChange 重同步 providers.json）。注册需 schemastery schema，故仅 canonical 路径注册；回退路径跳过（记 info 日志）。
- 目录里 `lib/` 为手写 ESM + 手写 client factory，无构建步骤；改源码即改产物。peerDependencies 仅 `@deepseek-ai/cordis`（仅类型）与 `@deepseek-ai/schemastery`，由 DSH 宿主解析。
