"""Kimi manifest marketplace metadata and localization contract (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATHS = [ROOT / "kimi.plugin.json", ROOT / ".claude-plugin/plugin.json"]
DISPLAY_FIELDS = {
    "displayName", "shortDescription", "longDescription", "developerName", "tryPrompts"
}


def _manifest(path=MANIFEST_PATHS[0]):
    return json.loads(path.read_text(encoding="utf-8"))


def _locale():
    return json.loads((ROOT / "locales/en-US.json").read_text(encoding="utf-8"))


def test_dual_manifests_are_byte_identical():
    assert MANIFEST_PATHS[0].read_bytes() == MANIFEST_PATHS[1].read_bytes()


def test_marketplace_interface_fields_and_locale_pointer():
    interface = _manifest()["interface"]
    assert DISPLAY_FIELDS <= interface.keys()
    assert interface["defaultLocale"] == "zh-CN"
    assert interface["locales"] == "./locales/"
    assert (ROOT / interface["locales"] / "en-US.json").is_file()


@pytest.mark.parametrize("path", MANIFEST_PATHS)
def test_short_description_and_try_prompts(path):
    interface = _manifest(path)["interface"]
    assert len(interface["shortDescription"]) <= 20
    assert not interface["shortDescription"].endswith(("。", "！", "？", ".", "!", "?"))
    assert interface["tryPrompts"]
    assert all(isinstance(prompt, str) and prompt.strip() for prompt in interface["tryPrompts"])


def test_skill_overrides_cover_every_declared_skill():
    manifest = _manifest()
    overrides = manifest["interface"]["skillOverrides"]
    for skill_path in manifest["skills"]:
        assert skill_path.startswith("./")
        skill_dir = ROOT / skill_path
        assert (skill_dir / "SKILL.md").is_file()
        override = overrides[skill_dir.name]
        assert override["displayName"].strip()
        assert override["description"].strip()


def test_english_locale_is_complete_and_distinct():
    default = _manifest()["interface"]
    translated = _locale()
    assert DISPLAY_FIELDS <= translated.keys()
    assert translated["skillOverrides"].keys() == default["skillOverrides"].keys()
    assert translated["displayName"] != default["displayName"]
    assert translated["shortDescription"] != default["shortDescription"]
    assert translated["longDescription"] != default["longDescription"]
    assert len(translated["shortDescription"]) <= 20
    for skill_name, override in translated["skillOverrides"].items():
        assert override["displayName"] != default["skillOverrides"][skill_name]["displayName"]
        assert override["description"] != default["skillOverrides"][skill_name]["description"]


def test_component_paths_and_try_prompt_commands_exist():
    manifest = _manifest()
    command_names = set()
    for command_path in manifest["commands"]:
        assert command_path.startswith("./")
        path = ROOT / command_path
        assert path.is_file()
        command_names.add(path.stem)
    for prompt in manifest["interface"]["tryPrompts"]:
        command = prompt.rsplit(":", 1)[-1]
        assert command in command_names
