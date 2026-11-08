"""宿主 Agent 插件包构建器（2026-11-08 三宿主安装反馈驱动）。

从 ``suite.yaml`` 成员 + 根 ``build_plugin.py`` 的 SKILL_LAYOUTS 内容策略，
组装各宿主「个人插件」目录（不含 .venv/.env/缓存，技能目录统一 ``langhuan-`` 前缀）：

- ``--host workbuddy`` → ``dist/workbuddy-marketplace/``
  （``.codebuddy-plugin/marketplace.json`` + ``plugins/langhuan-suite/``，
  6 个技能 = 5 成员 + ``langhuan-provider-sync``；
  安装：``plugin marketplace add <目录> --name langhuan-personal`` 后
  ``plugin install langhuan-suite@langhuan-personal --scope user``）
- ``--host trae`` → ``dist/trae-agent-plugin/``
  （``.trae-plugin/plugin.json`` 带 interface 块 + 5 个技能；
  安装：拷入 ``~/.trae-cn/plugins/trae-user-upload/langhuan/<version>/`` 后重启，
  见 trae-plugin/agent-plugin-INSTALL.zh.md）

清单 schema 取自 2026-11-08 实机验证通过的安装包（反馈 feedback/20261108），
非推测。构建后内置密钥自检（等价 scripts/check_dist_secrets.py 规则）。

用法::

    python scripts/build_host_plugin.py                    # 两种宿主包都构建
    python scripts/build_host_plugin.py --host workbuddy
    python scripts/build_host_plugin.py --host trae --output-dir D:/tmp/dist
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUITE_ROOT))

from build_plugin import load_suite, make_spec  # noqa: E402

# 目录/文件名整名命中即排除；".env" 精确匹配不影响 ".env.example"
IGNORE_NAMES = {
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "output", "outputs", ".git", ".github",
    "node_modules", ".env",
}
IGNORE_SUFFIXES = {".pyc", ".pyo", ".whl", ".egg-info", ".log", ".tmp"}

# 与 scripts/check_dist_secrets.py 同规则的密钥自检
KEY_RE = re.compile(
    rb"(sk-[A-Za-z0-9]{30,}|"
    rb"(?:DEEPSEEK|MINIMAX|QWEN|SCNET|SHANGHAI_SC|ALIYUN|JEV)[A-Z0-9_]*"
    rb"(?:KEY|TOKEN)\s*=\s*[\"']?[A-Za-z0-9_\-]{25,})"
)
PLACEHOLDER_MARKERS = (b"xxx", b"your-", b"XXX", b"YOUR-")


def _ignore(_src: Path, names: list[str]) -> set[str]:
    return {n for n in names
            if n in IGNORE_NAMES or any(n.endswith(s) for s in IGNORE_SUFFIXES)}


def suite_meta() -> dict:
    """从 suite.yaml 顶层 suite: 块取 name/version/license/author。"""
    meta: dict = {}
    in_suite = False
    for raw in (SUITE_ROOT / "suite.yaml").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            in_suite = stripped == "suite:"
            continue
        if in_suite and ":" in stripped:
            key, _, value = stripped.partition(":")
            meta[key.strip()] = value.strip().strip("\"'")
    return meta


def _excluded(rel: Path, patterns: tuple[str, ...]) -> bool:
    parts = rel.parts
    for pat in patterns:
        if any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
        if fnmatch.fnmatch(str(rel).replace("\\", "/"), pat):
            return True
    return False


def copy_skill(spec, dst: Path) -> None:
    """按 SkillSpec（SKILL_LAYOUTS 内容策略）把技能拷到 dst。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    copied = False
    for d in spec.dirs:
        src = spec.root / d
        if src.is_dir():
            shutil.copytree(src, dst / d, ignore=_ignore)
            copied = True
    for f in spec.files:
        src = spec.root / f
        if src.is_file():
            target = dst / f
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied = True
    if not copied:
        raise SystemExit(f"技能 {spec.name} 按布局未拷到任何文件，检查 SKILL_LAYOUTS")
    # 应用 exclude（路径任一分段或整体匹配即排除，如 "data/samples"、"*.pdf"）
    for path in sorted(dst.rglob("*"), reverse=True):
        rel = path.relative_to(dst)
        if path.is_file() and _excluded(rel, tuple(spec.exclude)):
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    assert_no_env(dst)
    if not (dst / "SKILL.md").is_file():
        raise SystemExit(f"技能 {spec.name} 缺 SKILL.md")


def assert_no_env(root: Path) -> None:
    """安全闸：任何情况下 .env 不得入包（.env.example 放行）。"""
    for path in root.rglob("*"):
        if path.is_file() and path.name == ".env":
            raise SystemExit(f"安全闸：发现 .env 入包 {path}")


def copy_suite_docs(dst: Path) -> None:
    for name in ("README.md", "LICENSE", "suite.yaml"):
        src = SUITE_ROOT / name
        if src.is_file():
            shutil.copy2(src, dst / name)


def secret_scan(root: Path) -> list[str]:
    findings = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for m in KEY_RE.finditer(data):
            if not any(marker in m.group() for marker in PLACEHOLDER_MARKERS):
                findings.append(f"疑似密钥 {path}: {m.group()[:60]!r}")
    return findings


