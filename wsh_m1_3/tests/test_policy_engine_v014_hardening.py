import json
import math
import tempfile
import unittest
from pathlib import Path
from wsh_m1_3.policy_engine import PACKAGE_NAME, POLICY_VERSION, _load_policy, evaluate_dataset

RUN = "123e4567-e89b-12d3-a456-426614174000"
DATASET = "sensibull.market_participants.fii_dii.core"
BASE = {
    "run_id": RUN, "source_id": "sensibull", "dataset_key": DATASET,
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS",
    "validation_status": "VALID", "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z",
}
CONTRACT = {
    "source_id": "sensibull", "dataset_key": DATASET,
    "evidence": {"raw_capture_required": False},
    "validation": {"reconciliation_rule": None, "calendar_required": False},
    "freshness": {"max_age_seconds": 3600},
}
REGISTRY = {"source_id": "sensibull", "dataset_key": DATASET}
CONTEXT = {"run_id": RUN, "run_started_at_utc": "2026-11-23T03:00:00Z", "market_date": "2026-11-23"}

def run_eval(status=None, contract=None, market_calendar=None):
    return evaluate_dataset(
        dict(BASE if status is None else status),
        contract=dict(CONTRACT if contract is None else contract),
        registry=dict(REGISTRY), run_context=dict(CONTEXT),
        market_calendar=market_calendar, now_utc="2026-11-23T03:10:00Z",
    )

class V014HardeningTests(unittest.TestCase):
    def test_loader_rejects_wrong_package(self):
        config = json.loads(Path("wsh_m1_3/policy_rules.json").read_text(encoding="utf-8"))
        config["package"] = "WRONG PACKAGE"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "policy_rules.json"
            p.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "package"):
                _load_policy(p)

    def test_loader_accepts_matching_package(self):
        config = json.loads(Path("wsh_m1_3/policy_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(config["package"], PACKAGE_NAME)
        self.assertEqual(config["policy_version"], POLICY_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "policy_rules.json"
            p.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(_load_policy(p)["package"], PACKAGE_NAME)

    def test_freshness_rejects_positive_infinity(self):
        c = dict(CONTRACT); c["freshness"] = {"max_age_seconds": math.inf}
        r = run_eval(contract=c)
        self.assertIn("FRESHNESS_POLICY_INVALID", {x["code"] for x in r["findings"]})

    def test_freshness_rejects_nan(self):
        c = dict(CONTRACT); c["freshness"] = {"max_age_seconds": math.nan}
        r = run_eval(contract=c)
        self.assertIn("FRESHNESS_POLICY_INVALID", {x["code"] for x in r["findings"]})

    def test_freshness_rejects_negative_infinity(self):
        c = dict(CONTRACT); c["freshness"] = {"max_age_seconds": -math.inf}
        r = run_eval(contract=c)
        self.assertIn("FRESHNESS_POLICY_INVALID", {x["code"] for x in r["findings"]})

    def test_optional_empty_calendar_is_fail(self):
        r = run_eval(market_calendar={})
        self.assertEqual(r["integrity"]["calendar_validation"], "FAIL")
        self.assertIn("CALENDAR_INPUT_INCOMPLETE", {x["code"] for x in r["findings"]})

    def test_optional_non_boolean_calendar_is_fail(self):
        r = run_eval(market_calendar={"market_open": "true"})
        self.assertEqual(r["integrity"]["calendar_validation"], "FAIL")

    def test_absent_optional_calendar_is_not_required(self):
        self.assertEqual(run_eval(market_calendar=None)["integrity"]["calendar_validation"], "NOT_REQUIRED")

    def test_list_status_value_is_controlled_finding(self):
        r = run_eval(status=dict(BASE, publication_status=["PUBLISHED"]))
        self.assertIn("STATUS_DIMENSION_INVALID", {x["code"] for x in r["findings"]})

    def test_dict_status_value_is_controlled_finding(self):
        r = run_eval(status=dict(BASE, extraction_status={"state": "SUCCESS"}))
        self.assertIn("STATUS_DIMENSION_INVALID", {x["code"] for x in r["findings"]})

    def test_integer_status_value_is_controlled_finding(self):
        r = run_eval(status=dict(BASE, validation_status=1))
        self.assertIn("STATUS_DIMENSION_INVALID", {x["code"] for x in r["findings"]})

    def test_valid_control_case_remains_pass(self):
        r = run_eval()
        self.assertEqual(r["policy_result"], "PASS")
        self.assertEqual(r["integrity"]["freshness_verification"], "PASS")

if __name__ == "__main__":
    unittest.main(verbosity=2)
