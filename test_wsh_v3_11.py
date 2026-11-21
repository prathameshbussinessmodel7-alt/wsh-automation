"""Current-contract smoke tests retained under the legacy launcher filename.

The old suite targeted wsh_v3_11_compare.py and Capture 1.0. This suite now
exercises the Capture 1.1 hardened comparator instead.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

COMPARE = Path(__file__).with_name(
    "wsh_v3_11_compare_HARDENED_V311_COMPAT.py"
)


def make_capture(path, session, run_id):
    frames = []
    for payload, clock in (("alpha", 100), ("beta", 200)):
        frames.append({
            "session": session,
            "payload_data": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "local_receipt_monotonic_ns": clock,
        })

    data = {
        "schema": "WSH_V3_11_CAPTURE_1.1",
        "session": session,
        "run_id": run_id,
        "frame_count": len(frames),
        "capture_start_local_utc": "2026-11-18T06:00:00+00:00",
        "capture_end_local_utc": "2026-11-18T06:03:00+00:00",
        "frames": frames,
        "socket_count_observed": 1,
        "identity_attestation": {
            "expected_underlying": "NIFTY",
            "expected_expiry": "23-Nov-2026",
            "operator_entered_underlying": "NIFTY",
            "operator_entered_expiry": "23-NOV-2026",
            "method": "manual_operator_attestation_not_independent_provider_verification",
        },
    }

    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class V311CompatibilityTests(unittest.TestCase):

    def invoke(self, paths, out):
        return subprocess.run(
            [sys.executable, str(COMPARE), *map(str, paths), "--out", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_two_session_capture_1_1_passes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            a = make_capture(td / "a.json", "A", "run-a")
            b = make_capture(td / "b.json", "B", "run-b")

            result = self.invoke([a, b], td / "report.json")

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_frame_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            a = make_capture(td / "a.json", "A", "run-a")
            b = make_capture(td / "b.json", "B", "run-b")

            d = json.loads(a.read_text())
            d["frame_count"] = 99
            a.write_text(json.dumps(d))

            result = self.invoke([a, b], td / "report.json")

            self.assertEqual(result.returncode, 2)
            report = json.loads((td / "report.json").read_text())
            self.assertFalse(report["declared_frame_counts_match"])

    def test_unsupported_schema_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            a = make_capture(td / "a.json", "A", "run-a")

            d = json.loads(a.read_text())
            d["schema"] = "WSH_V3_11_CAPTURE_9.9"
            a.write_text(json.dumps(d))

            b = make_capture(td / "b.json", "B", "run-b")

            result = self.invoke([a, b], td / "report.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unsupported capture schema", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
