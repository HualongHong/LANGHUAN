"""doctor 的 MinerU 配额看板测试；只读临时账本，零网络。"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("langhuan_doctor", ROOT / "scripts/doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


def _write_ledger(tmp_path: Path, **bucket) -> Path:
    state_dir = tmp_path / "quota_state"
    state_dir.mkdir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (state_dir / "mineru_quota.json").write_text(
        json.dumps({today: bucket}), encoding="utf-8"
    )
    return state_dir


@pytest.mark.parametrize(
    ("pages", "level", "message"),
    [
        (799, "OK", "token 2 任务/799 页"),
        (800, "WARN", "超过 80% 预警线"),
        (1000, "WARN", "日页数已达/超过 1000 页上限"),
    ],
)
def test_mineru_quota_thresholds(tmp_path, capsys, pages, level, message):
    state_dir = _write_ledger(tmp_path, token_tasks=2, token_pages=pages, flash_tasks=1)
    doctor.check_mineru_quota([state_dir])
    out = capsys.readouterr().out
    assert f"[{level}" in out
    assert message in out


def test_mineru_quota_accepts_job_dir_and_reports_unknown_pages(tmp_path, capsys):
    _write_ledger(tmp_path, token_tasks=1)
    doctor.check_mineru_quota([tmp_path])
    out = capsys.readouterr().out
    assert "token 1 任务/? 页" in out
    assert "token 页数未回传" in out


def test_mineru_quota_missing_explicit_ledger_warns(tmp_path, capsys):
    doctor.check_mineru_quota([tmp_path / "missing"])
    assert "配额账本不存在" in capsys.readouterr().out


def test_mineru_quota_env_override(tmp_path, capsys, monkeypatch):
    _write_ledger(tmp_path, flash_tasks=3, flash_pages=12)
    monkeypatch.setenv("LANGHUAN_MINERU_QUOTA", str(tmp_path))
    doctor.check_mineru_quota()
    out = capsys.readouterr().out
    assert "flash 3 任务/12 页" in out
    assert "[OK" in out


def _skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")


def test_duplicate_shared_and_plugin_skills_warn_without_mutation(tmp_path, capsys):
    name = "langhuan-lit-scout-harvester"
    shared = tmp_path / "shared"
    trae = tmp_path / "trae"
    _skill(shared, name)
    _skill(trae / "plugins/trae-user-upload/langhuan/1/skills", name)
    _skill(trae / "plugins/other/demo/1/skills", "langhuan-not-a-member")
    doctor.check_duplicate_skills(shared, tmp_path / "wb", trae, {name})
    assert "TRAE: 1 个同名技能可能重复命中" in capsys.readouterr().out
    assert (shared / name / "SKILL.md").is_file()


def test_disabled_skill_names_cover_plain_and_plugin_qualified_ids(tmp_path, capsys):
    names = {"langhuan-lit-scout-harvester", "langhuan-archive-plate-assembler"}
    shared = tmp_path / "shared"
    trae = tmp_path / "trae"
    for name in names:
        _skill(shared, name)
        _skill(trae / "plugins/registry/langhuan/1/skills", name)
    (trae / "skill-config.json").write_text(json.dumps({
        "disabledSkills": [
            "langhuan-lit-scout-harvester",
            "trae-user-upload:langhuan:langhuan-archive-plate-assembler",
        ]
    }), encoding="utf-8")
    doctor.check_duplicate_skills(shared, tmp_path / "wb", trae, names)
    out = capsys.readouterr().out
    assert "TRAE: 2 个同名技能均已列入 disabledSkills" in out
    assert "TRAE: 2 个同名技能可能" not in out


def test_workbuddy_host_skill_and_plugin_overlap_warns(tmp_path, capsys):
    name = "langhuan-peer-collab-strategizer"
    workbuddy = tmp_path / "workbuddy"
    _skill(workbuddy / "skills", name)
    _skill(workbuddy / "plugins/market/langhuan/1/skills", name)
    doctor.check_duplicate_skills(tmp_path / "shared", workbuddy, tmp_path / "trae", {name})
    assert "WorkBuddy: 1 个同名技能可能重复命中" in capsys.readouterr().out


def test_invalid_disabled_skills_config_is_visible_and_does_not_hide_duplicate(tmp_path, capsys):
    name = "langhuan-lit-scout-harvester"
    shared = tmp_path / "shared"
    trae = tmp_path / "trae"
    _skill(shared, name)
    _skill(trae / "plugins/registry/langhuan/1/skills", name)
    (trae / "skill-config.json").write_text("not json", encoding="utf-8")
    doctor.check_duplicate_skills(shared, tmp_path / "wb", trae, {name})
    out = capsys.readouterr().out
    assert "无法读取" in out
    assert "可能重复命中" in out


def test_duplicate_check_environment_overrides(tmp_path, capsys, monkeypatch):
    name = "langhuan-lit-scout-harvester"
    shared = tmp_path / "custom-shared"
    trae = tmp_path / "custom-trae"
    workbuddy = tmp_path / "custom-wb"
    _skill(shared, name)
    _skill(trae / "plugins/registry/langhuan/1/skills", name)
    monkeypatch.setenv("LANGHUAN_SKILLS_DIR", str(shared))
    monkeypatch.setenv("LANGHUAN_TRAE_HOME", str(trae))
    monkeypatch.setenv("LANGHUAN_WORKBUDDY_HOME", str(workbuddy))
    doctor.check_duplicate_skills(members={name})
    assert "TRAE: 1 个同名技能可能重复命中" in capsys.readouterr().out


def _fake_doctor_checks(monkeypatch, platform_calls, python_level="OK"):
    monkeypatch.setattr(doctor, "_json_output", False)
    monkeypatch.setattr(doctor, "check_python", lambda: doctor.emit(python_level, "python", "fake"))
    for name in ("check_venvs", "check_hosts", "check_duplicate_skills", "check_mineru_quota"):
        monkeypatch.setattr(doctor, name, lambda: None)
    monkeypatch.setattr(doctor, "check_creds", lambda: {})
    monkeypatch.setattr(doctor, "check_settings", lambda slots: None)

    def fake_platforms():
        platform_calls.append("called")
        doctor.emit("OK", "platforms", "fake network")

    monkeypatch.setattr(doctor, "check_platforms", fake_platforms)


def test_doctor_offline_json_skips_network_and_preserves_local_findings(tmp_path, capsys, monkeypatch):
    calls = []
    _fake_doctor_checks(monkeypatch, calls)
    assert doctor.main(["--offline", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls == []
    assert report["ok"] is True
    assert report["offline"] is True
    assert report["fail"] == 0
    assert any(item["group"] == "python" for item in report["findings"])
    assert any("--offline" in item["message"] for item in report["findings"])


def test_doctor_json_fail_exit_code(tmp_path, capsys, monkeypatch):
    _fake_doctor_checks(monkeypatch, [], python_level="FAIL")
    assert doctor.main(["--offline", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "ok": False,
        "fail": 1,
        "warn": 0,
        "offline": True,
        "findings": [
            {"level": "FAIL", "group": "python", "message": "fake"},
            {"level": "INFO", "group": "platforms", "message": "--offline：跳过 MinerU/Sciverse/Dianshi 网络探测"},
        ],
    }


def test_doctor_online_mode_still_runs_platform_probe(tmp_path, capsys, monkeypatch):
    calls = []
    _fake_doctor_checks(monkeypatch, calls)
    assert doctor.main(["--json"]) == 0
    assert calls == ["called"]
    report = json.loads(capsys.readouterr().out)
    assert report["offline"] is False
    assert report["findings"][-1]["message"] == "fake network"
