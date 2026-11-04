"""Focused deterministic tests for V3.1 WebSocket identity and freshness gates."""
import base64
import datetime
import gzip
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

# Minimal Selenium import stubs; no browser or network is used.
selenium = types.ModuleType("selenium")
webdriver = types.ModuleType("selenium.webdriver")
webdriver.Chrome = lambda **kwargs: None
chrome = types.ModuleType("selenium.webdriver.chrome")
options_mod = types.ModuleType("selenium.webdriver.chrome.options")
class Options:
    def add_argument(self, *args): pass
    def set_capability(self, *args): pass
options_mod.Options = Options
common = types.ModuleType("selenium.webdriver.common")
by_mod = types.ModuleType("selenium.webdriver.common.by")
class By:
    TAG_NAME = "tag name"
    XPATH = "xpath"
by_mod.By = By
support = types.ModuleType("selenium.webdriver.support")
ui_mod = types.ModuleType("selenium.webdriver.support.ui")
class WebDriverWait:
    def __init__(self, *args, **kwargs): pass
    def until(self, *args, **kwargs): return object()
ui_mod.WebDriverWait = WebDriverWait
ec_mod = types.ModuleType("selenium.webdriver.support.expected_conditions")
ec_mod.presence_of_element_located = lambda *args: object()
ec_mod.element_to_be_clickable = lambda *args: object()
for name, module in {
    "selenium": selenium, "selenium.webdriver": webdriver,
    "selenium.webdriver.chrome": chrome,
    "selenium.webdriver.chrome.options": options_mod,
    "selenium.webdriver.common": common,
    "selenium.webdriver.common.by": by_mod,
    "selenium.webdriver.support": support,
    "selenium.webdriver.support.ui": ui_mod,
    "selenium.webdriver.support.expected_conditions": ec_mod,
}.items():
    sys.modules[name] = module

SOURCE = pathlib.Path(__file__).with_name(
    "wsh_cloud_automation_isolation_manifest_candidate_v3.py"
)
spec = importlib.util.spec_from_file_location("wsh_candidate_v3_1_test", SOURCE)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def make_payload(symbol="NIFTY", timestamp=None, marker=1):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    payload = {
        "symbol": symbol,
        "timestamp": now if timestamp is None else timestamp,
        "pcr": 1.0 + marker / 100.0,
        "chain": {},
    }
    for i in range(8):
        strike = 22000 + i * 50
        payload["chain"][str(strike)] = {}
        for side, token in (("call", 100000 + i), ("put", 200000 + i)):
            payload["chain"][str(strike)][side] = {
                "instrument_token": token,
                "last_price": 100.0 + marker,
                "oi": 1000 + i,
                "oi_change": 0.1,
                "oi_change_quantity": 10,
                "volume": 100,
                "best_buy_price": 99.0,
                "best_sell_price": 101.0,
            }
    return payload


