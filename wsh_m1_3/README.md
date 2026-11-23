# WSH M1.3 â€” Semantic and Policy Validation v0.1

M1.3 is a separately versioned semantic/policy layer over the immutable M1.1 contracts and M1.2 conformance baseline.

## Scope
- Cross-field status and release-decision policies.
- Independent publication, extraction, validation, freshness and reconciliation dimensions.
- Source ID and dataset-key isolation across status, contract, registry and evidence.
- Evidence linkage, timestamp provenance, SHA-256 format and optional digest-to-bytes verification.
- Explicit market-calendar inputs; no weekday-only market-open inference.
- Reconciliation gates.
- Machine-readable JSON report.

## Run
From repository root:

```powershell
python -m unittest discover -s wsh_m1_3/tests -v
python -m wsh_m1_3.policy_report
```

The policy engine consumes M1.1-shaped records. It does not modify M1.1 schemas or M1.2 tests.

## Important limitations
- SHA-256 digest verification is `NOT_EVALUATED` unless the exact captured bytes are supplied.
- Calendar validation is `NOT_EVALUATED` unless an explicit calendar result is supplied.
- A syntactically valid hash is not proof of byte integrity.
- The policy engine does not itself fetch source data or establish source availability.
- Production release gating requires review of policy rules and adapter-specific calendar/reconciliation inputs.

## Release policy
`ACCEPT` requires PUBLISHED + SUCCESS + VALID + FRESH and an eligible reconciliation state. INVALID requires REJECT. QUARANTINED, NOT_RUN, FAILED and NOT_ATTEMPTED cannot be released. Partial, stale, out-of-window and unknown states cannot receive unconditional acceptance.

`MARKET_CLOSED + NOT_ATTEMPTED + NOT_RUN` remains a legitimate representable state, but is held from release.
