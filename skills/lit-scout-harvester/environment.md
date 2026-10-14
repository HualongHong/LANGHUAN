# 环境快照与本地改动说明

本文件记录**本机安装版**技能相对上游 `Lit-Scout-Harvester v0.3.0` 的差异、
自带虚拟环境的构成，以及验证结论。上游源码在 `D:\opt\dev\LangHuan\Lit-Scout-Harvester`，
分发包为 `dist/lit-scout-harvester-skill.zip`。

---

## 1. 解释器与虚拟环境

| 项 | 值 |
|---|---|
| 技能根目录 | `C:\Users\hongh\.agents\skills\langhuan-lit-scout-harvester` |
| 虚拟环境 | `<技能根目录>\.venv`（**随技能自带**，不要用系统 python） |
| 解释器 | `<技能根目录>\.venv\Scripts\python.exe` |
| 基础解释器 | `D:\models\python312\python.exe`（CPython **3.12.9**，win-amd64） |
| 系统默认 python | `C:\Python314\python.exe`（3.14，**不可用**：pandas<3 与 fasttext-wheel 无 cp314 wheel） |
| site-packages 钩子 | `<venv>\Lib\site-packages\lit_scout_harvester_skill.pth` → 技能根目录 |

`.pth` 钩子的作用：任意工作目录下用该解释器都能 `import lit_scout_harvester`（走技能源码树，
而非安装副本），因此改技能源码立即生效。

### 已安装依赖（关键项）

```
Python 3.12.9   pip 26.2.1
pandas 2.3.3      numpy 2.5.3        pycountry 22.3.5    PyYAML 6.0.3
pydantic 2.13.5   pydantic-settings 2.15.0  python-dotenv 1.2.3
aiohttp 3.14.3    aiofiles 23.2.1    requests 2.34.2     rich 13.9.4
click 8.5.0       geopy 2.5.0        langdetect 1.0.9    fasttext-wheel 0.9.2
pytest 9.1.1      pytest-asyncio 1.4.0
```

完整清单见 `requirements-agent.txt`（与上游 `requirements.txt` 的差异：**pandas 放宽到
`>=2.1,<3`**，因为上游 `<2.1` 没有 cp312 wheel；另加 pytest / pytest-asyncio）。

### 重建步骤

```powershell
$skill = "C:\Users\hongh\.agents\skills\langhuan-lit-scout-harvester"
D:\models\python312\python.exe -m venv "$skill\.venv"
$py = "$skill\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip setuptools wheel
& $py -m pip install -r "$skill\requirements-agent.txt"
# 导入钩子（让任意 cwd 都能 import 技能源码）
Set-Content -LiteralPath "$skill\.venv\Lib\site-packages\lit_scout_harvester_skill.pth" -Value $skill -Encoding ascii
```

`fasttext-wheel` 装不上也不影响运行：`LanguageIdentifier` 在模型文件缺失时会自动回退到
`langdetect`。fastText 模型（`data/cache/lid.176.ftz`）需要另外用
`scripts/download_fasttext_model.py` 下载，本机**未下载**。

---

