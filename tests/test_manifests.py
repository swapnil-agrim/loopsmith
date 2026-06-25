import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(rel):
    return json.loads((ROOT / rel).read_text())


def test_plugin_manifest_valid():
    m = _load(".claude-plugin/plugin.json")
    assert m["name"] == "sdlc-kit"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])   # semver shape, not a frozen value
    assert m["license"] == "MIT"
    assert m["description"]


def test_marketplace_lists_plugin_from_root():
    m = _load(".claude-plugin/marketplace.json")
    names = [p["name"] for p in m["plugins"]]
    assert "sdlc-kit" in names
    assert m["plugins"][0]["source"] == "./"
