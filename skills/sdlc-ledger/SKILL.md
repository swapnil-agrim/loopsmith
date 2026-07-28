---
name: sdlc-ledger
description: The team coordination ledger — set it up, read it, leave a note, or hand work off, all through the plugin instead of raw python paths. Use when the user runs /sdlc-ledger, wants to turn the ledger on or initialise it, check what's addressed to them, see who's done what, hand a blocker to a teammate, answer a hand-off, or regenerate TEAM.md. Do NOT use it to log claimed/done for a normal goal — the loop does that automatically.
allowed-tools: Bash(python3 *), Bash(bash *), Bash(gh *)
---

# sdlc-ledger

The team's shared coordination log — who claimed what, what's done, what's handed off to whom. It is
**not your code history**: it lives on its own git branch (`sdlc-ledger`), checked out as a worktree
under `.sdlc/ledger/`, so pulling it never touches your working tree. Each person writes only their
own `entries/<login>.jsonl`, so concurrent writers can never conflict; the team view is their union.

The scripts live in the loop skill. Resolve their directory once, then call them — the user never
types a python path:

```bash
LS="${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts"
```

## Turn it on + set it up (once per repo, then once per teammate's clone)

1. Ensure `.sdlc/config.json` has `"ledger": { "enabled": true }` (off by default — a team surface is
   opted into explicitly). Add the `lease` block too if you want to tune the claim-lease TTL.
2. **One command creates everything** — the ops branch, your entries file, the `TEAM.md` rollup, and
   pushes it so the team can see it:
   ```bash
   python3 "$LS/sync.py" bootstrap .sdlc
   ```
   Idempotent (safe to re-run). Each teammate runs this once in their own clone to join. The worktree
   must also be git-ignored so it never lands in a code PR — use the safe helper, which never clobbers
   or narrows an ignore rule you already set (and can target `.git/info/exclude` instead of the shared
   `.gitignore` for a local-only adoption): `python3 "${CLAUDE_SKILL_DIR}/../sdlc-setup/scripts/setup.py"
   ignore . --scope tracked` (or `--scope local`). Do NOT blindly `echo … >> .gitignore` — that has
   overwritten a repo's existing broader exclude.

`/sdlc-doctor` flags this automatically: if the ledger is enabled but not set up, it runs the same
bootstrap for you.

## Read it

- **What's addressed to me** (hand-offs waiting on you): `python3 "$LS/ledger.py" mine .sdlc`
- **One-line team summary** (counts + outstanding hand-offs): `python3 "$LS/ledger.py" summary .sdlc`
- **Regenerate the human rollup** `TEAM.md`: `python3 "$LS/ledger.py" render .sdlc --write`

## Keep it fresh

`python3 "$LS/sync.py" pull .sdlc` fetches the latest. In a normal loop you don't need to — the loop
auto-starts the watcher (`watch.sh`), which pulls + publishes on an interval and drops anything
addressed to you into `.sdlc/state/inbox.md`, surfaced between goals.

## Write — only for things OUTSIDE the automatic flow

Claiming a goal and recording `done`/`parked`/`failed` happen **by themselves** inside `/sdlc-loop`
when the ledger is on — never log those by hand. Use the ledger directly only for:

- **Leave a note**: `python3 "$LS/ledger.py" append .sdlc note <goal> --why "spike looks viable"`
- **Hand a blocker to a teammate** (opens an issue in their area, assigned to them, so their own loop
  picks it up): `python3 "$LS/handoff.py" open .sdlc <goal> --area <area> --why "needs your call on X" --priority P1`
- **Answer a hand-off that landed on you**:
  `python3 "$LS/handoff.py" ack .sdlc --issue <n> --state accepted|deferred|declined|resolved`
  (`deferred` does NOT settle it — reply `resolved`/`declined` to close it out.)

## The one safety feature to know: claim leases

A `claimed` entry with no later `done`/`parked`/`failed` is an **open lease** — the loop won't let a
second person start a goal someone else is already holding. It's advisory (it only sees claims already
synced to the ops branch, not a hard lock), and a crashed claim self-expires after
`ledger.lease.ttl_hours` (default 12h) so it can never block the team forever.

---

**How to respond:** map the user's intent to the right command, run it, and report the result in
plain language. Prefer this skill's commands over ad-hoc `python3 <path>` so what you show is
copy-pasteable and consistent for the whole team. If the ledger isn't set up yet (no `.sdlc/ledger/`
worktree) and they ask to read or write it, run `bootstrap` first.