## 2. 相对上游的本地改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `SKILL.md` | 重写 | 从"Kimi 开发视图"改为 **Agent 操作手册**：运行环境、三种驱动方式、`agent_run.py` 参数、工单/答案格式、配置与 schema、排错表；frontmatter 增加 `whenToUse`（DSH 只识别 `description`/`whenToUse`，原来的 `triggers` 不生效） |
| `scripts/agent_run.py` | 新增 | Agent 入口：非交互、`--workdir` 内闭环写入、stdout 只输出 JSON 摘要、`direct/collect/apply` 三种模式 |
| `lit_scout_harvester/core/llm_providers/agent_bridge.py` | 新增 | 宿主模型 LLM 供应商：工单落盘 + 答案回读，`collect` 用形状兼容的占位回答，保证两次运行的工单集合一致 |
| `lit_scout_harvester/core/llm_providers/__init__.py` | 修改 | 注册 `agent_bridge` / `agent` 两个名字 |
| `lit_scout_harvester/core/cache_manager.py` | 修复 | 每次操作都用 `contextlib.closing()` 关闭 SQLite 连接。上游把 `sqlite3.Connection` 当上下文管理器用，而它只提交、**不关闭** → 句柄泄漏 + Windows 下文件被占用；修复后 `tests/unit/test_cache_manager.py` 4 个用例由失败转为通过，长跑也不再堆积句柄 |
| `tests/unit/test_agent_bridge.py` | 新增 | 8 个用例覆盖工单 ID 稳定性、占位回答形状、`answers.jsonl`/`answers.json` 两种答案格式、pending 统计、strict 模式、注册表 |
| `requirements-agent.txt` | 新增 | 自带 venv 的依赖清单（Python 3.12 可用版本） |
| `environment.md` | 新增 | 本文件 |
| `.venv/` | 新增 | 自带的 Python 3.12 虚拟环境 |

未改动：解析器、模块、配置、schema、`scripts/` 下其他脚本、`references/` 文档。

---

## 3. 验证结论（本机实测）

```powershell
$py = "C:\Users\hongh\.agents\skills\langhuan-lit-scout-harvester\.venv\Scripts\python.exe"
$env:DEEPSEEK_API_KEY = "sk-test"     # 仅为让配置校验通过
Set-Location "C:\Users\hongh\.agents\skills\langhuan-lit-scout-harvester"
& $py -B -m pytest tests/unit tests/integration -q      # → 150 passed
```

| 验证项 | 命令 | 结果 |
|---|---|---|
| 单元 + 集成测试 | `pytest tests/unit tests/integration` | **150 passed** |
| 骨架解析（无 LLM） | `agent_run.py --disable-llm --limit 5` | CSV + CITATION.md，5 行，含 `detected_language`/`countries` |
| 宿主模型两段式 | `--mode collect` → 作答 → `--mode apply`（3 条记录、schema 抽取） | collect 生成 9 个工单（translate 3 / segment 3 / extract 3）；apply 后 `answered=9, pending=0`，CSV 含 `TI_zh`、`segmented_abstract`、`research_theme/methods/study_region` 及各自 confidence |
| 写入隔离 | 上述运行 | 所有写入（缓存、复核库、`data/inst_city_map.db`、工单）都在 `--workdir` 内，技能目录零写入 |

---

## 4. 已知约束

1. **始终用 `-B` 运行**：技能目录在受限沙箱（workspace-write）下只读，Python 若写
   `__pycache__` 会失败；`agent_run.py` 内部也设了 `sys.dont_write_bytecode = True`。
2. **运行时写入必须落在可写目录**：用 `--workdir` 指定，脚本会把 cache/output/review/
   工单/机构映射库全部重定向进去。
3. **pytest 在受限沙箱下不可用**：`pytest` 的临时目录由 `tempfile` 创建，Windows 上
   `os.mkdir(mode=0o700)` 会写入"仅属主"的显式 DACL，而沙箱受限令牌无法访问该目录
   （报 `PermissionError ... _pytest`）。这与技能本身无关：跑测试请在非受限模式下进行，
   或只跑 `agent_run.py` 冒烟。
4. **没有内置 API key**：`DEEPSEEK_API_KEY` 需自行提供；不想用外部 API 就走
   `--mode collect/apply`（宿主模型驱动，零成本）。技能目录只有 `.env.example`。
5. **fastText 模型未下载**：语言检测走 `langdetect` 回退，个别短文本判定可能略有差异。
6. **上游 `config/default.yaml` 的 `term_extractor.schema_path` 指向不存在的
   `schemas/generic.json`**：用 `--extract-schema` 指定 schema（或改用
   `sci_edu`/`paleoclimate`/`sample_rst` 内置 schema）即可，属上游遗留问题，未改动。
