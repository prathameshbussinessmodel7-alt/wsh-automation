
"""WSH R7.3 — dataset integration and governance tests."""

import unittest

from wsh_r7_semantic import (
    validate_dataset,
    validate_field_spec,
)


DATASETS = [
    "NSE_FII_DII",
    "CCIL_MONEY_MARKET",
    "CCIL_GSEC",
    "CCIL_FOREX",
    "CCIL_DERIVATIVES",
]

VALID_SPEC = {
    "definition": "Test-only documented numeric field.",
    "unit": "test-defined unit",
    "evidence_reference": "TEST-INTEGRATION-001",
    "value_type": "numeric",
}


class R73IntegrationTests(unittest.TestCase):

    def test_all_five_dataset_names_are_supported(self):
        for dataset in DATASETS:
            with self.subTest(dataset=dataset):
                result = validate_dataset(
                    dataset,
                    [{"value": "10"}],
                    {"value": VALID_SPEC},
                )
                self.assertEqual(result["status"], "PASS")

    def test_all_five_datasets_remain_unknown_without_contracts(self):
        for dataset in DATASETS:
            with self.subTest(dataset=dataset):
                result = validate_dataset(
                    dataset,
                    [{"value": "10"}],
                    {},
                )
                self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_dataset_is_rejected(self):
        result = validate_dataset(
            "SENSIBULL",
            [{"value": "10"}],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "UNKNOWN")

    def test_empty_records_return_unknown(self):
        result = validate_dataset(
            "CCIL_GSEC",
            [],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "UNKNOWN")

    def test_malformed_record_is_rejected(self):
        result = validate_dataset(
            "CCIL_FOREX",
            ["not-a-record"],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "FAIL")

    def test_missing_record_value_is_partial(self):
        result = validate_dataset(
            "CCIL_MONEY_MARKET",
            [{}],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "PARTIAL")

    def test_invalid_numeric_record_value_fails(self):
        result = validate_dataset(
            "NSE_FII_DII",
            [{"value": "not-numeric"}],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "FAIL")

    def test_documented_numeric_value_passes(self):
        result = validate_dataset(
            "CCIL_DERIVATIVES",
            [{"value": "10.25"}],
            {"value": VALID_SPEC},
        )
        self.assertEqual(result["status"], "PASS")

    def test_numeric_bounds_are_enforced(self):
        spec = dict(VALID_SPEC)
        spec["minimum"] = 0
        spec["maximum"] = 100

        result = validate_dataset(
            "CCIL_GSEC",
            [{"value": "101"}],
            {"value": spec},
        )
        self.assertEqual(result["status"], "FAIL")

    def test_relationship_violation_fails_dataset(self):
        specs = {
            "buy": dict(VALID_SPEC),
            "sell": dict(VALID_SPEC),
        }

        result = validate_dataset(
            "NSE_FII_DII",
            [{"buy": "90", "sell": "100"}],
            specs,
            relationships=[{
                "left": "buy",
                "right": "sell",
                "operator": "gte",
                "evidence_reference": "TEST-RELATION-002",
            }],
        )

        self.assertEqual(result["status"], "FAIL")

    def test_missing_relationship_values_are_partial(self):
        specs = {
            "buy": dict(VALID_SPEC),
            "sell": dict(VALID_SPEC),
        }

        result = validate_dataset(
            "NSE_FII_DII",
            [{"buy": "90"}],
            specs,
            relationships=[{
                "left": "buy",
                "right": "sell",
                "operator": "gte",
                "evidence_reference": "TEST-RELATION-003",
            }],
        )

        self.assertEqual(result["status"], "PARTIAL")

    def test_unsupported_relationship_operator_is_unknown(self):
        specs = {
            "buy": dict(VALID_SPEC),
            "sell": dict(VALID_SPEC),
        }

        result = validate_dataset(
            "NSE_FII_DII",
            [{"buy": "90", "sell": "100"}],
            specs,
            relationships=[{
                "left": "buy",
                "right": "sell",
                "operator": "approximately",
                "evidence_reference": "TEST-RELATION-004",
            }],
        )

        self.assertEqual(result["status"], "UNKNOWN")

    def test_missing_field_evidence_is_unknown(self):
        spec = dict(VALID_SPEC)
        spec["evidence_reference"] = ""

        result = validate_field_spec("value", spec)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_policy_disables_unit_inference(self):
        result = validate_dataset(
            "CCIL_FOREX",
            [{"value": "93.77"}],
            {"value": VALID_SPEC},
        )

        self.assertFalse(
            result["policy"]["units_inferred_from_magnitude"]
        )

    def test_policy_disables_header_semantic_inference(self):
        result = validate_dataset(
            "CCIL_FOREX",
            [{"value": "93.77"}],
            {"value": VALID_SPEC},
        )

        self.assertFalse(
            result["policy"]["definitions_inferred_from_header_names"]
        )

    def test_policy_requires_source_evidence(self):
        result = validate_dataset(
            "CCIL_GSEC",
            [{"value": "100"}],
            {"value": VALID_SPEC},
        )

        self.assertTrue(
            result["policy"]["source_evidence_required_for_semantic_pass"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
