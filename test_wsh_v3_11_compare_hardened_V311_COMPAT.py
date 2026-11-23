import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).with_name("wsh_v3_11_compare_HARDENED_V311_COMPAT.py")

spec = importlib.util.spec_from_file_location("hardened_compare", CANDIDATE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def make_capture(session="A", run_id="run-A", expiry="2026-11-17"):
    frames = []
    for payload, clock in [("alpha", 100), ("beta", 200)]:
        frames.append({
            "payload_data": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "local_receipt_monotonic_ns": clock,
        })

    return {
        "schema": "WSH_V3_11_CAPTURE_1.0",
        "session": session,
        "run_id": run_id,
        "capture_start_local_utc": "2026-11-16T09:30:00+00:00",
        "capture_end_local_utc": "2026-11-16T09:33:00+00:00",
        "frames": frames,
        "frame_count": len(frames),
        "socket_count_observed": 1,
        "manually_verified_underlying": "NIFTY",
        "manually_verified_expiry": expiry,
    }


class HardenedComparisonTests(unittest.TestCase):

    def write_capture(self, folder, name, data):
        path = Path(folder) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_cli(self, captures, folder):
        report = Path(folder) / "report.json"
        result = subprocess.run(
            [sys.executable, str(CANDIDATE),
             *[str(p) for p in captures], "--out", str(report)],
            capture_output=True, text=True
        )
        return result, json.loads(report.read_text(encoding="utf-8"))

    def test_valid_two_session_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.write_capture(folder, "A.json", make_capture("A", "run-A"))
            b = self.write_capture(folder, "B.json", make_capture("B", "run-B"))
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(report["overall_acceptance_gate_pass"])

    def test_valid_three_session_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            captures = [
                self.write_capture(folder, "A.json", make_capture("A", "run-A")),
                self.write_capture(folder, "B.json", make_capture("B", "run-B")),
                self.write_capture(folder, "C.json", make_capture("C_RELOAD", "run-C")),
            ]
            result, report = self.run_cli(captures, folder)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(report["overall_acceptance_gate_pass"])

    def test_tampered_payload_fails_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            a_data = make_capture("A", "run-A")
            a_data["frames"][0]["payload_data"] = "tampered"
            a = self.write_capture(folder, "A.json", a_data)
            b = self.write_capture(folder, "B.json", make_capture("B", "run-B"))
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["automated_integrity_gate_pass"])

    def test_missing_clock_fails_gate(self):
        data = make_capture()
        data["frames"][0]["local_receipt_monotonic_ns"] = None
        summary = mod.summarize(data)
        self.assertEqual(summary["invalid_or_missing_monotonic_clocks"], 1)

    def test_clock_regression_fails_gate(self):
        data = make_capture()
        data["frames"][1]["local_receipt_monotonic_ns"] = 50
        summary = mod.summarize(data)
        self.assertEqual(summary["monotonic_clock_regressions"], 1)

    def test_empty_capture_fails_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            a_data = make_capture("A", "run-A")
            a_data["frames"] = []
            a = self.write_capture(folder, "A.json", a_data)
            b = self.write_capture(folder, "B.json", make_capture("B", "run-B"))
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["nonempty_captures"])

    def test_duplicate_run_ids_fail_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.write_capture(folder, "A.json", make_capture("A", "same-run"))
            b = self.write_capture(folder, "B.json", make_capture("B", "same-run"))
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["run_ids_unique_and_present"])

    def test_identity_mismatch_fails_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.write_capture(folder, "A.json", make_capture("A", "run-A"))
            b = self.write_capture(
                folder, "B.json", make_capture("B", "run-B", "2026-11-24")
            )
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["all_manual_identity_entries_match"])

    def test_invalid_timestamp_fails_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            a_data = make_capture("A", "run-A")
            a_data["capture_end_local_utc"] = "not-a-timestamp"
            a = self.write_capture(folder, "A.json", a_data)
            b = self.write_capture(folder, "B.json", make_capture("B", "run-B"))
            result, report = self.run_cli([a, b], folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["capture_timestamps_valid"])

    def test_malformed_frame_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            data = make_capture()
            data["frames"][0] = "not-a-frame-object"
            path = self.write_capture(folder, "bad.json", data)
            loaded = mod.load(path)
            self.assertEqual(len(loaded["_malformed_frame_indices"]), 1)

    def test_v311_schema_and_nested_identity_are_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            captures = []
            for session, run_id in [("A", "run-A"), ("B", "run-B"), ("C_RELOAD", "run-C")]:
                data = make_capture(session, run_id)
                data["schema"] = "WSH_V3_11_CAPTURE_1.1"
                data.pop("manually_verified_underlying")
                data.pop("manually_verified_expiry")
                data["identity_attestation"] = {
                    "expected_underlying": "NIFTY",
                    "expected_expiry": "23-Nov-2026",
                    "operator_entered_underlying": "NIFTY",
                    "operator_entered_expiry": "23-NOV-2026",
                    "method": "manual_operator_attestation_not_independent_provider_verification",
                }
                captures.append(self.write_capture(folder, f"{session}.json", data))
            result, report = self.run_cli(captures, folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(report["overall_acceptance_gate_pass"])
            self.assertTrue(report["manual_identity_entries_complete_and_consistent"])

    def test_v311_operator_entry_mismatch_fails_identity_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            captures = []
            for session, run_id in [("A", "run-A"), ("B", "run-B")]:
                data = make_capture(session, run_id)
                data["schema"] = "WSH_V3_11_CAPTURE_1.1"
                data.pop("manually_verified_underlying")
                data.pop("manually_verified_expiry")
                data["identity_attestation"] = {
                    "expected_underlying": "NIFTY",
                    "expected_expiry": "23-Nov-2026",
                    "operator_entered_underlying": "BANKNIFTY" if session == "B" else "NIFTY",
                    "operator_entered_expiry": "23-NOV-2026",
                }
                captures.append(self.write_capture(folder, f"{session}.json", data))
            result, report = self.run_cli(captures, folder)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report["manual_identity_entries_complete_and_consistent"])
            self.assertFalse(report["overall_acceptance_gate_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
