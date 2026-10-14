# DSH Desktop 接入指引（@langhuan/dsh-plugin）

> 面向 DSH Desktop（Electron 壳）用户：把 LangHuan 插件装进 desktop profile，
> 让 LangHuan 技能群的模型运营商/角色/key 环境变量名在 DSH 体系里统一配置。
>
> **重要前提**：`profiles/desktop` 被 CLI 保留——`dsh` 拒绝针对 desktop 的
> 启动/dump/plugin 管理（`error: profile "desktop" is managed exclusively by
> the Electron application`）。因此全程**走文件编辑，不要用 `dsh plugin`
> 子命令**，也绝对不要在 Desktop 运行中时改动它的包目录。

## 步骤

### 1. 完全退出 DSH Desktop

托盘/菜单里退出，确保没有 dsh 服务进程占用 profile 目录
（任务管理器确认无 `dsh Desktop` / `dsh` 相关进程）。

### 2. 安装插件包

```bash
# Git Bash 中执行；路径换成你的 LangHuan 仓库实际位置
cd ~/.dsh/profiles/desktop
pnpm add D:/opt/dev/LangHuan/dsh-plugin
```

（若日后插件发布到 npm，此步等价于 `pnpm add @langhuan/dsh-plugin`——
仍须在 profile 目录内手动执行 pnpm，因为 `dsh plugin --profile desktop`
会被 CLI 拒绝。）

两种形态都可用，行为一致（见 README「实现备注」）：

- **link: 安装（上面的命令）**：文件夹被链接进 profile，插件回退到内置的
  零依赖 Standard Schema 做配置校验/默认值——已实测可正常加载落盘；
- **tarball 安装（canonical schema）**：`cd <LangHuan 仓库> && npm pack -w dsh-plugin`
  （或 `npm pack` 于 `dsh-plugin/` 目录内）得到 `langhuan-dsh-plugin-0.1.0.tgz`，
  然后 `pnpm add <tarball 路径>`。插件实体落在 profile node_modules 内，
  经 DSH 的 profiles 级共享 node_modules 解析宿主 schemastery，
  使用与内置插件相同的 `z.object` schema。

### 3. cordis.patch.yml 追加花名册行

编辑 `~/.dsh/profiles/desktop/cordis.patch.yml`，在顶层数组末尾追加
（新条目必须用 `- insert:` 包装——普通的 `- id:` 行只能修改已存在的条目）：

```yaml
- insert:
    - id: langhuan
      name: '@langhuan/dsh-plugin'
      config:
        annotationProvider: deepseek
        decisionProvider: deepseek
        # providers: {...}  # 需要改运营商入口时按 README「修改运营商配置」整段列出
```

`config` 整段可省略（走出厂默认：7 预置 + deepseek/deepseek 角色 + `~/.agents/skills`）。

### 4. 启动 Desktop 并验证

1. 启动 DSH Desktop；
2. 打开 设置 → 插件（Plugins）页——LangHuan 条目出现在插件清单中；
   （当前 DSH 0.1.5-rc.3 的插件配置卡片只覆盖内置宿主插件，第三方纯服务端
   插件以 cordis.patch.yml 为配置入口；字段模型即插件的 Config schema。）
3. 观察日志：插件加载成功会输出
   `[langhuan] providers.json 已同步 → <skillsDir>/langhuan-lit-scout-harvester/config/providers.json（7 个运营商；annotation=deepseek decision=deepseek）`；
4. 打开 `~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json`
   确认内容已同步（snake_case，与 Python 侧出厂格式逐字节一致）。

### 5. 配置模型与 key

1. **入口/角色**：编辑 desktop 的 `cordis.patch.yml` 里 `langhuan` 行的 config
   （保存后重启 Desktop 或触发重载生效）；
2. **key 值**：在 `~/.agents/skills/langhuan.env` 填对应环境变量
   （如 `DEEPSEEK_API_KEY=sk-...`）——单一事实来源，经硬链接分发给全部
   langhuan-* 技能；DSH 会话用 key 时把该文件导出进进程环境。

## 回滚

删除 `cordis.patch.yml` 里的 `langhuan` 行，并在 profile 目录
`pnpm remove @langhuan/dsh-plugin`；providers.json 保留最后同步的内容
（Python 侧可独立继续维护，不受影响）。

## 排错

| 现象 | 排查 |
|---|---|
| 日志无 `[langhuan]` 行 | 花名册行缩进/顶层数组位置是否正确；`pnpm why @langhuan/dsh-plugin` 确认包装进 profile |
| `[langhuan] 配置校验: ...` | 角色指向了不存在/未启用/reserved/缺 logprobs 的条目，按报错改 cordis.patch.yml |
| `providers.json 落盘失败` | `skillsDir` 是否指向正确技能目录；目录写权限 |
