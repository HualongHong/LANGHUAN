"""Anchor-Verify-Strategizer 统一非交互入口（agent_run.py）。

由宿主 Agent 通过 shell 驱动 AVS 的五个工具脚本，子命令：

* ``lint``            报告合规校验（包装 lint_report.py）
* ``synthesize``      Phase 4 SWOT 草稿合成（包装 synthesize_swot.py，全参数化）
* ``export-tasks``    导出外部 Agent 核验任务包 JSON（export_verification_tasks.py）
* ``import-verdicts`` 回导核验裁决（import_verdicts.py，默认 dry-run，--apply 写回）
* ``selfcheck``       DeepSeek API 连通性自检（test_deepseek.py，需 .env 与网络）

契约（agentkit.agent_entry）：stdout 仅一个 JSON 摘要（``ok`` / ``subcommand`` /
各子命令统计字段 / ``elapsed_s``，失败附 ``error`` 与 traceback 末 6 行），
被包装脚本的全部打印改道 stderr 作为日志，退出码 0=成功 / 1=失败；
argparse 参数错误（含必填缺失）退出码 2。相对路径相对 ``--workdir`` 解析，
缺省输出写入 ``<workdir>/output/``。
"""
from __future__ import annotations

import sys as _sys

# 技能目录对调用 Agent 可能只读：不写 __pycache__。
_sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

def _bootstrap_agentkit() -> None:
    """定位 agentkit 包所在目录并加入 sys.path（zip vendor 副本优先，monorepo libs/ 兜底）。"""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "libs", here.parents[1]):
        if (candidate / "agentkit").is_dir() and str(candidate) not in _sys.path:
            _sys.path.insert(0, str(candidate))


_bootstrap_agentkit()

import agentkit  # noqa: F401

import argparse  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

from agentkit.agent_entry import abs_under, resolve_path, run_entry  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _forward(buf: io.StringIO) -> None:
    """被包装脚本的 stdout 捕获内容转 stderr（日志通道）。"""
    text = buf.getvalue()
    if text:
        sys.stderr.write(text)


def _input_path(value, workdir: Path) -> Path:
    """输入文件路径：按 cwd → workdir 顺序解析首个存在者（只读，不归属 workdir）。"""
    return resolve_path(value, [Path.cwd(), workdir])


def run_lint(args: argparse.Namespace) -> dict:
    import lint_report

    total_err = total_warn = 0
    files = []
    for raw in args.reports:
        path = _input_path(raw, args.workdir)
        if not path.is_file():
            print(f"{path}:0 [ERROR] IO 文件不存在或不可读", file=sys.stderr)
            total_err += 1
            files.append({"file": str(path), "errors": 1, "warns": 0})
            continue
        findings, max_num = lint_report.lint_file(str(path))
        n_err = sum(1 for f in findings if f[1] == lint_report.ERROR)
        n_warn = sum(1 for f in findings if f[1] == lint_report.WARN)
        total_err += n_err
        total_warn += n_warn
        for line, level, rule, msg in findings:
            print(f"{path}:{line} [{level}] {rule} {msg}", file=sys.stderr)
        files.append({
            "file": str(path),
            "errors": n_err,
            "warns": n_warn,
            "max_ids": {k: f"{k}-{v:02d}" for k, v in sorted(max_num.items())},
        })
    print(f"lint 汇总: ERROR={total_err} WARN={total_warn}（{len(files)} 个文件）", file=sys.stderr)
    return {
        "subcommand": "lint",
        "files": files,
        "errors": total_err,
        "warns": total_warn,
        "exit_code": 1 if total_err else 0,
    }


