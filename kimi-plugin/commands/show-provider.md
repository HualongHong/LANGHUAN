---
description: 展示当前 config.toml 中 LangHuan 管理的 providers/models 块与同步状态。
---

向用户展示 Kimi Code `config.toml`（`$KIMI_CODE_HOME/config.toml`，缺省 `~/.kimi-code/config.toml`）里 LangHuan 插件管理的运营商配置现状。

用户参数：$ARGUMENTS（可为空或 provider id）

按以下步骤执行：

1. 读取 `config.toml`，抽取 `# >>> langhuan-providers` … `# <<< langhuan-providers` 标记块；不存在时明确告知"尚未接入，请运行 /langhuan-provider-card:set-provider"。$ARGUMENTS 给出 provider id 时只展示对应 `langhuan-<id>` 段落。
2. 对照源文件 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json` 汇总：
   - 块内每家 provider 的 `id / type / base_url / api_key_env / models`（只展示变量名，不展示、也不要求展示 key 值）；
   - 哪些条目被跳过及原因：`reserved=true`、未启用、`ssh_tunnel`（隧道条目）、未知类型。
3. 核对每个 `api_key_env` 变量在**当前 Kimi 进程环境**中是否有值（只看有无，不打印值），并列出缺失项——缺失是 401 的头号原因，引导用户用 `setx` 或在启动 shell 导出后重启 Kimi。
4. 提示：如需刷新块内容（providers.json 改过），运行 `/langhuan-provider-card:set-provider`。