def build_skills_dir(specs, dst: Path, extra_skills: dict[str, Path] | None = None) -> list[str]:
    """把成员技能（+可选额外技能源目录）拷入 dst，返回技能目录名列表。"""
    names = []
    for spec in specs:
        copy_skill(spec, dst / f"langhuan-{spec.name}")
        names.append(f"langhuan-{spec.name}")
    for skill_name, src in (extra_skills or {}).items():
        target = dst / skill_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target, ignore=_ignore)
        assert_no_env(target)
        if not (target / "SKILL.md").is_file():
            raise SystemExit(f"额外技能 {skill_name} 缺 SKILL.md")
        names.append(skill_name)
    return names


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_workbuddy(out_root: Path, specs, meta: dict) -> Path:
    mkt = out_root / "workbuddy-marketplace"
    if mkt.exists():
        shutil.rmtree(mkt)
    plugin = mkt / "plugins" / "langhuan-suite"
    description = (
        "琅嬛 LangHuan Suite：面向 Agent 的文献与科研证据处理技能套件"
        "（5 个成员技能 + WorkBuddy 模型供应商同步技能）"
    )

    # 组装 WorkBuddy 宿主技能（SKILL + 同步脚本副本）到独立暂存目录
    sync_stage = out_root / ".workbuddy-sync-skill-stage"
    if sync_stage.exists():
        shutil.rmtree(sync_stage)
    sync_stage.mkdir(parents=True)
    sync_skill = sync_stage / "langhuan-provider-sync"
    shutil.copytree(SUITE_ROOT / "workbuddy-plugin" / "skill", sync_skill, ignore=_ignore)
    scripts_dir = sync_skill / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy2(SUITE_ROOT / "workbuddy-plugin" / "scripts" / "sync_workbuddy_models.py",
                 scripts_dir / "sync_workbuddy_models.py")

    skill_names = build_skills_dir(specs, plugin / "skills",
                                   {"langhuan-provider-sync": sync_skill})
    shutil.rmtree(sync_stage)
    copy_suite_docs(plugin)

    write_json(plugin / ".codebuddy-plugin" / "plugin.json", {
        "name": "langhuan-suite",
        "version": meta.get("version", "0.1.0-dev"),
        "description": description,
        "author": {"name": "LangHuan Suite / HualongHong"},
        "homepage": {"url": "https://github.com/HualongHong/LANGHUAN", "type": "github"},
        "license": meta.get("license", "MIT"),
        "keywords": ["langhuan", "literature", "research", "evidence", "skills"],
        "skills": [f"./skills/{n}" for n in skill_names],
    })
    write_json(mkt / ".codebuddy-plugin" / "marketplace.json", {
        "name": "langhuan-personal",
        "owner": {"name": "HualongHong (LANGHUAN)"},
        "description": "LANGHUAN 本地个人插件市场（WorkBuddy 用户作用域，由 scripts/build_host_plugin.py 构建）",
        "plugins": [{
            "name": "langhuan-suite",
            "source": "./plugins/langhuan-suite",
            "description": description,
            "version": meta.get("version", "0.1.0-dev"),
            "author": {"name": "LangHuan Suite"},
            "homepage": "https://github.com/HualongHong/LANGHUAN",
            "repository": "https://github.com/HualongHong/LANGHUAN",
            "license": meta.get("license", "MIT"),
            "keywords": ["langhuan", "research", "suite"],
        }],
    })
    return mkt


def build_trae(out_root: Path, specs, meta: dict) -> Path:
    plugin = out_root / "trae-agent-plugin"
    if plugin.exists():
        shutil.rmtree(plugin)
    description = (
        "琅嬛 LangHuan Suite：面向 Agent 的文献与科研证据处理技能套件"
        "（文献题录收割 / 图鉴结构化 / 产学研合作评估 ×2 / 渗流探索纪律）"
    )
    build_skills_dir(specs, plugin / "skills")
    copy_suite_docs(plugin)
    write_json(plugin / ".trae-plugin" / "plugin.json", {
        "name": "langhuan",
        "version": meta.get("version", "0.1.0-dev"),
        "description": description,
        "author": {"name": "LangHuan Suite / HualongHong"},
        "license": meta.get("license", "MIT"),
        "keywords": ["langhuan", "literature", "research", "evidence", "skills"],
        "skills": "./skills/",
        "interface": {
            "displayName": "LangHuan（琅嬛）",
            "shortDescription": "文献与科研证据处理技能套件：LSH 题录收割 / APA 图鉴结构化 / AVS·PCS 合作评估 / 渗流探索",
            "longDescription": description + "。技能详情见各 langhuan-*/SKILL.md；模型供应商配置见 VS Code 扩展形态（trae-plugin）或中央 langhuan.env。",
            "developerName": "LangHuan Suite / HualongHong",
            "category": "Developer Tools",
            "capabilities": ["Interactive", "Read", "Write"],
            "defaultPrompt": [
                "用 langhuan-lit-scout-harvester 解析这批 WOS 题录",
                "用 langhuan-percolation-scout 评估这个选题的探索覆盖率",
            ],
        },
    })
    return plugin


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 WorkBuddy/Trae 宿主插件包")
    ap.add_argument("--host", choices=("workbuddy", "trae", "all"), default="all")
    ap.add_argument("--output-dir", type=Path, default=SUITE_ROOT / "dist",
                    help="输出根目录（默认 dist/，宿主包为其子目录）")
    args = ap.parse_args()

    suite = load_suite(SUITE_ROOT / "suite.yaml")
    specs = [make_spec(m) for m in suite["members"]]
    meta = suite_meta()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    targets = {"workbuddy": build_workbuddy, "trae": build_trae}
    for host in ("workbuddy", "trae"):
        if args.host in (host, "all"):
            path = targets[host](args.output_dir, specs, meta)
            findings = secret_scan(path)
            if findings:
                raise SystemExit("密钥自检失败：\n" + "\n".join(findings))
            files = sum(1 for p in path.rglob("*") if p.is_file())
            print(f"[OK] {host}: {path}（{files} 个文件，密钥自检通过）")


if __name__ == "__main__":
    main()
