---
name: provider-config
description: LangHuan 模型运营商注册表 → Kimi Code config.toml 字段手册。把 providers.json 条目翻译成 [providers.*]/[models.*] 写法，含 api_key_env 语义对照、Windows setx 引导与 Kimi 凭证读取规则。
whenToUse: 需要把 LangHuan 运营商卡片接入 Kimi Code、手写或校对 config.toml 的 providers/models 段、排查 api_key_env 不生效、向用户解释 Kimi 为什么不读 shell 环境变量时调用。
---

# LangHuan 运营商卡片 → Kimi Code 配置手册

本手册把 LangHuan 套件的**模型运营商注册表**（`providers.json`）逐字段映射到 Kimi Code CLI 的主配置 `config.toml`。配套斜杠命令：`/langhuan-provider-card:set-provider`（同步）、`/langhuan-provider-card:show-provider`（查看）。

## 0. 三条铁律

1. **Kimi Code 不从 shell 环境变量自动读 key**。凭证只从 `config.toml` 读取——要么写明文 `api_key`（禁止），要么显式声明 `api_key_env = "变量名"`，由 Kimi 凭变量名从**启动 Kimi 的那个进程环境**里取值。变量没导出、Kimi 进程拿不到，调用就 401。
2. **脚本与文档永不出现 key 本体**。我们全程只搬运环境变量**名**（如 `DEEPSEEK_API_KEY`），key 值只存在中央 `~/.agents/skills/langhuan.env`。
3. **`config.toml` 默认有文件 watch 热更新**：脚本改完即生效；个别版本未热更时让用户跑 `/reload`。

## 1. Kimi Code config.toml 的写法

数据根：`$KIMI_CODE_HOME/config.toml`，缺省 `~/.kimi-code/config.toml`（Windows 即 `%USERPROFILE%\.kimi-code\config.toml`）。

```toml
[providers."my-provider"]
type = "openai"
base_url = "https://api.example.com/v1"
api_key_env = "MY_PROVIDER_KEY"   # 只写变量名；不写明文 key

[models."my-model"]
provider = "my-provider"          # 指向上面的 provider id
model = "my-model"                # 对方服务认识的模型名
```

LangHuan 插件写入时统一加 `langhuan-` 前缀避免与用户自己的 provider 冲突：`[providers."langhuan-deepseek"]`、模型 id `langhuan-deepseek--deepseek-chat`。

## 2. 字段对照表（providers.json → config.toml）

providers.json 条目（snake_case）位于 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json`：

| providers.json 字段 | config.toml 落点 | 说明 |
|---|---|---|
| `id` | 前缀进 provider/model id | `langhuan-<id>` / `langhuan-<id>--<model>` |
| `operator` | 注释 | 写成块内注释行，如 `# DeepSeek（deepseek）` |
| `type` | `type = "openai"` | LangHuan 的 5 类见下表「类型处理」 |
| `base_url` | `base_url` | 原样 |
| `api_key_env` | `api_key_env` | **语义天然一致：都只存环境变量名，永不存 key 值** |
| `models[]` | 逐条 `[models."langhuan-<id>--<model>"]` | 每个模型一条，含 `provider=` 与 `model=` 两行 |
| `note` | 注释 | `# 备注：<note>`，换行压成空格 |
| `enabled` / `reserved` | 过滤条件 | 同步脚本只写 `enabled && !reserved` 的条目 |
| `supports_logprobs` | **不映射** | Kimi 侧无对应语义；决策模型需要 logprobs 的约束在 LangHuan 内部消化 |
| `ssh_host`/`ssh_port`/`ssh_user`/`ssh_key_path`/`remote_host`/`remote_port`/`local_port` | **不映射** | Kimi 不管隧道；ssh_tunnel 条目整体跳过并记日志，隧道由 LSH 的 `scripts/model_tunnel.py` 管理，隧道通了 base_url 就是普通 OpenAI 端点 |
| （无源字段） | `type = "openai"` | Kimi 的 provider type 取值；LangHuan 全部是 OpenAI 兼容 HTTP 层 |

类型处理：`openai_compatible`、`openai_compatible_decision`、`model_deployment` 三类 → `type = "openai"`；`ssh_tunnel` → 跳过（记日志，见上）；`jev_legacy` 等其他未知类型 → 跳过（记日志）。

## 3. Windows 下设置环境变量（setx 引导）

Kimi 只在**自己的进程环境**里按变量名找 key。Windows 持久化：

```bat
:: 用户级永久写入（新开的终端/程序生效；已运行的 Kimi 需重启或 /reload 前重开）
setx DEEPSEEK_API_KEY "sk-你的key"

:: 当前会话临时导出（Git Bash）
export DEEPSEEK_API_KEY="sk-你的key"
```

要点：

- `setx` 只影响**之后**启动的进程；正在运行的 Kimi 拿不到，需重启 Kimi 或在其 shell 里导出后重开。
- 套件各技能统一从中央 `~/.agents/skills/langhuan.env` 取 key（安装时经硬链接分发到各 `.env`）。让 Kimi 也吃到同一批 key，可在启动 Kimi 的 shell 里先 `set -a; . ~/langhuan.env; set +a`（路径按实际）再启动，或把变量逐个 `setx` 固化。
- 校验：让 Kimi 用户问一句 `/model` 相关或直接用同步进来的模型发一条消息，401 就是变量没进 Kimi 进程。

## 4. 同步脚本

`scripts/sync_kimi_providers.py`（Python ≥3.12，零依赖，只读环境变量名、**永不读 key 值**）：

```bash
python scripts/sync_kimi_providers.py            # 默认源 providers.json + 默认目标 config.toml
python scripts/sync_kimi_providers.py --dry-run  # 只打印将要写入的标记块，不动文件
python scripts/sync_kimi_providers.py --providers /path/to/providers.json --kimi-home /path/to/.kimi-code
```

行为：在 `config.toml` 管理 `# >>> langhuan-providers` … `# <<< langhuan-providers` 标记块（整块替换，块外内容原样保留）；写前 `.bak` 备份、原子写（tmp+replace）；内容无变化时跳过写入（幂等）；退出码 0 成功 / 1 失败。
