"""显式 .env 加载 + YAML 配置加载（extends / 深合并 / 环境变量插值）。

技能无关的通用配置能力；技能特异的 Pydantic section 模型留在各技能内部。
本模块 import 时无任何副作用（不读 .env、不碰 ``os.environ``）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


class ConfigError(Exception):
    """Raised when configuration loading fails (missing file, bad structure)."""


# ---------------------------------------------------------------------------
# .env 显式加载
# ---------------------------------------------------------------------------


def _parse_env_line(line: str) -> Optional[tuple[str, str]]:
    """Parse one ``KEY=VALUE`` line; return ``None`` for blanks/comments."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    key, sep, value = line.partition("=")
    if not sep:
        return None
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def _load_env_stdlib(path: Path, *, override: bool) -> bool:
    """Minimal stdlib .env loader (fallback when python-dotenv is absent).

    Supports ``KEY=VALUE`` lines, ``#`` comments, an optional ``export ``
    prefix, and single/double quoted values. Unlike python-dotenv it does not
    expand ``${VAR}`` references inside values.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
    return True


def load_env(path: Optional[Union[str, Path]] = None, *, override: bool = False) -> bool:
    """Explicitly load a ``.env`` file into ``os.environ``.

    Args:
        path: Path to the .env file. Defaults to ``.env`` under the current
            working directory. A missing file is not an error.
        override: When ``True``, values from the file replace existing
            environment variables.

    Returns:
        ``True`` if a file was found and loaded, ``False`` otherwise.

    Uses python-dotenv when it is importable (matching the historical LSH
    behaviour exactly); otherwise falls back to a minimal stdlib parser so
    that vendored skill zips stay self-contained.
    """
    env_path = Path(path) if path is not None else Path.cwd() / ".env"
    if not env_path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return _load_env_stdlib(env_path, override=override)
    load_dotenv(env_path, override=override)
    return True


# ---------------------------------------------------------------------------
# 环境变量插值与深合并
# ---------------------------------------------------------------------------


def interpolate(value: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """Recursively interpolate ``${VAR}`` / ``${VAR:default}`` in strings.

    Args:
        value: Arbitrary nested structure of dicts/lists/strings.
        env: Mapping to resolve variables from; defaults to ``os.environ``.
    """
    if env is None:
        env = os.environ
    if isinstance(value, str):

        def replacer(match: "re.Match[str]") -> str:
            var_name = match.group(1)
            default = match.group(2)
            return env.get(var_name, default if default is not None else "")

        return ENV_VAR_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(item, env) for item in value]
    return value


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge two dictionaries. Dictionaries are merged; other values are overwritten."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# YAML 加载（extends 递归继承 + 深合并 + 插值）
# ---------------------------------------------------------------------------


def _load_raw_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file, recursively resolving ``extends`` (no interpolation)."""
    import yaml  # 延迟导入：agentkit 核心保持纯 stdlib，yaml 仅在此处需要

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file {path} must contain a top-level mapping")

    parent_name = data.pop("extends", None)
    if parent_name:
        parent_path = path.parent / parent_name
        parent_data = _load_raw_yaml(parent_path)
        data = deep_merge(parent_data, data)

    return data


def load_yaml(
    path: Union[str, Path],
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Load a YAML config: resolve ``extends``, deep-merge, then interpolate.

    ``extends: <relative filename>`` is resolved against the including file's
    directory, recursively; child keys deep-merge over parent keys. After
    merging, ``${VAR}`` / ``${VAR:default}`` placeholders in string values are
    interpolated from ``env`` (default: ``os.environ``).

    Pydantic validation of the resulting dict is the caller's responsibility
    (skill-specific section models stay in the skill).

    Raises:
        ConfigError: If the file is missing or not a top-level mapping.
    """
    raw = _load_raw_yaml(Path(path))
    return interpolate(raw, env)
