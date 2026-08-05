# insight/contract/ — the engine↔product data contract (issue #298, [E15.S4])

Golden fixtures for the on-disk formats the LoopSmith engine (`skills/sdlc-loop/scripts/`)
writes and `insight/ingest` reads, plus the closed vocabularies both sides must agree on. This
directory is `insight`'s own copy — see "Kept in sync by hand" below for what that means in
practice.

## What's fixtured, and what's deliberately not

Four formats are fixtured as real data files:

| Fixture | Format it represents |
|---|---|
| `ledger_entries.jsonl` | one line per `ledger.py` entries-stream `KINDS` member (9), plus a 10th line carrying an unknown field |
| `telemetry_events.jsonl` | one line per `ledger.py` events-stream `EVENT_KINDS` member (8), each populated from its own `EVENT_FIELDS` whitelist |
| `goal_frontmatter.md` | one real `---`-fenced goal frontmatter block, the shape `frontmatter.py`/`artifact_reader.py` actually parse |
| `config.json` | one real `.sdlc/config.json`, touching every key the four independent config readers below touch |

`vocabulary.json` is a fifth file: not a format fixture, but the closed vocabularies
(`entries_kinds`, `event_kinds`, `phase_kinds`, `gate_kinds`, `verdicts`, `reason_classes`,
`retro_grades`, `severity_order`) as literal JSON data, for tests that need the vocabulary
itself rather than an example record.

**`state/*` (`.sdlc/state/`, e.g. `STATE.md`, `review-queue.md`) is deliberately NOT fixtured
here**, even though the issue's clause 1 names five formats. `insight/dash/manager.py`'s own
module docstring (lines 181-192, re-read for this goal) states outright that there is **zero
ingest path** for `state/*` anywhere under `insight/` — no reader, no dark column, no empty
view, "a complete absence of any route from this data to the store." Fixturing a format nothing
reads would be exactly the decorative-proof failure issue #297 exists to avoid. This is a
recorded decision, not an oversight: if a future goal adds a `state/*` reader, that goal adds the
fifth fixture then, backed by a real consumer.

## The version rule

`insight.contract.CONTRACT_VERSION` is one plain `int`, currently `1`. No directory-per-version,
no filename version suffixes, no resolver — nothing in this codebase reads two contract versions
at once, so a multi-version matrix would be unused machinery from day one.

