---
name: sdlc-radar
description: Proactive research scout (dry-run) — scan the open backlog, research the current SOTA against it, dedup against the radar ledger + the KG gap log, and write a ranked digest. Writes NOTHING external by default. Use when the user runs /sdlc-radar or wants a proactive "what's new / what should we look at" sweep over the backlog.
allowed-tools: Bash(python3 *), Bash(gh issue *), WebSearch, WebFetch
---

# sdlc-radar

A proactive research scout — the *supply* side that complements the gap log's *demand* side: it
surfaces what you didn't know to look for. **Phase A is dry-run: it writes a digest under `.sdlc/` and
records what it surfaced, but never files issues or touches GitHub.**

1. **Agenda (rotate over the backlog).** Get the open backlog — local `.sdlc/goals/*.md`, or
   `gh issue list --state open` in github mode — and count it (N). Pick this run's slice:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/radar.py" agenda <N> <k> <cursor>` (k≈3; `cursor` from the
   last run's `next_cursor`, starting 0). It returns the item indices to research + the next cursor,
   so successive runs cover different items.
2. **Research (fail-soft).** For each agenda item, research the current SOTA against it — your
   `WebSearch`/`WebFetch`, or the `deep-research` skill if available (a soft dep). Return cited
   findings `{topic, summary, url, issue}`. An item that errors is skipped — never abort the run.
3. **Dedup.** Form a short, stable key per finding (e.g. `issue-<n>:<topic-slug>`). Drop any already
   in the ledger (`radar.py seen .sdlc`) or already a known gap (`kg.py gap list .sdlc`) — don't repeat.
4. **Rank** the survivors by novelty × impact-on-the-item × actionability; tag 🚀 / 🔧 / 📌.
5. **Digest (dry-run write).** Write a ranked markdown digest to `.sdlc/knowledge/radar/<UTC-date>.md`
   (findings + a short "suggested actions" list), and record each surfaced finding so it isn't
   repeated: `radar.py record "<key>" .sdlc`. **Stop here — file nothing to GitHub.** (Opt-in,
   guard-railed filing is a later phase.)

Optionally seed the gap log with a finding worth chasing — `kg.py gap log "<...>" .sdlc` — so the
self-improving loop can fill it later: that's where the radar's *supply* meets the loop's *demand*.
Keep `.sdlc/knowledge/radar/` out of git (machine-accumulated, like `research/`).
