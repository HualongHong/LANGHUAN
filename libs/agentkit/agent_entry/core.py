"""非交互 Agent 入口骨架（P0b 抽取自 LSH scripts/agent_run.py）。

为"由宿主 Agent 通过 shell 驱动技能"的入口脚本提供可复用零件：

* 标准参数组（``--workdir`` / ``--mode`` / ``--output-dir`` / ``--quiet`` /
  ``--dry-run`` / ``--list-jobs``）；
* workdir 重定向工具：优先路径解析（``resolve_path`` / ``abs_under``），
  不提供裸 ``os.chdir`` 全局副作用；对确实依赖 cwd 的遗留技能，用
  ``redirected_workdir`` 受控上下文管理器实现等价行为（设计红线的
  受控豁免，见函数注释）；
* stdout JSON 摘要发射器（成功/失败契约）与异常→JSON+退出码包装器
  （``run_entry``）；
* ``set_dont_write_bytecode``（技能目录可能对调用方只读）。

契约（与 LSH agent_run 一致）：stdout 只打印一个 JSON 摘要（含 ``ok`` /
``mode`` / ``elapsed_s``，失败时附 ``error`` / ``traceback``），日志走
stderr，退出码 0=成功 / 1=失败。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

SUMMARY_TRACEBACK_LINES = 6


def set_dont_write_bytecode() -> None:
    """禁止写 ``__pycache__``（技能目录可能对调用 Agent 只读）。"""
    sys.dont_write_bytecode = True


def add_standard_arguments(
    parser: argparse.ArgumentParser,
    *,
    modes: Sequence[str] = ("direct", "collect", "apply"),
    default_mode: str = "direct",
) -> argparse.ArgumentParser:
    """向 parser 添加 agent_entry 标准参数组，返回原 parser。

    技能特异的参数（输入、配置、schema 等）由技能侧自行追加。
    """
    parser.add_argument(
        "--mode",
        choices=tuple(modes),
        default=default_mode,
        help="direct = HTTP LLM provider; collect/apply = host-agent answer queue",
    )
    parser.add_argument("--workdir", type=Path, default=Path("."), help="Directory for every runtime write")
    parser.add_argument("--output-dir", type=Path, default=None, help="Where result files go (default <workdir>/output)")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and input, run nothing")
    parser.add_argument(
        "--list-jobs",
        type=int,
        nargs="?",
        const=0,
        default=None,
        help="Do not run the pipeline: print the pending host-model jobs (optionally at most N) as JSON",
    )
    parser.add_argument("--quiet", action="store_true", help="Log warnings only")
    return parser


# ---------------------------------------------------------------------------
# 路径解析（替代 os.chdir 的首选方式）
# ---------------------------------------------------------------------------


def first_existing(candidates: Sequence[Path]) -> Path:
    """Return the first existing candidate path (or the first candidate)."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def resolve_path(value: Any, bases: Sequence[Path]) -> Path:
    """Resolve a possibly relative path against the given bases in order."""
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return first_existing([base / path for base in bases]).resolve()


def abs_under(value: Any, base: Path) -> Path:
    """Return ``value`` as an absolute path, interpreting it relative to ``base``."""
    path = Path(str(value))
    return path if path.is_absolute() else (base / path)


@contextmanager
def redirected_workdir(workdir: Any) -> Iterator[Path]:
    """受控的 cwd 重定向上下文：创建 workdir、切入、退出时恢复原 cwd。

    设计红线是"以路径解析替代 ``os.chdir(workdir)`` 全局副作用"——新技能应
    一律用 ``resolve_path`` / ``abs_under`` 把路径绝对化。本上下文是唯一的
    受控豁免：某些遗留技能（如 LSH）的配置/管线在运行期把多个相对路径
    （cache_db、output_dir、复核队列等）直接相对进程 cwd 打开，绝对化需要
    侵入其配置内部，因此允许在明确注释说明的前提下，用本上下文获得与旧
    ``os.chdir`` 等价、但作用域受控且可恢复的行为。
    """
    target = Path(workdir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield target
    finally:
        os.chdir(previous)


# ---------------------------------------------------------------------------
# stdout JSON 摘要契约
# ---------------------------------------------------------------------------


def configure_logging(quiet: bool = False) -> None:
    """日志走 stderr；--quiet 时只保留 WARNING 及以上。"""
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )


def emit_summary(summary: Dict[str, Any]) -> None:
    """Print the JSON summary as the only stdout payload."""
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def run_entry(args: argparse.Namespace, run_fn: Callable[[argparse.Namespace], Dict[str, Any]]) -> int:
    """Execute ``run_fn(args)`` under the stdout-JSON / exit-code contract.

    * configures stderr logging from ``args.quiet``;
    * on success merges the returned body into the summary and sets ``ok``;
    * on any exception records ``error`` plus the last few traceback lines;
    * always prints the summary on stdout and returns 0 (ok) or 1 (failed).
    """
    configure_logging(quiet=bool(getattr(args, "quiet", False)))
    started = time.time()
    summary: Dict[str, Any] = {"ok": False, "mode": getattr(args, "mode", None)}
    try:
        summary.update(run_fn(args))
        summary["ok"] = True
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc().splitlines()[-SUMMARY_TRACEBACK_LINES:]
    summary["elapsed_s"] = round(time.time() - started, 2)
    emit_summary(summary)
    return 0 if summary.get("ok") else 1
