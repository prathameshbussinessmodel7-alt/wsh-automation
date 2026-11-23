import unittest
from wsh_m1_3.policy_engine import build_report

class ReportTests(unittest.TestCase):
    def test_empty_report_is_not_evaluated(self):
        report = build_report([], "2026-11-23T00:00:00Z")
        self.assertEqual(report["release_eligibility"], "NOT_EVALUATED")
        self.assertEqual(report["execution"]["policies_evaluated"], 0)

    def test_failed_result_fails_report(self):
        result = {
            "policy_result": "FAIL", "release_eligibility": "HOLD",
            "integrity": {k: "FAIL" for k in ("source_identity", "evidence_provenance", "hash_verification", "calendar_validation")},
            "findings": [{"code": "TEST", "severity": "ERROR"}], "warnings": []
        }
        report = build_report([result], "2026-11-23T00:00:00Z")
        self.assertEqual(report["execution"]["result"], "FAIL")

    def test_all_accept_aggregates_accept(self):
        result = {
            "policy_result": "PASS", "release_eligibility": "ACCEPT",
            "integrity": {k: "PASS" for k in ("source_identity", "evidence_provenance", "hash_verification", "calendar_validation")},
            "findings": [], "warnings": []
        }
        self.assertEqual(build_report([result], "2026-11-23T00:00:00Z")["release_eligibility"], "ACCEPT")

if __name__ == "__main__":
    unittest.main(verbosity=2)
