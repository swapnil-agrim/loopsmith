import importlib.util
import json
import os
import pathlib
import subprocess

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


classify = _mod("watch_classify")
watch = _mod("watch")
sync = _mod("sync")
ledger = _mod("ledger")

ME = "rae"
ON = {"ledger": {"enabled": True, "actor": ME}}


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or ON))
    return d


def _entry(actor, seq, **kw):
    base = {"id": f"{actor}:{seq}", "ts": f"2026-07-25T09:{seq:02d}:00Z",
            "actor": actor, "kind": "handoff", "goal": "g.md"}
    base.update(kw)
    return base


# ------------------------------------------------------------------ classify


def test_only_entries_addressed_to_me_surface():
    entries = [_entry("amy", 1, to=ME), _entry("amy", 2, to="someone-else"), _entry("amy", 3)]
    items, _ = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    assert [e["id"] for e in items] == ["amy:1"]


def test_my_own_entries_never_wake_me():
    entries = [_entry(ME, 1, to=ME)]
    items, _ = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    assert items == []


def test_a_second_tick_over_the_same_ledger_is_silent():
    entries = [_entry("amy", 1, to=ME, issue=61)]
    first, cursor = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    second, _ = classify.classify(entries, cursor, ME)
    assert len(first) == 1 and second == []


def test_a_replayed_entry_after_a_rebase_does_not_refire():
    """A colleague's file can be rewritten by a rebase, resetting seq — the signature catches it."""
    cursor = classify.classify([_entry("amy", 4, to=ME, issue=61, state="open")],
                               dict(classify.EMPTY_CURSOR), ME)[1]
    replayed = [_entry("amy", 1, to=ME, issue=61, state="open")]        # same news, lower seq
    items, _ = classify.classify(replayed, cursor, ME)
    assert items == []


def test_a_state_change_is_news():
    cursor = classify.classify([_entry("amy", 1, to=ME, issue=61, state="open")],
                               dict(classify.EMPTY_CURSOR), ME)[1]
    items, _ = classify.classify([_entry("amy", 2, to=ME, issue=61, state="deferred")], cursor, ME)
    assert len(items) == 1 and items[0]["state"] == "deferred"


def test_most_urgent_first_then_oldest():
    entries = [_entry("amy", 1, to=ME, priority="P2", issue=1),
               _entry("amy", 2, to=ME, priority="P0", issue=2),
               _entry("bo", 1, to=ME, issue=3)]                          # no priority = last
    items, _ = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    assert [e["issue"] for e in items] == [2, 1, 3]


def test_cursor_round_trips_and_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "cursor.json"
    assert classify.load_cursor(path) == classify.EMPTY_CURSOR          # absent
    classify.save_cursor(path, {"seen": {"amy": {"entries": 3}}, "signatures": ["x"]})
    assert classify.load_cursor(path)["seen"] == {"amy": {"entries": 3}}
    path.write_text("{not json")
    assert classify.load_cursor(path) == classify.EMPTY_CURSOR          # corrupt = start over, not crash


def test_cursor_round_trips_the_nested_per_stream_shape(tmp_path):
    path = tmp_path / "cursor.json"
    cursor = {"seen": {"amy": {"entries": 3, "events": 7}}, "signatures": ["x"]}
    classify.save_cursor(path, cursor)
    assert classify.load_cursor(path) == cursor


def test_load_cursor_migrates_the_pre_137_flat_seen_shape(tmp_path):
    """Before #137 the only stream that ever existed was entries, so an old flat {actor: seq}
    cursor's baseline can only ever have meant entries."""
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"seen": {"amy": 5, "bo": 2}, "signatures": []}))
    assert classify.load_cursor(path)["seen"] == {"amy": {"entries": 5}, "bo": {"entries": 2}}


def test_migrated_cursor_baseline_prevents_a_full_history_refire(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"seen": {"amy": 5}, "signatures": []}))
    cursor = classify.load_cursor(path)
    entries = [_entry("amy", n, to=ME, issue=n) for n in range(1, 6)]
    items, _ = classify.classify(entries, cursor, ME)
    assert items == []                                       # nothing older than the baseline re-fires


