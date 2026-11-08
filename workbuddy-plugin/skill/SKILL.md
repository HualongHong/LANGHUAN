---
name: langhuan-provider-sync
version: 0.1.0
description: >
  LangHuan Suite · WorkBuddy 模型供应商同步技能（Provider-Sync v0.1.0）。
  把 LangHuan 中央运营商注册表（providers.json + langhuan.env）中启用的
  OpenAI 兼容供应商，展开为 WorkBuddy（腾讯 CodeBuddy 团队桌面端）自定义
  模型条目并合并写入 models.json（双路径探测、.bak 备份、原子写、幂等），
  使 WorkBuddy 模型选择器可直接选用 langhuan-* 条目。
  ⚠ WorkBuddy 不支持环境变量间接引用，apiKey 只能明文落 models.json——
  这是对 LangHuan「key 只存中央 langhuan.env」默认红线的显性偏离，
  运行同步脚本前须向用户显式提示，且 models.json 不得分发。
whenToUse: >
  当用户已安装 WorkBuddy，希望把 LangHuan 已配置的模型运营商同步为
  WorkBuddy 自定义模型时使用；或 providers.json / langhuan.env 发生变更
  （启用/禁用供应商、改 base_url、增删模型、轮换 key）后需要重新同步时。
  WorkBuddy 设置页无第三方配置卡片 API，同步只能经本脚本完成。
triggers:
  - 同步 LangHuan 运营商到 WorkBuddy
  - 更新 WorkBuddy 自定义模型列表
  - 轮换 API key 后重跑 WorkBuddy 同步
  - WorkBuddy 里 langhuan-* 模型不见了
category: integration
author: LangHuan Suite
license: MIT
---

# LangHuan Provider-Sync（WorkBuddy 模型供应商同步）

WorkBuddy 没有第三方设置卡片 API，模型供应商配置落在用户本机的
`models.json`（`%USERPROFILE%\.workbuddy-ai\models.json` 新版 /
`%USERPROFILE%\.workbuddy\models.json` 旧版）。本技能包把 LangHuan
中央 providers.json 中 `enabled && !reserved` 且类型可导出的供应商，
经同步脚本展开为 `langhuan-*` 自定义模型条目合并写入该文件。

## 何时重跑同步脚本

`workbuddy-plugin/scripts/sync_workbuddy_models.py`（Python ≥3.12，零依赖）
在以下情况需要重新运行：

1. **首次安装**：装好 WorkBuddy 后跑一次（见 INSTALL.zh.md）。
2. **providers.json 变更**：启用/禁用供应商、修改 base_url、增删 models。
3. **key 轮换**：中央 langhuan.env 中对应 `*_API_KEY` 的值更新后
   （WorkBuddy 存的是明文副本，不同步不会自动更新）。
4. **WorkBuddy 升级或重装**：models.json 路径（`.workbuddy` ↔
   `.workbuddy-ai`）或 schema 可能漂移，重跑脚本重新探测并 fail-soft 合并。
5. **langhuan-* 条目在 WorkBuddy 中消失/失效**：先跑 `--dry-run` 核对，
   再实跑，然后完全退出并重启 WorkBuddy。

## 运行

```bash
python workbuddy-plugin/scripts/sync_workbuddy_models.py --dry-run   # 先核对
python workbuddy-plugin/scripts/sync_workbuddy_models.py             # 实跑
```

默认读取 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json`
与 `~/.agents/skills/langhuan.env`，可用 `--providers` / `--env` / `--models`
覆盖。脚本每次运行开头都会打印密钥红线偏离提醒；合并规则为保留所有非
`langhuan-` 前缀条目、替换旧 `langhuan-` 条目、写前 `.bak`、原子写、重复
运行幂等。同步完成后须**完全退出 WorkBuddy 再重启**（仅关窗口不生效）。

## 红线

除脚本运行时从中央 langhuan.env 解析值写入用户本机 models.json 外，
任何仓库文件不得出现真实 key。models.json 含明文 key，**请勿分发**。

## 局限

- `ssh_tunnel` / `jev_legacy` 类型无 WorkBuddy 落点，同步时跳过并记日志。
- supportsReasoning 按 type（decision 类）与模型名（含 "reason"）推断，
  可能与 WorkBuddy 实际能力位有出入，可在 WorkBuddy 模型设置页手工修正
  （修正不会被下次同步覆盖——仅 `langhuan-` 前缀条目会被替换）。
- models.json 逐字段 schema 未获官方公布（社区实证）；读取对未知字段
  宽容保留，若 WorkBuddy 升级后 schema 漂移导致条目不识别，按 fail-soft
  退回 WorkBuddy 设置页手动添加并反馈套件维护者。
