import hashlib
import unittest
from wsh_m1_3.policy_engine import evaluate_dataset, build_report

RUN = "123e4567-e89b-12d3-a456-426614174000"
EID = "123e4567-e89b-12d3-a456-426614174001"
BASE = {
    "run_id": RUN, "source_id": "sensibull", "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS", "validation_status": "VALID",
    "freshness_status": "FRESH", "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z"
}
CONTRACT = {
    "source_id": "sensibull", "dataset_key": BASE["dataset_key"],
    "evidence": {"raw_capture_required": True},
    "validation": {"reconciliation_rule": None, "calendar_required": False},
    "freshness": {"max_age_seconds": 3600}
}
REGISTRY = {"source_id": "sensibull", "dataset_key": BASE["dataset_key"]}
RUN_CONTEXT = {"run_id": RUN, "run_started_at_utc": "2026-11-23T03:00:00Z", "market_date": "2026-11-23"}
EV = {"evidence_id": EID, "run_id": RUN, "source_id": "sensibull", "dataset_key": BASE["dataset_key"],
      "captured_at_utc": "2026-11-23T03:01:00Z", "sha256": hashlib.sha256(b"WSH evidence sample").hexdigest()}
DEFAULT_CONTRACT = {
    "source_id": "sensibull", "dataset_key": BASE["dataset_key"],
    "evidence": {"raw_capture_required": False},
    "validation": {"reconciliation_rule": None, "calendar_required": False},
    "freshness": {"max_age_seconds": 3600}
}


