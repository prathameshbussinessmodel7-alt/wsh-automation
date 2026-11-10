
"""
WSH R7.4 — Integrated Evidence Gate Regression Tests.

Tests:
1. Acquisition evidence
2. Provenance validation
3. Semantic validation
4. Cross-layer consistency
5. Final acceptance decision

Run:
python -m unittest -v test_wsh_r7_evidence_gate.py
"""

import unittest

from wsh_r7_evidence_gate import evaluate_dataset_gate


DATASET = "CCIL_FOREX"

ACQUISITION = {
    "dataset": DATASET,
    "status": "PASS",
    "source_url": "https://example.com/ccil",
    "raw_payload_sha256": "a" * 64,
    "records_count": 2,
}

PROVENANCE = {
    "dataset": DATASET,
    "status": "PASS",
    "source_identity_status": "PASS",
    "capture_timestamp_utc": "2026-11-10T05:30:00Z",
    "record_date_status": "PASS",
    "publication_date_status": "PASS",
}

SEMANTIC = {
    "dataset": DATASET,
    "status": "PASS",
    "records_checked": 2,
    "policy": {
        "units_inferred_from_magnitude": False,
        "definitions_inferred_from_header_names": False,
        "source_evidence_required_for_semantic_pass": True,
    },
}


def evaluate(
    acquisition=None,
    provenance=None,
    semantic=None,
    dataset=DATASET,
):
    return evaluate_dataset_gate(
        dataset,
        ACQUISITION if acquisition is None else acquisition,
        PROVENANCE if provenance is None else provenance,
        SEMANTIC if semantic is None else semantic,
    )


class R74EvidenceGateTests(unittest.TestCase):

    def test_all_layers_pass_accepts_dataset(self):
        result = evaluate()

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["acceptance_eligible"])

    def test_result_contains_correct_version(self):
        result = evaluate()

        self.assertEqual(result["validator_version"], "R7.4")

    def test_result_contains_dataset_identity(self):
        result = evaluate()

        self.assertEqual(result["dataset"], DATASET)

    def test_unknown_dataset_is_not_accepted(self):
        result = evaluate(dataset="SENSIBULL")

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["acceptance_eligible"])

    def test_malformed_acquisition_is_unknown(self):
        result = evaluate(acquisition={})

        self.assertEqual(result["status"], "UNKNOWN")

    def test_acquisition_dataset_mismatch_fails(self):
        acquisition = dict(ACQUISITION)
        acquisition["dataset"] = "CCIL_GSEC"

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "FAIL")

    def test_missing_source_url_is_unknown(self):
        acquisition = dict(ACQUISITION)
        acquisition["source_url"] = ""

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_missing_payload_hash_is_unknown(self):
        acquisition = dict(ACQUISITION)
        acquisition["raw_payload_sha256"] = ""

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_malformed_payload_hash_fails(self):
        acquisition = dict(ACQUISITION)
        acquisition["raw_payload_sha256"] = "invalid-hash"

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "FAIL")

    def test_zero_acquisition_records_is_unknown(self):
        acquisition = dict(ACQUISITION)
        acquisition["records_count"] = 0

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_provenance_dataset_mismatch_fails(self):
        provenance = dict(PROVENANCE)
        provenance["dataset"] = "CCIL_GSEC"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "FAIL")

    def test_naive_capture_timestamp_is_unknown(self):
        provenance = dict(PROVENANCE)
        provenance["capture_timestamp_utc"] = "2026-11-10T05:30:00"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_missing_capture_timestamp_is_unknown(self):
        provenance = dict(PROVENANCE)
        provenance["capture_timestamp_utc"] = ""

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_source_identity_failure_is_preserved(self):
        provenance = dict(PROVENANCE)
        provenance["source_identity_status"] = "FAIL"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "FAIL")

    def test_record_date_failure_is_preserved(self):
        provenance = dict(PROVENANCE)
        provenance["record_date_status"] = "FAIL"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "FAIL")

    def test_unknown_publication_date_is_not_accepted(self):
        provenance = dict(PROVENANCE)
        provenance["publication_date_status"] = "UNKNOWN"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["acceptance_eligible"])

    def test_partial_publication_date_is_preserved(self):
        provenance = dict(PROVENANCE)
        provenance["publication_date_status"] = "PARTIAL"

        result = evaluate(provenance=provenance)

        self.assertEqual(result["status"], "PARTIAL")

    def test_semantic_dataset_mismatch_fails(self):
        semantic = dict(SEMANTIC)
        semantic["dataset"] = "CCIL_GSEC"

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "FAIL")

    def test_unit_inference_policy_fails(self):
        semantic = dict(SEMANTIC)
        semantic["policy"] = dict(SEMANTIC["policy"])
        semantic["policy"]["units_inferred_from_magnitude"] = True

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "FAIL")

    def test_header_definition_inference_policy_fails(self):
        semantic = dict(SEMANTIC)
        semantic["policy"] = dict(SEMANTIC["policy"])
        semantic["policy"][
            "definitions_inferred_from_header_names"
        ] = True

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "FAIL")

    def test_source_evidence_policy_must_be_enabled(self):
        semantic = dict(SEMANTIC)
        semantic["policy"] = dict(SEMANTIC["policy"])
        semantic["policy"][
            "source_evidence_required_for_semantic_pass"
        ] = False

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "FAIL")

    def test_record_count_mismatch_fails(self):
        semantic = dict(SEMANTIC)
        semantic["records_checked"] = 1

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["acceptance_eligible"])

    def test_missing_semantic_policy_is_unknown(self):
        semantic = dict(SEMANTIC)
        semantic.pop("policy")

        result = evaluate(semantic=semantic)

        self.assertEqual(result["status"], "UNKNOWN")

    def test_partial_acquisition_prevents_acceptance(self):
        acquisition = dict(ACQUISITION)
        acquisition["status"] = "PARTIAL"

        result = evaluate(acquisition=acquisition)

        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["acceptance_eligible"])

    def test_missing_layer_prevents_acceptance(self):
        result = evaluate(
            acquisition={},
            provenance=PROVENANCE,
            semantic=SEMANTIC,
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["acceptance_eligible"])

    def test_all_four_layer_results_are_reported(self):
        result = evaluate()

        self.assertEqual(
            set(result["layers"].keys()),
            {
                "acquisition",
                "provenance",
                "semantic",
                "cross_layer",
            },
        )

    def test_each_layer_has_valid_status(self):
        result = evaluate()

        valid_statuses = {
            "PASS",
            "PARTIAL",
            "UNKNOWN",
            "FAIL",
        }

        for layer in result["layers"].values():
            self.assertIn(layer["status"], valid_statuses)

    def test_only_full_pass_is_acceptance_eligible(self):
        result = evaluate()

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["acceptance_eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
