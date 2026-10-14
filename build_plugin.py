"""LangHuan 套件构建入口（Phase 4 · C10；D7/S3 增 freeze/verify 子命令）。

读 ``suite.yaml``，按 ``members`` 逐技能调用 ``agentkit.packaging`` 打 zip
到 ``dist/``（zip 根 = 技能根，``agentkit/`` vendor 在 zip 根与 ``scripts/``
平级，运行时自包含、不依赖 PYTHONPATH）。

用法：

    python build_plugin.py                      # 全部成员（build 子命令，默认）
    python build_plugin.py build --only lit-scout-harvester
    python build_plugin.py build --output-dir D:/tmp/dist
    python build_plugin.py build --python <解释器>   # 指定 wheel 构建用 Python
    python build_plugin.py freeze --version 0.3 [--out DIR]   # 冻结 dist/（D7/S3）
    python build_plugin.py verify [--manifest dist/MANIFEST.sha256]

freeze/verify（版本冻结纪律，S3）：freeze 把输出目录按当前状态生成
``MANIFEST.sha256``（相对路径 → sha256，稳定排序，UTF-8）+ ``FROZEN.md``
（时间/版本/文件数/总字节/生成命令）；verify 重算哈希逐条比对，报告
missing/changed/added 三类清单——完全一致退出码 0，有任何差异退出码 1。

兼容：不带子命令的旧用法（``python build_plugin.py [--only ...]``）自动视为
``build`` 子命令。

suite.yaml 解析：优先 pyyaml；当前解释器无 pyyaml 时使用内嵌的最小解析器
（仅支持本仓库 suite.yaml 用到的结构：顶层映射 + members 列表），不引入依赖。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SUITE_ROOT / "libs"))

from agentkit.packaging import SkillSpec, build_skill_zip  # noqa: E402
from agentkit.packaging import freeze_directory, verify_directory  # noqa: E402

SUBCOMMANDS = ("build", "freeze", "verify")

# 各成员技能的 zip 内容策略（目录/文件白名单 + 额外排除）。
SKILL_LAYOUTS = {
    "lit-scout-harvester": {
        "dirs": [
            "lit_scout_harvester",
            "config",
            "scripts",
            "tests",
            "references",
            "data",
            "microservices",
        ],
        "files": [
            "requirements.txt",
            "requirements-agent.txt",
            "environment.md",
            ".env.example",
            "README.md",
            "pyproject.toml",
            "PROJECT_BRIEF.md",
            "SKILL.md",
        ],
        "exclude": ["data/samples", "*.pdf"],
        "wheel": True,
    },
    "archive-plate-assembler": {
        "dirs": ["archive_plate_assembler", "scripts", "tests", "data"],
        "files": ["SKILL.md", "pyproject.toml", "requirements.txt", ".env.example"],
        # 图鉴 PDF 有版权且被根 .gitignore 排除，不入分发包。
        "exclude": ["data/samples", "*.pdf"],
        "wheel": True,
    },
    "anchor-verify-strategizer": {
        "dirs": ["anchor-verify-strategizer", "scripts"],
        "files": ["SKILL.md", "requirements.txt", ".env.example"],
        "exclude": [],
        "wheel": False,
        "green_install": True,
    },
    "peer-collab-strategizer": {
        "dirs": ["peer-collab-strategizer", "scripts"],
        "files": ["SKILL.md", "pyproject.toml", "requirements.txt", ".env.example"],
        "exclude": [],
        "wheel": False,
        "green_install": True,
    },
}


def _strip_comment(line: str) -> str:
    """去掉行尾注释（suite.yaml 的值不含 '#'，最小解析器可接受此简化）。"""
    return line.split("#", 1)[0].rstrip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _mini_parse_suite(text: str) -> dict:
    """suite.yaml 的最小解析：只提取 members 列表与 build.output_dir。

    支持的结构：顶层 ``members:`` 下的 ``- key: value`` 列表项与后续缩进的
    ``key: value``，以及顶层 ``build:`` 下的一层映射。无法解析时抛错。
    """
    members: list[dict] = []
    build: dict = {}
    section = None
    current: dict | None = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0:
            section = stripped.split(":", 1)[0]
            current = None
            continue
        if section == "members":
            if stripped.startswith("- "):
                current = {}
                members.append(current)
                stripped = stripped[2:].strip()
                if not stripped:
                    continue
            if current is None or ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            current[key.strip()] = _unquote(value)
        elif section == "build" and ":" in stripped:
            key, _, value = stripped.partition(":")
            build[key.strip()] = _unquote(value)
    if not members:
        raise ValueError("suite.yaml 最小解析失败：未找到 members 列表")
    return {"members": members, "build": build}


def load_suite(path: Path) -> dict:
    """加载 suite.yaml；有 pyyaml 用 pyyaml，否则走内嵌最小解析器。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        return _mini_parse_suite(text)
    data = yaml.safe_load(text)
    return {"members": data.get("members") or [], "build": data.get("build") or {}}


def make_spec(member: dict) -> SkillSpec:
    """按 suite.yaml 成员条目 + SKILL_LAYOUTS 内容策略生成 SkillSpec。"""
    name = str(member["name"])
    layout = SKILL_LAYOUTS.get(name)
    if layout is None:
        raise KeyError(f"build_plugin.py 缺少成员 {name!r} 的内容策略（SKILL_LAYOUTS）")
    return SkillSpec(
        name=name,
        root=SUITE_ROOT / str(member["path"]),
        version=str(member.get("version", "0.0.0")),
        kind=str(member.get("kind", "python-package")),
        dirs=layout["dirs"],
        files=layout["files"],
        exclude=layout.get("exclude", ()),
        wheel=bool(layout.get("wheel", False)),
        green_install=bool(layout.get("green_install", False)),
    )


