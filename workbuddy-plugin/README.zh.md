# WorkBuddy 插件（LangHuan 套件）

把 LangHuan 中央运营商注册表同步为 **WorkBuddy**（腾讯 CodeBuddy 团队桌面端）
的自定义模型条目。WorkBuddy 没有第三方设置卡片 API，模型供应商配置落在用户
本机 `models.json`，本插件以**本地同步脚本**方式完成注入，附 WorkBuddy 技能包
`langhuan-provider-sync`。

> ## ⚠ 密钥红线偏离（必读）
>
> **WorkBuddy 不支持环境变量间接引用——apiKey 只能明文写入 models.json。**
> 这与 LangHuan「key 只存中央 langhuan.env」的默认红线**不同**，属对
> WorkBuddy 能力的显性适配。脚本每次运行开头都会打印此提醒。
>
> - **请勿分发 models.json**（内含明文 key），也不要把它截图贴进公开渠道。
> - 同步脚本只从中央 langhuan.env 解析值写入**用户本机**的 models.json；
>   除此之外，套件任何仓库文件都不会出现真实 key。
> - WorkBuddy 存的是 key 的明文副本：中央 langhuan.env 轮换 key 后，必须
>   重跑同步脚本，否则 WorkBuddy 侧仍是旧 key。

## 目录结构

```
workbuddy-plugin/
├── scripts/
│   └── sync_workbuddy_models.py   # 同步脚本（Python ≥3.12，零依赖）
├── skill/
│   ├── SKILL.md                   # WorkBuddy 技能包（含 frontmatter）
│   └── manifest.json              # WorkBuddy 技能清单（id/name/triggers…）
├── README.zh.md                   # 本文件
└── INSTALL.zh.md                  # 安装与排错
```

## 工作原理

1. 读取 `providers.json`（默认
   `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json`），
   过滤 `enabled && !reserved` 且 `type ∈ {openai_compatible,
   openai_compatible_decision, model_deployment}` 的条目；
   `ssh_tunnel` / `jev_legacy` / 未知 type 跳过并记日志（fail-soft）。
2. 每个 `(provider, model)` 展开为一条 models.json 条目：
   - `id = "langhuan-<pid>--<model>"`（非法字符折叠为 `-`）
   - `name = "<operator> · <model>"`，`vendor = "Custom"`
   - `url = base_url` 规范化（去尾斜杠；不以 `/v1` 结尾则补 `/v1`）
   - `apiKey =` 该 provider 的 `api_key_env` 变量在 langhuan.env
     （回退进程环境）中的值；解析不到 → 记 WARN 并写入空串
   - `supportsToolCall = true`，`supportsImages = false`，
     `useCustomProtocol = false`
   - `supportsReasoning` 按 type（decision 类 → true）与模型名
     （含 "reason"，如 `deepseek-reasoner` → true）推断
3. 合并写盘：**保留所有非 `langhuan-` 前缀条目**（含你在 WorkBuddy
   里手工添加的其他自定义模型），删除旧 `langhuan-` 条目后追加新条目；
   写前生成 `models.json.bak`，临时文件 + 原子替换写入；重复运行幂等。

## models.json 路径（双路径探测）

| 版本 | 路径 | 优先级 |
|---|---|---|
| 新版 | `%USERPROFILE%\.workbuddy-ai\models.json` | 优先 |
| 旧版 | `%USERPROFILE%\.workbuddy\models.json` | 兜底 |

两处都无现存文件时，若对应目录已存在（装过 WorkBuddy 但还没加过自定义
模型），脚本会在**新版目录**就地新建 models.json；两个目录都不存在则报错
退出，提示先装 WorkBuddy。以 WorkBuddy 模型设置页显示的路径为准；非常规
安装可用 `--models <路径>` 显式指定。

## ⚠ schema 未官方公布（fail-soft）

models.json 条目字段（`id/name/vendor/url/apiKey/supportsToolCall/
supportsImages/supportsReasoning/useCustomProtocol`）来自社区实证，腾讯
未公布逐字段官方 schema（历史资料中 url/baseUrl 两派写法并存）。因此：

- **写盘**按社区实证的字段集；**读取**对未知字段宽容——现有条目整条目
  原样保留回写，不认识的字段不丢。
- WorkBuddy 升级后若 schema 漂移导致 `langhuan-*` 条目不识别：先跑
  `--dry-run` 看报告，再退回 WorkBuddy 设置页手动添加对照差异，并反馈
  套件维护者更新脚本。

## 用法

```bash
# 默认路径：providers 与 langhuan.env 取自中央 skills 目录
python workbuddy-plugin/scripts/sync_workbuddy_models.py --dry-run   # 先核对
python workbuddy-plugin/scripts/sync_workbuddy_models.py             # 实跑

# 全部参数
python workbuddy-plugin/scripts/sync_workbuddy_models.py \
    --providers <providers.json>   # 默认 ~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json \
    --env <langhuan.env>           # 默认 ~/.agents/skills/langhuan.env \
    --models <models.json>         # 覆盖双路径探测（测试/非常规安装） \
    --dry-run                      # 只报告不写盘
```

输出说明：`[SKIP]` 过滤掉的供应商、`[WARN]` 需关注（key 未解析、URL 含
占位符等，不致命）、`[ADD]` 新增条目（apiKey 只显示是否解析到，**永不打印
值本身**）、`[MERGE]` 保留/删除/新增计数、`[BACKUP]/[WRITE]` 落盘确认。
退出码：0 = 完成（WARNING 另计）；1 = 致命错误（输入缺失、两处 WorkBuddy
目录都不存在等）。

同步完成后须**完全退出 WorkBuddy 再重启**（托盘图标右键退出，仅关窗口不
生效），然后在模型选择器选用 `langhuan-*` 条目。

## 何时重跑

见 `skill/SKILL.md`：providers.json 变更、key 轮换、WorkBuddy 升级/重装、
langhuan-* 条目消失时。

## 与套件其他部分的关系

- 与 `dsh-plugin/`（DSH 设置卡片）共享同一份 providers.json 与中央
  langhuan.env——**单一事实来源不变**，本插件只是多一个下游消费者。
- 共享核心语义对照见 `docs/research/workbuddy-plugin-contract.md`（调研
  结论：WorkBuddy 无第三方设置卡片 API，models.json 注入是唯一主路径）。

## 许可

MIT（LangHuan Suite）
