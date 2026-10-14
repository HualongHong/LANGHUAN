"""LangHuan 套件技能 zip 构建器（C10 · Phase 4）。

把 LSH ``scripts/build_skill_zip.py`` 的逻辑泛化为技能无关构建器：

1. 暂存目录（系统临时目录，不污染技能目录）；
2. 复制 ``SkillSpec.dirs`` / ``SkillSpec.files``（忽略 ``__pycache__`` /
   ``*.pyc`` / ``.pytest_cache`` / ``*.db`` / ``cache`` / ``.env`` 等，
   见 :data:`DEFAULT_IGNORE_NAMES` / :data:`DEFAULT_IGNORE_GLOBS`）；
3. vendor agentkit：``libs/agentkit`` 复制为 ``<zip根>/agentkit/``（与
   ``scripts/`` 平级），剔除构建元数据与缓存，运行时 ``import agentkit``
   不依赖 PYTHONPATH；
4. 可选 wheel：``python -m build --wheel --no-isolation``，缺 ``build`` 或
   构建失败时降级为仅源码，并在 MANIFEST 注明；
5. ``MANIFEST.json``：built_at、wheel 名 + sha256、文件清单、agentkit 版本；
6. 打 zip 到指定输出目录。

绿包形态（``green_install=True``，AVS 传统）：额外生成 ``install.sh``
（建 venv → pip install -r requirements.txt → 复制 .env 占位符 →
py_compile 自检），根 README 缺失时生成最小 README。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Sequence

# 目录/文件名精确匹配即排除（作用于相对路径的每一段与顶层条目）。
DEFAULT_IGNORE_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        ".git",
        ".idea",
        ".vscode",
        ".skill_staging",
        "dist",
        "output",
        "cache",
        ".env",  # 密钥隔离红线：真实 .env 永不入包（.env.example 不受影响）
    }
)
# 文件名 glob 匹配即排除。
DEFAULT_IGNORE_GLOBS = frozenset(
    {"*.pyc", "*.pyo", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "*.egg-info"}
)
# vendor agentkit 时的排除集。注意不能复用 DEFAULT_IGNORE_NAMES：
# 其中的 "cache" 是技能数据目录排除项，而 agentkit.cache 是库的子模块。
AGENTKIT_IGNORE_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", "tests", "test", ".git", ".idea", ".vscode"}
)
AGENTKIT_IGNORE_GLOBS = frozenset({"*.pyc", "*.pyo", "*.egg-info", "pyproject.toml"})


@dataclass(frozen=True)
class SkillSpec:
    """一个成员技能的打包描述。"""

    name: str
    root: Path
    version: str = "0.0.0"
    kind: str = "python-package"  # python-package | content-skill
    dirs: Sequence[str] = ()
    files: Sequence[str] = ()
    exclude: Sequence[str] = ()  # 额外排除：相对路径前缀（"data/samples"）或文件名 glob（"*.pdf"）
    wheel: bool = False  # 尝试构建 wheel 一并入包
    green_install: bool = False  # 生成 install.sh；根 README 缺失时生成最小 README


@dataclass
class BuildResult:
    """一次构建的结果摘要。"""

    name: str
    zip_path: Path
    size_bytes: int
    file_count: int
    wheel: Optional[str]
    wheel_sha256: Optional[str]
    agentkit_version: str
    notes: List[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.wheel is None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_ignored(rel: Path, extra_exclude: Sequence[str]) -> bool:
    """按相对路径判断排除：默认名单 + 规格里的路径前缀/文件名 glob。"""
    parts = rel.parts
    if any(part in DEFAULT_IGNORE_NAMES for part in parts):
        return True
    if any(fnmatch(rel.name, glob) for glob in DEFAULT_IGNORE_GLOBS):
        return True
    posix = rel.as_posix()
    for pattern in extra_exclude:
        if posix == pattern or posix.startswith(pattern.rstrip("/") + "/"):
            return True
        if fnmatch(rel.name, pattern):
            return True
    return False


def _copy_into(src: Path, dst_root: Path, skill_root: Path, extra_exclude: Sequence[str]) -> List[Path]:
    """把 ``src``（文件或目录）复制进暂存目录，返回实际复制的文件清单。"""
    copied: List[Path] = []
    if src.is_dir():
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(skill_root)
            if _is_ignored(rel, extra_exclude):
                continue
            target = dst_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append(target)
    elif src.is_file():
        rel = src.relative_to(skill_root)
        if not _is_ignored(rel, extra_exclude):
            dst_root.joinpath(rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_root / rel)
            copied.append(dst_root / rel)
    return copied


def agentkit_version(agentkit_root: Path) -> str:
    """从 ``agentkit/__init__.py`` 解析 ``__version__``（不导入包）。"""
    text = (agentkit_root / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def vendor_agentkit(agentkit_root: Path, staging: Path) -> str:
    """把 agentkit 包复制为 ``<staging>/agentkit/``，返回其版本号。

    ``agentkit_root`` 指向包目录（含 ``__init__.py`` 的那层，monorepo 中即
    ``libs/agentkit/``）。剔除测试、缓存与构建元数据，保证 zip 自包含。
    """
    if not (agentkit_root / "__init__.py").is_file():
        raise FileNotFoundError(f"agentkit 包目录无效（缺 __init__.py）: {agentkit_root}")
    target = staging / "agentkit"
    if target.exists():
        shutil.rmtree(target)
    for path in sorted(agentkit_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(agentkit_root)
        if any(part in AGENTKIT_IGNORE_NAMES for part in rel.parts):
            continue
        if any(fnmatch(rel.name, glob) for glob in AGENTKIT_IGNORE_GLOBS):
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return agentkit_version(agentkit_root)


def _build_wheel(spec: SkillSpec, build_python: str, wheel_dir: Path) -> Optional[Path]:
    """构建 wheel；缺工具或失败返回 ``None``（调用方降级为仅源码）。"""
    wheel_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            build_python,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
        ],
        cwd=str(spec.root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    wheels = sorted(wheel_dir.glob("*.whl"))
    return wheels[-1] if wheels else None


_INSTALL_SH = """#!/usr/bin/env bash
# {name} 独立安装包 · 一键安装脚本（Git Bash / Linux / macOS）
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] 创建隔离虚拟环境 .venv ..."
python -m venv .venv

