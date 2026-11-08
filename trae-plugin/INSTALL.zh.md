# TRAE 接入指引（langhuan-provider-card）

> 面向 TRAE（VS Code fork）用户：把 LangHuan 设置卡片装进 TRAE，让
> LangHuan 技能群的模型运营商/角色/key 环境变量名在 TRAE 设置里统一配置。
> TRAE 按标准 VS Code 扩展契约加载——以下三种装法任选其一。

## 0. 前提

```bash
cd <LangHuan 仓库>/trae-plugin
npm install
```

`@langhuan/plugin-core` 经 `file:../plugin-core` 本地依赖解析（仓库内零下载）。

## 1. 本地调试（F5，开发态）

1. 用 VS Code / TRAE 打开 `trae-plugin/` 目录；
2. 新建 `.vscode/launch.json`（仓库 .gitignore 排除了 .vscode/，此文件只留本地）：

   ```jsonc
   {
     "version": "0.2.0",
     "configurations": [
       {
         "name": "Run Extension",
         "type": "extensionHost",
         "request": "launch",
         "args": ["--extensionDevelopmentPath=${workspaceFolder}"]
       }
     ]
   }
   ```

3. F5 启动 Extension Development Host；新窗口命令面板 →
   `Preferences: Open Settings (UI)` → 搜 "LangHuan" 改设置 → 切回原窗口
   Output 通道「LangHuan」看同步日志。

## 2. 开发态目录加载（不打包）

把整个 `trae-plugin/` 目录（含 `node_modules/`）拷到 TRAE 用户扩展目录：

```bash
# 国际版
cp -r <LangHuan 仓库>/trae-plugin ~/.trae/extensions/langhuan-provider-card
# CN 版（.trae-cn 扩展子目录为强推断，以本机实际目录为准）
cp -r <LangHuan 仓库>/trae-plugin ~/.trae-cn/extensions/langhuan-provider-card
```

重启 TRAE 即加载（`onStartupFinished` 激活）。

## 3. 打 .vsix 安装（分发形态）

```bash
cd <LangHuan 仓库>/trae-plugin
npm install              # file: 依赖装为目录联接（junction）
npx @vscode/vsce package # 生成 langhuan-provider-card-0.1.0.vsix
```

TRAE 里 `Extensions: Install from VSIX…` 选该文件；或直接拖入扩展面板。
（package.json 已带 `repository` 字段，vsce 对 README 相对链接的校验因此通过。）

> 本机（Windows，npm 12）已实测：vsce 会跟随目录联接，plugin-core 实体
> 文件进包。若在其他平台打包后发现包内缺 `node_modules/@langhuan/plugin-core`
> （symlink 未被跟随），打包前把联接换成实体拷贝即可：
> `rm -rf node_modules/@langhuan/plugin-core && cp -r ../plugin-core node_modules/@langhuan/`，
> 打完 `npm install` 恢复联接。
> 注意 `npm install --install-links` 对本包不可用——npm 12 下它与 file: 依赖
> 搭配会使 `npm ls` 报 invalid、vsce 依赖检查失败（2026-11-08 实测）。

## 4. 配置与验证

1. 设置 → 搜 "LangHuan"（或 settings.json 编辑 `langhuan.*` 键）：标注/决策角色
   下拉、skillsDir、`langhuan.providers` 进阶覆盖，详见 [README.zh.md](./README.zh.md)；
2. 同步目标固定为 `<skillsDir>/langhuan-lit-scout-harvester/config/providers.json`
   （默认 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json`）；
3. 查看同步日志：命令面板 → `Output: Focus on Output View` → 通道选 **LangHuan**
   （boot / config-change 两次触发；内容未变化记 debug 行并跳过写入）。

## 5. key 值在哪填

设置卡片与 providers.json 只有环境变量**名**（`apiKeyEnv`）。key 值填在中央
`~/.agents/skills/langhuan.env`（单一事实来源，经硬链接分发给全部 langhuan-*
技能），运行时：

```bash
set -a; . ~/.agents/skills/langhuan.env; set +a
```

## 回滚

`Extensions: Uninstall`（或删除开发态目录）；providers.json 保留最后同步的内容
（Python 侧可独立继续维护，不受影响）。

## 排错

| 现象 | 排查 |
|---|---|
| Output 无 LangHuan 通道/无日志 | 扩展是否启用（扩展面板搜 langhuan-provider-card）；激活事件为 onStartupFinished，重启后才有 boot 行 |
| 日志报 `加载 @langhuan/plugin-core 失败` | `npm install` 没跑或 node_modules 没随目录拷贝（开发态目录加载最容易漏） |
| `配置校验 (…)` 错误行 | 角色指向了不存在/未启用/reserved/缺 logprobs 的条目，按报错改设置 |
| `providers.json 落盘失败` | `langhuan.skillsDir` 是否指向正确技能目录；目录写权限 |
| Git Bash 下 `node --test test/` 报 MODULE_NOT_FOUND | MSYS2 参数转换坑：用裸 `node --test`（= npm test）或 `node --test test/sync.test.js` |
| `vsce package` 报 README 链接校验失败 | package.json 已带 `repository` 字段；确认保留 |
| 打出的 .vsix 缺 plugin-core | 打包前把 node_modules/@langhuan/plugin-core 联接换成实体拷贝（见 §3 注），打完 `npm install` 恢复；勿用 `--install-links`（npm 12 + file: 依赖会使 npm ls 报 invalid） |
