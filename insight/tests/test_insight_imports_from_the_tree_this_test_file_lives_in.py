# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Pins a general invariant -- `import insight` (and its submodules) must resolve from THE SAME
insight/ tree this test file itself lives in, never a stale ambient install elsewhere on
sys.path -- and doubles as the load-bearing check for issue #297 [E15.S3]'s standalone-extraction
proof (insight/tests/test_standalone_extraction.py).

WHY THIS LIVES IN ITS OWN FILE, NOT INSIDE test_standalone_extraction.py: that file's own
recursion guard (`_CHILD_SENTINEL`) makes its WHOLE module skip itself the instant it runs inside
the child copy's own pytest process (see that file's docstring) -- so an assertion planted inside
it would never actually execute there, only in the outer run. This file carries no such guard and
is collected normally in both the outer run and the child's, which is exactly what makes it able to
prove something the outer run alone cannot: that `insight` resolved correctly INSIDE the process
the standalone proof's child pytest run actually is -- not inside a second, separately spawned
process that merely shares its `cwd` (a decoupled proxy, not an inspection of the run it claims to
guard -- issue #297 plan review finding A2, which an earlier draft of this story got wrong: it
spawned `python3 -c "import insight; print(insight.__file__)"` as a second subprocess and credited
the result to `insight/tests/__init__.py` + pytest's "prepend" import mode, when neither of those
is what actually did the work -- see test_standalone_extraction.py's own corrected docstring).

test_standalone_extraction.py's `test_child_planted_self_check_ran_and_passed` requires this exact
test to have reported PASSED inside the child's own captured output (that file's child invocation
runs with `-v`, which prints every outcome by name, precisely so this can be read from the outside
without spawning a second process)."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")  # insight.ingest.store imports duckdb at module load

import insight  # noqa: E402
import insight.ingest.store  # noqa: E402


def test_insight_imports_from_the_tree_this_test_file_lives_in():
    """`insight/` is both this test's own parent-of-parent and, per issue #297, a directory that
    must be copyable as a standalone unit -- so the one useful thing to check is not "does `import
    insight` succeed" (it would, even from a stale install) but "did it resolve to THIS tree", the
    one this test file is itself sitting inside of, wherever that tree currently is: the real repo,
    or a copy issue #297's proof made of it."""
    here = pathlib.Path(__file__).resolve().parents[1]  # insight/ itself
    resolved_pkg = pathlib.Path(insight.__file__).resolve()
    resolved_store = pathlib.Path(insight.ingest.store.__file__).resolve()
    assert resolved_pkg.is_relative_to(here), (
        f"insight resolved to {resolved_pkg}, not under {here} -- an ambient/stale install "
        "shadowed the tree this test file actually lives in"
    )
    assert resolved_store.is_relative_to(here), (
        f"insight.ingest.store resolved to {resolved_store}, not under {here} -- same failure "
        "mode, one level deeper"
    )