def test_classify_baseline_is_not_mutated_by_later_entries_in_the_same_call():
    """seen[actor] and baseline[actor] must be independent dicts, or the second entry would
    wrongly suppress itself against the first's just-written seq."""
    entries = [_entry("amy", 1, to=ME, issue=1), _entry("amy", 2, to=ME, issue=2)]
    items, _ = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    assert [e["issue"] for e in items] == [1, 2]


def test_entries_and_events_cursors_for_the_same_actor_are_independent():
    entries = [_entry("amy", 1, to=ME, issue=1)]
    events = [dict(id="amy:9", ts="2026-07-25T09:09:00Z", actor="amy", kind="phase")
              for _ in range(1)]
    _, cursor = classify.classify(entries, dict(classify.EMPTY_CURSOR), ME)
    _, cursor = classify.classify(events, cursor, ME, stream=classify.EVENTS)
    assert cursor["seen"]["amy"] == {"entries": 1, "events": 9}


def test_classify_keys_the_seen_baseline_on_actor_and_stream():
    """No EVENT_FIELDS kind carries a `to`, so events structurally never surface — but the
    cursor's advancement must still be real and independent per (actor, stream)."""
    phase_events = [dict(id="amy:1", ts="2026-07-25T09:01:00Z", actor="amy", kind="phase"),
                    dict(id="amy:2", ts="2026-07-25T09:02:00Z", actor="amy", kind="phase")]
    items, cursor = classify.classify(phase_events, dict(classify.EMPTY_CURSOR), ME,
                                      stream=classify.EVENTS)
    assert items == []
    assert cursor["seen"] == {"amy": {"events": 2}}


def test_classify_does_not_raise_on_a_garbage_seen_value_after_migration(tmp_path):
    """A corrupted pre-137 cursor value (`"garbage"`) must migrate to baseline 0, not to
    {"entries": "garbage"} — the latter makes classify() raise TypeError comparing int to str
    on every subsequent tick until someone deletes the cursor file by hand."""
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"seen": {"amy": "garbage"}, "signatures": []}))
    cursor = classify.load_cursor(path)
    items, cursor2 = classify.classify([_entry("amy", 1, to=ME)], cursor, ME)
    assert len(items) == 1                                   # baseline 0, not a crash
    assert cursor2["seen"]["amy"]["entries"] == 1             # self-heals to a real int


def test_load_cursor_survives_a_top_level_json_null(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text("null")
    assert classify.load_cursor(path) == classify.EMPTY_CURSOR


def test_load_cursor_survives_a_truthy_non_dict_seen_or_signatures(tmp_path):
    """`or {}` only substitutes on a FALSY value, so a truthy non-dict `seen` reached `.items()`
    and a truthy non-list `signatures` reached `list()`. load_cursor runs BEFORE save_cursor, so
    either raise disabled every later tick rather than self-healing on the next write."""
    for bad in ('{"seen": "garbage"}', '{"seen": 5}', '{"seen": [1, 2]}', '{"seen": true}'):
        path = tmp_path / "cursor.json"
        path.write_text(bad)
        assert classify.load_cursor(path) == classify.EMPTY_CURSOR
    path = tmp_path / "cursor.json"
    path.write_text('{"seen": {"amy": 3}, "signatures": 7}')
    assert classify.load_cursor(path) == {"seen": {"amy": {"entries": 3}}, "signatures": []}


def test_classify_survives_a_nested_baseline_value_that_is_not_an_int():
    """Hardens the nested case too: a dict whose inner values aren't ints (however it got that
    way) must not crash classify() either."""
    cursor = {"seen": {"amy": {"entries": "bad"}}, "signatures": []}
    items, cursor2 = classify.classify([_entry("amy", 1, to=ME)], cursor, ME)
    assert len(items) == 1                                   # corrupt baseline treated as 0
    assert cursor2["seen"]["amy"]["entries"] == 1             # self-heals on the next write


def test_render_and_summarise():
    assert classify.render_inbox([], ME) == ""
    assert classify.summarise([]) == ""
    items = [_entry("amy", 1, to=ME, issue=61, priority="P0", why="needs a flag", area="engine")]
    text = classify.render_inbox(items, ME)
    assert "#61" in text and "needs a flag" in text and "engine" in text and "ack" in text
    assert "P0" in classify.summarise(items) and "amy" in classify.summarise(items)


# ------------------------------------------------------------------ tick


def test_tick_writes_the_inbox_and_reports(tmp_path):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, "amy").write_text(
        json.dumps(_entry("amy", 1, to=ME, issue=61, priority="P0", why="needs a flag")) + "\n")
    assert "need you" in watch.tick(d)
    assert "#61" in watch.read_inbox(d)


