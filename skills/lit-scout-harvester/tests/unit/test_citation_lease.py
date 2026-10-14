"""Tests for the optional host capability lease field in CITATION.md (B3)."""

from __future__ import annotations

from pathlib import Path

from lit_scout_harvester.utils.citation import HOST_LEASE_ENV_VAR, generate_citation

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_lease_recorded_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv(HOST_LEASE_ENV_VAR, "lease-abc123:300")
    path = generate_citation(tmp_path / "CITATION.md")
    content = path.read_text(encoding="utf-8")
    assert "Host Capability Lease" in content
    assert "lease-abc123" in content
    assert "ttl=300" in content


def test_no_lease_output_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(HOST_LEASE_ENV_VAR, raising=False)
    path = generate_citation(tmp_path / "CITATION.md")
    content = path.read_text(encoding="utf-8")
    assert "Host Capability Lease" not in content
    assert "host_capability_lease" not in content


def test_malformed_lease_still_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv(HOST_LEASE_ENV_VAR, "lease-without-ttl")
    path = generate_citation(tmp_path / "CITATION.md")
    content = path.read_text(encoding="utf-8")
    assert "lease-without-ttl" in content


def test_lease_combines_with_caller_extra_sources(tmp_path, monkeypatch):
    monkeypatch.setenv(HOST_LEASE_ENV_VAR, "lease-xyz:60")
    path = generate_citation(
        tmp_path / "CITATION.md",
        extra_sources=[{"name": "Custom Source", "details": "details here"}],
    )
    content = path.read_text(encoding="utf-8")
    assert "Custom Source" in content
    assert "lease-xyz" in content


def test_env_example_documents_lease_placeholder():
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert HOST_LEASE_ENV_VAR in example
