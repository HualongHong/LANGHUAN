---
name: set-provider
description: 把 LangHuan 运营商注册表同步进 Kimi Code config.toml（或按卡片字段手工编辑）。
---

把 LangHuan 模型运营商注册表接入当前 Kimi Code 的 `config.toml`（`$KIMI_CODE_HOME/config.toml`，缺省 `~/.kimi-code/config.toml`）。

用户参数：$ARGUMENTS（可为空、provider id，或 `--dry-run` 等脚本参数）

按以下步骤执行：

1. 默认优先调用同步脚本（幂等、整块替换、写前 `.bak` 备份）：

   ```bash
   python <本插件目录>/scripts/sync_kimi_providers.py $ARGUMENTS
   ```

   - 用户给了 provider id 等定位参数时，照常全量同步即可（标记块机制本身就是按 providers.json 全量重建该块）；若是 `--dry-run`，只打印块内容并说明将发生什么。
   - 脚本不可用时（缺 Python 等），降级为手工编辑 `config.toml`：在 `# >>> langhuan-providers` … `# <<< langhuan-providers` 标记块内，按技能 `provider-config` 手册的字段对照表写入 `[providers."langhuan-<id>"]`（type/base_url/api_key_env）与逐模型 `[models."langhuan-<id>--<model>"]`。

2. 只同步 `enabled && !reserved` 且类型为 `openai_compatible` / `openai_compatible_decision` / `model_deployment` 的条目；`ssh_tunnel` 跳过（隧道由 LangHuan 的 model_tunnel.py 管理）。

3. **密钥红线**：全程只出现 `api_key_env` 环境变量名，绝不写明文 key、绝不让用户把 key 贴进 config.toml。

4. 完成后提示用户：
   - `config.toml` 默认文件 watch 热更新，一般无需操作；未生效时执行 `/reload`；
   - key 需存在于 **Kimi 进程的环境**中：Windows 用 `setx <变量名> "sk-..."`（新开的进程生效）或在启动 Kimi 的 shell 里先导出；变量名清单看刚写入的 `api_key_env` 行。
