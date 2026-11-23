import unittest
from datetime import datetime, timezone, timedelta
from wsh_m1_3.policy_engine import evaluate_dataset, build_report

RUN = "123e4567-e89b-12d3-a456-426614174000"
EID = "123e4567-e89b-12d3-a456-426614174001"
BASE = {
    "run_id": RUN, "source_id": "sensibull",
    "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS",
    "validation_status": "VALID", "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z"
}
CONTRACT = {
    "source_id": "sensibull", "dataset_key": BASE["dataset_key"],
    "evidence": {"raw_capture_required": True},
    "validation": {"reconciliation_rule": None}
}
REGISTRY = {"source_id": "sensibull", "dataset_key": BASE["dataset_key"]}
RUN_CONTEXT = {"run_id": RUN, "run_started_at_utc": "2026-11-23T03:00:00Z", "market_date": "2026-11-23"}
EV = {
    "evidence_id": EID, "run_id": RUN, "source_id": "sensibull",
    "dataset_key": BASE["dataset_key"], "captured_at_utc": "2026-11-23T03:01:00Z",
    "sha256": "a" * 64
}

class PolicyTests(unittest.TestCase):
    def evaluate(self, status=None, **kwargs):
        run_context = kwargs.pop("run_context", RUN_CONTEXT)
        return evaluate_dataset(dict(BASE if status is None else status), now_utc="2026-11-23T03:10:00Z", run_context=run_context, **kwargs)

    def test_valid_accept(self):
        self.assertEqual(self.evaluate()["policy_result"], "PASS")

    def test_invalid_requires_reject(self):
        s = dict(BASE, validation_status="INVALID")
        self.assertIn("INVALID_MUST_REJECT", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_invalid_reject_is_permitted(self):
        s = dict(BASE, validation_status="INVALID", release_decision="REJECT")
        self.assertNotIn("INVALID_MUST_REJECT", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_quarantined_cannot_accept(self):
        s = dict(BASE, validation_status="QUARANTINED")
        self.assertIn("QUARANTINED_CANNOT_RELEASE", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_not_run_cannot_accept(self):
        s = dict(BASE, validation_status="NOT_RUN")
        self.assertIn("VALIDATION_NOT_RUN_CANNOT_RELEASE", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_failed_extraction_cannot_accept(self):
        s = dict(BASE, extraction_status="FAILED")
        self.assertIn("FAILED_EXTRACTION_CANNOT_RELEASE", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_not_attempted_cannot_accept(self):
        s = dict(BASE, extraction_status="NOT_ATTEMPTED")
        self.assertIn("EXTRACTION_NOT_ATTEMPTED_CANNOT_RELEASE", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_partial_cannot_unconditionally_accept(self):
        s = dict(BASE, extraction_status="PARTIAL")
        self.assertIn("PARTIAL_CANNOT_ACCEPT", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_stale_cannot_accept(self):
        s = dict(BASE, freshness_status="STALE")
        self.assertIn("FRESHNESS_GATE_FAILED", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_out_of_window_cannot_accept(self):
        s = dict(BASE, freshness_status="OUT_OF_WINDOW")
        self.assertIn("FRESHNESS_GATE_FAILED", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_unknown_freshness_cannot_accept(self):
        s = dict(BASE, freshness_status="UNKNOWN")
        self.assertIn("FRESHNESS_GATE_FAILED", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_mismatch_requires_reject(self):
        s = dict(BASE, reconciliation_status="MISMATCH")
        self.assertIn("RECONCILIATION_MISMATCH_MUST_REJECT", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_reconciliation_not_run_required(self):
        c = dict(CONTRACT, validation={"reconciliation_rule": "compare-with-authoritative-source"})
        s = dict(BASE, reconciliation_status="NOT_RUN")
        self.assertIn("RECONCILIATION_REQUIRED_NOT_RUN", [x["code"] for x in self.evaluate(s, contract=c)["findings"]])

    def test_reconciliation_not_available_required(self):
        c = dict(CONTRACT, validation={"reconciliation_rule": "compare-with-authoritative-source"})
        self.assertIn("REQUIRED_RECONCILIATION_UNAVAILABLE", [x["code"] for x in self.evaluate(contract=c)["findings"]])

    def test_accept_requires_published(self):
        s = dict(BASE, publication_status="DELAYED")
        self.assertIn("PUBLICATION_STATE_NOT_ACCEPTABLE", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_accept_requires_all_prerequisites(self):
        s = dict(BASE, extraction_status="PARTIAL")
        self.assertIn("ACCEPT_PREREQUISITES_NOT_MET", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_limited_accept_requires_limitations(self):
        s = dict(BASE, release_decision="ACCEPT_WITH_LIMITATIONS")
        self.assertIn("LIMITATIONS_NOT_DOCUMENTED", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_limited_accept_with_limitations(self):
        s = dict(BASE, release_decision="ACCEPT_WITH_LIMITATIONS", limitations=["Delayed secondary confirmation"])
        self.assertNotIn("LIMITATIONS_NOT_DOCUMENTED", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_market_closed_not_attempted_holds(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED",
                 validation_status="NOT_RUN", freshness_status="UNKNOWN", reconciliation_status="NOT_RUN",
                 release_decision="HOLD")
        self.assertNotIn("CLOSED_MARKET_NOT_ATTEMPTED_REQUIRES_HOLD", [x["code"] for x in self.evaluate(s, market_calendar={"market_open": False})["findings"]])

    def test_market_closed_cannot_accept(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED",
                 validation_status="NOT_RUN", freshness_status="UNKNOWN", release_decision="ACCEPT")
        self.assertIn("CLOSED_MARKET_NOT_ATTEMPTED_REQUIRES_HOLD", [x["code"] for x in self.evaluate(s, market_calendar={"market_open": False})["findings"]])

    def test_calendar_contradiction(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED",
                 validation_status="NOT_RUN", freshness_status="UNKNOWN", release_decision="HOLD")
        self.assertIn("CALENDAR_STATUS_CONTRADICTION", [x["code"] for x in self.evaluate(s, market_calendar={"market_open": True})["findings"]])

    def test_contract_source_mismatch(self):
        c = dict(CONTRACT, source_id="other-source")
        self.assertIn("SOURCE_IDENTITY_MISMATCH", [x["code"] for x in self.evaluate(contract=c)["findings"]])

    def test_contract_dataset_mismatch(self):
        c = dict(CONTRACT, dataset_key="other.dataset")
        self.assertIn("DATASET_KEY_MISMATCH", [x["code"] for x in self.evaluate(contract=c)["findings"]])

    def test_registry_source_mismatch(self):
        r = dict(REGISTRY, source_id="other-source")
        self.assertIn("SOURCE_IDENTITY_MISMATCH", [x["code"] for x in self.evaluate(registry=r)["findings"]])

    def test_registry_dataset_mismatch(self):
        r = dict(REGISTRY, dataset_key="other.dataset")
        self.assertIn("DATASET_KEY_MISMATCH", [x["code"] for x in self.evaluate(registry=r)["findings"]])

    def test_required_evidence_missing(self):
        self.assertIn("REQUIRED_EVIDENCE_MISSING", [x["code"] for x in self.evaluate(contract=CONTRACT)["findings"]])

    def test_evidence_source_isolation(self):
        ev = dict(EV, source_id="wrong-source")
        self.assertIn("EVIDENCE_IDENTITY_MISMATCH", [x["code"] for x in self.evaluate(evidence_records=[ev])["findings"]])

    def test_evidence_dataset_isolation(self):
        ev = dict(EV, dataset_key="wrong.dataset")
        self.assertIn("EVIDENCE_IDENTITY_MISMATCH", [x["code"] for x in self.evaluate(evidence_records=[ev])["findings"]])

    def test_evidence_run_id_match(self):
        ev = dict(EV, run_id="123e4567-e89b-12d3-a456-426614174099")
        self.assertIn("REQUIRED_EVIDENCE_MISSING", [x["code"] for x in self.evaluate(contract=CONTRACT, evidence_records=[ev])["findings"]])

    def test_hash_format_invalid(self):
        ev = dict(EV, sha256="bad")
        self.assertIn("EVIDENCE_HASH_FORMAT_INVALID", [x["code"] for x in self.evaluate(evidence_records=[ev])["findings"]])

    def test_hash_bytes_not_supplied_is_not_evaluated(self):
        r = self.evaluate(evidence_records=[EV])
        self.assertEqual(r["integrity"]["hash_verification"], "NOT_EVALUATED")

    def test_hash_bytes_match(self):
        import hashlib
        raw = b"WSH evidence sample"
        ev = dict(EV, sha256=hashlib.sha256(raw).hexdigest())
        r = self.evaluate(evidence_records=[ev], evidence_bytes={EID: raw}, run_context=RUN_CONTEXT)
        self.assertEqual(r["integrity"]["hash_verification"], "PASS")

    def test_required_evidence_hash_unverified_blocks_accept(self):
        r = self.evaluate(contract=CONTRACT, evidence_records=[EV])
        self.assertIn("REQUIRED_EVIDENCE_HASH_UNVERIFIED", [x["code"] for x in r["findings"]])

    def test_hash_bytes_mismatch(self):
        r = self.evaluate(evidence_records=[EV], evidence_bytes={EID: b"wrong bytes"}, run_context=RUN_CONTEXT)
        self.assertIn("EVIDENCE_HASH_MISMATCH", [x["code"] for x in r["findings"]])

    def test_future_evidence_timestamp(self):
        ev = dict(EV, captured_at_utc="2026-11-24T03:01:00Z")
        self.assertIn("EVIDENCE_TIMESTAMP_IN_FUTURE", [x["code"] for x in self.evaluate(evidence_records=[ev])["findings"]])

    def test_invalid_evidence_timestamp(self):
        ev = dict(EV, captured_at_utc="not-a-timestamp")
        self.assertIn("EVIDENCE_TIMESTAMP_INVALID", [x["code"] for x in self.evaluate(evidence_records=[ev])["findings"]])

    def test_run_context_mismatch(self):
        rc = dict(RUN_CONTEXT, run_id="123e4567-e89b-12d3-a456-426614174099")
        self.assertIn("EVIDENCE_RUN_ID_MISMATCH", [x["code"] for x in self.evaluate(evidence_records=[EV], run_context=rc)["findings"]])

    def test_calendar_missing_is_not_evaluated(self):
        self.assertEqual(self.evaluate()["integrity"]["calendar_validation"], "NOT_EVALUATED")

    def test_calendar_requires_explicit_market_open(self):
        r = self.evaluate(market_calendar={"source": "test-calendar"})
        self.assertIn("CALENDAR_INPUT_INCOMPLETE", [x["code"] for x in r["findings"]])

    def test_no_weekday_inference(self):
        r = self.evaluate(market_calendar={})
        self.assertEqual(r["integrity"]["calendar_validation"], "NOT_EVALUATED")

    def test_reconciliation_mismatch_is_reject(self):
        s = dict(BASE, reconciliation_status="MISMATCH", release_decision="REJECT")
        result = self.evaluate(s)
        self.assertNotIn("RECONCILIATION_MISMATCH_MUST_REJECT", [x["code"] for x in result["findings"]])
        self.assertEqual(result["policy_result"], "PASS")

    def test_unknown_release_decision_rejected(self):
        s = dict(BASE, release_decision="MAYBE")
        self.assertIn("UNKNOWN_RELEASE_DECISION", [x["code"] for x in self.evaluate(s)["findings"]])

    def test_report_counts(self):
        results = [self.evaluate(), self.evaluate(dict(BASE, validation_status="INVALID"))]
        report = build_report(results, "2026-11-23T03:15:00Z")
        self.assertEqual(report["execution"]["policies_evaluated"], 2)
        self.assertEqual(report["execution"]["failed"], 1)
        self.assertEqual(report["release_eligibility"], "REJECT")

if __name__ == "__main__":
    unittest.main(verbosity=2)
