# sdlc-kit

**Portable Goal-Based SDLC for any repo.** Install once, and every prompt is held to a 7-phase
development spine — Goal → Research → Plan → Plan-Review → Implement → Review → Retrospective — so the
agent stops jumping straight to code. Run it two ways: **goal-mode** (interactive, intervention-driven
— "day") or **loop-mode** (autonomous, park-and-continue — "night").

The discipline borrows from [loop-maker](https://github.com/EricTechPro/loop-maker)'s loop
engineering — a checkable goal, durable-vs-changing state, a separate verifier, a mandatory budget,
and a non-skippable human gate — wrapped around the SDLC as the per-item engine.

> **Status (v0.4):** all commands shipped — the always-on **SDLC hook**, install paths,
> **`/sdlc-init`** (scaffold), **`/sdlc-goal`** (interactive day mode), **`/sdlc-loop` +
> `/sdlc-status`** (autonomous loop driver), and a generic **`sdlc-plan-review`** (the Phase-4 gate
> `superpowers` doesn't provide).

## Install (plugin — recommended)

```
/plugin marketplace add <git-url-or-local-path>
/plugin install sdlc-kit
```

Installs the durable spine globally — the SDLC hook then fires in **every** project. Then run
`/sdlc-init` in each repo to scaffold its per-project `.sdlc/` layer (project stub, `goals/`,
`config.json`, loop `state/`). Re-running `/sdlc-init` is safe — it never clobbers existing state.

## Install (fallback — no plugin system)

```
git clone <git-url> && cd sdlc-kit && ./install.sh
```

`install.sh` copies the spine into `~/.claude/skills/sdlc-kit/` and **prints** the `settings.json`
hook snippet for you to paste (it never edits your settings — malformed JSON silently disables
hooks). Parse-check `settings.json` after pasting.

## The two modes

- **`/sdlc-goal <goal>`** — one goal through the engine, interactive, pausing for your approval at
  each checkpoint, then recorded to `.sdlc/` so it shows in `/sdlc-status`. **Shipped.** *(Day.)*
- **`/sdlc-loop`** — pulls the `.sdlc/goals/` backlog and runs each goal autonomously; anything
  needing you is **parked** to `.sdlc/state/review-queue.md` while the loop continues; halts on a
  per-run iteration budget. **Shipped.** Run **`/sdlc-status`** any time for backlog counts + whether
  the review queue needs attention. *(Night.)*

The always-on 7-phase hook underpins both. **See the [worked walkthrough](examples/hello-sdlc/)** for
a runnable end-to-end example. Publishing the kit as its own repo? See [EXTRACT.md](EXTRACT.md).

## Status (honest)

v0.4 is **Claude Code only.** The core is plain markdown + shell, structured to be host-portable, but
a second-host (Codex/etc.) adapter is not yet shipped.

## Recommended companion (soft dependency)

The SDLC phases name [`superpowers`](https://github.com/obra/superpowers) skills (brainstorming,
writing-plans, TDD, …) as the execution engine. Install the **superpowers** plugin to get them.
Without it, the kit still injects the 7-phase policy and the phase *names* guide the work — it
degrades gracefully, it does not break.

## Requirements

- **Runtime:** bash + python3 (stdlib) — zero dependencies.
- **Dev/test:** `pip install pytest`, then `pytest tests/ -v`.

## License

MIT.
