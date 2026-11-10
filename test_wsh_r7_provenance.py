
"""WSH R7.2 provenance validator tests — offline and deterministic."""

import unittest

from wsh_r7_provenance import (
    inspect_evidence,
    source_identity,
    validate_capture_time,
    validate_date_field,
    validate_record_dates,
)


class R72ProvenanceTests(unittest.TestCase):

    def test_nse_source_identity_passes(self):
        result = source_identity(
            "NSE",
            "https://www.nseindia.com/api/fiidiiTradeReact",
        )
        self.assertEqual(result["status"], "PASS")

    def test_ccil_source_identity_passes(self):
        result = source_identity(
            "CCIL",
            "https://www.ccilindia.com/",
        )
        self.assertEqual(result["status"], "PASS")

    def test_wrong_source_domain_fails(self):
        result = source_identity(
            "NSE",
            "https://example.com/data",
        )
        self.assertEqual(result["status"], "FAIL")

    def test_missing_source_url_is_unknown(self):
        result = source_identity("CCIL", None)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_capture_timestamp_is_not_publication_date(self):
        metadata = {
            "captured_at": "2026-11-10T10:30:00+05:30"
        }

        report = inspect_evidence(metadata, "CCIL", [])

        self.assertEqual(
            report["capture_timestamp"]["status"], "PASS"
        )
        self.assertEqual(
            report["publication_date"]["status"], "UNKNOWN"
        )
        self.assertEqual(
            report["market_as_of_date"]["status"], "UNKNOWN"
        )
        self.assertFalse(
            report["inference_policy"][
                "capture_time_used_as_publication_date"
            ]
        )

    def test_capture_time_normalizes_to_utc_and_ist(self):
        result = validate_capture_time({
            "captured_at": "2026-11-10T10:30:00+05:30"
        })

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(
            result["utc"].startswith("2026-11-10T05:00:00")
        )
        self.assertTrue(
            result["ist"].startswith("2026-11-10T10:30:00")
        )

    def test_naive_capture_timestamp_is_not_accepted(self):
        result = validate_capture_time({
            "captured_at": "2026-11-10T10:30:00"
        })
        self.assertEqual(result["status"], "UNKNOWN")

    def test_explicit_publication_date_is_accepted(self):
        result = validate_date_field(
            {"publication_date": "2026-11-09"},
            ("publication_date",),
            "publication date",
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["date"], "2026-11-09")

    def test_unknown_ccil_date_remains_unknown(self):
        result = validate_date_field(
            {
                "source_as_of_date":
                    "UNKNOWN_NOT_EXPLICIT_IN_CAPTURE"
            },
            ("source_as_of_date",),
            "market/as-of date",
        )

        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["date"])

    def test_record_dates_consistent(self):
        result = validate_record_dates([
            {"date": "2026-11-09"},
            {"date": "2026-11-09"},
        ])

        self.assertEqual(result["status"], "PASS")

    def test_record_date_conflict_fails(self):
        result = validate_record_dates([
            {"date": "2026-11-09"},
            {"date": "2026-11-10"},
        ])

        self.assertEqual(result["status"], "FAIL")

    def test_missing_record_date_is_partial(self):
        result = validate_record_dates([
            {"date": "2026-11-09"},
            {"date": None},
        ])

        self.assertEqual(result["status"], "PARTIAL")

    def test_no_record_dates_is_unknown(self):
        result = validate_record_dates([])
        self.assertEqual(result["status"], "UNKNOWN")

    def test_nse_record_dates_are_not_publication_dates(self):
        metadata = {
            "endpoint":
                "https://www.nseindia.com/api/fiidiiTradeReact",
            "captured_at": "2026-11-10T10:30:00+05:30",
        }

        records = [
            {"participant": "DII", "date": "2026-11-09"},
            {"participant": "FII/FPI", "date": "2026-11-09"},
        ]

        report = inspect_evidence(metadata, "NSE", records)

        self.assertEqual(
            report["record_date_consistency"]["status"], "PASS"
        )
        self.assertEqual(
            report["publication_date"]["status"], "UNKNOWN"
        )
        self.assertEqual(
            report["market_as_of_date"]["status"], "UNKNOWN"
        )

    def test_weekday_inference_is_disabled(self):
        report = inspect_evidence({}, "CCIL", [])

        self.assertFalse(
            report["inference_policy"][
                "weekday_used_to_infer_publication"
            ]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