def test_tick_is_a_noop_when_the_ledger_is_off(tmp_path):
    d = _sdlc(tmp_path, {"ledger": {"enabled": False}})
    assert watch.tick(d) == "" and watch.read_inbox(d) == ""


def test_tick_appends_rather_than_dropping_an_unread_item(tmp_path):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    path = ledger.entry_file(d, "amy")
    path.write_text(json.dumps(_entry("amy", 1, to=ME, issue=1, why="first")) + "\n")
    watch.tick(d)
    with path.open("a") as f:
        f.write(json.dumps(_entry("amy", 2, to=ME, issue=2, why="second")) + "\n")
    watch.tick(d)
    inbox = watch.read_inbox(d)
    assert "first" in inbox and "second" in inbox        # the unread first item was not lost


def test_clear_inbox(tmp_path):
    d = _sdlc(tmp_path)
    watch.inbox_path(d).write_text("stuff")
    watch.clear_inbox(d)
    assert watch.read_inbox(d) == ""


def test_watch_cli(tmp_path, capsys):
    d = _sdlc(tmp_path)
    assert watch.main(["watch.py", str(d)]) == 0
    assert watch.main(["watch.py", str(d), "show"]) == 0
    assert "inbox empty" in capsys.readouterr().out
    assert watch.main(["watch.py", str(tmp_path / "missing")]) == 1


# ------------------------------------------------------------------ loop surfacing


