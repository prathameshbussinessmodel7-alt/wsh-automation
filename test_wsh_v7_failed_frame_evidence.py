#!/usr/bin/env python3
"""Isolated tests for bounded failed-frame evidence; never imports/runs collector."""
import ast
import hashlib
from pathlib import Path

CANDIDATE = Path("wsh_cloud_automation_ws_diagnostics_v7_source_evidence_instrumented.py")
source = CANDIDATE.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(CANDIDATE))
node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_wsh_record_failed_frame_evidence"), None)
assert node is not None, "failed-frame evidence helper missing"
mod = ast.Module(body=[node], type_ignores=[])
ast.fix_missing_locations(mod)
ns = {"hashlib": hashlib}
exec(compile(mod, "<isolated-failed-frame-evidence-helper>", "exec"), ns)
record = ns["_wsh_record_failed_frame_evidence"]

def diag(stage="JSON_PARSE"):
    return {"failure_stage": stage, "failure_reason": f"synthetic {stage}", "candidate_representations_tried": ["base64", "hex"]}

# 1: malformed payload evidence includes exact length/hash and stage.
evidence = {"websocket_events": 7, "failed_frame_samples": [], "failed_frame_samples_omitted": 0}
payload = "malformed-frame"
record(evidence, payload, 2, diag("GZIP_SIGNATURE_NOT_FOUND"))
s = evidence["failed_frame_samples"][0]
assert s["payload_data_length_characters"] == len(payload)
assert s["payload_data_utf8_length_bytes"] == len(payload.encode("utf-8"))
assert s["payload_data_sha256_utf8"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
assert s["payload_data_representation_sample"] == payload
assert s["payload_data_representation_complete"] is True and s["payload_data_truncated"] is False
assert s["decoder_failure_stage"] == "GZIP_SIGNATURE_NOT_FOUND"
assert s["decoder_failure_reason"] == "synthetic GZIP_SIGNATURE_NOT_FOUND"
print("PASS: malformed payload records length/hash/original sample/failure stage")

# 2: oversized payload is bounded to 16 KiB and explicitly marked truncated.
evidence = {"failed_frame_samples": [], "failed_frame_samples_omitted": 0}
payload = "X" * (16 * 1024 + 37)
record(evidence, payload, 2, diag("JSON_PARSE"))
s = evidence["failed_frame_samples"][0]
assert len(s["payload_data_representation_sample"]) == 16 * 1024
assert s["payload_data_length_characters"] == len(payload)
assert s["payload_data_sha256_utf8"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
assert s["payload_data_representation_complete"] is False and s["payload_data_truncated"] is True
print("PASS: oversized payload sample capped at 16 KiB with exact hash/length and truncation flags")

# 3: non-string payload is handled without throwing or fabricating payload data.
for value in (None, b"binary-frame", 123):
    evidence = {"failed_frame_samples": [], "failed_frame_samples_omitted": 0}
    record(evidence, value, 2, diag("PAYLOAD_TYPE_OR_EMPTY"))
    s = evidence["failed_frame_samples"][0]
    assert s["payload_data_type"] == type(value).__name__
    assert s["payload_data_representation_sample"] is None
    assert s["payload_data_sha256_utf8"] is None
    assert s["payload_data_representation_complete"] is False
print("PASS: None/bytes/integer payloads handled without fabricated representation")

# 4: sample count is capped at three; additional failures are counted as omitted.
evidence = {"failed_frame_samples": [], "failed_frame_samples_omitted": 0}
for i in range(5):
    record(evidence, f"bad-{i}", 2, diag())
assert len(evidence["failed_frame_samples"]) == 3
assert evidence["failed_frame_samples_omitted"] == 2
print("PASS: failed-frame samples capped at 3 and omitted count increments")

# 5: exact cap boundary is complete, not truncated.
evidence = {"failed_frame_samples": [], "failed_frame_samples_omitted": 0}
payload = "Y" * (16 * 1024)
record(evidence, payload, 2, diag())
s = evidence["failed_frame_samples"][0]
assert len(s["payload_data_representation_sample"]) == 16 * 1024
assert s["payload_data_representation_complete"] is True and s["payload_data_truncated"] is False
print("PASS: payload exactly at 16 KiB boundary remains complete")

print("FAILED_FRAME_EVIDENCE_TESTS_PASS: 5 cases; collector not imported; no browser/network/live collection.")
