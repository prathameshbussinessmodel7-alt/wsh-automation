# WSH M1.2 — Schema Validation and Contract Conformance v0.1

Status: conformance harness for the immutable M1.1 v0.1 schema baseline. Review and execution evidence must accompany the commit.

## Repository placement
Extract this package at the repository root. Its files are placed under `tests/m1_2/`. The harness reads the canonical schemas from `schemas/m1_1/`; it does not copy, edit, or replace M1.1 schemas.

## Scope
- Validate all seven M1.1 schemas against JSON Schema Draft 2020-12 meta-schema.
- Validate representative valid instances for six instance-oriented schemas; shared definitions are exercised through their `$ref` consumers.
- Exercise cross-file `$ref` resolution for shared definitions, dataset status, and evidence records.
- Enable format assertions for date, date-time, URI, and UUID.
- Exercise strict rejection of undeclared properties, invalid enums, malformed identifiers, dates, date-times, URLs, and SHA-256 values.
- Preserve independent status dimensions; the suite confirms `MARKET_CLOSED` + `NOT_ATTEMPTED` + `NOT_RUN` + `HOLD` remains representable.

## Run from repository root
Python 3.10+:

```powershell
python -m pip install -r tests/m1_2/requirements-test.txt
python -m unittest discover -s tests/m1_2 -v
```

The suite uses `jsonschema` Draft 2020-12 validators with `FormatChecker`. Format checks are assertions, not annotation-only.

## Findings and limitations
1. **Cross-field release policy is not encoded by M1.1 JSON Schema.** For example, `validation_status: INVALID` can structurally coexist with `release_decision: ACCEPT`. The test records this limitation rather than claiming the schema prevents it. A separately reviewed policy validator or schema revision is required before production release gating.
2. `wsh_quality_manifest.schema.json` permits additional properties inside `run_context` (`additionalProperties: true`), unlike the stricter top-level contracts. This M1.1 behavior is preserved and flagged for review.
3. Fixtures are synthetic, not live WSH source payloads. Source-specific semantics, units, market calendars, publication windows, contamination fingerprints, and reconciliation require adapter/domain tests.
4. This is schema conformance only. It does not prove browser extraction, source availability, evidence-store durability, or production pipeline behavior.

## Interpretation
A passing suite proves only that the tested schema structures and references behave as asserted by these fixtures. It does not prove all operational/business invariants. M1.1 schemas are not modified by this package; any schema change requires a separately reviewed versioned baseline.
