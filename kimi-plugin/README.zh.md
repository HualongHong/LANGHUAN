# langhuan-provider-card（Kimi Code 插件）

LangHuan Suite 的 Kimi Code CLI 插件：把套件**中央模型运营商注册表**（`providers.json`）同步为 Kimi Code `config.toml` 的 `[providers.*]` / `[models.*]` 配置，附字段手册技能与两个斜杠命令。密钥只经 `api_key_env` 环境变量**名**引用，全程无明文 key。

- manifest：`kimi.plugin.json`（name `langhuan-provider-card`，v0.1.0）
- 技能：`skills/provider-config/SKILL.md`（providers.json ↔ config.toml 字段手册 + Windows setx 引导）
- 命令：`/langhuan-provider-card:set-provider`、`/langhuan-provider-card:show-provider`
- 同步脚本：`scripts/sync_kimi_providers.py`（Python ≥3.12，零依赖，只读环境变量名、永不读 key 值）
- 许可证：MIT

## 它做什么

```
~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json
        │  只取 enabled && !reserved 的 OpenAI 兼容条目
        ▼  scripts/sync_kimi_providers.py（标记块整块替换，块外原样保留）
$KIMI_CODE_HOME/config.toml   缺省 ~/.kimi-code/config.toml
        │  # >>> langhuan-providers … # <<< langhuan-providers
        ▼
[providers."langhuan-<id>"]  type/base_url/api_key_env
[models."langhuan-<id>--<model>"]  provider/model
        │  运行时 Kimi 凭 api_key_env 变量名从进程环境取 key
        ▼
key 值唯一事实来源：~/.agents/skills/langhuan.env（永不进本插件任何文件）
```

要点：

- **幂等**：重复运行整块替换标记块，内容无变化时跳过写入（不产生新 .bak）。
- **安全写**：写前 `config.toml.bak` 备份；原子写（tmp + replace）；块外内容原样保留。
- **跳过并记日志**：`reserved` / 未启用 / `ssh_tunnel`（隧道由 LSH 的 `scripts/model_tunnel.py` 管理）/ 未知类型 / 缺 `base_url` 或 `api_key_env` 的条目。

## 安装

### Kimi Code CLI

```
/plugins install D:/opt/dev/LangHuan/kimi-plugin
```

（也支持 zip：把 `kimi-plugin/` 目录打包后 `/plugins install <zip 路径或 URL>`；或 GitHub 仓库。）装完按提示 `/plugins reload` 或开新会话。卸载：`/plugins uninstall langhuan-provider-card`。

### Kimi Work 桌面端

经 Plugin Builder 转换进入（Plugin Builder 可把 `.codex-plugin` / `.claude-plugin` / `plugin.json` 转为 `kimi.plugin.json` 登记为个人插件）；转换后配置入口仍是本插件的斜杠命令与脚本。

## 使用

### 1. 同步运营商配置

在 Kimi Code 里：

```
/langhuan-provider-card:set-provider
```

宿主 Agent 会调用 `scripts/sync_kimi_providers.py`（幂等），或在脚本不可用时按技能手册手工编辑 `config.toml` 的标记块。手动跑脚本等价：

```bash
python scripts/sync_kimi_providers.py                    # 实跑
python scripts/sync_kimi_providers.py --dry-run          # 只看将要写入的块
python scripts/sync_kimi_providers.py --providers /path/providers.json --kimi-home /path/.kimi-code
```

### 2. 查看现状

```
/langhuan-provider-card:show-provider
```

展示已接入的 provider/model、`api_key_env` 变量名，以及各变量在当前 Kimi 进程环境里**是否有值**（只看有无，不显示值）。

### 3. 生效时机

`config.toml` 默认有文件 watch 热更新，同步完一般立刻生效；未生效时执行 `/reload`（或开新会话）。

## 与中央 langhuan.env 的关系

```
apiKeyEnv/api_key_env = "DEEPSEEK_API_KEY"   ← 插件与脚本只搬运这个「变量名」
                         │
key 的值                 ▼
~/.agents/skills/langhuan.env（DEEPSEEK_API_KEY=sk-...）
```

- 本插件管「**用哪个入口、key 叫什么名字**」；`langhuan.env` 管「**key 的值**」——单一事实来源。
- **Kimi 不从 shell 环境变量自动回退**，必须 `api_key_env` 显式声明；且 Kimi 只在自己的**进程环境**里按名取值。Windows 持久化用 `setx`（新进程生效）：

  ```bat
  setx DEEPSEEK_API_KEY "sk-你的key"
  ```

  已在运行的 Kimi 拿不到 `setx` 新值，需重启；也可在启动 Kimi 的 shell 里先导出再启动：

  ```bash
  set -a; . ~/.agents/skills/langhuan.env; set +a   # Git Bash 示例，路径按实际
  ```

- 排障口诀：401 → 变量没进 Kimi 进程；模型列表缺 → 跑 `show-provider` 看跳过原因。

## 密钥红线

任何文件不得出现真实 API key：manifest、技能、命令、脚本、README 只出现环境变量名；`config.toml` 只写 `api_key_env`，不写明文 `api_key`。

## 备注

- Kimi 侧 provider type 一律写 `"openai"`（LangHuan 五类在 HTTP 层全部 OpenAI 兼容）。
- 与用户自有 provider 不冲突：本插件全部条目加 `langhuan-` 前缀，且只动标记块内。
- 调研依据：`docs/research/kimi-plugin-contract.md`（Kimi 全系无「设置卡片注入」机制，故 DSH 形态降级为 插件 + 斜杠命令 + 文档引导）。