def test_loop_next_prints_the_inbox_on_stderr_and_clears_it(tmp_path, capsys):
    loop = _mod("loop")
    d = _sdlc(tmp_path)
    (d / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\n")
    watch.inbox_path(d).write_text("# Inbox — rae\n\nP0 from amy\n")
    loop._surface_inbox(str(d))
    captured = capsys.readouterr()
    assert "LEDGER INBOX" in captured.err and "P0 from amy" in captured.err
    assert captured.out == ""                            # stdout stays the goal channel
    assert watch.read_inbox(d) == ""                     # surfaced once, then cleared


def test_surface_inbox_is_silent_and_safe_with_no_ledger(tmp_path, capsys):
    loop = _mod("loop")
    loop._surface_inbox(str(tmp_path / "nope"))
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------------ sync (transport)


def test_branch_and_remote_defaults_and_overrides():
    assert sync.branch({}) == "sdlc-ledger" and sync.remote({}) == "origin"
    cfg = {"ledger": {"branch": "ops/ledger", "remote": "upstream"}}
    assert sync.branch(cfg) == "ops/ledger" and sync.remote(cfg) == "upstream"


def test_sync_refuses_to_act_before_init(tmp_path):
    d = _sdlc(tmp_path)
    assert "run `sync.py init`" in sync.pull(d, ON)
    assert "run `sync.py init`" in sync.publish(d, ON)


def test_publish_retries_by_rebasing_then_gives_up(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    calls = []

    def git(cwd, args):
        calls.append(args[0])
        if args[0] == "push":
            raise RuntimeError("non-fast-forward")
        return "entries/rae.jsonl" if args[0] == "diff" else ""

    out = sync.publish(d, ON, run=git, attempts=3)
    assert "publish deferred" in out and "non-fast-forward" in out
    assert calls.count("push") == 3 and calls.count("rebase") == 2      # fetch+rebase between tries


def test_publish_is_a_noop_when_nothing_changed(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    assert sync.publish(d, ON, run=lambda c, a: "") == "nothing to publish"


def test_publish_reports_success(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    assert sync.publish(d, ON, run=lambda c, a: "x" if a[0] == "diff" else "") == "published"


def test_bootstrap_seeds_my_file_and_pushes(tmp_path, monkeypatch):
    """One command stands the ledger up: init, seed MY (empty) entries file, publish — so the branch,
    my file, and TEAM.md reach the remote the moment the ledger is switched on (not only after the
    first claim). Here init sees an existing worktree; the point is the seed + the push."""
    d = _sdlc(tmp_path)
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)     # pretend init already made the worktree
    calls = []
    def git(cwd, args):
        calls.append(args[0])
        return "entries/rae.jsonl" if args[0] == "diff" else ""
    out = sync.bootstrap(d, ON, run=git)
    mine = sync.worktree(d) / "entries" / f"{ledger.actor(ON)}.jsonl"
    assert mine.exists()                                          # my entries file was seeded
    assert "push" in calls                                        # and the branch pushed
    assert "already a worktree" in out and "published" in out


def test_publish_renders_and_stages_team_md(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text(
        '{"id":"rae:1","ts":"2026-07-25T09:00:00Z","actor":"rae","kind":"claimed","goal":"7"}\n')
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    calls = []

    def git(cwd, args):
        calls.append(list(args))
        return "TEAM.md" if args[0] == "diff" else ""

    assert sync.publish(d, ON, run=git) == "published"
    team = (ledger.ledger_dir(d) / "TEAM.md").read_text()
    assert team.startswith("# Team ledger") and "claimed" in team    # the rolled-up view goes on the branch
    staged = next(a for a in calls if a[0] == "add")
    assert "TEAM.md" in staged and f"entries/{ME}.jsonl" in staged    # committed alongside my entries


def test_ensure_gitattributes_is_additive_and_idempotent(tmp_path):
    d = tmp_path / "wt"
    d.mkdir()
    assert sync._ensure_gitattributes(d) is True
    text = (d / ".gitattributes").read_text()
    assert "entries/*.jsonl merge=union" in text and "events/*.jsonl merge=union" in text
    assert sync._ensure_gitattributes(d) is False              # second call: nothing to add
    assert (d / ".gitattributes").read_text() == text          # unchanged

    (d / ".gitattributes").write_text("entries/*.jsonl merge=union\n")   # pre-#137 worktree
    assert sync._ensure_gitattributes(d) is True
    repaired = (d / ".gitattributes").read_text()
    assert "entries/*.jsonl merge=union" in repaired and "events/*.jsonl merge=union" in repaired


def test_publish_stages_my_events_file_when_present(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text("{}\n")
    ledger.entries_dir(d, ledger.EVENTS).mkdir(parents=True)
    ledger.entry_file(d, ME, ledger.EVENTS).write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    calls = []

    def git(cwd, args):
        calls.append(list(args))
        return "x" if args[0] == "diff" else ""

    assert sync.publish(d, ON, run=git) == "published"
    staged = next(a for a in calls if a[0] == "add")
    assert f"events/{ME}.jsonl" in staged
    assert f"entries/{ME}.jsonl" in staged
    assert "TEAM.md" in staged


def test_publish_does_not_stage_a_phantom_events_path_when_absent(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    ledger.entries_dir(d).mkdir(parents=True)
    ledger.entry_file(d, ME).write_text("{}\n")
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    calls = []

    def git(cwd, args):
        calls.append(list(args))
        return "x" if args[0] == "diff" else ""

    assert sync.publish(d, ON, run=git) == "published"
    staged = next(a for a in calls if a[0] == "add")
    assert f"events/{ME}.jsonl" not in staged


def test_pull_aborts_a_bad_rebase_instead_of_wedging(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    seen = []

    def git(cwd, args):
        seen.append(args[0])
        if args[0] == "rebase" and "--abort" not in args:
            raise RuntimeError("conflict")
        return ""

    assert "pull deferred" in sync.pull(d, ON, run=git)
    assert seen[-1] == "rebase"                          # the abort ran


def test_pull_reports_success(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    monkeypatch.setattr(sync, "is_worktree", lambda _d: True)
    assert sync.pull(d, ON, run=lambda c, a: "") == "pulled"


def test_sync_cli_refuses_when_the_ledger_is_off(tmp_path, capsys):
    d = _sdlc(tmp_path, {"ledger": {"enabled": False}})
    assert sync.main(["sync.py", "pull", str(d)]) == 1
    assert "ledger is off" in capsys.readouterr().err
    assert sync.main(["sync.py"]) == 2


# ------------------------------------------------------------------ sync against real git


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})


def test_init_creates_an_empty_orphan_branch_as_a_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")

    d = repo / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))

    def git(cwd, args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    out = sync.init(d, ON, run=git)
    assert "ready" in out and sync.is_worktree(d)
    listed = subprocess.run(["git", "-C", str(repo / ".sdlc" / "ledger"), "ls-files"],
                            capture_output=True, text=True).stdout.split()
    assert "code.py" not in listed                        # started from the EMPTY tree
    assert ".gitattributes" in listed and "README.md" in listed
    attrs = (repo / ".sdlc" / "ledger" / ".gitattributes").read_text()
    assert "entries/*.jsonl merge=union" in attrs and "events/*.jsonl merge=union" in attrs
    assert "already a worktree" in sync.init(d, ON, run=git)      # idempotent


def test_init_carries_over_entries_written_before_the_worktree_existed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    d = repo / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))
    ledger.append(d, ON, "note", "g.md")                  # PR-1 behaviour: a plain directory

    def git(cwd, args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    sync.init(d, ON, run=git)
    assert ledger.read_all(d)[0]["goal"] == "g.md"        # the pre-existing entry survived


def test_publish_leaves_no_uncommitted_events_in_the_worktree(tmp_path):
    """The mutation test the done-when demands: if publish() ever stopped staging events, this
    fails — `git status --porcelain` would show `?? events/rae.jsonl`. init()'s scaffold commit
    runs BEFORE the events fixture file is written below, so it starts genuinely untracked."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(bare))

    d = repo / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))

    def git(cwd, args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    sync.init(d, ON, run=git)
    wt = sync.worktree(d)
    (wt / ledger.ENTRIES / f"{ME}.jsonl").write_text(
        '{"id":"rae:1","ts":"2026-07-25T09:00:00Z","actor":"rae","kind":"claimed","goal":"7"}\n')
    (wt / ledger.EVENTS).mkdir(parents=True, exist_ok=True)
    (wt / ledger.EVENTS / f"{ME}.jsonl").write_text(
        '{"id":"rae:1","ts":"2026-07-25T09:00:00Z","actor":"rae","kind":"phase","goal":"7"}\n')

    assert sync.publish(d, ON, run=git) == "published"
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_publish_adds_the_missing_events_union_merge_line_to_an_existing_worktree(tmp_path):
    """Decision 3's own mutation test: a worktree initialized before #137 (only the entries
    union-merge line) gets repaired the first time anyone publishes after upgrading."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    _git(repo, "remote", "add", "origin", str(bare))

    d = repo / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))

    def git(cwd, args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    sync.init(d, ON, run=git)
    wt = sync.worktree(d)
    (wt / ".gitattributes").write_text("entries/*.jsonl merge=union\n")   # simulate a pre-#137 worktree
    git(wt, ["add", "-A"])
    git(wt, ["commit", "-qm", "downgrade simulation"])

    (wt / ledger.ENTRIES / f"{ME}.jsonl").write_text('{}\n')

    assert sync.publish(d, ON, run=git) == "published"
    attrs = (wt / ".gitattributes").read_text()
    assert "entries/*.jsonl merge=union" in attrs and "events/*.jsonl merge=union" in attrs
    status = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""                                # the repair itself is committed


def test_bootstrap_e2e_creates_the_worktree_and_seeds_my_file(tmp_path):
    """End to end against real git: bootstrap makes the ledger worktree and seeds my entries file.
    No remote is configured, so the push half defers (fail-open) — but the local setup completes,
    which is the state a teammate needs before they can publish."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init")
    d = repo / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))

    def git(cwd, args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            capture_output=True, text=True, check=True).stdout.strip()

    sync.bootstrap(d, ON, run=git)
    assert sync.is_worktree(d)                                    # the ops-branch worktree exists
    assert (sync.worktree(d) / "entries" / f"{ME}.jsonl").exists()   # my entries file was seeded


# ------------------------------------------------------------------ watch.sh


def test_watch_sh_ticks_and_honours_the_stop_file(tmp_path):
    d = _sdlc(tmp_path)
    (d / "state" / "watch.stop").write_text("")
    proc = subprocess.run(["bash", str(S / "watch.sh"), str(d)], capture_output=True, text=True,
                          env={**os.environ, "LOOPSMITH_WATCH_SLEEP_SCALE": "0"})
    assert proc.returncode == 0 and "stop-file present" in proc.stdout


def test_watch_sh_stops_after_max_ticks(tmp_path):
    d = _sdlc(tmp_path)
    proc = subprocess.run(["bash", str(S / "watch.sh"), str(d)], capture_output=True, text=True,
                          env={**os.environ, "LOOPSMITH_WATCH_SLEEP_SCALE": "0",
                               "LOOPSMITH_WATCH_MAX_TICKS": "2"})
    assert proc.returncode == 0 and "max ticks (2)" in proc.stdout
    assert (d / "state" / "watch.log").exists()


def test_watch_sh_no_ops_when_a_live_watcher_already_holds_the_pid(tmp_path):
    """Idempotent by design: a loop trigger fires watch.sh on every run, so a second copy over a
    LIVE pid must exit without ticking and without clobbering the first watcher's pid file."""
    d = _sdlc(tmp_path)
    (d / "state" / "watch.pid").write_text(f"{os.getpid()}\n")     # our own pid — guaranteed alive
    proc = subprocess.run(["bash", str(S / "watch.sh"), str(d)], capture_output=True, text=True,
                          env={**os.environ, "LOOPSMITH_WATCH_SLEEP_SCALE": "0",
                               "LOOPSMITH_WATCH_MAX_TICKS": "1"})
    assert proc.returncode == 0 and "already running" in proc.stdout
    assert not (d / "state" / "watch.log").exists()                # never ticked
    assert (d / "state" / "watch.pid").read_text().strip() == str(os.getpid())   # first pid intact


def test_watch_sh_ignores_a_stale_pid_and_runs(tmp_path):
    """A crashed watcher leaves its pid behind. A dead pid must NOT wedge the watcher forever — the
    next trigger takes over the file and ticks normally, cleaning it up on exit."""
    d = _sdlc(tmp_path)
    (d / "state" / "watch.pid").write_text("2147483647\n")         # INT_MAX — no such process
    proc = subprocess.run(["bash", str(S / "watch.sh"), str(d)], capture_output=True, text=True,
                          env={**os.environ, "LOOPSMITH_WATCH_SLEEP_SCALE": "0",
                               "LOOPSMITH_WATCH_MAX_TICKS": "1"})
    assert proc.returncode == 0 and "max ticks (1)" in proc.stdout
    assert (d / "state" / "watch.log").exists()                    # it ticked despite the stale pid
    assert not (d / "state" / "watch.pid").exists()                # cleaned up on exit

def test_worktree_path_is_absolute_even_for_a_relative_sdlc_dir(tmp_path, monkeypatch):
    """Regression: every git call runs with `-C <elsewhere>`, so a relative worktree path made
    `git worktree add` create the worktree under the PROJECT ROOT's own name (a/.sdlc/ledger inside
    a/) and the next write landed nowhere. Caught by a two-clone e2e, not by the unit tests, because
    they all passed tmp_path — which is already absolute."""
    (tmp_path / "proj" / ".sdlc").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert sync.worktree("proj/.sdlc").is_absolute()
    assert sync.worktree("proj/.sdlc") == (tmp_path / "proj" / ".sdlc" / "ledger").resolve()
