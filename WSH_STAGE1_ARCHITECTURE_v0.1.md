# WSH Stage 1 — Unified Institutional Data Automation Architecture
**Document status:** Architecture baseline v0.1 — initial design, pending source-owner review  
**Prepared:** 23 November 2026  
**Target:** Internal integration target 27 December 2026; hard deadline before 31 December 2026

## 1. Mission and non-negotiable controls

Build one orchestrated daily data system while retaining independent source adapters, dataset identities, evidence, validation contracts, publication states, and historical records.

The architecture must:
- Preserve Sensibull V3.11.1 as a frozen, independently released component. New OI-vs-Strike integration is a separate adapter/integration task; do not modify the frozen release.
- Preserve the FII/DII and CCIL R&D work as source-specific implementations. Promote only after regression and evidence review.
- Keep StockEdge EOD Market Participants separate from Sensibull and CCIL.
- Never infer publication from weekday alone. Use source calendars, explicit source evidence, and dataset-specific rules.
- Never equate successful page navigation, a non-empty table, or a successful script exit with valid data.
- Preserve raw source evidence and its hash before normalization.
- Never convert missing, stale, ambiguous, or contaminated data into a PASS.
- Keep publication, extraction, validation, freshness, and reconciliation as separate dimensions.

## 2. Repository and change-control boundary

Observed repository baseline:
- `main` is at `0661085877513912a2ef21d306d0073c8c3a207a`.
- `release/wsh-sensibull-v3.11.1` and `archive/wsh-sensibull-v3.11.1` are at `8ad0c921c1caf47a7d8e6bb9e260fa04cc635055`.
- `wsh-collector-rnd` is at `a95748ee6a518e2935b133bb5480a4d662408990`.
- The repository already contains a main-branch collector and a separate FII/CCIL R&D implementation.
- The GitHub integration denied branch creation with HTTP 403. No remote repository changes were made in this architecture-initiation step.

Required development boundary:
- Create a new integration branch from `main` using GitHub Desktop or a user-authorized Git operation.
- Do not edit release/archive branches.
- Do not merge R&D into main wholesale.
- Bring source-specific modules across only after inventory, tests, dependency review, and explicit promotion approval.
- Keep historical reports and ZIP archives read-only.

## 3. Target architecture

```text
                 WSH Stage 1 Orchestrator
                           |
        +------------------+-------------------+
        |                  |                   |
   Run Scheduler      Run Context         Source Calendar
        |                  |                   |
        +------------------+-------------------+
                           |
                  Source Adapter Registry
                           |
      +--------------------+------------------------------+
      |           |          |          |                 |
   Sensibull   NSE/FII    CCIL       TradingEconomics   TradingView
   adapters    adapters   adapters   adapters           adapters
      |           |          |          |                 |
      +-----------+----------+----------+-----------------+
                           |
                  Immutable Raw Evidence
                           |
                 Source-Specific Parsers
                           |
                 Canonical Dataset Records
                           |
       +-------------------+--------------------+
       |                   |                    |
  Schema/semantic      Freshness and       Provenance /
  validation           calendar checks     contamination checks
       |                   |                    |
       +-------------------+--------------------+
                           |
                 Reconciliation / QA gates
                           |
               Per-dataset quality manifest
                           |
           +---------------+----------------+
           |               |                |
      Source packages  Unified daily    Failure / exception
                       data package     report
                           |
                 Historical data store
                           |
              WSH research engines consume
             approved, traceable data packages
```

### Architectural layers

1. **Control plane:** schedules, run IDs, source registry, calendar policy, concurrency, retries, timeouts, configuration.
2. **Adapter plane:** one adapter per source (and, where necessary, per dataset or page/tab). No cross-source parsing assumptions.
3. **Evidence plane:** immutable raw HTML, JSON/API responses, downloaded files, browser/network logs, screenshots where required, and capture metadata.
4. **Normalization plane:** source-preserving raw fields mapped to canonical fields without losing native names, units, labels, or source identifiers.
5. **Quality plane:** source-specific schema, semantic, freshness, calendar, cross-tab contamination, identity, and reconciliation tests.
6. **Publication plane:** per-source packages, unified package, manifest, exception report, and explicit release decision.
7. **Storage plane:** append-only raw evidence and versioned normalized records with stable dataset keys and provenance.

## 4. Canonical run and dataset identity

