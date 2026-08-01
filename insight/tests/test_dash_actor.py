# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.actor (issue #126, E4.S3, Task 1). Pure stdlib -- no duckdb needed."""
import ast
import json

import pytest

from insight.dash.actor import ActorResolutionError, _safe_name, resolve_actor


def test_explicit_flag_wins_and_is_sanitized():
    """Explicit wins even when the config path doesn't exist at all -- config is never consulted
    once --actor is given."""
    assert resolve_actor("/does/not/exist", explicit="al ice!!") == "alice"


def test_config_actor_used_when_no_explicit_flag(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"ledger": {"actor": "swapnil-agrim"}}), encoding="utf-8",
    )
    assert resolve_actor(tmp_path) == "swapnil-agrim"


def test_missing_config_raises_actor_resolution_error(tmp_path):
    with pytest.raises(ActorResolutionError) as exc:
        resolve_actor(tmp_path)
    assert "--actor" in str(exc.value)
    assert "ledger.actor" in str(exc.value)


def test_malformed_json_config_degrades_to_unresolved_not_a_crash(tmp_path):
    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ActorResolutionError):
        resolve_actor(tmp_path)


def test_empty_or_blank_actor_field_is_treated_as_unset(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"ledger": {"actor": "   "}}), encoding="utf-8",
    )
    with pytest.raises(ActorResolutionError):
        resolve_actor(tmp_path)


def test_config_without_a_ledger_block_is_treated_as_unset(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(ActorResolutionError):
        resolve_actor(tmp_path)


def test_ledger_block_not_an_object_is_treated_as_unset(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"ledger": "not-an-object"}), encoding="utf-8",
    )
    with pytest.raises(ActorResolutionError):
        resolve_actor(tmp_path)


def test_config_top_level_not_an_object_is_treated_as_unset(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ActorResolutionError):
        resolve_actor(tmp_path)


def test_module_never_imports_subprocess_or_duckdb():
    """AST-based, mirrors tests/test_import_boundary.py's own technique -- the durable, structural
    proof that Decision 1's 'no gh/network fallback' claim can't silently regress."""
    import insight.dash.actor as mod

    source = ast.parse(open(mod.__file__, encoding="utf-8").read(), filename=mod.__file__)
    banned = {"subprocess", "duckdb"}
    found = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".", 1)[0])
    assert not (found & banned), found & banned


def test_path_traversal_characters_are_stripped_from_explicit_actor():
    result = resolve_actor("/does/not/exist", explicit="../../etc")
    assert "/" not in result
    assert ".." not in result


def test_safe_name_defaults_to_unknown_when_nothing_survives():
    assert _safe_name("!!!") == "unknown"


def test_safe_name_caps_at_64_chars():
    assert len(_safe_name("a" * 100)) == 64
