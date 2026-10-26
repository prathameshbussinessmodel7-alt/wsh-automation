
#!/usr/bin/env python3
"""Offline source-evidence audit. Never runs the WSH collector."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
CANDIDATE = ROOT / "wsh_cloud_automation_ws_diagnostics_v7_source_evidence_instrumented.py"
BASELINE = ROOT / "WSH-collector-39948804037.zip"
OUT = ROOT / "WSH_V7_Source_Evidence_Audit"
REPORT = OUT / "source_evidence_audit.json"

MARKERS = {
    "diagnostic_decoder_helper": "_wsh_unpack_ws_payload_with_diagnostics",
    "frame_evidence": "frame_evidence",
    "failure_stage": "failure_stage",
    "sha256_hashing": "sha256",
    "decode_success_counter": "decode_successes",
    "decode_failure_counter": "decode_failures_by_stage",
}

RAW_KEYS = {
    "source_frame_bytes_base64",
    "selected_frame_sha256",
    "decoded_source_object",
    "raw_frame_bytes",
    "source_evidence_file",
}


def main():
    OUT.mkdir(exist_ok=True)
    report = {
        "schema": "WSH_V7_SOURCE_EVIDENCE_AUDIT_V1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "live_acquisition_performed": False,
        "candidate": {"name": CANDIDATE.name, "exists": CANDIDATE.is_file()},
        "baseline_archive": {"name": BASELINE.name, "exists": BASELINE.is_file()},
        "instrumentation_checks": {},
        "archive_inventory": {},
        "interpretation": [],
    }
    failures = []

    if not CANDIDATE.is_file():
        failures.append("Instrumented candidate file is missing.")
    else:
        raw = CANDIDATE.read_bytes()
        source = raw.decode("utf-8")
        compile(source, str(CANDIDATE), "exec")
        tree = ast.parse(source, filename=str(CANDIDATE))
        funcs = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        report["candidate"].update({
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "syntax": "PASS",
        })
        for name, marker in MARKERS.items():
            passed = (
                marker in funcs
                if name == "diagnostic_decoder_helper"
                else marker in source
            )
            report["instrumentation_checks"][name] = {
                "status": "PASS" if passed else "FAIL",
                "marker": marker,
            }
            if not passed:
                failures.append(f"Missing instrumentation marker: {marker}")

    raw_hits = []
    members = 0
    json_files = 0
    inventory_error = None

    if BASELINE.is_file():
        try:
            with zipfile.ZipFile(BASELINE) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    members += 1
                    if not info.filename.lower().endswith(".json"):
                        continue
                    json_files += 1
                    try:
                        obj = json.loads(archive.read(info).decode("utf-8"))
                    except Exception:
                        continue

                    stack = [(info.filename, obj)]
                    while stack:
                        path, value = stack.pop()
                        if isinstance(value, dict):
                            for key, child in value.items():
                                if key in RAW_KEYS:
                                    raw_hits.append({"file": path, "key": key})
                                stack.append((path + "." + str(key), child))
                        elif isinstance(value, list):
                            stack.extend((path + "[]", item) for item in value)

            report["archive_inventory"] = {
                "files_examined": members,
                "json_files_examined": json_files,
                "recognized_raw_source_evidence_keys": raw_hits,
                "raw_source_evidence_found": bool(raw_hits),
            }
        except Exception as exc:
            inventory_error = f"{type(exc).__name__}: {exc}"
    else:
        inventory_error = "Preserved baseline ZIP is missing; inventory not performed."

    if inventory_error:
        report["archive_inventory"] = {"error": inventory_error}
        failures.append(inventory_error)

    if raw_hits:
        report["interpretation"].append(
            "Recognized source-evidence keys were found. Inspect the matching records "
            "before concluding that complete original frames are preserved."
        )
    else:
        report["interpretation"].append(
            "No recognized raw source-evidence keys were found in JSON files in the "
            "preserved archive. This is an evidence gap, not proof of a decoder fault."
        )

    report["interpretation"].append(
        "Static/offline audit only. It does not prove live WebSocket capture, source "
        "identity, quote freshness, or institutional acceptance."
    )
    report["overall_status"] = "PASS_WITH_LIMITATIONS" if not failures else "FAIL"
    report["errors"] = failures
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("SOURCE_EVIDENCE_AUDIT_STATUS:", report["overall_status"])
    print("LIVE_ACQUISITION_PERFORMED: false")
    print("INSTRUMENTATION_CHECKS:", json.dumps(report["instrumentation_checks"], sort_keys=True))
    print("ARCHIVE_INVENTORY:", json.dumps(report["archive_inventory"], sort_keys=True))
    print("REPORT:", REPORT.as_posix())

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
