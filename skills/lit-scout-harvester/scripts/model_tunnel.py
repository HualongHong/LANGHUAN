"""SSH 隧道管理 —— 为 providers.json 中 type=ssh_tunnel 且 enabled 的条目
维护 ``ssh -L`` 端口转发进程（纯标准库，不做真实连接测试）。

用法（cwd = 技能根目录）：

    python -B scripts/model_tunnel.py status [--config config/providers.json] [--workdir .]
    python -B scripts/model_tunnel.py start  [--id ssh_selfhost]
    python -B scripts/model_tunnel.py stop   [--id ssh_selfhost]

行为约定：
- 只管理 providers.json 里 ``type == "ssh_tunnel" 且 enabled`` 的条目；
- start 以 ``ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -L ...`` 拉起进程，
  pid 写在 ``<workdir>/tunnels/<id>.pid``、日志写在 ``<workdir>/tunnels/<id>.log``；
  隧道已在运行（pid 存活）时幂等跳过；
- 本脚本不探测远端连通性（禁止真实连接）；ssh 自身 BatchMode 免密失败会立刻
  退出，start 的宽限期检查会捕获这种情况并提示看日志；
- Windows 上 liveness 经 ``tasklist`` 判定（os.kill(pid, 0) 在 Windows 会
  真的终止进程，不能用）。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core import provider_registry as reg  # noqa: E402

START_GRACE_SECONDS = 0.5  # spawn 后等待 ssh 立即失败退出的宽限
STOP_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def default_config_path() -> Path:
    return reg.skill_root() / "config" / "providers.json"


def tunnels_dir_for(workdir: Path) -> Path:
    return workdir / "tunnels"


def pid_path(tunnels_dir: Path, entry_id: str) -> Path:
    return tunnels_dir / f"{entry_id}.pid"


def log_path(tunnels_dir: Path, entry_id: str) -> Path:
    return tunnels_dir / f"{entry_id}.log"


def _pid_alive(pid: int) -> bool:
    """跨平台 liveness 检查（Windows 走 tasklist；os.kill(pid,0) 在 Windows 会杀进程）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def read_pid(tunnels_dir: Path, entry_id: str) -> Optional[int]:
    """读 pid 文件；不存在或内容非法返回 None。"""
    path = pid_path(tunnels_dir, entry_id)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def tunnel_state(tunnels_dir: Path, entry_id: str) -> str:
    """stopped（无 pid 文件）/ running（pid 存活）/ stale（pid 文件残留但进程已死）。"""
    pid = read_pid(tunnels_dir, entry_id)
    if pid is None:
        return "stopped"
    return "running" if _pid_alive(pid) else "stale"


def managed_entries(registry: reg.ProviderRegistry, only_id: Optional[str] = None) -> List[reg.ProviderEntry]:
    """type=ssh_tunnel 且 enabled 的条目；--id 时精确定位并给出清晰错误。"""
    entries = [e for e in registry.providers if e.type == reg.TYPE_SSH_TUNNEL and e.enabled]
    if only_id is None:
        return entries
    for entry in entries:
        if entry.id == only_id:
            return [entry]
    known = ", ".join(e.id for e in entries) or "（无）"
    raise SystemExit(f"错误：'{only_id}' 不是已启用的 ssh_tunnel 条目（可管理: {known}）")


def validate_tunnel_fields(entry: reg.ProviderEntry) -> List[str]:
    """start 前的字段检查（validate() 已覆盖启用必填项，这里兜底报错文案）。"""
    errors: List[str] = []
    if not entry.ssh_host:
        errors.append("ssh_host 为空")
    if not entry.ssh_user:
        errors.append("ssh_user 为空")
    if not entry.local_port:
        errors.append("local_port 未填写")
    if not entry.remote_host:
        errors.append("remote_host 为空")
    if not entry.remote_port:
        errors.append("remote_port 未填写")
    return errors


def build_ssh_command(entry: reg.ProviderEntry) -> List[str]:
    """组装 ssh -L 命令行（与 settings_app 展示的等价命令一致）。"""
    cmd = [
        "ssh",
        "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-L",
        f"127.0.0.1:{entry.local_port}:{entry.remote_host}:{entry.remote_port}",
        f"{entry.ssh_user}@{entry.ssh_host}",
        "-p", str(entry.ssh_port or 22),
    ]
    if entry.ssh_key_path:
        cmd += ["-i", entry.ssh_key_path]
    return cmd


# ---------------------------------------------------------------------------
# start / stop / status
# ---------------------------------------------------------------------------


