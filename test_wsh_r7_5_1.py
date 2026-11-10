"""WSH R7.5.1 regression and RND-4 integration tests."""
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import zipfile
import json

import wsh_r7_5_1 as r751

RND4 = Path(__file__).with_name("WSH-FII-CCIL-RND-4.zip")


class R751UnitTests(unittest.TestCase):
    def test_nse_dd_mon_yyyy_is_supported_and_raw_preserved(self):
        got = r751.parse_source_date("09-Nov-2026")
        self.assertEqual(got["status"], "PASS")
        self.assertEqual(got["normalized_date"], "2026-11-09")
        self.assertEqual(got["raw_value"], "09-Nov-2026")

    def test_iso_date_is_supported(self):
        self.assertEqual(r751.parse_source_date("2026-11-09")["normalized_date"], "2026-11-09")

    def test_invalid_date_is_unknown(self):
        self.assertEqual(r751.parse_source_date("09-XYZ-2026")["status"], "UNKNOWN")

    def test_capture_timestamp_is_aware_utc(self):
        fixed = datetime(2026, 11, 11, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        got = r751.create_capture_timestamp(fixed)
        self.assertEqual(got, "2026-11-11T04:30:00.000000+00:00")

    def test_naive_capture_time_is_rejected(self):
        with self.assertRaises(ValueError):
            r751.create_capture_timestamp(datetime(2026, 11, 11, 10, 0))

    def test_attach_capture_metadata_does_not_overwrite_source_date(self):
        fixed = datetime(2026, 11, 11, tzinfo=timezone.utc)
        got = r751.attach_capture_timestamp({"date": "09-Nov-2026"}, fixed)
        self.assertEqual(got["date"], "09-Nov-2026")
        self.assertEqual(got["capture_timestamp_source"], "collector_runtime_clock")

    def test_ccil_does_not_infer_date_from_capture_time(self):
        got = r751.extract_explicit_ccil_date({"captured_at": "2026-11-09T18:36:59+00:00", "source_as_of_date": "UNKNOWN_NOT_EXPLICIT_IN_CAPTURE"})
        self.assertEqual(got["status"], "UNKNOWN")

    def test_ccil_accepts_explicit_as_of_label(self):
        got = r751.extract_explicit_ccil_date({}, "<span>As of: 09-Nov-2026</span>")
        self.assertEqual(got["status"], "PASS")
        self.assertEqual(got["normalized_date"], "2026-11-09")

    def test_ccil_ignores_unlabelled_date(self):
        got = r751.extract_explicit_ccil_date({}, "<div>Last updated 09-Nov-2026</div>")
        self.assertEqual(got["status"], "UNKNOWN")

    def test_five_dataset_semantic_registry_exists(self):
        self.assertEqual(set(r751.SEMANTIC_CONTRACT_REGISTRY), set(r751.DATASET_FILES))

    def test_unknown_units_remain_undocumented(self):
        got = r751.semantic_contract_status("NSE_FII_DII_CASH")
        self.assertEqual(got["status"], "PARTIAL")
        self.assertIn("buy", got["undocumented_fields"])

    def test_explicit_ccil_volume_units_are_recorded(self):
        got = r751.SEMANTIC_CONTRACT_REGISTRY["CCIL_FOREX"]["fields"]["Vol(USD In Mio)"]
        self.assertEqual(got["unit"], "USD million")


@unittest.skipUnless(RND4.exists(), "RND-4 ZIP fixture is not beside the test file")
class RND4IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = r751.inspect_rnd4_package(RND4)

    def test_all_five_live_datasets_are_examined(self):
        self.assertEqual(self.report["dataset_count"], 5)

    def test_nse_date_compatibility_on_actual_rnd4_record(self):
        got = self.report["results"]["NSE_FII_DII_CASH"]["source_date"]
        self.assertEqual(got["status"], "PASS")
        self.assertEqual(got["raw_value"], "09-Nov-2026")

    def test_nse_capture_timestamp_gap_is_reported_not_fabricated(self):
        got = self.report["results"]["NSE_FII_DII_CASH"]["capture_timestamp_status"]
        self.assertEqual(got, "UNKNOWN")

    def test_ccil_capture_timestamps_are_timezone_aware(self):
        for dataset in ("CCIL_MONEY_MARKET", "CCIL_GSEC", "CCIL_FOREX", "CCIL_DERIVATIVES"):
            self.assertEqual(self.report["results"][dataset]["capture_timestamp_status"], "PASS")

    def test_ccil_publication_dates_remain_unknown_without_source_date(self):
        for dataset in ("CCIL_MONEY_MARKET", "CCIL_GSEC", "CCIL_FOREX", "CCIL_DERIVATIVES"):
            self.assertEqual(self.report["results"][dataset]["source_date"]["status"], "UNKNOWN")

    def test_semantic_gaps_are_not_promoted_to_pass(self):
        for result in self.report["results"].values():
            self.assertNotEqual(result["semantic_contract"]["status"], "PASS")

    def test_raw_payload_hashes_match_rnd4_manifest(self):
        for result in self.report["results"].values():
            self.assertEqual(result["raw_payload_integrity_status"], "PASS")

    def test_no_file_mtime_or_inference_policy(self):
        self.assertFalse(self.report["policy"]["file_mtime_used"])
        self.assertFalse(self.report["policy"]["units_inferred"])
        self.assertFalse(self.report["policy"]["sensibull_in_scope"])


class CollectorIntegrationSourceTests(unittest.TestCase):
    def test_r7_5_1_collector_uses_runtime_capture_and_ccil_date_helper(self):
        source = Path(__file__).with_name("wsh_fii_ccil_only.py").read_text(encoding="utf-8")
        self.assertIn("attach_capture_timestamp", source)
        self.assertIn("extract_explicit_ccil_date", source)
        self.assertIn("WSH_FII_CCIL_ISOLATED_R7.5.1", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
