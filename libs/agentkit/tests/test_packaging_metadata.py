"""Keep wheel metadata aligned with agentkit's single-file modules."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_every_declared_package_has_a_real_directory():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    for package in metadata["packages"]:
        assert (root.parent / Path(*package.split("."))).is_dir(), package
    assert not {"agentkit.fetch", "agentkit.quota"}.intersection(metadata["packages"])
