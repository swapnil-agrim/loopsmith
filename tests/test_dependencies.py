"""The kit auto-installs its companion plugins (superpowers + code-review) via the native
plugin-dependency mechanism. plugin.json declares the deps; the root marketplace.json allowlists
the cross-marketplace they live in. Unversioned (track latest, no git-tag resolution).
Ref: https://code.claude.com/docs/en/plugin-dependencies"""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKETPLACE = "claude-plugins-official"
COMPANIONS = {"superpowers", "code-review"}


def _load(rel):
    return json.loads((ROOT / rel).read_text())


def _dep_name(entry):
    # a dependency entry is either a bare name string or {"name": ..., "marketplace": ...}
    return entry if isinstance(entry, str) else entry["name"]


def test_plugin_declares_companion_dependencies():
    deps = _load(".claude-plugin/plugin.json").get("dependencies", [])
    by_name = {_dep_name(d): d for d in deps}
    assert COMPANIONS <= set(by_name), f"missing companion deps: {COMPANIONS - set(by_name)}"
    for name in COMPANIONS:
        entry = by_name[name]
        assert isinstance(entry, dict), f"{name} dep must be an object carrying its marketplace"
        assert entry.get("marketplace") == MARKETPLACE
        assert "version" not in entry, f"{name} dep is intentionally unversioned (tracks latest)"


def test_root_marketplace_allows_cross_marketplace():
    mk = _load(".claude-plugin/marketplace.json")
    assert MARKETPLACE in mk.get("allowCrossMarketplaceDependenciesOn", []), \
        "root marketplace must allowlist the cross-marketplace the deps resolve in"
