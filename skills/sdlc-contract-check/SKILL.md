---
name: sdlc-contract-check
description: Detect breaking changes to public APIs, event shapes, exported types, CLI flags, or env vars before consumers hit them. Triggers on "breaking change", "API change", "contract", "consumers", "exported type", "event shape", "route change". A conditional-risk review orthogonal to sdlc-review; always LoopSmith's own (no companion equivalent). Use when a diff changes a public surface, or when the user runs /sdlc-contract-check.
allowed-tools: Bash, Read, Grep
---

# sdlc-contract-check

> Detect breaking changes to public APIs, event shapes, and exported types before the consumers do.

**Judge blast radius from the code, not the diff.** A "safe-looking" signature change is breaking if a
caller you never read depends on the old shape. Ground yourself in the project's declared contracts:
`.sdlc/context/north-star.md`, the repo's `CLAUDE.md` (any FROZEN-contract rules), and the actual
schema/exported-type surfaces (OpenAPI / GraphQL / protobuf / package boundaries). Then `grep` every
consumer.

## Goal
Given a diff, produce a list of every public contract changed, every consumer affected, and whether each
change is safe or breaking.

## Steps
1. Identify contracts in the diff: HTTP routes, event shapes, exported types, CLI flags, env vars.
2. For each: classify as `added` / `removed` / `changed-safe` / `changed-breaking`.
3. For each `removed` or `changed-breaking`: list the consumers (repos, services, teams) — by grep, not memory.
4. For each consumer: state the action required (notify, version bump, migrate-then-remove).
5. Propose a versioning / deprecation strategy if any breaking change remains unresolved.

## Gates
- Every contract change is classified.
- Every breaking change has a named consumer list (or "no known consumers — confirmed by grep").
- Every breaking change has a rollout plan (versioned, notified, or migrate-first).

## Stop when
- Consumer ownership is unclear → **park the goal for a human** rather than guess.
- The contract is cross-org / a FROZEN contract → park and flag a human owner before any further work.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/contract-check-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# contract diff · <slug>

## summary
<N> changes · <M> breaking · ready: <yes / no>

## changes
- <kind>: <name>
  classification: <added / removed / changed-safe / changed-breaking>
  consumers: <list or "none — confirmed by grep">
  action: <notify / version-bump / migrate-then-remove>

## rollout
<paragraph: order, timing, deprecation window>
```
