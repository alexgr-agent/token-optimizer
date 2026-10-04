#!/usr/bin/env python3
"""Packaging guards for the desktop status bar.

The bar ships inside the token-optimizer plugin: the root hooks/hooks.json names
desktop/token-optimizer-desktop/hooks/register.tsx under ``modules`` beside the
classic hooks (an older Claude Code skips the module and keeps every hook).
These tests pin that:

  * the root hooks.json names the module, which exists inside the plugin;
  * the Codex and Cowork builds leave it out;
  * the marketplace has no separate desktop listing (it would draw a second bar);
  * installing asks for no settings (no userConfig in the shipped manifests);
  * the desktop folder still loads on its own, for its own test runs.

Run: python3 -m pytest tests/test_desktop_plugin_packaging.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
ROOT_MANIFEST = REPO / ".claude-plugin" / "plugin.json"
DESKTOP_NAME = "token-optimizer-desktop"
DESKTOP_DIR = REPO / "desktop" / DESKTOP_NAME
DESKTOP_MANIFEST = DESKTOP_DIR / ".claude-plugin" / "plugin.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketplace_has_no_separate_desktop_entry():
    # The bar ships inside token-optimizer; a second listing would draw it twice.
    names = [p["name"] for p in _load(MARKETPLACE)["plugins"]]
    assert DESKTOP_NAME not in names


def test_main_plugin_hooks_json_names_the_desktop_module():
    hooks = _load(REPO / "hooks" / "hooks.json")
    assert hooks["modules"] == ["../desktop/token-optimizer-desktop/hooks/register.tsx"]
    module = (REPO / "hooks" / hooks["modules"][0]).resolve()
    assert module == (DESKTOP_DIR / "hooks" / "register.tsx").resolve()
    assert module.is_file()
    assert REPO.resolve() in module.parents, "the module must sit inside the plugin"
    assert hooks["hooks"], "the classic hooks stay beside the module"


def test_codex_and_cowork_builds_leave_the_module_out():
    # Neither build ships desktop/, and neither harness loads hooks modules.
    for hooks_json in (
        REPO / "plugins" / "token-optimizer" / "hooks" / "hooks.json",
        REPO / "cowork" / "token-optimizer" / "hooks" / "hooks.json",
    ):
        assert "modules" not in _load(hooks_json), f"{hooks_json.relative_to(REPO)} has a modules key"


def test_main_manifest_declares_the_bars_state_types():
    # Claude Code's plugin validator requires every state key the module writes
    # to be declared in the types file the manifest names.
    types = _load(ROOT_MANIFEST)["types"]
    path = (REPO / types).resolve()
    assert path == (DESKTOP_DIR / "types" / "index.d.ts").resolve()
    assert path.is_file()
    assert _load(DESKTOP_MANIFEST)["types"] == "./types/index.d.ts"
    # The Cowork build carries no bar, so no types pointer either.
    assert "types" not in _load(REPO / "cowork" / "token-optimizer" / ".claude-plugin" / "plugin.json")


def test_installing_token_optimizer_asks_for_no_settings():
    # Plugin options make Claude Code ask to configure them on install; the
    # bar's switches are environment variables instead.
    for manifest in (ROOT_MANIFEST, REPO / "cowork" / "token-optimizer" / ".claude-plugin" / "plugin.json"):
        assert "userConfig" not in _load(manifest), f"{manifest.relative_to(REPO)} declares userConfig"


def test_desktop_folder_still_loads_on_its_own_for_its_tests():
    manifest = _load(DESKTOP_MANIFEST)
    # The same name as the shipped plugin: the bar's state lives under it.
    assert manifest["name"] == _load(ROOT_MANIFEST)["name"]
    assert "userConfig" not in manifest
    # No version of its own: it never ships alone, and a second version would drift.
    assert "version" not in manifest
    assert manifest["license"] == _load(ROOT_MANIFEST)["license"]
    assert manifest["types"] == "./types/index.d.ts"
    assert (DESKTOP_DIR / "types" / "index.d.ts").is_file()
    hooks = _load(DESKTOP_DIR / "hooks" / "hooks.json")
    assert hooks == {"modules": ["./register.tsx"]}
