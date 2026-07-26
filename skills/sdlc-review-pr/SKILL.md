---
name: sdlc-review-pr
description: The PR review pipeline — drain the open pull requests on the review base by rebasing, running a real code-review pass, checking CI, then merging the clean ones (only when policy allows) or requesting changes and holding the rest. Every step is recorded to the team ledger so the whole team can see who is reviewing and merging what. Use when the user runs /sdlc-review-pr, or when a routine is set to keep the review queue moving. Needs the `review` block enabled in config.json and the `gh` CLI. The script (pr.py) is the deterministic scaffold; the judgement is yours.
allowed-tools: Bash
---

# sdlc-review-pr

Review bandwidth is the bottleneck once several people run the loop on one repo. This keeps the PR
queue moving without a human babysitting it — and records the cycle to the ledger so it is as visible
as the goal cycle.

**One rule above all: the script never judges code.** `pr.py` picks, rebases, reads CI, records, and
(only when allowed) merges. The actual review — is this diff correct, does it fight the vision, is a
doc contradicted — is *your* `/code-review` pass. A script that decides "this diff is fine" is the
two-engine trap the kit exists to avoid.

Scripts live next to the loop:

```bash
LP="${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts"     # pr.py, ledger.py, sync.py …
```

## The cycle (one PR at a time)

1. **Pick.** `python3 "$LP/pr.py" next .sdlc` → the oldest open, non-draft PR you did not author and
   that is not already approved. Nothing printed = queue drained; stop.
2. **Claim.** `python3 "$LP/pr.py" claim .sdlc <pr>` — records a `review` line to the ledger so the
   team sees you have it (nobody double-reviews).
3. **Rebase.** `python3 "$LP/pr.py" rebase .sdlc <pr>` — brings it current with the base (server-side;
   no local push) and records `rebased`. Skip if already current.
4. **Review — your judgement.** Run **`/code-review`** on the PR diff (or `sdlc-review` in diff-review
   mode on any host). Read it against the north-star: correctness, a FROZEN-contract or ADR violation,
   a doc it contradicts, scope creep.
5. **CI.** `python3 "$LP/pr.py" ci .sdlc <pr>` → `passing | pending | failing`. `pending` is not a pass —
   wait or come back.
6. **Decide + record:**
   - **Clean** (review passed **and** CI green): `python3 "$LP/pr.py" approve .sdlc <pr> --why "…"`, then
     `python3 "$LP/pr.py" merge .sdlc <pr>`. Merge is **gated** — it only lands when
     `review.auto_merge` is on (and the base is safe to land on directly); otherwise it records the
     approval and returns a parked decision for a human. **Never force it.**
   - **Needs a fix** (an ordinary finding the author can address): post your review, then
     `python3 "$LP/pr.py" request-changes .sdlc <pr> --why "…"`. It re-enters the queue when they push.
   - **Serious — a design conflict or a contradicted doc:** `python3 "$LP/pr.py" hold .sdlc <pr>
     --why "…"`. This converts the PR back to **draft** so it cannot be merged, records it, and escalates
     to a human. Post your reasoning as the review body.
7. **Publish the ledger** so the team sees it: `python3 "$LP/sync.py" publish .sdlc` (needs the ledger
   set up — `sync.py init .sdlc` once).

Then go back to step 1 until `next` is empty.

## What lands on the ledger

`review` (picked it up) · `rebased` · `changes-requested` · `approved` · `merged` — each tagged with the
PR number and your handle, and all team-visible in `TEAM.md`. So a lead reading the ledger sees who is
reviewing what, what got sent back, and what merged — the review cycle, not just the goal cycle.

## Never

- Merge on your own judgement when `auto_merge` is off — that is a human's call (and the base may be
  protected). The pipeline parks it for a reason.
- Approve without a real `/code-review` pass. The ledger says you reviewed it; mean it.
- Review your own PR. `next` skips it; do not override.