# Windows(Git Bash) 与 POSIX 的 venv 路径不同
if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
else
  PY=".venv/bin/python"
fi
echo "[2/4] 安装依赖（$PY）..."
# 网络受限环境可改用阿里云镜像：
#   "$PY" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
"$PY" -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[3/4] 已生成 .env（请编辑填入真实密钥）"
else
  echo "[3/4] .env 已存在，跳过"
fi

echo "[4/4] 脚本语法自检 ..."
"$PY" -m py_compile scripts/*.py && echo "      全部脚本编译通过"

echo ""
echo "安装完成。agentkit/ 为 LangHuan 套件共享库的 vendor 副本，运行时自包含。"
echo "后续步骤：编辑 .env 填入密钥，然后阅读 {skill_md} 开始使用。"
"""

_MINIMAL_README = """# {name} v{version} — 独立安装包

> LangHuan 套件成员技能（{kind}）。本包为独立分发版，解压即可安装使用，
> 不依赖原始开发仓库；`agentkit/` 为套件共享库的 vendor 副本，运行时自包含。

## 安装

要求：Python 3.12+（仅 scripts/ 需要；纯 Markdown 内容无需 Python 环境）。

```bash
cd <解压根目录>
bash install.sh   # 创建 .venv、安装依赖、生成 .env（如不存在）、语法自检
```

网络受限环境可在 install.sh 中改用阿里云 PyPI 镜像（脚本内注释有现成命令）。

## 使用

阅读技能目录下的 SKILL.md 入手；命令行入口：

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -B scripts/agent_run.py --help
```
"""


def _apply_green_install(spec: SkillSpec, staging: Path, notes: List[str]) -> None:
    """绿包形态补充：install.sh + 缺失时生成最小根 README。"""
    install_sh = staging / "install.sh"
    skill_md = "SKILL.md"
    for candidate in staging.iterdir():
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            skill_md = f"{candidate.name}/SKILL.md"
            break
    install_sh.write_text(
        _INSTALL_SH.format(name=spec.name, skill_md=skill_md), encoding="utf-8", newline="\n"
    )
    install_sh.chmod(install_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not (staging / "README.md").is_file():
        (staging / "README.md").write_text(
            _MINIMAL_README.format(name=spec.name, version=spec.version, kind=spec.kind),
            encoding="utf-8",
        )
        notes.append("根 README.md 缺失，已生成最小 README")
    if not (staging / ".env.example").is_file():
        notes.append("警告：.env.example 未入包，install.sh 的 .env 占位步骤将失败")


def build_skill_zip(
    spec: SkillSpec,
    output_dir: Path,
    agentkit_root: Optional[Path] = None,
    build_python: Optional[str] = None,
    zip_name: Optional[str] = None,
) -> BuildResult:
    """构建一个技能 zip 并返回结果摘要。

    Args:
        spec: 技能打包描述。
        output_dir: zip 输出目录（monorepo 根 ``dist/``）。
        agentkit_root: agentkit 包目录；缺省取本文件上一级（``libs/agentkit``）。
        build_python: 用于 wheel 构建的 Python 解释器；缺省为当前解释器。
        zip_name: 输出文件名；缺省 ``<name>-skill.zip``。
    """
    skill_root = Path(spec.root).resolve()
    if not skill_root.is_dir():
        raise FileNotFoundError(f"技能目录不存在: {skill_root}")
    agentkit_root = Path(agentkit_root).resolve() if agentkit_root else Path(__file__).resolve().parents[1]
    build_python = build_python or sys.executable
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    notes: List[str] = []

    staging = Path(tempfile.mkdtemp(prefix=f"agentkit-packaging-{spec.name}-"))
    try:
        for name in spec.dirs:
            _copy_into(skill_root / name, staging, skill_root, spec.exclude)
        for name in spec.files:
            _copy_into(skill_root / name, staging, skill_root, spec.exclude)

        version = vendor_agentkit(agentkit_root, staging)

        # 密钥红线自检：暂存区绝不允许出现真实 .env。
        leaked = [p for p in staging.rglob("*") if p.is_file() and p.name == ".env"]
        if leaked:
            raise RuntimeError(f"暂存区检出 .env，已中止: {leaked}")

        wheel_name = wheel_sha = None
        if spec.wheel:
            wheel_dir = staging.parent / (staging.name + "-wheels")
            wheel = _build_wheel(spec, build_python, wheel_dir)
            if wheel is None:
                notes.append(
                    "wheel 构建不可用或失败（python -m build --no-isolation），已降级为仅源码分发"
                )
            else:
                shutil.copy2(wheel, staging / wheel.name)
                wheel_name = wheel.name
                wheel_sha = _sha256(wheel)
            shutil.rmtree(wheel_dir, ignore_errors=True)

        if spec.green_install:
            _apply_green_install(spec, staging, notes)

        files = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
        manifest = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "generator": "agentkit.packaging",
            "skill": {"name": spec.name, "version": spec.version, "kind": spec.kind},
            "agentkit": {"version": version, "layout": "agentkit/ 位于 zip 根（与 scripts/ 平级）"},
            "wheel": {"name": wheel_name, "sha256": wheel_sha} if wheel_name else None,
            "notes": notes,
            "file_count": len(files),
            "files": files,
        }
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        zip_path = output_dir / (zip_name or f"{spec.name}-skill.zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging).as_posix())

        return BuildResult(
            name=spec.name,
            zip_path=zip_path,
            size_bytes=zip_path.stat().st_size,
            file_count=len(files) + 1,
            wheel=wheel_name,
            wheel_sha256=wheel_sha,
            agentkit_version=version,
            notes=notes,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
