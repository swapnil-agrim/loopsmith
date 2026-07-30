---
name: sdlc-debug
description: Diagnose a bug hypothesis-first, reproduce-as-a-test-first — before writing any fix. Triggers on "bug", "broken", "not working", "error", "exception", "investigate", "why is X". A conditional-risk skill orthogonal to sdlc-review; always LoopSmith's own (no companion equivalent). Use to diagnose before sdlc-implement, or when the user runs /sdlc-debug.
allowed-tools: Bash, Read, Grep
---

# sdlc-debug

> Reproduce as a test before fixing. Hypothesis-first.

Ground yourself first: `.sdlc/context/north-star.md`, the repo's `CLAUDE.md`, the files the bug appears
to live in, and the existing tests near them.

## Goal
Take a reported bug and produce: a reproducing test (RED), a fix (GREEN), a one-paragraph explanation, and
a prevention note.

## Steps
1. Restate the bug. Confirm steps and expected vs actual.
2. Form a *single* hypothesis. Write it down.
3. Write a test that fails *for the hypothesised reason*. Run it. Confirm RED.
4. If red for the wrong reason → revise the hypothesis, go to step 2.
5. Apply the minimum fix. Run the test. Confirm GREEN.
6. Run the full suite. Confirm nothing else broke.
7. Write up the report.

**The Iron Law for bugs:** no fix without a reproducing test first. If the test is hard to write, the bug
is harder to fix — that is the signal, not the problem.

## Gates
- There is a named reproducing test in the repo, currently passing.
- The fix is smaller than the test.
- A one-sentence prevention note exists.

## Stop when
- The bug is in a no-go / out-of-scope area → **park for a human**.
- The fix would require a schema or contract change → exit to `sdlc-plan` (and run `sdlc-migration-check`
  / `sdlc-contract-check`); do not expand the fix.
- You cannot reproduce after ~30 minutes → write up what you tried and park for the user.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/debug-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# debug · <slug>

## report
<verbatim ticket / quote>

## repro
<exact steps the test takes>

## hypothesis
<one sentence>

## root cause
<one paragraph, plain English>

## fix
<file:line summary>

## test
<test name in repo>

## prevention
<one sentence: lint rule? skill update? doc? type? property test?>
```
