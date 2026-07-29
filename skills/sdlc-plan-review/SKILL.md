---
name: sdlc-plan-review
description: Adversarially review an implementation plan BEFORE coding — verify its claims against the actual code and stress-test what could go wrong after it ships. Use at SDLC Phase 4, when the user says "plan review" / "review the plan", or before implementing any non-trivial plan.
---

# sdlc-plan-review

The last gate before code. Review the active plan with two lenses; finish with one verdict.

**You are an independent reviewer — you did not write this plan.** In the loop you run as a fresh
subagent grounded in the *project* (the north-star, conventions, and the whole codebase, via the
`review_context.py` brief), never the plan author's reasoning — so you can find the blast radius the
author didn't and disagree where they were wrong. A maker reviewing its own plan just confirms it. You
have full read access to the repo: use it. The diff-to-come is the change; the codebase is the impact
surface.

## 1. Forensic verification
Every claim in the plan is a hypothesis. For each file path, function, line, or behavior the plan
asserts — open the real code and confirm it. Classify each: Correct / Partially correct / Incorrect,
each backed by a `file:line`. "The plan says X" is not evidence; the file showing X is.

## 2. Adversarial robustness (assume it ships and a bug surfaces in two weeks)
- **Caller sites (trace the blast radius across the WHOLE repo):** for every function/contract the plan changes, grep *all* callers — not just the files the plan names. You have full repo access; a plan that lists three files it touches has a blast radius of every site that calls them. Are they all handled, or is this a one-site patch with broken siblings?
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

## 5. Disposition — closing the loop on a FIX-FIRST
A verdict that sends the plan back is only half the gate. When the revised plan returns, **give every
finding an explicit disposition** — otherwise the loop either swallows findings silently or obeys a
wrong one. Each disposition needs its own `file:line`; "the review seems right" is not evidence.

- **Accept** — the finding is real. Confirm it in the code first, then change the plan and cite what
  confirmed it.
- **Reject** — the finding is wrong or reads stale code. Confirm the *plan* was right, keep it
  unchanged, and record why with evidence.
- **Partially accept** — the concern is real but the suggested fix isn't. Adapt it; cite both the
  concern and why the adjusted approach is better.

**The review can also be wrong.** It is a hypothesis exactly like the plan was in §1. A finding that
claims a file or function doesn't exist gets checked against the filesystem before it is accepted —
the reviewer misreads code too, and a plan patched to satisfy a false finding is worse than the
original.

**Structural over patchwork.** If a finding proposes a one-site patch where a shared fix is feasible,
reject the approach and propose the structural one — the same root-cause discipline §2's caller-site
sweep applies. If patchwork is genuinely the right scope, say so explicitly and leave a tracked item
for the structural fix; never let it pass unnamed.

**Regen threshold.** If more than half the findings are substantive (not surface corrections like a
stale line number or a renamed symbol), the plan has structural problems — say so and recommend
regenerating it from the goal rather than patching. Counting matters here: three real defects in five
findings is a different situation from three in twenty.

Carry the dispositions with the revised plan so the next review round starts from what was already
settled. When a later round contradicts an earlier decision, re-read the code, pick one direction on
the evidence, and record the reversal — don't flip-flop.

Record the rejections in the goal's audit trail —
`python3 "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/loop.py" note .sdlc "<goal>" "plan-review: <what was rejected and why>"`
— which comments the issue in github mode and appends to `.sdlc/journey/` locally. A *rejected*
finding is the one worth writing down: the accepted ones are visible in the revised plan, while the
reasoning for overruling a reviewer exists nowhere else, and it is the first thing anyone asks when
the same objection comes back a month later.

> On Claude with the companion installed, `superpowers:receiving-code-review` applies this same
> "verify before you agree" discipline to code review; this is its plan-stage equivalent.
