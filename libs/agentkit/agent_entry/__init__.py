"""agentkit.agent_entry — 非交互 Agent 入口骨架。

workdir 重定向、stdout JSON 摘要、stderr 日志、退出码 0/1、
--dry-run/--list-jobs 标准参数、sys.dont_write_bytecode。
技能特异部分（模块清单、默认 schema、管线执行）留在技能侧钩子。
"""

from agentkit.agent_entry.core import (
    SUMMARY_TRACEBACK_LINES,
    abs_under,
    add_standard_arguments,
    configure_logging,
    emit_summary,
    first_existing,
    redirected_workdir,
    resolve_path,
    run_entry,
    set_dont_write_bytecode,
)

__all__ = [
    "SUMMARY_TRACEBACK_LINES",
    "abs_under",
    "add_standard_arguments",
    "configure_logging",
    "emit_summary",
    "first_existing",
    "redirected_workdir",
    "resolve_path",
    "run_entry",
    "set_dont_write_bytecode",
]
