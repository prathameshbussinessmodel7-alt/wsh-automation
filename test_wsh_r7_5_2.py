"""WSH R7.5.2 regression tests for RND-4 and RND-5 evidence."""
import unittest
from pathlib import Path
import zipfile
import json
import wsh_r7_5_2 as r752

ROOT = Path(__file__).parent
RND4 = ROOT / "WSH-FII-CCIL-RND-4.zip"
RND5 = ROOT / "WSH-FII-CCIL-RND-5.zip"

class R752UnitTests(unittest.TestCase):
    def test_version(self): self.assertEqual(r752.VERSION, "WSH_R7.5.2")
    def test_semantics_remain_partial(self):
        self.assertEqual(r752.semantic_contract_status("CCIL_GSEC")["status"], "PARTIAL")
    def test_unknown_date_not_inferred_from_capture(self):
        self.assertEqual(r752.extract_explicit_ccil_date({"captured_at":"2026-11-10T08:00:00+00:00"})["status"], "UNKNOWN")

@unittest.skipUnless(RND4.exists(), "RND-4 fixture missing")
class RND4RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.audit = r752.inspect_evidence_package(RND4)
    def test_five_datasets_present(self): self.assertEqual(self.audit["dataset_count"], 5)
    def test_all_raw_hashes_pass(self):
        self.assertTrue(all(x["raw_payload_integrity_status"] == "PASS" for x in self.audit["results"].values()))
    def test_four_ccil_datasets_populated(self):
        for k in ("CCIL_MONEY_MARKET","CCIL_GSEC","CCIL_FOREX","CCIL_DERIVATIVES"):
            self.assertEqual(self.audit["results"][k]["acquisition_verdict"], "ROWS_CAPTURED")
    def test_acceptance_stays_partial(self): self.assertEqual(self.audit["overall_status"], "PARTIAL")

@unittest.skipUnless(RND5.exists(), "RND-5 fixture missing")
class RND5RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.audit = r752.inspect_evidence_package(RND5)
    def test_five_datasets_present(self): self.assertEqual(self.audit["dataset_count"], 5)
    def test_all_raw_hashes_pass(self):
        self.assertTrue(all(x["raw_payload_integrity_status"] == "PASS" for x in self.audit["results"].values()))
    def test_money_market_populated(self): self.assertEqual(self.audit["results"]["CCIL_MONEY_MARKET"]["acquisition_verdict"], "ROWS_CAPTURED")
    def test_three_ccil_empty_states_preserved(self):
        for k in ("CCIL_GSEC","CCIL_FOREX","CCIL_DERIVATIVES"):
            self.assertEqual(self.audit["results"][k]["acquisition_verdict"], "EMPTY_UNRESOLVED")
    def test_empty_not_relabelled_closed(self): self.assertFalse(self.audit["policy"]["empty_means_market_closed"])
    def test_r752_pipeline_fingerprint(self): self.assertIn("R7.5.1", self.audit["pipeline_version"])

class CollectorSourceTests(unittest.TestCase):
    def test_no_stale_r6_report_labels(self):
        source=(ROOT/"wsh_fii_ccil_only.py").read_text(encoding="utf-8")
        self.assertNotIn("— R6", source)
        self.assertNotIn("not implemented in R6", source)
    def test_r752_version_fingerprint(self):
        source=(ROOT/"wsh_fii_ccil_only.py").read_text(encoding="utf-8")
        self.assertIn("WSH_FII_CCIL_ISOLATED_R7.5.3", source)
    def test_empty_capture_diagnostics_added(self):
        source=(ROOT/"wsh_fii_ccil_only.py").read_text(encoding="utf-8")
        self.assertIn("page_state_diagnostics", source)
        self.assertIn("performance_resources", source)

if __name__ == "__main__": unittest.main(verbosity=2)
