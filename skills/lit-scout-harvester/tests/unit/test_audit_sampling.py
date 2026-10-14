"""Tests for lit_scout_harvester.utils.audit_sampling (C5)."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.config_loader import load_caliber
from lit_scout_harvester.utils.audit_sampling import (
    resolve_audit_rules,
    select_audit_sample,
)

IDS = [f"r{i:04d}" for i in range(1000)]


def test_same_seed_same_input_byte_reproducible():
    a = select_audit_sample(IDS, rate=0.05, seed=42)
    b = select_audit_sample(IDS, rate=0.05, seed=42)
    assert a == b
    assert "\n".join(a) == "\n".join(b)
    assert 0 < len(a) < len(IDS)


def test_different_seed_differs():
    a = select_audit_sample(IDS, rate=0.2, seed=1)
    b = select_audit_sample(IDS, rate=0.2, seed=2)
    assert a != b


def test_rate_boundaries():
    assert select_audit_sample(IDS, rate=0.0, seed=1) == []
    assert select_audit_sample(IDS, rate=1.0, seed=1) == IDS


def test_rate_validation():
    with pytest.raises(ValueError):
        select_audit_sample(IDS, rate=1.5, seed=1)
    with pytest.raises(ValueError):
        select_audit_sample(IDS, rate=-0.1, seed=1)


def test_sample_keeps_input_order():
    sample = select_audit_sample(IDS, rate=0.5, seed=7)
    positions = [IDS.index(rid) for rid in sample]
    assert positions == sorted(positions)


def test_caliber_defaults_applied():
    caliber = load_caliber()
    assert caliber.audit.audit_rate == 0.05
    assert isinstance(caliber.audit.default_seed, int)
    rate, seed = resolve_audit_rules()
    assert rate == 0.05
    assert seed == caliber.audit.default_seed
    # 不传 rate/seed 时也能直接抽样
    sample = select_audit_sample(IDS)
    assert 0 < len(sample) < len(IDS)


def test_explicit_rules_avoid_caliber_load():
    from lit_scout_harvester.core.config_loader import AuditRules

    rules = AuditRules(audit_rate=0.1, default_seed=99)
    sample = select_audit_sample(IDS, audit_rules=rules)
    assert 0 < len(sample) < len(IDS)