class PolicyTests(unittest.TestCase):
    def evaluate(self, status=None, **kwargs):
        run_context = kwargs.pop("run_context", RUN_CONTEXT)
        contract = kwargs.pop("contract", DEFAULT_CONTRACT)
        registry = kwargs.pop("registry", REGISTRY)
        return evaluate_dataset(dict(BASE if status is None else status), now_utc="2026-11-23T03:10:00Z",
                                run_context=run_context, contract=contract, registry=registry, **kwargs)

    def codes(self, result):
        return [x["code"] for x in result["findings"]]

    def test_valid_accept(self):
        self.assertEqual(self.evaluate()["policy_result"], "PASS")

    def test_invalid_requires_reject(self):
        r = self.evaluate(dict(BASE, validation_status="INVALID"))
        self.assertIn("INVALID_MUST_REJECT", self.codes(r))
        self.assertEqual(r["release_eligibility"], "REJECT")

    def test_invalid_reject_is_permitted(self):
        r = self.evaluate(dict(BASE, validation_status="INVALID", release_decision="REJECT"))
        self.assertNotIn("INVALID_MUST_REJECT", self.codes(r))

    def test_quarantined_cannot_accept(self):
        self.assertIn("QUARANTINED_CANNOT_RELEASE", self.codes(self.evaluate(dict(BASE, validation_status="QUARANTINED"))))

    def test_not_run_cannot_accept(self):
        self.assertIn("VALIDATION_NOT_RUN_CANNOT_RELEASE", self.codes(self.evaluate(dict(BASE, validation_status="NOT_RUN"))))

    def test_failed_extraction_cannot_accept(self):
        self.assertIn("EXTRACTION_NOT_SUCCESSFUL_CANNOT_RELEASE", self.codes(self.evaluate(dict(BASE, extraction_status="FAILED"))))

    def test_not_attempted_cannot_accept(self):
        self.assertIn("EXTRACTION_NOT_SUCCESSFUL_CANNOT_RELEASE", self.codes(self.evaluate(dict(BASE, extraction_status="NOT_ATTEMPTED"))))

    def test_partial_cannot_unconditionally_accept(self):
        self.assertIn("ACCEPT_REQUIRES_SUCCESSFUL_EXTRACTION", self.codes(self.evaluate(dict(BASE, extraction_status="PARTIAL"))))

    def test_stale_cannot_accept(self):
        self.assertIn("FRESHNESS_GATE_FAILED", self.codes(self.evaluate(dict(BASE, freshness_status="STALE"))))

    def test_out_of_window_cannot_accept(self):
        self.assertIn("FRESHNESS_GATE_FAILED", self.codes(self.evaluate(dict(BASE, freshness_status="OUT_OF_WINDOW"))))

    def test_unknown_freshness_cannot_accept(self):
        self.assertIn("FRESHNESS_GATE_FAILED", self.codes(self.evaluate(dict(BASE, freshness_status="UNKNOWN"))))

    def test_mismatch_requires_reject(self):
        self.assertIn("RECONCILIATION_MISMATCH_MUST_REJECT", self.codes(self.evaluate(dict(BASE, reconciliation_status="MISMATCH"))))

    def test_reconciliation_not_run_required(self):
        c = dict(DEFAULT_CONTRACT, validation={"reconciliation_rule": "compare-authoritative"})
        r = self.evaluate(dict(BASE, reconciliation_status="NOT_RUN"), contract=c)
        self.assertIn("REQUIRED_RECONCILIATION_NOT_MATCHED", self.codes(r))

    def test_reconciliation_not_available_required(self):
        c = dict(DEFAULT_CONTRACT, validation={"reconciliation_rule": "compare-authoritative"})
        self.assertIn("REQUIRED_RECONCILIATION_NOT_MATCHED", self.codes(self.evaluate(contract=c)))

    def test_accept_requires_published(self):
        self.assertIn("PUBLICATION_STATE_NOT_ACCEPTABLE", self.codes(self.evaluate(dict(BASE, publication_status="DELAYED"))))

    def test_accept_requires_all_prerequisites(self):
        self.assertIn("ACCEPT_REQUIRES_SUCCESSFUL_EXTRACTION", self.codes(self.evaluate(dict(BASE, extraction_status="PARTIAL"))))

    def test_limited_accept_requires_limitations(self):
        self.assertIn("LIMITATIONS_NOT_DOCUMENTED", self.codes(self.evaluate(dict(BASE, release_decision="ACCEPT_WITH_LIMITATIONS"))))

    def test_limited_accept_with_limitations(self):
        s = dict(BASE, release_decision="ACCEPT_WITH_LIMITATIONS", limitations=["Delayed secondary confirmation"])
        self.assertEqual(self.evaluate(s)["release_eligibility"], "ACCEPT_WITH_LIMITATIONS")

    def test_market_closed_not_attempted_holds(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED", validation_status="NOT_RUN",
                 freshness_status="UNKNOWN", reconciliation_status="NOT_RUN", release_decision="HOLD")
        self.assertEqual(self.evaluate(s, market_calendar={"market_open": False})["release_eligibility"], "HOLD")

    def test_market_closed_cannot_accept(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED", validation_status="NOT_RUN",
                 freshness_status="UNKNOWN", release_decision="ACCEPT")
        self.assertIn("PUBLICATION_STATE_NOT_ACCEPTABLE", self.codes(self.evaluate(s, market_calendar={"market_open": False})))

    def test_calendar_contradiction(self):
        s = dict(BASE, publication_status="MARKET_CLOSED", extraction_status="NOT_ATTEMPTED", validation_status="NOT_RUN",
                 freshness_status="UNKNOWN", release_decision="HOLD")
        self.assertIn("CALENDAR_STATUS_CONTRADICTION", self.codes(self.evaluate(s, market_calendar={"market_open": True})))

    def test_contract_source_mismatch(self):
        c = dict(DEFAULT_CONTRACT, source_id="other-source")
        self.assertIn("SOURCE_IDENTITY_MISMATCH", self.codes(self.evaluate(contract=c)))

    def test_contract_dataset_mismatch(self):
        c = dict(DEFAULT_CONTRACT, dataset_key="other.dataset")
        self.assertIn("DATASET_KEY_MISMATCH", self.codes(self.evaluate(contract=c)))

    def test_registry_source_mismatch(self):
        self.assertIn("SOURCE_IDENTITY_MISMATCH", self.codes(self.evaluate(registry=dict(REGISTRY, source_id="other-source"))))

    def test_registry_dataset_mismatch(self):
        self.assertIn("DATASET_KEY_MISMATCH", self.codes(self.evaluate(registry=dict(REGISTRY, dataset_key="other.dataset"))))

    def test_contract_reference_missing_dataset_key_fails_identity(self):
        c = dict(DEFAULT_CONTRACT)
        c.pop("dataset_key")
        r = self.evaluate(contract=c)
        self.assertIn("DATASET_KEY_MISMATCH", self.codes(r))
        self.assertNotIn(r["release_eligibility"], ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"))

    def test_contract_reference_missing_source_id_fails_identity(self):
        c = dict(DEFAULT_CONTRACT)
        c.pop("source_id")
        r = self.evaluate(contract=c)
        self.assertIn("SOURCE_IDENTITY_MISMATCH", self.codes(r))
        self.assertNotIn(r["release_eligibility"], ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"))

    def test_registry_reference_missing_dataset_key_fails_identity(self):
        reg = dict(REGISTRY)
        reg.pop("dataset_key")
        r = self.evaluate(registry=reg)
        self.assertIn("DATASET_KEY_MISMATCH", self.codes(r))
        self.assertNotIn(r["release_eligibility"], ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"))

    def test_registry_reference_missing_source_id_fails_identity(self):
        reg = dict(REGISTRY)
        reg.pop("source_id")
        r = self.evaluate(registry=reg)
        self.assertIn("SOURCE_IDENTITY_MISMATCH", self.codes(r))
        self.assertNotIn(r["release_eligibility"], ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"))

    def test_required_evidence_missing(self):
        self.assertIn("REQUIRED_EVIDENCE_MISSING", self.codes(self.evaluate(contract=CONTRACT)))

    def test_evidence_source_isolation(self):
        ev = dict(EV, source_id="wrong-source")
        self.assertIn("EVIDENCE_IDENTITY_MISMATCH", self.codes(self.evaluate(evidence_records=[ev], contract=CONTRACT)))

    def test_evidence_dataset_isolation(self):
        ev = dict(EV, dataset_key="wrong.dataset")
        self.assertIn("EVIDENCE_IDENTITY_MISMATCH", self.codes(self.evaluate(evidence_records=[ev], contract=CONTRACT)))

    def test_evidence_run_id_match(self):
        ev = dict(EV, run_id="123e4567-e89b-12d3-a456-426614174099")
        self.assertIn("REQUIRED_EVIDENCE_MISSING", self.codes(self.evaluate(contract=CONTRACT, evidence_records=[ev])))

    def test_hash_format_invalid(self):
        self.assertIn("EVIDENCE_HASH_FORMAT_INVALID", self.codes(self.evaluate(evidence_records=[dict(EV, sha256="bad")], contract=CONTRACT)))

    def test_hash_bytes_not_supplied_is_not_evaluated(self):
        r = self.evaluate(evidence_records=[EV], contract=CONTRACT)
        self.assertEqual(r["integrity"]["hash_verification"], "NOT_EVALUATED")

    def test_hash_bytes_match(self):
        raw = b"WSH evidence sample"
        r = self.evaluate(evidence_records=[EV], evidence_bytes={EID: raw}, contract=CONTRACT)
        self.assertEqual(r["integrity"]["hash_verification"], "PASS")

    def test_required_evidence_hash_unverified_blocks_accept(self):
        r = self.evaluate(contract=CONTRACT, evidence_records=[EV])
        self.assertIn("REQUIRED_EVIDENCE_HASH_UNVERIFIED", self.codes(r))

    def test_hash_bytes_mismatch(self):
        r = self.evaluate(evidence_records=[EV], evidence_bytes={EID: b"wrong bytes"}, contract=CONTRACT)
        self.assertIn("EVIDENCE_HASH_MISMATCH", self.codes(r))

    def test_future_evidence_timestamp(self):
        ev = dict(EV, captured_at_utc="2026-11-24T03:01:00Z")
        self.assertIn("EVIDENCE_TIMESTAMP_IN_FUTURE", self.codes(self.evaluate(evidence_records=[ev], contract=CONTRACT)))

    def test_invalid_evidence_timestamp(self):
        ev = dict(EV, captured_at_utc="not-a-timestamp")
        self.assertIn("EVIDENCE_PROVENANCE_INVALID", self.codes(self.evaluate(evidence_records=[ev], contract=CONTRACT)))

    def test_run_context_mismatch(self):
        rc = dict(RUN_CONTEXT, run_id="123e4567-e89b-12d3-a456-426614174099")
        self.assertIn("EVIDENCE_RUN_ID_MISMATCH", self.codes(self.evaluate(evidence_records=[EV], contract=CONTRACT, run_context=rc)))

    def test_calendar_missing_is_not_required(self):
        self.assertEqual(self.evaluate()["integrity"]["calendar_validation"], "NOT_REQUIRED")

    def test_calendar_requires_explicit_market_open(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={"source": "test-calendar"})
        self.assertIn("CALENDAR_INPUT_INCOMPLETE", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_no_weekday_inference(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={})
        self.assertEqual(r["integrity"]["calendar_validation"], "NOT_EVALUATED")

    def test_reconciliation_mismatch_is_reject(self):
        r = self.evaluate(dict(BASE, reconciliation_status="MISMATCH", release_decision="REJECT"))
        self.assertNotIn("RECONCILIATION_MISMATCH_MUST_REJECT", self.codes(r))
        self.assertEqual(r["policy_result"], "PASS")

    def test_unknown_release_decision_rejected(self):
        self.assertIn("UNKNOWN_RELEASE_DECISION", self.codes(self.evaluate(dict(BASE, release_decision="MAYBE"))))

    def test_report_counts(self):
        results = [self.evaluate(), self.evaluate(dict(BASE, validation_status="INVALID"))]
        report = build_report(results, "2026-11-23T03:15:00Z")
        self.assertEqual(report["execution"]["policies_evaluated"], 2)
        self.assertEqual(report["execution"]["failed"], 1)
        self.assertEqual(report["release_eligibility"], "REJECT")

    # v0.1.2 regression coverage
    def test_identity_not_evaluated_without_reference(self):
        r = self.evaluate(registry={}, contract={"freshness": {"max_age_seconds": 3600}, "validation": {}, "evidence": {}})
        self.assertEqual(r["integrity"]["source_identity"], "NOT_EVALUATED")
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_limited_accept_delayed_publication_blocked(self):
        s = dict(BASE, publication_status="DELAYED", release_decision="ACCEPT_WITH_LIMITATIONS", limitations=["Delayed"])
        self.assertIn("PUBLICATION_STATE_NOT_ACCEPTABLE", self.codes(self.evaluate(s)))

    def test_limited_accept_partial_with_limitations_allowed(self):
        s = dict(BASE, extraction_status="PARTIAL", release_decision="ACCEPT_WITH_LIMITATIONS", limitations=["One optional table unavailable"])
        self.assertEqual(self.evaluate(s)["release_eligibility"], "ACCEPT_WITH_LIMITATIONS")

    def test_missing_required_status_blocked(self):
        s = dict(BASE)
        del s["publication_status"]
        self.assertIn("STATUS_DIMENSION_INVALID", self.codes(self.evaluate(s)))

    def test_unknown_extraction_state_blocked(self):
        self.assertIn("STATUS_DIMENSION_INVALID", self.codes(self.evaluate(dict(BASE, extraction_status="MAYBE"))))

    def test_missing_identity_reference_blocks_accept(self):
        r = self.evaluate(registry={}, contract={"freshness": {"max_age_seconds": 3600}, "validation": {}, "evidence": {}})
        self.assertIn("IDENTITY_NOT_VERIFIED", self.codes(r))

    def test_missing_freshness_policy_blocks_accept(self):
        c = dict(DEFAULT_CONTRACT)
        c.pop("freshness")
        r = self.evaluate(contract=c)
        self.assertIn("FRESHNESS_NOT_VERIFIED", self.codes(r))

    def test_market_date_missing_blocks_accept(self):
        r = self.evaluate(run_context={"run_id": RUN})
        self.assertIn("MARKET_DATE_MISSING", self.codes(r))

    def test_freshness_age_exceeded_blocks_accept(self):
        s = dict(BASE, updated_at_utc="2026-11-23T00:00:00Z")
        self.assertIn("FRESHNESS_AGE_EXCEEDED", self.codes(self.evaluate(s)))

    def test_required_calendar_missing_blocks_accept(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c)
        self.assertIn("REQUIRED_CALENDAR_NOT_VERIFIED", self.codes(r))

    def test_required_calendar_passes_explicit_result(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={"market_open": True, "market_date": "2026-11-23"})
        self.assertEqual(r["integrity"]["calendar_validation"], "PASS")
        self.assertEqual(r["release_eligibility"], "ACCEPT")

    def test_required_calendar_missing_date_blocks_accept(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={"market_open": True})
        self.assertIn("CALENDAR_MARKET_DATE_MISSING", self.codes(r))
        self.assertEqual(r["integrity"]["calendar_validation"], "FAIL")
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_required_evidence_bytes_and_provenance_pass(self):
        r = self.evaluate(contract=CONTRACT, evidence_records=[EV], evidence_bytes={EID: b"WSH evidence sample"})
        self.assertEqual(r["integrity"]["evidence_provenance"], "PASS")
        self.assertEqual(r["integrity"]["hash_verification"], "PASS")
        self.assertEqual(r["release_eligibility"], "ACCEPT")

    def test_unknown_publication_state_blocked(self):
        self.assertIn("STATUS_DIMENSION_INVALID", self.codes(self.evaluate(dict(BASE, publication_status="SOMETHING"))))

    def test_unknown_reconciliation_state_blocks_release(self):
        r = self.evaluate(dict(BASE, reconciliation_status="UNKNOWN"))
        self.assertIn("RECONCILIATION_STATE_NOT_ELIGIBLE", self.codes(r))


    def test_status_run_id_mismatch_blocks_release(self):
        s = dict(BASE, run_id="123e4567-e89b-12d3-a456-426614174099")
        r = self.evaluate(s)
        self.assertIn("RUN_CONTEXT_IDENTITY_MISMATCH", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_missing_status_run_id_blocks_release(self):
        s = dict(BASE)
        s.pop("run_id")
        r = self.evaluate(s)
        self.assertIn("STATUS_RUN_ID_MISSING", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_missing_context_run_id_blocks_release(self):
        r = self.evaluate(run_context={"market_date": "2026-11-23"})
        self.assertIn("RUN_CONTEXT_ID_MISSING", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_invalid_market_date_blocks_release(self):
        r = self.evaluate(run_context=dict(RUN_CONTEXT, market_date="2026-02-30"))
        self.assertIn("MARKET_DATE_INVALID", self.codes(r))
        self.assertNotEqual(r["release_eligibility"], "ACCEPT")

    def test_noncanonical_market_date_blocks_release(self):
        r = self.evaluate(run_context=dict(RUN_CONTEXT, market_date="20261123"))
        self.assertIn("MARKET_DATE_INVALID", self.codes(r))
        self.assertNotEqual(r["release_eligibility"], "ACCEPT")

    def test_calendar_date_mismatch_blocks_release(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={"market_open": True, "market_date": "2026-11-24"})
        self.assertIn("CALENDAR_DATE_MISMATCH", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")

    def test_invalid_calendar_date_blocks_release(self):
        c = dict(DEFAULT_CONTRACT, validation={"calendar_required": True})
        r = self.evaluate(contract=c, market_calendar={"market_open": True, "market_date": "2026-02-30"})
        self.assertIn("CALENDAR_MARKET_DATE_INVALID", self.codes(r))
        self.assertEqual(r["release_eligibility"], "HOLD")


    # v0.1.4 runtime policy tolerance regression coverage
    def test_update_timestamp_uses_configured_freshness_tolerance(self):
        import wsh_m1_3.policy_engine as engine
        original = engine.POLICY_RULES
        try:
            engine.POLICY_RULES = dict(original)
            engine.POLICY_RULES["freshness_policy"] = dict(original["freshness_policy"], future_timestamp_tolerance_seconds=120)
            status = dict(BASE, updated_at_utc="2026-11-23T03:11:00Z")
            result = self.evaluate(status)
            self.assertNotIn("UPDATED_TIMESTAMP_IN_FUTURE", self.codes(result))
            engine.POLICY_RULES["freshness_policy"]["future_timestamp_tolerance_seconds"] = 30
            result = self.evaluate(status)
            self.assertIn("UPDATED_TIMESTAMP_IN_FUTURE", self.codes(result))
        finally:
            engine.POLICY_RULES = original

    def test_evidence_capture_uses_independent_configured_tolerance(self):
        import wsh_m1_3.policy_engine as engine
        original = engine.POLICY_RULES
        try:
            engine.POLICY_RULES = dict(original)
            engine.POLICY_RULES["evidence_policy"] = dict(original["evidence_policy"], future_capture_tolerance_seconds=120)
            ev = dict(EV, captured_at_utc="2026-11-23T03:11:00Z")
            result = self.evaluate(evidence_records=[ev], contract=CONTRACT)
            self.assertNotIn("EVIDENCE_TIMESTAMP_IN_FUTURE", self.codes(result))
            engine.POLICY_RULES["evidence_policy"]["future_capture_tolerance_seconds"] = 30
            result = self.evaluate(evidence_records=[ev], contract=CONTRACT)
            self.assertIn("EVIDENCE_TIMESTAMP_IN_FUTURE", self.codes(result))
        finally:
            engine.POLICY_RULES = original

    def test_policy_loader_rejects_boolean_tolerance(self):
        import json
        import tempfile
        from pathlib import Path
        from wsh_m1_3.policy_engine import _load_policy
        import wsh_m1_3.policy_engine as engine
        config = json.loads(json.dumps(engine.POLICY_RULES))
        config["freshness_policy"]["future_timestamp_tolerance_seconds"] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                _load_policy(path)

    def test_policy_loader_rejects_version_mismatch(self):
        import json
        import tempfile
        from pathlib import Path
        from wsh_m1_3.policy_engine import _load_policy
        import wsh_m1_3.policy_engine as engine
        config = json.loads(json.dumps(engine.POLICY_RULES))
        config["policy_version"] = "0.1.3"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                _load_policy(path)

if __name__ == "__main__":
    unittest.main(verbosity=2)