def _resolve_output_dir(suite: dict, override: Path | None) -> Path:
    """输出目录：命令行优先，缺省 suite.yaml build.output_dir（相对 suite 根）。"""
    if override is not None:
        return override
    return SUITE_ROOT / str(suite["build"].get("output_dir") or "dist")


def cmd_build(args: argparse.Namespace, suite: dict) -> int:
    """按 suite.yaml 逐技能打 zip（原默认行为）。"""
    output_dir = _resolve_output_dir(suite, args.output_dir)

    members = suite["members"]
    if args.only:
        members = [m for m in members if m.get("name") == args.only]
        if not members:
            print(f"suite.yaml 中不存在成员: {args.only}", file=sys.stderr)
            return 2

    failures = 0
    for member in members:
        spec = make_spec(member)
        try:
            result = build_skill_zip(spec, output_dir, build_python=args.python)
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {spec.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        size_kb = result.size_bytes / 1024
        wheel = result.wheel or "无（仅源码，见 MANIFEST notes）"
        print(f"[OK] {result.name}: {result.zip_path} ({size_kb:.0f} KiB, {result.file_count} 文件, wheel: {wheel})")
        for note in result.notes:
            print(f"     note: {note}")
    print(f"输出目录: {output_dir}（{len(members) - failures}/{len(members)} 成功）")
    return 1 if failures else 0


def cmd_freeze(args: argparse.Namespace, suite: dict) -> int:
    """冻结输出目录：FROZEN.md + MANIFEST.sha256（S3 版本冻结纪律）。"""
    out_dir = _resolve_output_dir(suite, args.out)
    if not out_dir.is_dir():
        print(f"[FAIL] 冻结目录不存在: {out_dir}", file=sys.stderr)
        return 1
    command = f"{Path(sys.argv[0]).name} freeze --version {args.version}"
    if args.out is not None:
        command += f" --out {args.out}"
    result = freeze_directory(out_dir, args.version, command=command)
    print(f"[FROZEN] version={result.version} root={result.root}")
    print(f"  {result.file_count} 文件入册，payload {result.total_bytes} 字节")
    print(f"  manifest: {result.manifest_path}")
    print(f"  frozen:   {result.frozen_path}")
    return 0


def cmd_verify(args: argparse.Namespace, suite: dict) -> int:
    """按 MANIFEST 重算哈希逐条比对；missing/changed/added 全空才退出 0。"""
    manifest = args.manifest
    if manifest is None:
        manifest = _resolve_output_dir(suite, None) / "MANIFEST.sha256"
    try:
        result = verify_directory(manifest_path=manifest)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    if result.ok:
        print(f"[OK] 零回归：{result.root} 与 {result.manifest_path.name} 完全一致")
        return 0
    print(f"[DIFF] {result.root} 相对清单存在差异：")
    for label, items in (("MISSING", result.missing), ("CHANGED", result.changed), ("ADDED", result.added)):
        if items:
            print(f"  {label} ({len(items)}):")
            for rel in items:
                print(f"    {rel}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_plugin.py",
        description="LangHuan 套件构建：build（打 zip）/ freeze（冻结）/ verify（核验）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{build,freeze,verify}")

    p_build = subparsers.add_parser("build", help="按 suite.yaml 逐技能打 zip（缺省子命令）")
    p_build.add_argument("--only", default=None, help="只构建指定成员（suite.yaml 里的 name）")
    p_build.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="zip 输出目录（默认 suite.yaml build.output_dir，即 dist/）",
    )
    p_build.add_argument(
        "--python",
        default=sys.executable,
        help="wheel 构建用的 Python 解释器（默认当前解释器；缺 build 工具时自动降级为仅源码）",
    )
    p_build.add_argument("--suite", type=Path, default=SUITE_ROOT / "suite.yaml", help=argparse.SUPPRESS)

    p_freeze = subparsers.add_parser("freeze", help="冻结输出目录：生成 MANIFEST.sha256 + FROZEN.md")
    p_freeze.add_argument("--version", required=True, help="冻结版本号（原样记录，如 0.3 / 1.0rc1）")
    p_freeze.add_argument(
        "--out",
        type=Path,
        default=None,
        help="待冻结目录（默认 suite.yaml build.output_dir，即 dist/）",
    )

    p_verify = subparsers.add_parser("verify", help="按 MANIFEST.sha256 逐字节核验（零回归退出 0）")
    p_verify.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="清单路径（默认 <输出目录>/MANIFEST.sha256；核验根目录取清单所在目录）",
    )

    # 兼容旧用法：无子命令（或以选项开头）视为 build。
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["build"]
    elif argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help"):
        argv = ["build", *argv]

    args = parser.parse_args(argv)
    if args.command == "freeze":
        return cmd_freeze(args, load_suite(SUITE_ROOT / "suite.yaml"))
    if args.command == "verify":
        return cmd_verify(args, load_suite(SUITE_ROOT / "suite.yaml"))

    suite = load_suite(args.suite)
    return cmd_build(args, suite)


if __name__ == "__main__":
    sys.exit(main())
