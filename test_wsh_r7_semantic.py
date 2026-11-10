
"""WSH R7.3 — semantic and unit validator regression tests."""

import unittest

from wsh_r7_semantic import (
    to_decimal,
    validate_dataset,
    validate_field_spec,
    validate_field_value,
    validate_relationship,
)


VALID_NUMERIC_SPEC = {
    "definition": "Illustrative documented numeric field.",
    "unit": "source-defined unit",
    "evidence_reference": "TEST-DOCUMENT-001",
    "value_type": "numeric",
}

VALID_TEXT_SPEC = {
    "definition": "Illustrative documented instrument identifier.",
    "unit": "not_applicable",
    "evidence_reference": "TEST-DOCUMENT-002",
    "value_type": "text",
}


class R73SemanticTests(unittest.TestCase):

    def test_decimal_preserves_numeric_precision(self):
        result = to_decimal("0.123456789")
        self.assertEqual(str(result), "0.123456789")

    def test_non_numeric_value_is_rejected(self):
        self.assertIsNone(to_decimal("not-a-number"))

    def test_non_finite_values_are_rejected(self):
        self.assertIsNone(to_decimal("NaN"))
        self.assertIsNone(to_decimal("Infinity"))

    def test_documented_field_contract_passes(self):
        result = validate_field_spec(
            "sample_field",
            VALID_NUMERIC_SPEC,
        )
        self.assertEqual(result["status"], "PASS")

    def test_missing_unit_remains_unknown(self):
        spec = dict(VALID_NUMERIC_SPEC)
        spec["unit"] = ""

        result = validate_field_spec("sample_field", spec)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_missing_evidence_reference_remains_unknown(self):
        spec = dict(VALID_NUMERIC_SPEC)
        spec["evidence_reference"] = ""

        result = validate_field_spec("sample_field", spec)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_invalid_minimum_maximum_fails(self):
        spec = dict(VALID_NUMERIC_SPEC)
        spec["minimum"] = 100
        spec["maximum"] = 10

        result = validate_field_spec("sample_field", spec)
        self.assertEqual(result["status"], "FAIL")

    def test_numeric_value_passes_documented_contract(self):
        result = validate_field_value(
            "25.50",
            VALID_NUMERIC_SPEC,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["normalized_value"], "25.50")

    def test_numeric_value_outside_documented_bounds_fails(self):
        spec = dict(VALID_NUMERIC_SPEC)
        spec["minimum"] = 0
        spec["maximum"] = 100

        result = validate_field_value(150, spec)
        self.assertEqual(result["status"], "FAIL")

    def test_missing_numeric_value_is_partial(self):
        result = validate_field_value(
            None,
            VALID_NUMERIC_SPEC,
        )
        self.assertEqual(result["status"], "PARTIAL")

    def test_undocumented_field_value_remains_unknown(self):
        result = validate_field_value(93.77, {})
        self.assertEqual(result["status"], "UNKNOWN")

    def test_text_field_allowed_values_are_enforced(self):
        spec = dict(VALID_TEXT_SPEC)
        spec["allowed_values"] = ["DII", "FII/FPI"]

        passed = validate_field_value("DII", spec)
        failed = validate_field_value("OTHER", spec)

        self.assertEqual(passed["status"], "PASS")
        self.assertEqual(failed["status"], "FAIL")

    def test_documented_numeric_relationship_passes(self):
        specs = {
            "buy": dict(VALID_NUMERIC_SPEC),
            "sell": dict(VALID_NUMERIC_SPEC),
        }

        record = {
            "buy": "120",
            "sell": "100",
        }

        relationship = {
            "left": "buy",
            "right": "sell",
            "operator": "gte",
            "evidence_reference": "TEST-RELATION-001",
        }

        result = validate_relationship(
            record,
            relationship,
            specs,
        )

        self.assertEqual(result["status"], "PASS")

    def test_undocumented_relationship_is_unknown(self):
        result = validate_relationship(
            {"buy": 120, "sell": 100},
            {
                "left": "buy",
                "right": "sell",
                "operator": "gte",
                "evidence_reference": "",
            },
            {
                "buy": VALID_NUMERIC_SPEC,
                "sell": VALID_NUMERIC_SPEC,
            },
        )

        self.assertEqual(result["status"], "UNKNOWN")

    def test_empty_semantic_contracts_are_unknown(self):
        result = validate_dataset(
            "CCIL_FOREX",
            [{"instrument": "USDINR", "ltp": "93.77"}],
            {},
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["records_checked"], 1)
        self.assertEqual(result["field_contracts"], {})
        self.assertEqual(result["records"], [])

    def test_undocumented_field_contract_keeps_dataset_unknown(self):
        result = validate_dataset(
            "CCIL_FOREX",
            [{"ltp": "93.77"}],
            {"ltp": {}},
        )

        self.assertEqual(result["status"], "UNKNOWN")

    def test_dataset_with_valid_contracts_passes(self):
        result = validate_dataset(
            "NSE_FII_DII",
            [{"buy": "120", "sell": "100"}],
            {
                "buy": VALID_NUMERIC_SPEC,
                "sell": VALID_NUMERIC_SPEC,
            },
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["records_checked"], 1)

    def test_unknown_dataset_is_not_accepted(self):
        result = validate_dataset(
            "SENSIBULL",
            [{"value": 100}],
            {},
        )

        self.assertEqual(result["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
