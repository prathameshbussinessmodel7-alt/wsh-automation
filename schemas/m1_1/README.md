# WSH M1.1 — Common Data Contracts v0.1

Status: initial schema baseline; engineering/source-owner review required.

Files:
- `wsh_common_definitions.schema.json`: shared identifiers, dates, hashes and status enums.
- `wsh_source_registry_entry.schema.json`: one source/dataset registry entry.
- `wsh_dataset_contract.schema.json`: field mapping, observation, cadence, extraction, evidence, validation and retention contract.
- `wsh_run_context.schema.json`: run identity, dates, versions, environment and trigger.
- `wsh_dataset_status.schema.json`: independent publication, extraction, validation, freshness, reconciliation and release states.
- `wsh_evidence_record.schema.json`: raw evidence provenance and SHA-256.
- `wsh_quality_manifest.schema.json`: run-level manifest with dataset results and evidence index.

Non-negotiable semantics:
1. Registry presence is not proof of adapter implementation or dataset validity.
2. Publication, extraction, validation, freshness and reconciliation are independent dimensions.
3. `MARKET_CLOSED` is a publication state, not extraction or validation success.
4. No PARTIAL, QUARANTINED, INVALID, STALE or MISMATCH state may be silently promoted to VALID or ACCEPT.
5. Capture raw evidence before parsing/normalization and retain its SHA-256.
6. Preserve source-native field names, units, timestamps and source identifiers.
7. Dataset keys are stable and source-qualified.
8. Schemas reject undeclared fields; changes require a reviewed schema version.

Validator requirement: enforce JSON Schema format assertions for date, date-time, URI and UUID. Format-as-annotation-only mode is insufficient for production gates.
