#!/usr/bin/env python3
"""WSH M1.2 schema conformance tests for the M1.1 v0.1 contract baseline.

Requires: Python 3.10+ and jsonschema>=4.18,<5
Run: python -m unittest discover -s tests -v
Format assertions are enabled via FormatChecker.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from jsonschema.validators import RefResolver

PACKAGE_DIR = Path(__file__).resolve().parents[1]
# In the repository, tests/m1_2 reads the immutable M1.1 schemas from schemas/m1_1.
# WSH_SCHEMA_DIR supports running this harness from an extracted package elsewhere.
SCHEMA_DIR = Path(os.environ["WSH_SCHEMA_DIR"]) if "WSH_SCHEMA_DIR" in os.environ else Path(__file__).resolve().parents[2] / "schemas" / "m1_1"
FORMAT_CHECKER = FormatChecker()

SCHEMA_FILES = {
    "common": "wsh_common_definitions.schema.json",
    "registry": "wsh_source_registry_entry.schema.json",
    "contract": "wsh_dataset_contract.schema.json",
    "run": "wsh_run_context.schema.json",
    "status": "wsh_dataset_status.schema.json",
    "evidence": "wsh_evidence_record.schema.json",
    "quality": "wsh_quality_manifest.schema.json",
}

SCHEMAS = {
    key: json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    for key, filename in SCHEMA_FILES.items()
}
STORE = {}
for filename, schema in zip(SCHEMA_FILES.values(), SCHEMAS.values()):
    STORE[filename] = schema
    STORE[(SCHEMA_DIR / filename).as_uri()] = schema
# The common schema has an absolute $id; register it under that identifier too.
STORE[SCHEMAS["common"]["$id"]] = SCHEMAS["common"]


def validator_for(key: str) -> Draft202012Validator:
    schema = SCHEMAS[key]
    resolver = RefResolver(
        base_uri=(SCHEMA_DIR / SCHEMA_FILES[key]).as_uri(),
        referrer=schema,
        store=STORE,
    )
    return Draft202012Validator(schema, resolver=resolver, format_checker=FORMAT_CHECKER)


VALID = {
    "registry": {
        "source_id": "sensibull",
        "dataset_key": "sensibull.market_participants.fii_dii.core",
        "dataset_segments": ["core"],
        "url": "https://web.sensibull.com/fii-dii-data",
        "status": "registered",
    },
    "contract": {
        "contract_version": "0.1",
        "source_id": "sensibull",
        "dataset_key": "sensibull.market_participants.fii_dii.core",
        "purpose": "Capture the source-native FII/DII core view.",
        "source_url": "https://web.sensibull.com/fii-dii-data",
        "owner": {"team_or_role": "WSH Data Engineering"},
        "observation": {
            "market_date_semantics": "source trading date",
            "timezone": "Asia/Kolkata",
            "capture_timestamp_required": True,
        },
        "cadence": {
            "expected_frequency": "trading-day",
            "calendar_policy": "exchange-calendar-aware",
        },
        "fields": [{
            "native_name": "FII Net",
            "canonical_name": "fii_net",
            "data_type": "number",
            "required": True,
            "unit": "INR crore",
        }],
        "extraction": {"method": "browser", "adapter_id": "sensibull.fii_dii.core"},
        "evidence": {
            "raw_capture_required": True,
            "capture_metadata_required": True,
            "hash_algorithm": "SHA-256",
        },
        "validation": {
            "schema_checks": ["required fields present"],
            "semantic_checks": ["source-native values preserved"],
            "freshness_policy": "trading-calendar-aware",
            "contamination_checks": ["source fingerprint matches Sensibull"],
        },
        "retention": {
            "raw_retention_policy": "append-only",
            "normalized_retention_policy": "versioned",
        },
    },
    "run": {
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "run_started_at_utc": "2026-11-23T03:00:00Z",
        "pipeline_version": "0.1.0",
        "git_commit": "1c76bac",
        "environment": "test",
        "market_date": "2026-11-23",
        "timezone": "Asia/Kolkata",
        "source_registry_version": "0.3",
        "schema_version": "0.1",
        "trigger_type": "manual",
    },
    "status": {
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "source_id": "sensibull",
        "dataset_key": "sensibull.market_participants.fii_dii.core",
        "publication_status": "PUBLISHED",
        "extraction_status": "SUCCESS",
        "validation_status": "VALID",
        "freshness_status": "FRESH",
        "reconciliation_status": "NOT_AVAILABLE",
        "release_decision": "ACCEPT",
        "updated_at_utc": "2026-11-23T03:05:00Z",
    },
    "evidence": {
        "evidence_id": "123e4567-e89b-12d3-a456-426614174001",
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "source_id": "sensibull",
        "dataset_key": "sensibull.market_participants.fii_dii.core",
        "captured_at_utc": "2026-11-23T03:01:00Z",
        "requested_url": "https://web.sensibull.com/fii-dii-data",
        "final_url": "https://web.sensibull.com/fii-dii-data",
        "media_type": "text/html",
        "sha256": "a" * 64,
        "storage_uri": "s3://wsh-evidence/2026/11/23/capture.html",
        "capture_method": "browser",
        "byte_length": 1024,
        "http_status": 200,
    },
    "quality": {
        "manifest_version": "0.1",
        "run_context": {
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "market_date": "2026-11-23",
        },
        "dataset_results": [{
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "source_id": "sensibull",
            "dataset_key": "sensibull.market_participants.fii_dii.core",
            "publication_status": "PUBLISHED",
            "extraction_status": "SUCCESS",
            "validation_status": "VALID",
            "freshness_status": "FRESH",
            "reconciliation_status": "NOT_AVAILABLE",
            "release_decision": "ACCEPT",
            "updated_at_utc": "2026-11-23T03:05:00Z",
        }],
        "overall_decision": "ACCEPT",
        "generated_at_utc": "2026-11-23T03:06:00Z",
        "evidence_index": [{
            "evidence_id": "123e4567-e89b-12d3-a456-426614174001",
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "source_id": "sensibull",
            "dataset_key": "sensibull.market_participants.fii_dii.core",
            "captured_at_utc": "2026-11-23T03:01:00Z",
            "requested_url": "https://web.sensibull.com/fii-dii-data",
            "final_url": "https://web.sensibull.com/fii-dii-data",
            "media_type": "text/html",
            "sha256": "a" * 64,
            "storage_uri": "s3://wsh-evidence/2026/11/23/capture.html",
            "capture_method": "browser",
        }],
    },
}


class SchemaConformanceTests(unittest.TestCase):
    def test_all_schemas_are_draft_2020_12_valid(self):
        for key, schema in SCHEMAS.items():
            with self.subTest(schema=key):
                Draft202012Validator.check_schema(schema)

    def test_valid_examples_pass_all_schemas(self):
        for key in ("registry", "contract", "run", "status", "evidence", "quality"):
            with self.subTest(schema=key):
                validator_for(key).validate(VALID[key])

    def test_dataset_contract_resolves_shared_definitions(self):
        # Exercises local cross-file $refs to common definitions, including URI format.
        instance = dict(VALID["contract"])
        instance["source_url"] = "not a URI"
        self.assert_invalid("contract", instance)

    def test_quality_manifest_resolves_status_and_evidence_schemas(self):
        instance = json.loads(json.dumps(VALID["quality"]))
        instance["dataset_results"][0]["validation_status"] = "GREEN"
        self.assert_invalid("quality", instance)

    def test_quality_manifest_resolves_evidence_schema(self):
        instance = json.loads(json.dumps(VALID["quality"]))
        instance["evidence_index"][0]["sha256"] = "bad-hash"
        self.assert_invalid("quality", instance)

    def test_status_rejects_unknown_enum(self):
        instance = dict(VALID["status"], publication_status="OPEN")
        self.assert_invalid("status", instance)

    def test_status_rejects_unknown_property(self):
        instance = dict(VALID["status"], inferred_success=True)
        self.assert_invalid("status", instance)

    def test_registry_rejects_unknown_property(self):
        instance = dict(VALID["registry"], source_family="market-data")
        self.assert_invalid("registry", instance)

    def test_contract_rejects_unknown_nested_property(self):
        instance = json.loads(json.dumps(VALID["contract"]))
        instance["owner"]["email"] = "not-authorized-by-schema"
        self.assert_invalid("contract", instance)

    def test_run_context_rejects_invalid_uuid(self):
        instance = dict(VALID["run"], run_id="not-a-uuid")
        self.assert_invalid("run", instance)

    def test_run_context_rejects_invalid_date(self):
        instance = dict(VALID["run"], market_date="2026-02-30")
        self.assert_invalid("run", instance)

    def test_evidence_rejects_invalid_sha256(self):
        instance = dict(VALID["evidence"], sha256="f" * 63)
        self.assert_invalid("evidence", instance)

    def test_evidence_rejects_invalid_url_format(self):
        instance = dict(VALID["evidence"], requested_url="not a uri")
        self.assert_invalid("evidence", instance)

    def test_evidence_rejects_invalid_datetime_format(self):
        instance = dict(VALID["evidence"], captured_at_utc="not-a-date-time")
        self.assert_invalid("evidence", instance)

    def test_quality_rejects_unknown_top_level_property(self):
        instance = dict(VALID["quality"], unreviewed_field=1)
        self.assert_invalid("quality", instance)

    def test_status_dimensions_remain_independent(self):
        # The schema validates each declared status dimension independently.
        # This deliberately unusual combination must remain structurally representable;
        # policy/semantic gates, not JSON Schema, decide whether it may be released.
        instance = dict(
            VALID["status"],
            publication_status="MARKET_CLOSED",
            extraction_status="NOT_ATTEMPTED",
            validation_status="NOT_RUN",
            freshness_status="UNKNOWN",
            reconciliation_status="NOT_RUN",
            release_decision="HOLD",
        )
        validator_for("status").validate(instance)

    def test_invalid_state_cannot_be_mislabeled_as_valid_enum(self):
        # Enum validation blocks invented aliases, but JSON Schema v0.1 does not
        # implement cross-field policy such as INVALID => REJECT.
        instance = dict(VALID["status"], validation_status="INVALID")
        validator_for("status").validate(instance)

    def assert_invalid(self, key, instance):
        with self.assertRaises(ValidationError):
            validator_for(key).validate(instance)


if __name__ == "__main__":
    unittest.main(verbosity=2)