Every execution receives:
- `run_id` (UUID)
- `run_started_at_utc`, `run_completed_at_utc`
- `pipeline_version`, `git_commit`, `environment`
- `scheduled_window`, `market_date`, `timezone`
- `source_registry_version`, `schema_version`
- `operator_triggered` / `scheduled_triggered`

Every dataset key is stable and source-qualified, for example:
- `sensibull.option_chain.nifty`
- `sensibull.oi_vs_strike.nifty`
- `nse.fii_dii.cash_market`
- `stockedge.market_participants.eod`
- `ccil.gsec`
- `ccil.forex`
- `ccil.derivatives`
- `tradingeconomics.economic_calendar`
- `tradingview.heatmap.us_equities`

Do not use a display title as the identity key. Dataset identity must remain stable when a URL, page title, selector, or display label changes.

## 5. Dataset status model

Keep these dimensions independent.

### Publication status
`PUBLISHED | MARKET_CLOSED | NOT_DUE | DELAYED | WITHDRAWN | UNKNOWN`

This describes whether the provider has published the dataset for the expected observation window. It is not an extraction result.

### Extraction status
`SUCCESS | PARTIAL | FAILED | NOT_ATTEMPTED`

This describes whether the adapter obtained the expected source evidence.

### Validation status
`VALID | PARTIAL | QUARANTINED | INVALID | NOT_RUN`

This describes whether the captured evidence satisfies the dataset contract.

### Freshness status
`FRESH | STALE | OUT_OF_WINDOW | UNKNOWN`

Freshness is measured against source-specific expected publication/observation timing, not simply the local capture timestamp.

### Reconciliation status
`MATCHED | MISMATCH | NOT_AVAILABLE | NOT_RUN`

Only use when an independent comparison source or prior accepted baseline exists.

### Dataset release decision
`ACCEPT | ACCEPT_WITH_LIMITATIONS | HOLD | REJECT`

The run-level decision must not erase per-dataset states. One failed dataset should not cause unrelated datasets to be relabelled as failed; it may, however, hold the consolidated package if the dataset is mandatory.

## 6. Dataset contract (minimum fields)

Each source dataset contract must define:
- stable `dataset_key`, source name, source URL, source owner/domain
- purpose and WSH consumer(s)
- native schema / expected headers / fields
- canonical schema mapping
- field types, units, currency, timezone, date semantics
- expected publication cadence and market calendar
- source timestamp field and capture timestamp
- required/optional fields and cardinality rules
- selectors/API endpoint and extraction method
- raw evidence requirements and retention
- freshness threshold and late-publication grace period
- semantic validation and cross-field constraints
- contamination/fingerprint controls
- independent reconciliation rule (if available)
- known limitations, login/session requirements, rate limits
- owner, adapter version, contract version, last verified date

No adapter receives `VALID` until its contract and acceptance tests are implemented.

## 7. Evidence and data lifecycle

For each dataset:
1. Resolve run context and publication expectation.
2. Capture raw evidence before parsing; preserve original bytes/text where available.
3. Record URL, redirects, response metadata, browser/session context, local UTC capture time, source timestamp (if provided), and SHA-256.
4. Parse into a source-native intermediate representation.
5. Validate native structure and expected identity.
6. Normalize into canonical records while retaining native fields and units.
7. Run semantic, freshness, calendar, contamination, and reconciliation gates.
8. Write dataset result and evidence references to the manifest.
9. Publish source package only under its dataset decision.
10. Include accepted/limited/held dataset states in the unified package without hiding exceptions.

Suggested storage layout:
```text
WSH_DATA/
  runs/<market_date>/<run_id>/
    run_context.json
    manifest.json
    sources/<source_id>/<dataset_key>/
      raw/
      parsed/
      normalized/
      evidence/
      validation/
    packages/
      <source>_package.json
      WSH_Unified_Daily_Data_Package.json
    exceptions/
      exceptions.json
```

Raw evidence should be append-only. Corrections create a new version linked to the superseded record; do not silently overwrite historical captures.

## 8. Orchestration and reliability

- Use a source registry to instantiate adapters; do not hard-code all source logic in one monolithic script.
- Parallelize independent adapters with bounded concurrency. Apply per-domain concurrency limits to avoid rate limiting.
- Use explicit timeouts, retry budgets, exponential backoff with jitter, and circuit-breaker behavior for repeated provider failures.
- Retry transient network/HTTP failures; do not retry deterministic schema failures indefinitely.
- Make each dataset idempotent within a run; repeated execution must not silently duplicate accepted records.
- A source failure must not abort unrelated source adapters unless a declared shared dependency fails.
- Preserve partial outputs and error evidence when a source fails.
- Use a run lock/concurrency group to prevent overlapping production runs.
- Separate live authenticated browser sessions from public-source collectors; secrets belong in GitHub Actions secrets or local secret stores, never in source code or artifacts.

