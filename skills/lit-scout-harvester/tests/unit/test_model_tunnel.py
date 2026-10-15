"""Tests for scripts/model_tunnel.py —— ssh -L 隧道管理（mock subprocess，零真实连接）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lit_scout_harvester.core import provider_registry as pr
from scripts import model_tunnel as mt


def _entry(**overrides) -> pr.ProviderEntry:
    entry = pr.preset_entry("ssh_selfhost")
    entry.enabled = True
    entry.ssh_host = "bastion.example.com"
    entry.ssh_port = 2222
    entry.ssh_user = "deploy"
    entry.ssh_key_path = "/home/deploy/.ssh/id_ed25519"
    entry.remote_host = "10.0.0.8"
    entry.remote_port = 8080
    entry.local_port = 8377
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def _registry(*entries) -> pr.ProviderRegistry:
    return pr.ProviderRegistry(providers=list(entries))


class _FakeProc:
    """mock 的 Popen 返回：poll() 控制宽限期判定，pid 供 pid 文件写入。"""

    def __init__(self, pid: int = 4321, exit_code=None):
        self.pid = pid
        self.returncode = None
        self._exit_code = exit_code

    def poll(self):
        if self._exit_code is not None and self.returncode is None:
            self.returncode = self._exit_code
            return self.returncode
        return self.returncode


# ---------------------------------------------------------------------------
# 命令行构造
# ---------------------------------------------------------------------------


def test_build_ssh_command_with_key():
    cmd = mt.build_ssh_command(_entry())
    assert cmd == [
        "ssh", "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-L", "127.0.0.1:8377:10.0.0.8:8080",
        "deploy@bastion.example.com",
        "-p", "2222",
        "-i", "/home/deploy/.ssh/id_ed25519",
    ]


def test_build_ssh_command_without_key_and_default_port():
    entry = _entry(ssh_key_path="", ssh_port=22)
    cmd = mt.build_ssh_command(entry)
    assert "-i" not in cmd
    assert cmd[cmd.index("-p") + 1] == "22"
    assert "deploy@bastion.example.com" in cmd


# ---------------------------------------------------------------------------
# managed_entries / 状态判定
# ---------------------------------------------------------------------------


def test_managed_entries_filters_enabled_ssh_tunnel():
    disabled = _entry()
    disabled.enabled = False
    deploy = pr.preset_entry("aliyun_model_deploy")
    deploy.enabled = True
    registry = _registry(_entry(), disabled, deploy)
    assert [e.id for e in mt.managed_entries(registry)] == ["ssh_selfhost"]
    picked = mt.managed_entries(registry, only_id="ssh_selfhost")
    assert len(picked) == 1
    with pytest.raises(SystemExit):
        mt.managed_entries(registry, only_id="aliyun_model_deploy")


def test_tunnel_state_stopped_running_stale(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    entry = _entry()
    assert mt.tunnel_state(tunnels, entry.id) == "stopped"
    tunnels.mkdir()
    (tunnels / f"{entry.id}.pid").write_text("4321\n", encoding="utf-8")
    monkeypatch.setattr(mt, "_pid_alive", lambda pid: True)
    assert mt.tunnel_state(tunnels, entry.id) == "running"
    monkeypatch.setattr(mt, "_pid_alive", lambda pid: False)
    assert mt.tunnel_state(tunnels, entry.id) == "stale"
    (tunnels / f"{entry.id}.pid").write_text("not-a-pid\n", encoding="utf-8")
    assert mt.tunnel_state(tunnels, entry.id) == "stopped"


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_spawns_ssh_and_writes_pid(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    recorded = {}

    class FakePopen(_FakeProc):
        def __init__(self, cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["kwargs"] = kwargs
            super().__init__(pid=4321)

    monkeypatch.setattr(mt.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(mt.time, "sleep", lambda _s: None)

    results = mt.start_tunnels(_registry(_entry()), tunnels, grace_seconds=0.5)
    assert results[0]["action"] == "started"
    assert results[0]["detail"].startswith("pid 4321")
    # 命令行与 build_ssh_command 完全一致
    assert recorded["cmd"] == mt.build_ssh_command(_entry())
    # Windows 脱离控制台；stdin 不走交互
    assert recorded["kwargs"]["stdin"] is not None
    assert recorded["kwargs"]["stderr"] is mt.subprocess.STDOUT
    # pid 文件与日志文件就位
    assert (tunnels / "ssh_selfhost.pid").read_text(encoding="utf-8").strip() == "4321"
    assert (tunnels / "ssh_selfhost.log").exists()


def test_start_is_idempotent_when_running(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    tunnels.mkdir()
    (tunnels / "ssh_selfhost.pid").write_text("4321\n", encoding="utf-8")
    monkeypatch.setattr(mt, "_pid_alive", lambda pid: True)
    called = []
    monkeypatch.setattr(mt.subprocess, "Popen", lambda *a, **k: called.append((a, k)))
    results = mt.start_tunnels(_registry(_entry()), tunnels)
    assert results[0]["action"] == "skipped"
    assert "已在运行" in results[0]["detail"]
    assert called == []  # 幂等：不再 spawn


def test_start_fails_fast_on_missing_fields_without_spawn(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    called = []
    monkeypatch.setattr(mt.subprocess, "Popen", lambda *a, **k: called.append((a, k)))
    entry = _entry(remote_host="")
    results = mt.start_tunnels(_registry(entry), tunnels)
    assert results[0]["action"] == "failed"
    assert "remote_host" in results[0]["detail"]
    assert called == []


def test_start_reports_immediate_exit_with_log_tail(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    monkeypatch.setattr(mt.subprocess, "Popen", lambda *a, **k: _FakeProc(pid=99, exit_code=255))
    monkeypatch.setattr(mt.time, "sleep", lambda _s: None)
    results = mt.start_tunnels(_registry(_entry()), tunnels, grace_seconds=0.5)
    assert results[0]["action"] == "failed"
    assert "code 255" in results[0]["detail"]
    # 立即失败时保留 pid 文件便于排查（日志在同目录）
    assert (tunnels / "ssh_selfhost.log").exists()


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_terminates_running_tunnel(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    tunnels.mkdir()
    (tunnels / "ssh_selfhost.pid").write_text("4321\n", encoding="utf-8")
    alive_calls = []

    def fake_alive(pid):
        alive_calls.append(pid)
        return len(alive_calls) <= 2  # 存活判定 True×2，之后进程已回收

    monkeypatch.setattr(mt, "_pid_alive", fake_alive)
    terminated = []
    monkeypatch.setattr(mt, "_terminate", lambda pid: terminated.append(pid))
    monkeypatch.setattr(mt.time, "sleep", lambda _s: None)
    results = mt.stop_tunnels(_registry(_entry()), tunnels)
    assert terminated == [4321]
    assert results[0]["action"] == "stopped"
    assert not (tunnels / "ssh_selfhost.pid").exists()


def test_stop_skips_when_no_pidfile(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(mt, "_terminate", lambda pid: called.append(pid))
    results = mt.stop_tunnels(_registry(_entry()), tmp_path / "tunnels")
    assert results[0]["action"] == "skipped"
    assert called == []


def test_stop_cleans_stale_pidfile(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    tunnels.mkdir()
    (tunnels / "ssh_selfhost.pid").write_text("4321\n", encoding="utf-8")
    monkeypatch.setattr(mt, "_pid_alive", lambda pid: False)
    called = []
    monkeypatch.setattr(mt, "_terminate", lambda pid: called.append(pid))
    results = mt.stop_tunnels(_registry(_entry()), tunnels)
    assert results[0]["action"] == "cleaned"
    assert called == []
    assert not (tunnels / "ssh_selfhost.pid").exists()


# ---------------------------------------------------------------------------
# status / CLI 冒烟
# ---------------------------------------------------------------------------


def test_status_report_covers_managed_entries(tmp_path, monkeypatch):
    tunnels = tmp_path / "tunnels"
    monkeypatch.setattr(mt, "_pid_alive", lambda pid: True)
    tunnels.mkdir()
    (tunnels / "ssh_selfhost.pid").write_text("4321\n", encoding="utf-8")
    report = mt.status_report(_registry(_entry(), pr.preset_entry("qwen")), tunnels)
    assert len(report) == 1
    assert report[0]["state"] == "running"
    assert report[0]["pid"] == 4321
    assert report[0]["target"] == "10.0.0.8:8080"


def test_cli_status_smoke(tmp_path, capsys):
    path = tmp_path / "providers.json"
    pr.save_registry(path, _registry(_entry()))
    rc = mt.main(["status", "--config", str(path), "--workdir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ssh_selfhost" in out and "未启动" in out


def test_cli_start_and_stop_smoke(tmp_path, capsys, monkeypatch):
    path = tmp_path / "providers.json"
    pr.save_registry(path, _registry(_entry()))
    monkeypatch.setattr(mt.subprocess, "Popen", lambda *a, **k: _FakeProc(pid=7))
    monkeypatch.setattr(mt.time, "sleep", lambda _s: None)
    rc = mt.main(["start", "--config", str(path), "--workdir", str(tmp_path)])
    assert rc == 0
    assert "已启动" in capsys.readouterr().out
    alive_calls = []

    def fake_alive(pid):
        alive_calls.append(pid)
        return len(alive_calls) <= 2

    monkeypatch.setattr(mt, "_pid_alive", fake_alive)
    monkeypatch.setattr(mt, "_terminate", lambda pid: None)
    rc = mt.main(["stop", "--config", str(path), "--workdir", str(tmp_path)])
    assert rc == 0
    assert "已停止" in capsys.readouterr().out


def test_cli_unknown_id_exits_nonzero(tmp_path):
    path = tmp_path / "providers.json"
    pr.save_registry(path, _registry(_entry()))
    with pytest.raises(SystemExit):
        mt.main(["start", "--id", "ghost", "--config", str(path), "--workdir", str(tmp_path)])


def test_pid_file_path_layout(tmp_path):
    tunnels = mt.tunnels_dir_for(Path("/tmp/wd"))
    assert tunnels == Path("/tmp/wd") / "tunnels"
    assert mt.pid_path(tunnels, "ssh_selfhost") == tunnels / "ssh_selfhost.pid"
    assert mt.log_path(tunnels, "ssh_selfhost") == tunnels / "ssh_selfhost.log"