`CONTRACT_VERSION` bumps **only** when one of the four fixtured formats changes in a way an
*older* reader could not tolerate — a field renamed or removed, a vocabulary member removed or
repurposed, a stream's required-field set shrinking. Adding an optional field, adding a new
vocabulary member, or adding a new example line to a fixture is **not** a breaking change (it is
exactly clause 4's own "additive, ignorable" contract) and does not bump the version.

The only enforced check: every contract test that reads `vocabulary.json` also asserts
`json.loads(vocabulary.json)["contract_version"] == insight.contract.CONTRACT_VERSION` — a cheap
consistency check that the fixture's self-reported version and the package constant cannot
quietly diverge from each other. **Nothing enforces that a human actually bumps the version when
a breaking change lands** — that is discipline, not a mechanism, same as the sync rule below.

## Kept in sync by hand, not enforced

The engine's own vocabulary pins (`tests/test_ledger.py`'s literal tuples,
`tests/test_pipeline.py`'s severity-order literal) live in `tests/` and must not reference
`insight/` at all (clause 3's own requirement — the failure must be provable with zero mention of
insight anywhere in the failing test). This directory's fixtures cannot be read from `tests/`
without recreating exactly the coupling this goal removes. **The two sides are therefore
hand-typed literals, independently, on both sides — real, admitted duplication, not hidden
behind a helper.** This mirrors a pattern this codebase already uses everywhere the
plugin/product boundary bites: `gh_reader._repo_from_config`, `goal_lifecycle._discovery_source`,
`ledger_reader._telemetry_share_is_off`, and `artifact_reader._discovery_source` are four
independent reimplementations of the same seven-line config read, each stating "a DELIBERATE
second copy" as the chosen posture.

Two guardrails, both cheap, neither a real mechanism:
* A one-line breadcrumb comment at each vocabulary constant in `ledger.py` and at `pipeline.py`'s
  `_ORDER`, pointing at both halves by file path.
* This table, so "did I update both" is a one-glance lookup instead of a search:

| Fixture | Engine source of truth | Engine-side pin test | Insight-side reader test |
|---|---|---|---|
| `ledger_entries.jsonl` | `ledger.py`'s `KINDS` (skills/sdlc-loop/scripts/ledger.py:35) | `tests/test_ledger.py::test_vocabulary_constants_match_spec_table`, `tests/test_ledger_contract.py::test_every_entries_kind_round_trips` | `insight/tests/test_ledger_reader_contract.py` |
| `telemetry_events.jsonl` | `ledger.py`'s `EVENT_KINDS`/`EVENT_FIELDS` (skills/sdlc-loop/scripts/ledger.py:55,60) | `tests/test_ledger.py::test_vocabulary_constants_match_spec_table`, `tests/test_ledger_contract.py::test_every_event_kind_round_trips` | `insight/tests/test_ledger_reader_contract.py` |
| `goal_frontmatter.md` | `frontmatter.py`'s fence regex (skills/sdlc-loop/scripts/frontmatter.py:4) | (no dedicated engine-side pin — the format is exercised throughout `tests/test_frontmatter.py`) | `insight/tests/test_artifact_config_contract.py` |
| `config.json` | `sources.py`/`loop.py`'s config reads | (no single engine-side pin — config keys are read ad hoc across the plugin) | `insight/tests/test_artifact_config_contract.py` (all four independent insight-side readers) |
| `vocabulary.json` | `ledger.py`'s `KINDS`/`EVENT_KINDS`/`PHASE_KINDS`/`GATE_KINDS`/`VERDICTS`/`REASON_CLASSES`/`RETRO_GRADES`, `pipeline.py`'s `_ORDER` | `tests/test_ledger.py::test_vocabulary_constants_match_spec_table`, `tests/test_pipeline.py::test_severity_order_matches_the_contract` | `insight/tests/test_metrics_sql_kinds_match_contract_vocabulary.py`, `insight/tests/test_metric_severity_rank.py`, `insight/tests/test_gaps_severity_vocabulary.py`, `insight/tests/test_metric_23_gate_catch_rate.py` |

Nothing fails the build if only one side is edited. This is a real, accepted residue — see the
implement-phase report for issue #298 for the full risk list — not a hidden gap.

## Known, out-of-scope forward reference

`insight/metrics/19.sql` selects `kind='run_stop'`, an event kind that does not (yet) exist in
`event_kinds` above. This is a deliberate, tracked forward reference (see that file's own
guardrail comment) — a writer for it does not exist yet, and adding one is real engine work, out
of this goal's scope per the issue's own "do not change what the engine writes." It is the one
named, allowlisted exception `insight/tests/test_metrics_sql_kinds_match_contract_vocabulary.py`
carries.

## Algorithmic agreements — NOT fixtured here

The git-log/velocity counting rule (`velocity.py`'s `measure()` vs. `git_reader.py`'s
`measure_window()`) is an **algorithm**, not a data format, so it is not represented in this
directory. See `tests/test_velocity_contract.py` (engine) and
`insight/tests/test_git_reader.py` (insight) — each pins literal expected counts against a real,
disposable git repository it builds itself, independently of the other.

One naming divergence between those two implementations, found while building this contract and
left as a named follow-up (not fixed here — the issue's own "do not change what the engine
writes" applies): `velocity.py` returns the merges-per-day figure under the key `prs_per_day`;
`git_reader.py`'s `measure_window` returns the same computation under `merges_per_day`.
