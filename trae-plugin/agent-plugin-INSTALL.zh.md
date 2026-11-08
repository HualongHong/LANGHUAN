# Trae CN 原生 Agent 插件安装指南（langhuan）

> 本文档对应 **Agent 插件形态**（`.trae-plugin/plugin.json` + `skills/`，
> 装在 `~/.trae-cn/plugins/`），与 `trae-plugin/` 的 **VS Code 扩展形态**
> （设置卡片，`.vsix`）是两套独立体系，可分别或同时安装。
> 内容依据 2026-11-08 Trae CN 3.4.15 实装反馈整理。

## 前置条件

- Trae CN（Windows）；插件根目录约定：
  `C:\Users\<你>\.trae-cn\plugins\<registry>\<plugin-name>\<version>\`
- Python ≥ 3.14（技能运行时基线；插件安装本身不涉及）

## 1. 构建插件包

```bash
python scripts/build_host_plugin.py --host trae   # → dist/trae-agent-plugin/
```

包结构（清单 schema 取自 2026-11-08 实机验证通过的安装包）：

```
trae-agent-plugin/
├── .trae-plugin/plugin.json    # name=langhuan, skills="./skills/", 含 interface 块
├── README.md / LICENSE / suite.yaml
└── skills/langhuan-{lit-scout-harvester,archive-plate-assembler,
                      anchor-verify-strategizer,peer-collab-strategizer,
                      percolation-scout}/
```

## 2. 部署到插件目录

把包内容拷入个人插件注册表（`trae-user-upload` 是 Trae 个人插件的
registry，第三方市场只白名单它）：

```powershell
$dst = "$env:USERPROFILE\.trae-cn\plugins\trae-user-upload\langhuan\0.1.0-dev"
robocopy D:\opt\dev\LangHuan\dist\trae-agent-plugin $dst /E
```

写安装成功标记 `.success`（JSON，字段为 2026-11-08 实测格式）：

```json
{
  "logical_name": "trae-user-upload:langhuan",
  "registry": "trae-user-upload",
  "plugin_name": "langhuan",
  "version": "0.1.0-dev",
  "installed_at": "2026-11-08T13:00:00Z"
}
```

## 3. 重启 Trae 交 scanner 登记

**完全退出并重启 Trae**。启动时官方 `plugin-scanner` 会扫描
`plugins/` 目录、自动写 `installed-plugins.json` 注册条目，
技能以 `trae-user-upload:langhuan:langhuan-*` 进技能列表。

验证清单：

```powershell
# 注册条目已生成（trae-user-upload 分组应 +1）
Select-String -Path "$env:USERPROFILE\.trae-cn\installed-plugins.json" -Pattern 'trae-user-upload'
# 插件目录存在
Get-ChildItem "$env:USERPROFILE\.trae-cn\plugins\trae-user-upload\langhuan" -Recurse -Depth 1
# 新开会话：技能列表搜 langhuan，试触发词「解析 WOS 题录」
# 日志：输出/日志中搜 [load_plugin_skills]、[plugin-scanner]
```

## ⚠ 已知限制：登记可能不持久（2026-11-08 实装发现）

scanner 登记后，app 某次重写 `installed-plugins.json` 时会把**无市场凭据**
（无 `marketplace_plugin_id`/`checksum`）的目录当作孤儿**回收**——实测
21:15 登记、21:24 条目与目录即被清理，日志无 uninstall 记录。
官方 `install_marketplace_plugin` 强制要求 `download_url`+`checksum`，
即 Trae 期望插件经市场服务器分发；本地目录登记属非官方路径。

应对选项（按侵入性排序）：

1. **接受现状**：被清理后重新部署目录 + 重启即可恢复（适合个人使用）；
2. **手工注册表写入**（进阶）：退出 Trae 后备份并改写
   `installed-plugins.json`（追加 trae-user-upload 分组条目）与
   `plugin-config.json`（`"trae-user-upload:langhuan": {"user_enabled": true}`），
   再启动 Trae。**只追加、先备份**；即使写入成功，若 app 对账逻辑仍视其为
   孤儿，仍可能被再次清理，需以「写入 + 重启 + 观察 24h」实测确认；
3. 观察中的方向（套件 BACKLOG）：给包补 `checksum`/`provenance.json`/
   `SHA256SUMS` 等市场凭据字段，验证能否躲过孤儿回收。

## 与 VS Code 扩展形态的关系

| | Agent 插件（本文档） | VS Code 扩展（INSTALL.zh.md） |
|---|---|---|
| 能力 | 5 个 langhuan-* 技能进 Trae Agent | 设置卡片（模型运营商/角色配置区） |
| 装载方 | plugin-scanner（启动时） | Trae 扩展宿主 |
| 安装 | 拷目录 + 重启 | `Extensions: Install from VSIX…` |

两者互补：要技能装 Agent 插件，要设置卡片装 VSIX。

## 卸载

删除 `~/.trae-cn/plugins/trae-user-upload/langhuan/` 目录并重启 Trae；
若曾手工写入注册表条目，从备份还原 `installed-plugins.json` /
`plugin-config.json` 后重启。
