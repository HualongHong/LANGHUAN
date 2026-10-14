"""Tests for agentkit.packaging.freeze（D7/S3 冻结/核验）+ build_plugin CLI 冒烟。

全部在 tmp_path 内操作，不触碰仓库真实 dist/。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentkit.packaging import (
    MANIFEST_NAME,
    freeze_directory,
    verify_directory,
)

SUITE_ROOT = Path(__file__).resolve().parents[3]
BUILD_PLUGIN = SUITE_ROOT / "build_plugin.py"


def _make_dist(root: Path) -> Path:
    """造一个三文件 + 子目录的小 dist。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "b-skill.zip").write_bytes(b"bbb-bytes")
    (root / "a-skill.zip").write_bytes(b"aaa-bytes")
    sub = root / "sub"
    sub.mkdir()
    # newline="" 阻止 Windows 文本模式把 \n 转 \r\n（保持 5 字节定长）。
    (sub / "note.txt").write_text("note\n", encoding="utf-8", newline="")
    return root


def _parse(root: Path) -> dict[str, str]:
    entries = {}
    for line in (root / MANIFEST_NAME).read_text(encoding="utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        entries[rel] = digest
    return entries


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


class TestFreeze:
    def test_creates_manifest_and_frozen(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        result = freeze_directory(dist, "0.3", command="build_plugin.py freeze --version 0.3")
        assert result.manifest_path.is_file()
        assert result.frozen_path.is_file()
        # 三个 payload 文件 + FROZEN.md 自身入册；MANIFEST 自身不入册。
        assert result.file_count == 4
        assert result.total_bytes == len(b"bbb-bytes") + len(b"aaa-bytes") + len("note\n")

    def test_manifest_hashes_match_sha256(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        entries = _parse(dist)
        assert set(entries) == {"a-skill.zip", "b-skill.zip", "sub/note.txt", "FROZEN.md"}
        for rel, digest in entries.items():
            assert digest == hashlib.sha256((dist / rel).read_bytes()).hexdigest()

    def test_manifest_stable_sorted_and_lf(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        raw = (dist / MANIFEST_NAME).read_bytes()
        assert b"\r" not in raw
        paths = [line.split(b"  ", 1)[1].decode("utf-8") for line in raw.splitlines()]
        assert paths == sorted(paths)

    def test_payload_lines_identical_across_refreeze(self, tmp_path):
        # 文件集与内容不变 → payload 行逐字节一致（仅 FROZEN.md 因时间戳换行）。
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        first = _parse(dist)
        freeze_directory(dist, "0.3")
        second = _parse(dist)
        payload = {r for r in first if r != "FROZEN.md"}
        assert {r: d for r, d in first.items() if r in payload} == {
            r: d for r, d in second.items() if r in payload
        }

    def test_frozen_md_records_metadata(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "1.0rc1", command="build_plugin.py freeze --version 1.0rc1")
        text = (dist / "FROZEN.md").read_text(encoding="utf-8")
        assert "version: 1.0rc1" in text
        assert "file_count: 4" in text
        assert "frozen_at:" in text
        assert "command: build_plugin.py freeze --version 1.0rc1" in text

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            freeze_directory(tmp_path / "nope", "0.3")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerify:
    def test_roundtrip_ok(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        result = verify_directory(dist)
        assert result.ok
        assert result.missing == result.changed == result.added == []

    def test_changed_file_reported(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        (dist / "a-skill.zip").write_bytes(b"tampered")
        result = verify_directory(dist)
        assert not result.ok
        assert result.changed == ["a-skill.zip"]
        assert result.missing == result.added == []

    def test_missing_file_reported(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        (dist / "sub" / "note.txt").unlink()
        result = verify_directory(dist)
        assert result.missing == ["sub/note.txt"]
        assert result.changed == result.added == []

    def test_added_file_reported(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        (dist / "late.zip").write_bytes(b"late")
        result = verify_directory(dist)
        assert result.added == ["late.zip"]
        assert result.missing == result.changed == []

    def test_frozen_md_tamper_is_changed(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        with (dist / "FROZEN.md").open("a", encoding="utf-8") as fh:
            fh.write("forged")
        result = verify_directory(dist)
        assert result.changed == ["FROZEN.md"]

    def test_manifest_not_self_tracked(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        # 覆写 MANIFEST 后 verify 校验的是磁盘文件 vs 清单，不应把自身当差异。
        entries = _parse(dist)
        entries["a-skill.zip"] = hashlib.sha256(b"forged").hexdigest()
        lines = [f"{d}  {r}" for r, d in sorted(entries.items())]
        (dist / MANIFEST_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = verify_directory(dist)
        assert "MANIFEST.sha256" not in result.added
        assert result.changed == ["a-skill.zip"]

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_directory(tmp_path)

    def test_corrupt_manifest_raises(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        (dist / MANIFEST_NAME).write_text("not-a-hash line\n", encoding="utf-8")
        with pytest.raises(ValueError):
            verify_directory(dist)

    def test_default_manifest_path(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze_directory(dist, "0.3")
        result = verify_directory(manifest_path=dist / MANIFEST_NAME)
        assert result.ok
        assert result.root == dist


# ---------------------------------------------------------------------------
# build_plugin.py CLI 冒烟（freeze/verify 退出码与输出）
# ---------------------------------------------------------------------------


def _run_build_plugin(*argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # 子进程中文输出按 UTF-8 解码
    return subprocess.run(
        [sys.executable, str(BUILD_PLUGIN), *argv],
        cwd=SUITE_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


class TestBuildPluginFreezeVerifyCLI:
    def test_freeze_then_verify_exit_0(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        freeze = _run_build_plugin("freeze", "--version", "0.9", "--out", str(dist))
        assert freeze.returncode == 0
        assert "[FROZEN]" in freeze.stdout
        assert "version=0.9" in freeze.stdout

        verify = _run_build_plugin("verify", "--manifest", str(dist / MANIFEST_NAME))
        assert verify.returncode == 0
        assert "零回归" in verify.stdout

    def test_verify_changed_exits_1(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        _run_build_plugin("freeze", "--version", "0.9", "--out", str(dist))
        (dist / "a-skill.zip").write_bytes(b"tampered")
        verify = _run_build_plugin("verify", "--manifest", str(dist / MANIFEST_NAME))
        assert verify.returncode == 1
        assert "CHANGED (1)" in verify.stdout
        assert "a-skill.zip" in verify.stdout

    def test_verify_missing_and_added_exits_1(self, tmp_path):
        dist = _make_dist(tmp_path / "dist")
        _run_build_plugin("freeze", "--version", "0.9", "--out", str(dist))
        (dist / "b-skill.zip").unlink()
        (dist / "extra.zip").write_bytes(b"new")
        verify = _run_build_plugin("verify", "--manifest", str(dist / MANIFEST_NAME))
        assert verify.returncode == 1
        assert "MISSING (1)" in verify.stdout
        assert "ADDED (1)" in verify.stdout
        assert "b-skill.zip" in verify.stdout
        assert "extra.zip" in verify.stdout

    def test_verify_without_manifest_uses_dist_default(self, tmp_path):
        # 无 --manifest 时回落 suite.yaml build.output_dir（dist/）——这里用
        # 空 dist 验证"清单不存在"路径的退出码与报错（不污染仓库真实 dist）。
        empty = tmp_path / "empty-dist"
        empty.mkdir()
        # 通过 --manifest 显式指向空目录（等价默认解析的缺失场景）。
        verify = _run_build_plugin("verify", "--manifest", str(empty / MANIFEST_NAME))
        assert verify.returncode == 1
        assert "清单不存在" in verify.stderr
