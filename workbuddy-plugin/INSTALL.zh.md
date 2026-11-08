# WorkBuddy 插件安装指南（LangHuan 套件）

> ## ⚠ 密钥红线偏离（安装前必读）
>
> **WorkBuddy 仅支持 apiKey 明文存储在 models.json，不支持环境变量间接引用。**
> 运行同步脚本即表示接受：LangHuan 中央 langhuan.env 中的 key 值将以**明文**
> 写入你本机的 `models.json`。这与 LangHuan「key 只存中央 langhuan.env」的
> 默认红线不同，属对 WorkBuddy 能力的适配。
>
> - **请勿分发 models.json**（内含明文 key）。
> - key 轮换后必须重跑同步脚本（WorkBuddy 存的是明文副本，不会自动更新）。

## 前置条件

| 项 | 要求 |
|---|---|
| WorkBuddy | Windows x64 / macOS 桌面端（腾讯 CodeBuddy 团队出品），未安装则脚本会报错退出 |
| Python | ≥ 3.12（零依赖标准库脚本，无需 pip 安装任何东西） |
| LangHuan 配置 | 中央 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json` 已有启用且配好 `models` 的供应商；key 在中央 `~/.agents/skills/langhuan.env` 已填值 |

## 安装步骤

### 1. 安装 WorkBuddy 并确认 models.json 路径

安装 WorkBuddy 桌面端并启动一次（让它生成配置目录）。模型设置页
（头像 → 系统设置 → 模型）会显示实际路径：

- 新版：`%USERPROFILE%\.workbuddy-ai\models.json`
- 旧版：`%USERPROFILE%\.workbuddy\models.json`

脚本按 **`.workbuddy-ai` 优先、`.workbuddy` 兜底** 双路径探测，无需手填。
两处都没有 models.json 但目录在时，脚本会在新版目录就地新建。

### 2. 运行同步脚本（先 dry-run 核对）

```bash
python workbuddy-plugin/scripts/sync_workbuddy_models.py --dry-run
```

核对输出：过滤/跳过是否符合预期、`[ADD]` 条目数、apiKey 是否全部
"已解析"（**输出永不打印 key 值**）、`[MERGE]` 保留/删除/新增计数。
有 `[WARN]`（如某供应商 key 未解析到 → 会写入空串）先回中央 langhuan.env
补值，再重新核对。

### 3. 实跑写入

```bash
python workbuddy-plugin/scripts/sync_workbuddy_models.py
```

确认 `[BACKUP]`（models.json.bak）与 `[WRITE]` 行。脚本保留所有非
`langhuan-` 前缀条目（你在 WorkBuddy 里手工加的其他自定义模型不受影响），
仅替换旧的 `langhuan-` 条目；重复运行幂等。

### 4. 完全退出并重启 WorkBuddy

**仅关窗口不生效**——须托盘图标右键「退出」（macOS 为菜单栏图标退出）
彻底结束进程后重新启动。

### 5. 选用 langhuan-* 模型

在 WorkBuddy 模型选择器中选用同步进来的 `langhuan-<供应商>--<模型>`
条目（显示名为「<运营商> · <模型>」）。若列表里没有：

1. 确认是**完全退出**后重启（见第 4 步）；
2. 打开 models.json 核对条目存在且 JSON 合法；
3. 换 `--models` 指定实际路径重跑一次。

## 可选：安装技能包

把 `workbuddy-plugin/skill/` 打成 zip（`SKILL.md` + `manifest.json` 在包
根）经 WorkBuddy 技能市场上传安装，可在 WorkBuddy 内直接唤起
"同步 LangHuan 运营商到 WorkBuddy" 等触发词。技能包只含方法论与脚本
路径说明，不含 key。

## 排错

| 症状 | 处理 |
|---|---|
| `未找到 WorkBuddy：两个目录均不存在` | 先安装 WorkBuddy 并启动一次；非常规安装用 `--models <路径>` |
| 某条目 apiKey 为空串（`[WARN] …未解析到值`） | 检查中央 langhuan.env 变量名与 providers.json 的 `api_key_env` 是否一致、值是否已填；补值后重跑 |
| 模型选择器里条目灰色/不可用 | WorkBuddy 未官方公布逐字段 schema；先在 WorkBuddy 模型设置页对照手工添加一条可用条目，把字段差异反馈套件维护者（fail-soft：未知字段我们不丢、手工修正不被下次同步覆盖） |
| 想撤掉同步 | 删除 models.json 中全部 `langhuan-` 前缀条目（或用 `.bak` 还原整个文件），再重启 WorkBuddy |
| WorkBuddy 升级后条目消失/schema 变化 | 重跑 `--dry-run` 看报告；必要时回设置页手动添加，并反馈维护者 |

## 卸载

删除 models.json 中的 `langhuan-` 前缀条目（或整体还原 `.bak`）即可，
同步脚本不会在别处留状态。