def make_log(payload):
    raw = b"20261103" + gzip.compress(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    payload_text = base64.b64encode(raw).decode("ascii")
    message = {
        "message": {
            "method": "Network.webSocketFrameReceived",
            "params": {"response": {"opcode": 2, "payloadData": payload_text}},
        }
    }
    return {"message": json.dumps(message)}


class FakeClock:
    def __init__(self):
        self.value = 0.0
    def monotonic(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class FakeDriver:
    current_url = (
        "https://web.sensibull.com/option-chain?"
        "view=all&tradingsymbol=nifty&expiry=2026-11-03"
    )
    def __init__(self, logs):
        self.logs = list(logs)
        self.calls = 0
    def get_log(self, name):
        self.calls += 1
        if self.calls == 1:
            return self.logs
        return []


class FakeManifest:
    def __init__(self):
        self.record = None
    def record_dataset(self, *args):
        self.record = args


class WebSocketIdentityFreshnessTests(unittest.TestCase):
    def test_explicit_payload_identity_matches(self):
        found = candidate._wsh_find_payload_identity({"symbol": "NIFTY", "chain": {}})
        self.assertEqual(found, [{"path": "symbol", "value": "NIFTY"}])

    def test_payload_identity_does_not_infer_from_chain(self):
        found = candidate._wsh_find_payload_identity({
            "chain": {"22000": {"call": {"instrument_name": "NIFTY"}}}
        })
        self.assertEqual(found, [])

    def test_underscore_identity_keys_are_normalized_and_recognized(self):
        found = candidate._wsh_find_payload_identity({
            "underlying_symbol": "NIFTY",
            "instrument_name": "NIFTY 50",
            "exchange_symbol": "NIFTY",
            "chain": {},
        })
        self.assertEqual(
            [item["path"] for item in found],
            ["underlying_symbol", "instrument_name", "exchange_symbol"],
        )

    def test_metadata_diagnostic_excludes_chain_and_preserves_candidate_values(self):
        diagnostic = candidate._wsh_collect_payload_metadata_diagnostic({
            "underlying_symbol": "NIFTY",
            "server_time": "2026-11-03T13:00:00Z",
            "opaque_meta": {"sequence": 42},
            "chain": {"22000": {"call": {"instrument_name": "MUST_NOT_LEAK"}}},
        })
        self.assertTrue(diagnostic["chain_subtree_excluded"])
        paths = [item["path"] for item in diagnostic["fields"]]
        self.assertIn("underlying_symbol", paths)
        self.assertIn("opaque_meta.sequence", paths)
        self.assertNotIn("chain.22000.call.instrument_name", paths)
        identity = next(item for item in diagnostic["fields"] if item["key"] == "underlying_symbol")
        self.assertEqual(identity["value"], "NIFTY")

    def test_metadata_diagnostic_is_bounded(self):
        payload = {f"meta_{i}": i for i in range(150)}
        diagnostic = candidate._wsh_collect_payload_metadata_diagnostic(payload, max_fields=25)
        self.assertEqual(diagnostic["field_count"], 25)
        self.assertTrue(diagnostic["truncated"])

    def test_iso_timestamp_parses_as_utc_epoch(self):
        value = "2026-11-03T12:44:21+00:00"
        parsed = candidate._wsh_parse_source_timestamp(value)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed, 1793709861.0, delta=1)

    def test_millisecond_epoch_parses(self):
        epoch = 1793709861000
        self.assertEqual(candidate._wsh_parse_source_timestamp(epoch), 1793709861.0)

    def test_non_timestamp_values_are_rejected(self):
        self.assertIsNone(candidate._wsh_parse_source_timestamp("03 Nov"))
        self.assertEqual(candidate._wsh_find_payload_timestamps({"chain": {"timestamp": 1793709861}}), [])

    def run_capture(self, payloads):
        driver = FakeDriver([make_log(p) for p in payloads])
        manifest = FakeManifest()
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(candidate.time, "monotonic", side_effect=clock.monotonic), \
             mock.patch.object(candidate.time, "sleep", side_effect=clock.sleep):
            result = candidate.capture_sensibull_ws_option_chain(
                driver, "NIFTY", "2026-11-03", manifest, output_folder=temp
            )
        return result, manifest

    def test_two_distinct_matching_fresh_snapshots_can_pass(self):
        result, manifest = self.run_capture([
            make_payload("NIFTY", marker=1),
            make_payload("NIFTY", marker=2),
        ])
        self.assertEqual(result["payload_identity_status"], "PASS")
        self.assertEqual(result["source_freshness_status"], "FRESH")
        self.assertEqual(result["distinct_snapshot_count"], 2)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(manifest.record[4], "VALID")

    def test_missing_payload_identity_remains_partial(self):
        payload = make_payload("NIFTY", marker=1)
        payload.pop("symbol")
        result, manifest = self.run_capture([payload, dict(payload, pcr=1.5)])
        self.assertEqual(result["payload_identity_status"], "UNPROVEN")
        self.assertEqual(result["status"], "PARTIAL")

    def test_mismatched_payload_identity_is_invalid(self):
        result, manifest = self.run_capture([
            make_payload("BANKNIFTY", marker=1),
            make_payload("BANKNIFTY", marker=2),
        ])
        self.assertEqual(result["payload_identity_status"], "MISMATCH")
        self.assertEqual(result["status"], "INVALID")

    def test_stale_source_timestamp_remains_partial(self):
        old = datetime.datetime.now(datetime.timezone.utc).timestamp() - 600
        result, manifest = self.run_capture([
            make_payload("NIFTY", timestamp=old, marker=1),
            make_payload("NIFTY", timestamp=old, marker=2),
        ])
        self.assertEqual(result["source_freshness_status"], "STALE")
        self.assertEqual(result["status"], "PARTIAL")

    def test_single_snapshot_does_not_prove_updates(self):
        result, manifest = self.run_capture([make_payload("NIFTY", marker=1)])
        self.assertEqual(result["distinct_snapshot_count"], 1)
        self.assertEqual(result["successive_snapshot_status"], "SINGLE_DISTINCT_SNAPSHOT")
        self.assertEqual(result["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