## 9. Security and governance

- No cookies, browser profiles, API keys, passwords, or session tokens in Git.
- Minimize artifact contents; redact sensitive headers and URLs where tokens might be embedded.
- Use least-privilege GitHub Actions permissions.
- Pin or constrain dependencies and record Python/browser versions.
- Protect release branches; develop integration work on a dedicated branch and merge by reviewed PR.
- Keep source terms, access restrictions, and rate limits in the source registry.
- Treat third-party page text as untrusted data, never as executable instructions.

## 10. Initial source inventory

The initial inventory below is a registration baseline, not proof that an adapter exists or that a source is currently accessible. Source-specific datasets and tabs must be registered separately.

| Source family | Initial datasets / segments | Notes |
|---|---|---|
| Sensibull | Existing option-chain capture; OI vs Strike by instrument | V3.11.1 frozen; OI-vs-Strike integration separately versioned |
| NSE | Index performances; unchanged stocks; gainers/losers; option chain; FII/DII portal/API | Separate dataset contracts per segment |
| StockEdge | EOD Market Participants | Separate source identity; independent of Sensibull and CCIL |
| CCIL | Money Market; G-Sec; Forex; Derivatives; settlement datasets as confirmed | Preserve each market segment independently |
| TradingEconomics | Economic calendar; Baltic Dry Index; US business confidence; bonds; commodities YTD treemap | Each page/series separately registered |
| TradingView | Crypto heatmap; US equities sector heatmap; EU equities sector heatmap; FX cross-rates heatmap; NIFTY futures chart | Repeated NIFTY URL entries need checkpoint intent confirmation |
| Research360 | F&O sector analysis | Confirm page fields and observation timestamp |
| Bloomberg | India news / daily intelligence | News item identity, timestamp, title, URL, and deduplication contract required |
| Financial stress / rates sources | OFR financial stress; SOFR; CBOE volatility; commercial paper; ICE BofA HY and other previously specified tabs | Exact source URLs and dataset list must be taken from the user's existing source inventory, not guessed |

## 11. First implementation milestones

**M0 — Repository safety and inventory**
- Establish a new integration branch from `main`.
- Inventory files, workflows, dependencies, source adapters, tests, manifests, and artifacts on `main` and R&D.
- Record the exact Sensibull release boundary.
- Identify reusable FII/DII and CCIL modules and their regression tests.

**M1 — Architecture skeleton**
- Add source registry and dataset contract schema.
- Add run context, adapter interface, orchestration entry point, evidence store, and manifest writer.
- Add contract validation and unit tests.
- Do not migrate source-specific adapters until the skeleton passes tests.

**M2 — Promotion of existing collectors**
- Evaluate FII/DII and CCIL independently against source contracts.
- Port only required modules and tests; do not copy R&D archive clutter or nested workflow directories.
- Keep StockEdge independent.
- Run isolated and integrated regression suites.

**M3 — Additional providers**
- Implement source adapters in parallel according to dependencies and access constraints.
- Validate each dataset against captured source evidence and source-specific contracts.

**M4 — Unified packaging and end-to-end acceptance**
- Historical storage, reconciliation, run-level release gates, consolidated package, exception report, schedule validation, failure injection, and clean-clone reproducibility.

## 12. Immediate decisions / operator confirmations

1. Confirm the intended meaning of the three repeated TradingView NIFTY futures chart URL entries: duplicates, or three distinct collection checkpoints (e.g., 09:30, 10:30, and EOD).
2. Confirm whether the existing local GitHub Desktop clone is available and clean, and whether the user can create a new branch named `wsh-stage1-architecture` from `main`.
3. Provide the exact remaining financial-stress source URLs/tabs if they are not already present in the current source inventory; do not infer them from a summary.

## 13. Acceptance principles

A dataset is accepted only when:
- correct source and dataset identity are established;
- required source-native fields are present and parseable;
- source timestamp/publication window is understood;
- semantic and unit checks pass;
- freshness and calendar checks pass;
- contamination checks pass;
- raw evidence and hashes are retained;
- test evidence supports the adapter behavior;
- known limitations are explicit.

A successful workflow run is not equivalent to institutional data acceptance.
