# langhuan-provider-card（TRAE/VS Code 扩展）

LangHuan Suite 的 TRAE 插件：把 LangHuan 技能群的**模型运营商注册表**搬进 TRAE（VS Code fork）的设置体系——在设置卡片里统一维护标注/决策两个角色槽、技能目录，以及运营商入口的进阶覆盖，并由扩展在启动/配置变更时自动落盘为 lit-scout-harvester 的 `config/providers.json`。

- 形态：纯 JavaScript、无构建步骤的标准 VS Code 扩展（`package.json` + `extension.js` + `lib/`），TRAE 按 VS Code 扩展契约加载（.vsix / 开发态目录）
- 共享核心：`@langhuan/plugin-core`（ESM，零依赖）——10 家运营商出厂预置、校验、providers.json 原子落盘；与 DSH 插件单点同源
- 许可证：MIT

## 它做什么

```
TRAE 设置卡片（设置里搜 "LangHuan"，写 settings.json）
  ├─ langhuan.annotationProvider（enum：未配置 + 10 家 provider id）
  ├─ langhuan.decisionProvider（同上）
  ├─ langhuan.skillsDir（默认 ~/.agents/skills）
  └─ langhuan.providers（object，按 id 逐字段覆盖出厂预置，进阶）
        │  extension.js：onStartupFinished activate → sync('boot')
        │  onDidChangeConfiguration(affectsConfiguration('langhuan')) → sync('config-change')
        ▼
lib/sync.js 合并出厂预置 + 覆盖（逐 id 逐字段浅覆盖，disabled/reserved 语义不被误动）
        │  plugin-core：unwrapResolved → validateConfig（错误记 Output Channel "LangHuan"）
        ▼
syncProvidersJson：原子写（临时文件 + rename；字节对齐 Python save_registry 输出；
                  内容不变跳过；目标文件里插件管辖区之外的本地字段/条目原样保留）
        ▼
<skillsDir>/langhuan-lit-scout-harvester/config/providers.json
        │  snake_case，provider_registry.py 的唯一持久层
        ▼
Lit-Scout-Harvester 两段式标注（annotation → llm 主段；decision → llm.models.decision）
```

设计要点：

- **密钥红线**：配置里只存 `apiKeyEnv`（环境变量**名**），永不存 key 本体；key 值维护在中央 `~/.agents/skills/langhuan.env`（见下文「与 langhuan.env 的关系」）。
- **同步绝不抛给宿主**：动态加载 plugin-core、读设置、合并、校验、落盘全链路 try/catch，失败只落 **Output Channel「LangHuan」**（查看：命令面板 → `Output: Focus on Output View` → 通道选 LangHuan）。
- **CJS/ESM 边界**：扩展宿主只加载 CJS，`extension.js` 顶层不静态 import ESM，activate 后按需 `import('@langhuan/plugin-core')` 惰性加载（只做一次，失败记日志降级为空操作）。
- **单向同步**：设置卡片是管辖区的事实来源；providers.json 里 Python 侧新增的未知字段/条目被合并保留。

## 设置卡片

安装并启用后，**设置 → 搜索 "LangHuan"**（或编辑 settings.json 的 `langhuan.*` 键）：

| 设置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `langhuan.annotationProvider` | enum | `deepseek` | 标注角色 provider id；`""` = 不指定（落盘 `annotation_provider: null`） |
| `langhuan.decisionProvider` | enum | `deepseek` | 决策角色 provider id（阶段 B 概率输出要求 `supportsLogprobs`）；`""` = 不指定 |
| `langhuan.skillsDir` | string | `~/.agents/skills` | 技能安装目录，`~` 自动展开 |
| `langhuan.providers` | object | `{}` | 按 provider id 覆盖出厂预置的字段（进阶） |

角色约束（未启用 / reserved / 决策角色缺 logprobs 等）违反时不阻止写入，但每条都会记到 Output Channel——以日志为准修正配置。

### langhuan.providers 覆盖语法

在 settings.json 里按「provider id → 要改的字段」逐字段覆盖，未提及的出厂字段（含 `enabled` / `reserved` / `models` / `apiKeyEnv`）原样保留：

```jsonc
{
  "langhuan.providers": {
    "deepseek": { "baseUrl": "https://proxy.internal/v1" },
    "qwen": { "enabled": true, "models": ["qwen-max"] },
    "my_gateway": { "operator": "自研网关", "baseUrl": "https://gw.internal/v1", "apiKeyEnv": "GW_API_KEY" }
  }
}
```

未知 id（如 `my_gateway`）视为用户自建运营商，缺省字段在落盘时按 plugin-core 兜底值补齐。

## 与中央 langhuan.env 的关系

- 设置卡片 / providers.json 里只有 `apiKeyEnv` **变量名**（如 `DEEPSEEK_API_KEY`），**没有 key 值**——任何文件都不应出现真实 key。
- key 值的单一事实来源是中央 `~/.agents/skills/langhuan.env`（经硬链接分发给全部 langhuan-* 技能）。运行需要 key 的脚本前：

  ```bash
  set -a; . ~/.agents/skills/langhuan.env; set +a
  ```

- 要换 key：改 langhuan.env，**不需要**动 TRAE 设置；要换 key 存哪个变量名：改 `langhuan.providers` 里对应条目的 `apiKeyEnv`。

## 代码结构与测试

```
trae-plugin/
├── package.json        # 扩展清单 + contributes.configuration（设置卡片）
├── extension.js        # CJS 宿主入口：activate/deactivate、output channel、sync
├── lib/sync.js         # 纯逻辑：合并覆盖 + 构造 configSlice（不依赖 vscode）
├── test/sync.test.js   # node:test 单测（node --test）
└── node_modules/@langhuan/plugin-core  # file:../plugin-core（见 INSTALL.zh.md 打包注意）
```

```bash
cd trae-plugin
npm install        # 装 file:../plugin-core
npm test           # = node --test（Git Bash 下请勿写 node --test test/，见 INSTALL.zh.md 排错）
```

安装与调试（F5 开发态、.vsix 分发）见 [INSTALL.zh.md](./INSTALL.zh.md)。
