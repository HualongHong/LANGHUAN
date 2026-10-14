"""Build the skill installation zip for Lit-Scout-Harvester.

Thin shell over ``agentkit.packaging`` (LangHuan Phase 4): the actual builder
is skill-agnostic and shared by all suite members; this script only keeps the
historical CLI (``--output``) and the LSH content policy.

Produces ``dist/lit-scout-harvester-skill.zip`` (skill-local by default, for
CLI compatibility with the pre-suite script; the suite-level entry point is
the repo-root ``build_plugin.py``, which writes to the suite ``dist/``).
"""

from __future__ import annotations

import argparse
import sys as _sys
from pathlib import Path
from typing import List, Optional

_here = Path(__file__).resolve()
for _candidate in (_here.parents[3] / "libs", _here.parents[1]):
    if (_candidate / "agentkit").is_dir() and str(_candidate) not in _sys.path:
        _sys.path.insert(0, str(_candidate))

from agentkit.packaging import SkillSpec, build_skill_zip as _build  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"

LSH_SPEC = SkillSpec(
    name="lit-scout-harvester",
    root=PROJECT_ROOT,
    version="0.3.0",
    kind="python-package",
    dirs=(
        "lit_scout_harvester",
        "config",
        "scripts",
        "tests",
        "references",
        "data",
        "microservices",
    ),
    files=(
        "requirements.txt",
        "requirements-agent.txt",
        "environment.md",
        ".env.example",
        "README.md",
        "pyproject.toml",
        "PROJECT_BRIEF.md",
        "SKILL.md",
    ),
    exclude=("data/samples", "*.pdf"),
    wheel=True,
)


def build_skill_zip(output_path: Optional[Path] = None) -> Path:
    """Build the skill zip and return its path."""
    output_path = output_path or DIST_DIR / "lit-scout-harvester-skill.zip"
    result = _build(LSH_SPEC, output_path.parent, zip_name=output_path.name)
    print(f"Built skill zip: {result.zip_path}")
    print(f"Included wheel: {result.wheel or '无（降级为仅源码，见 MANIFEST notes）'}")
    return result.zip_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the skill installation zip")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path (default: dist/lit-scout-harvester-skill.zip)",
    )
    args = parser.parse_args(argv)
    build_skill_zip(args.output)
    return 0


if __name__ == "__main__":
    _sys.exit(main())
