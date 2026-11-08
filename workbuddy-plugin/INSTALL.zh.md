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

## 可选：以「个人插件」形态安装（推荐，含全部 6 个技能）

除上面的 models.json 同步外，套件还正式提供 WorkBuddy **个人插件市场**形态：
一次安装获得 5 个成员技能 + `langhuan-provider-sync` 宿主技能
（插件包由 `scripts/build_host_plugin.py` 构建，清单 schema 已经实机验证）。

### 1. 构建插件市场包

```bash
python scripts/build_host_plugin.py --host workbuddy   # → dist/workbuddy-marketplace/
```

### 2. 用官方 CLI 登记并安装

CLI 随 WorkBuddy 桌面端安装（`resources/app.asar.unpacked/cli/bin/codebuddy`，
未注册进 PATH，需 node）。**两个环境变量坑**（2026-11-08 实装反馈）：

- CLI 默认配置目录是 `~/.codebuddy`，桌面端读的是 `~/.workbuddy`——
  必须用 `CODEBUDDY_CONFIG_DIR` + `WORKBUDDY_CONFIG_DIR` 同时指过去，否则
  登记写进错误的注册表，桌面端看不到；
- 若当前 shell 注入了 `SERVER__PORT`（桌面端守护进程占用中），CLI 子命令会
  `EADDRINUSE` 崩掉——用 `env -u SERVER__PORT` 取消后再执行。

```bash
CLI="D:/Users/hongh/AppData/Local/Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
RUN=(env -u SERVER__PORT \
     CODEBUDDY_CONFIG_DIR='C:\Users\hongh\.workbuddy' \
     WORKBUDDY_CONFIG_DIR='C:\Users\hongh\.workbuddy')
"${RUN[@]}" node "$CLI" plugin validate "$PWD/dist/workbuddy-marketplace"
"${RUN[@]}" node "$CLI" plugin marketplace add "$PWD/dist/workbuddy-marketplace" --name langhuan-personal
"${RUN[@]}" node "$CLI" plugin install langhuan-suite@langhuan-personal --scope user
```

### 3. 重启并在插件页确认启用

**完全退出** WorkBuddy（托盘右键退出）后重启 → 左侧「插件」→「已安装」→
找到 LangHuan 确认启用。注意（实装反馈）：`plugin install` 只写
`installed_plugins.json`，不一定自动补 `settings.json → enabledPlugins`；
若插件列表里没有本插件，点一下启用即可。**不要在 WorkBuddy 运行时直接
改写 settings.json**——桌面端有文件保护，原子替换会被 `WinError 5` 拒绝。

插件内技能在**新会话**才会进技能索引（安装前启动的会话看不到），可试
触发词「解析 WOS 题录」「同步 LangHuan 运营商到 WorkBuddy」。

### 4. 卸载

```bash
"${RUN[@]}" node "$CLI" plugin uninstall langhuan-suite@langhuan-personal
"${RUN[@]}" node "$CLI" plugin marketplace remove langhuan-personal
```

> 若 `~/.workbuddy/skills/langhuan-*` 还存在此前技能形态安装的同名技能，
> 两者会重复命中触发词——在宿主 UI 里停用其中一份即可，不要两边都删
> （中央 langhuan.env / providers.json 的硬链接在技能目录树内）。

## 可选：安装技能包

把 `workbuddy-plugin/skill/` 打成 zip（`SKILL.md` + `manifest.json` 在包
根）经 WorkBuddy 技能市场上传安装，可在 WorkBuddy 内直接唤起
"同步 LangHuan 运营商到 WorkBuddy" 等触发词。技能包只含方法论与脚本
路径说明，不含 key。若已按上一节安装个人插件，则无需再装此技能包
（插件内已含 `langhuan-provider-sync`）。

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
