---
name: sdlc-setup
description: One-command adoption of LoopSmith into an EXISTING repo — detect the repo + board, scaffold .sdlc/, and write a safe config with the right defaults (github discovery scoped to @me, ledger on, PRs on), then bootstrap the ledger and verify. Use when the user runs /sdlc-setup, says "set LoopSmith up here", or is adopting the plugin into a project that already has code and history. For a brand-new empty project, /sdlc-init alone is enough.
allowed-tools: Bash(python3 *), Bash(bash *), Bash(git *), Bash(gh *), Read, Edit
---

# sdlc-setup

Adopt LoopSmith into a repo that already has code and history, in one pass, with defaults that don't
surprise you. This is the "master prompt": you run it, it inspects the repo, and it configures the
plugin the way a real team wants it — instead of ten manual edits to `config.json`.

```bash
SETUP="${CLAUDE_SKILL_DIR}/scripts/setup.py"
LOOP="${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts"
```

Work through these in order. Report what you find and what you set at each step; only ask the user
when a choice genuinely can't be inferred.

## 1. Inspect the repo

- **Repo:** `python3 "$SETUP" detect .` → `owner/name` from the git remote. If empty, ask (or the repo
  has no remote → use `--source local-goals` below).
- **Board:** `gh project list --owner <owner> --format json` — if exactly one plausible board exists,
  use it; if several, ask which; if none or `gh project` isn't scoped, leave the board off (the loop
  runs on issues + labels regardless). Use what you know from context/memory about which repo and board
  this project uses before asking.
- **Already adopted?** If `.sdlc/config.json` exists, you're re-running — that's fine, everything below
  is idempotent and preserves existing settings.

## 2. Scaffold `.sdlc/` if it isn't there

If there's no `.sdlc/`, run **`/sdlc-init`** first (it only adds the `.sdlc/` folder, never touches
code). Then continue.

## 3. Pick the verify command (do NOT skip — this is a known trap)

`verify.enforce: true` with an **empty** `verify.command` refuses *every* `done` forever. So find a
real command: detect the repo's test runner (`pytest`, `npm test`, `go test ./...`, a `Makefile`
target, an existing CI step) — read `pyproject.toml` / `package.json` / the CI workflow — or ask the
user for the exact command. If you genuinely can't get one yet, leave verify off and say so; never
enable enforce without a command. `setup.py configure` guarantees this, but choose the command here.

## 4. Choose the ignore scope (respect an existing choice)

The runtime dirs (`.sdlc/state/`, `.sdlc/ledger/`, `.sdlc/work/`) must be git-ignored. Check what's
already there: `python3 "$SETUP" ignore-status .`.
- **Default `tracked`** — add them to the shared `.gitignore`. Right for a repo adopting LoopSmith as
  its real workflow.
- **`local`** — add them to `.git/info/exclude` instead, touching nothing the team sees. Use this when
  the adopter's intent is "local experiment, don't modify tracked files," or when `.git/info/exclude`
  already carries a blanket `.sdlc/` line (never narrow it). If you see a blanket exclude, prefer
  `local` and leave the existing line alone.

## 5. Write the config + ignores (one call each)

```bash
python3 "$SETUP" configure .sdlc --repo <owner/name> --verify "<the command, or omit>" [--source local-goals]
python3 "$SETUP" ignore . --scope <tracked|local>
```

Defaults `configure` sets: **github discovery scoped to `assignee: @me`**, **ledger on**, **work
(a PR per goal) on** with `auto_merge: off` (a clean PR is left for a human — change to `protected` or
`always` only on an explicit per-repo authorization). It preserves anything already set and never
turns on the verify trap.

## 6. Bootstrap the ledger + verify

```bash
python3 "$LOOP/sync.py" bootstrap .sdlc     # create the ops branch + seed your file + TEAM.md + push
```
Then run **`/sdlc-doctor`** and show the result — it confirms the board scope, ledger, verify, and
`work.enabled` state at a glance.

## 7. Hand back a two-line summary

State plainly: the repo + board it's wired to, that discovery is scoped to `@me`, that the ledger is up
and pushed, that PRs are on (and the `auto_merge` value), and the verify command (or that verify is off
until one is set). Then: "`/sdlc-loop` runs a goal; `/sdlc-ledger` reads/hands-off the ledger."

**One caveat to mention if the host repo has its own edit-gating hooks:** LoopSmith's Implement phase
edits go through the same tool calls a human would, so a host `PreToolUse` hook that gates source edits
(e.g. on a plan-freshness check) applies to them too. If the repo has one, make sure whatever it
expects (a plan doc, a sentinel) is satisfied, or a source-code goal can be denied mid-Implement.