def run_synthesize(args: argparse.Namespace) -> dict:
    import synthesize_swot

    ns = argparse.Namespace(
        enterprise=args.enterprise,
        institution=args.institution,
        tech_directions=args.tech_directions,
        gap_start=args.gap_start,
        phase1=_input_path(args.phase1, args.workdir),
        phase2=_input_path(args.phase2, args.workdir),
        phase3=_input_path(args.phase3, args.workdir),
        output=abs_under(args.output, args.workdir),
        model=args.model,
        max_tokens=args.max_tokens,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        stats = synthesize_swot.run(ns)
    _forward(buf)
    stats["subcommand"] = "synthesize"
    stats["exit_code"] = 0
    return stats


def run_export_tasks(args: argparse.Namespace) -> dict:
    import export_verification_tasks

    report = _input_path(args.report, args.workdir)
    package = export_verification_tasks.build_task_package(
        str(report), only_confirmed=not args.all)
    if args.output:
        out = abs_under(args.output, args.workdir)
    else:
        out = abs_under(Path("output") / f"verification-tasks-{report.stem}.json", args.workdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = package["metadata"]
    print(f"已导出 {meta['total_tasks']} 条核验任务 → {out}（filter={meta['filter']}）",
          file=sys.stderr)
    return {
        "subcommand": "export-tasks",
        "report": str(report),
        "output": str(out),
        "filter": meta["filter"],
        "entries": meta["total_tasks"],
        "exit_code": 0,
    }


def run_import_verdicts(args: argparse.Namespace) -> dict:
    import import_verdicts

    report = _input_path(args.report, args.workdir)
    verdicts = _input_path(args.verdicts, args.workdir)
    argv = ["import_verdicts.py", str(report), str(verdicts)]
    if args.apply:
        argv.append("--apply")
    if args.tasks:
        argv += ["--tasks", str(_input_path(args.tasks, args.workdir))]
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = import_verdicts.main(argv)
    _forward(buf)
    if rc != 0:
        raise RuntimeError(f"import_verdicts 失败，退出码 {rc}")
    return {
        "subcommand": "import-verdicts",
        "report": str(report),
        "verdicts": str(verdicts),
        "dry_run": not args.apply,
        "exit_code": rc,
    }


def run_selfcheck(args: argparse.Namespace) -> dict:
    import test_deepseek

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = test_deepseek.main()
    _forward(buf)
    return {
        "subcommand": "selfcheck",
        "api_ok": rc == 0,
        "exit_code": rc,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_run.py",
        description="AVS 统一非交互入口：stdout 仅 JSON 摘要，日志走 stderr")
    parser.add_argument("--workdir", type=Path, default=Path("."),
                        help="相对路径解析与运行时写入的基准目录（默认当前目录）")
    parser.add_argument("--quiet", action="store_true", help="日志仅 WARNING 及以上")
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    p = sub.add_parser("lint", help="报告合规校验（包装 lint_report.py）")
    p.add_argument("reports", nargs="+", type=Path, help="待校验报告 .md（可多个）")
    p.set_defaults(func=run_lint)

    p = sub.add_parser("synthesize", help="Phase 4 SWOT 草稿合成（DeepSeek API，全参数化）")
    p.add_argument("--enterprise", required=True,
                   help="目标企业名称（含曾用名/股票代码等消歧信息）")
    p.add_argument("--institution", required=True,
                   help="目标机构名称（含组织边界说明）")
    p.add_argument("--tech-directions", required=True, nargs="+",
                   help="技术方向（可多个，如：碳足迹追踪 碳金融）")
    p.add_argument("--gap-start", required=True,
                   help="上游 GAP/VT/SD 最大编号与本阶段编号接续说明")
    p.add_argument("--phase1", required=True, type=Path, help="Phase 1 企业锚点报告路径")
    p.add_argument("--phase2", required=True, type=Path, help="Phase 2 机构锚点报告路径")
    p.add_argument("--phase3", required=True, type=Path, help="Phase 3 竞争格局报告路径")
    p.add_argument("--output", type=Path, default=Path("output/phase4-swot-draft.md"),
                   help="草稿输出路径（默认 <workdir>/output/phase4-swot-draft.md）")
    p.add_argument("--model", default="deepseek-v4-pro", help="DeepSeek 模型名")
    p.add_argument("--max-tokens", type=int, default=16384, help="max_tokens 上限")
    p.set_defaults(func=run_synthesize)

    p = sub.add_parser("export-tasks", help="导出外部 Agent 核验任务包 JSON")
    p.add_argument("report", type=Path, help="报告 Markdown 文件路径")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="输出 JSON（默认 <workdir>/output/verification-tasks-<报告名>.json）")
    p.add_argument("--all", action="store_true",
                   help="导出全部含 URL 条目（默认仅『已确认事实』小节）")
    p.set_defaults(func=run_export_tasks)

    p = sub.add_parser("import-verdicts", help="回导核验裁决（默认 dry-run，--apply 写回）")
    p.add_argument("report", type=Path, help="报告 Markdown 文件路径")
    p.add_argument("verdicts", type=Path, help="裁决文件 verdicts.json 路径")
    p.add_argument("--apply", action="store_true", help="实际写回报告（默认 dry-run）")
    p.add_argument("--tasks", type=Path, default=None,
                   help="任务包 JSON（缺省按默认过滤从报告重建映射）")
    p.set_defaults(func=run_import_verdicts)

    p = sub.add_parser("selfcheck", help="DeepSeek API 连通性自检（需 .env 与网络）")
    p.set_defaults(func=run_selfcheck)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    args.mode = args.subcommand  # run_entry 摘要的 mode 字段承载子命令名
    return run_entry(args, lambda a: a.func(a))


if __name__ == "__main__":
    sys.exit(main())
