# WSH M1.3 — Semantic and Policy Validation v0.1.4
M1.3 is a separately versioned semantic/policy layer over the immutable M1.1 contracts and M1.2 conformance baseline. This corrective revision strengthens identity, release, freshness, evidence, calendar and aggregate-report gates.

## Scope
- Cross-field status and release-decision policies.
- Explicit enumerations for all required status dimensions.
- Independent source identity verification against at least one supplied contract or registry for release.
- Contract-driven evidence, freshness, reconciliation and calendar requirements.
- SHA-256 verification against the exact captured bytes when raw evidence is required.
- Explicit distinction between `PASS`, `FAIL`, `NOT_REQUIRED` and `NOT_EVALUATED` integrity states.
- Machine-readable report with strict JSON Schema.

## Run
From repository root:

```powershell
python -m pip install -r wsh_m1_3/requirements.txt
python -m unittest discover -s wsh_m1_3/tests -v
python -m wsh_m1_3.generate_report
```

## Release controls
- `ACCEPT` requires `PUBLISHED`, `SUCCESS`, `VALID`, `FRESH`, eligible reconciliation, independently verified identity and independently verified freshness.
- `ACCEPT_WITH_LIMITATIONS` requires `PUBLISHED`, `SUCCESS` or `PARTIAL`, `VALID`, `FRESH`, documented limitations, independently verified identity and independently verified freshness.
- `INVALID` and reconciliation `MISMATCH` require `REJECT`.
- Unknown or missing required status dimensions fail policy evaluation.
- If raw capture is contractually required, matching evidence provenance and hash verification against exact bytes are mandatory.
- If calendar validation is contractually required, an explicit boolean `market_open` result is mandatory. Weekdays are never used to infer exchange sessions.
- `NOT_REQUIRED` means a contract explicitly makes a control inapplicable. `NOT_EVALUATED` means the control could not be verified. A required `NOT_EVALUATED` control blocks release.

## Important limitations
- The engine evaluates supplied records; it does not fetch or authenticate source data.
- Contract, registry, run context, evidence bytes and calendar inputs must be supplied by trusted upstream adapters.
- The generated report is a synthetic demonstration fixture, not a production Sensibull validation or WSH release authorization.
- Production integration remains disabled pending independent review and adapter-level validation.
