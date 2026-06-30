---
name: sdlc-plan-review
description: Adversarially review an implementation plan BEFORE coding — verify its claims against the actual code and stress-test what could go wrong after it ships. Use at SDLC Phase 4, when the user says "plan review" / "review the plan", or before implementing any non-trivial plan.
---

# sdlc-plan-review

The last gate before code. Review the active plan with two lenses; finish with one verdict.

## 1. Forensic verification
Every claim in the plan is a hypothesis. For each file path, function, line, or behavior the plan
asserts — open the real code and confirm it. Classify each: Correct / Partially correct / Incorrect,
each backed by a `file:line`. "The plan says X" is not evidence; the file showing X is.

## 2. Adversarial robustness (assume it ships and a bug surfaces in two weeks)
- **Caller sites:** for every function/contract the plan changes, grep all callers — are they all handled, or is this a one-site patch with broken siblings?
- **Regression risk:** what working behavior could break? Name the test that would catch it, or flag the gap.
- **Negative scenarios:** empty/null input, stale/partial state, concurrent/out-of-order, boundary sizes. Which defeat the plan?
- **Loopholes:** where can invalid state enter without hitting the new guard (defaults, alternate code paths, deserialization, trust boundaries)?

## 3. Scope & fit
Does each step serve the goal? What's over-built (YAGNI)? Does it contradict the project's own
rules, conventions, or stated direction? Quote the specific rule if so.

## 4. Strategic & architectural alignment (vision-first projects)
If `.sdlc/context/north-star.md` exists, hold the plan to it on two axes:
- **Strategy / Non-goals** — does it serve a stated priority? Does any step **advance a declared
  non-goal** or **contradict the strategy**?
- **Architecture rules** — does any step **violate a numbered architecture rule** (layering,
  dependency direction, module boundaries, "where new code goes")?

A plan that fights the north-star — its strategy, a non-goal, or an architecture rule — is **FIX-FIRST**;
quote the line it violates. (No north-star = drop-in project: skip this check; it's a no-op.)

## Verdict
One of: **SOUND** (implement as-is) / **SOUND-WITH-REFINEMENTS** (list them) / **FIX-FIRST**
(blocking issues). Be specific and opinionated; don't pad with praise. If you didn't try to break
it, you didn't review it.
