---
name: sdlc-security-review
description: Threat-model a change that touches auth, user data, external inputs, billing, or a public endpoint — catch the exposure before the internet does. Triggers on "security", "auth", "permissions", "PII", "external API", "public endpoint", "rate limit", "token". A conditional-risk review orthogonal to sdlc-review's code-quality pass; always LoopSmith's own (no companion equivalent). Use when a diff opens a security surface, or when the user runs /sdlc-security-review.
allowed-tools: Bash, Read, Grep
---

# sdlc-security-review

> Threat-model the change. Catch things before the public internet does.

**You review as an independent skeptic.** Read the change *and its call graph* — a security hole is
usually in what the diff enables two files away, not in the changed line. Ground yourself in the
project first: `.sdlc/context/north-star.md` (non-negotiables / architecture rules) and the repo's
`CLAUDE.md`, plus the auth/authz middleware the change routes through.

## Goal
For a change set or endpoint, produce a threat-modelled review: findings, severities, concrete
remediations.

## Steps
1. List the entry points the change exposes or modifies.
2. For each entry point: who can call it? authenticated? authorised? rate-limited?
3. Trace data flow: inputs → validation → persistence → outputs.
4. Run the eight-point checklist. One bullet per item; "n/a" with a reason if truly not applicable.
5. Assign a severity to each finding: `critical` / `high` / `med` / `low` / `info`.
6. Propose a concrete remediation for each non-info finding.

## Eight-point checklist
1. **authn** — is the caller identified, correctly?
2. **authz** — does the caller have the right to do this?
3. **input validation** — every external input typed and bounded?
4. **injection surfaces** — SQL, command, template, prompt, log?
5. **PII / secrets** — anything sensitive in responses, logs, errors?
6. **rate / cost** — can a single caller exhaust budget?
7. **dependencies** — any new packages? trusted? pinned?
8. **failure modes** — does the change fail open or fail closed under stress?

## Gates
- Every finding has a severity AND a concrete remediation.
- "n/a" lines explain why, not just blank.
- A critical or high finding also appears in the plan's risks section (feed it back to `sdlc-plan`).

## Stop when
- A finding is `critical` → halt the change; in the loop, **park the goal for a human** and record why.
- The area touches an external compliance regime (SOC2, GDPR, PCI) → park and flag a human owner before
  going further.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/security-review-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# security review · <slug or route>

## summary
<X> critical · <Y> high · <Z> med · <N> low
ready to ship: <yes / no / not without remediation>

## entry points
- <method> <route> · auth: <kind> · authz: <rule>

## findings
[S1] severity:high — authz — <where> — <what> — fix: <how>
[S2] severity:med  — input — <where> — <what> — fix: <how>

## checklist trace
1. authn — <one line>
2. authz — <one line>
3. input validation — <one line>
4. injection surfaces — <one line>
5. PII / secrets — <one line>
6. rate / cost — <one line>
7. dependencies — <one line>
8. failure modes — <one line>
```