def _spawn(entry: reg.ProviderEntry, tunnels_dir: Path) -> subprocess.Popen:
    """拉起 ssh 进程：stdin 关闭、stdout/stderr 并入日志文件，pid 写盘。"""
    tunnels_dir.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        # 脱离父进程控制台，避免 Ctrl+C / 终端关闭连带杀掉转发进程。
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    log = open(log_path(tunnels_dir, entry.id), "ab")
    try:
        proc = subprocess.Popen(
            build_ssh_command(entry),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    finally:
        log.close()
    pid_path(tunnels_dir, entry.id).write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc


def _log_tail(tunnels_dir: Path, entry_id: str, limit: int = 5) -> str:
    try:
        lines = log_path(tunnels_dir, entry_id).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " | ".join(line.strip() for line in lines[-limit:] if line.strip())


def start_tunnels(
    registry: reg.ProviderRegistry,
    tunnels_dir: Path,
    only_id: Optional[str] = None,
    grace_seconds: float = START_GRACE_SECONDS,
) -> List[dict]:
    """启动隧道；幂等（已在运行则跳过）。返回每条目的结果字典。"""
    results: List[dict] = []
    for entry in managed_entries(registry, only_id):
        base = {"id": entry.id, "command": " ".join(build_ssh_command(entry))}
        state = tunnel_state(tunnels_dir, entry.id)
        if state == "running":
            results.append({**base, "action": "skipped", "detail": f"隧道已在运行（pid {read_pid(tunnels_dir, entry.id)}）"})
            continue
        errors = validate_tunnel_fields(entry)
        if errors:
            results.append({**base, "action": "failed", "detail": "；".join(errors)})
            continue
        if state == "stale":  # 残留 pid 文件：清理后重启
            pid_path(tunnels_dir, entry.id).unlink(missing_ok=True)
        proc = _spawn(entry, tunnels_dir)
        if grace_seconds > 0:
            time.sleep(grace_seconds)
        if proc.poll() is not None:
            detail = f"ssh 启动后立即退出（code {proc.returncode}）"
            tail = _log_tail(tunnels_dir, entry.id)
            if tail:
                detail += f"；日志尾部: {tail}"
            results.append({**base, "action": "failed", "detail": detail})
            continue
        results.append({**base, "action": "started", "detail": f"pid {proc.pid}，日志 {log_path(tunnels_dir, entry.id)}"})
    return results


def stop_tunnels(
    registry: reg.ProviderRegistry,
    tunnels_dir: Path,
    only_id: Optional[str] = None,
    timeout_seconds: float = STOP_TIMEOUT_SECONDS,
) -> List[dict]:
    """停止隧道：SIGTERM + 等待回收，清理 pid 文件。返回每条目的结果字典。"""
    results: List[dict] = []
    for entry in managed_entries(registry, only_id):
        base = {"id": entry.id}
        pid = read_pid(tunnels_dir, entry.id)
        if pid is None:
            results.append({**base, "action": "skipped", "detail": "无 pid 文件，隧道未在运行"})
            continue
        if not _pid_alive(pid):
            pid_path(tunnels_dir, entry.id).unlink(missing_ok=True)
            results.append({**base, "action": "cleaned", "detail": f"进程 {pid} 已死，清理残留 pid 文件"})
            continue
        _terminate(pid)
        deadline = time.monotonic() + timeout_seconds
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _pid_alive(pid):
            results.append({**base, "action": "failed", "detail": f"进程 {pid} 未在 {timeout_seconds:.0f}s 内退出"})
            continue
        pid_path(tunnels_dir, entry.id).unlink(missing_ok=True)
        results.append({**base, "action": "stopped", "detail": f"进程 {pid} 已终止"})
    return results


def status_report(registry: reg.ProviderRegistry, tunnels_dir: Path) -> List[dict]:
    """汇总所有受管条目的隧道状态。"""
    report: List[dict] = []
    for entry in managed_entries(registry):
        state = tunnel_state(tunnels_dir, entry.id)
        report.append(
            {
                "id": entry.id,
                "state": state,
                "pid": read_pid(tunnels_dir, entry.id) if state != "stopped" else None,
                "local_port": entry.local_port,
                "target": f"{entry.remote_host}:{entry.remote_port}" if entry.remote_host else "（未填写）",
            }
        )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ACTION_LABEL = {
    "started": "已启动",
    "stopped": "已停止",
    "skipped": "跳过",
    "cleaned": "已清理",
    "failed": "失败",
}
_STATE_LABEL = {"running": "运行中", "stopped": "未启动", "stale": "残留（进程已死）"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model_tunnel.py",
        description="管理 providers.json 中 ssh_tunnel 条目的 ssh -L 端口转发进程（不做真实连接测试）。",
    )
    parser.add_argument("command", choices=("status", "start", "stop"), help="子命令")
    parser.add_argument("--id", default=None, help="只操作指定 provider id（默认：全部已启用 ssh_tunnel 条目）")
    parser.add_argument("--config", type=Path, default=None, help="providers.json 路径（默认技能根 config/providers.json）")
    parser.add_argument("--workdir", type=Path, default=None, help="tunnels/ 目录所在工作目录（默认当前目录）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    registry = reg.load_registry(args.config or default_config_path())
    tunnels_dir = tunnels_dir_for((args.workdir or Path.cwd()).resolve())

    if args.command == "status":
        report = status_report(registry, tunnels_dir)
        if not report:
            print("（providers.json 中没有已启用的 ssh_tunnel 条目）")
            return 0
        for item in report:
            pid_text = f" pid={item['pid']}" if item.get("pid") else ""
            print(f"{item['id']}: {_STATE_LABEL[item['state']]}{pid_text}  本地端口 {item['local_port']} → {item['target']}")
        return 0

    if args.command == "start":
        results = start_tunnels(registry, tunnels_dir, only_id=args.id)
    else:
        results = stop_tunnels(registry, tunnels_dir, only_id=args.id)
    failed = 0
    for item in results:
        print(f"{item['id']}: {_ACTION_LABEL[item['action']]} — {item['detail']}")
        if item["action"] == "failed":
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
