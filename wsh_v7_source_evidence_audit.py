#!/usr/bin/env python3
"""Offline/static audit of V7 WebSocket source-evidence instrumentation.

This script never imports the collector, opens a browser, or performs live collection.
It distinguishes code-level instrumentation from actual raw-source evidence in the
preserved baseline archive. Absence of raw frames is reported as a limitation, not
treated as proof that capture or decoding is broken.
"""
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
OUTDIR = ROOT / "WSH_V7_Source_Evidence_Audit"
REPORT = OUTDIR / "source_evidence_audit.json"

REQUIRED_MARKERS = {
    "diagnostic_unpack_helper": "_wsh_unpack_ws_payload_with_diagnostics",
    "frame_evidence_record": "frame_evidence",
    "failure_stage_reporting": "failure_stage",
    "payload_hashing": "sha256",
    "decode_success_counter": "decode_successes",
    "decode_failure_counter": "decode_failures_by_stage",
}

# Historical keys used by earlier evidence outputs. These are searched only in the
# preserved baseline archive; current candidate output fields are checked separately.
RAW_EVIDENCE_KEYS = {
    "source_frame_bytes_base64",
    "selected_frame_sha256",
    "decoded_source_object",
    "frame_evidence",
    "capture_boundary_evidence",
    "payload_data_representation",
    "decoder_selected_bytes_base64",
}

CURRENT_EVIDENCE_MARKERS = {
    "capture_boundary_record": "capture_boundary_evidence",
    "original_payload_representation": "payload_data_representation",
    "original_payload_sha256": "payload_data_sha256_utf8",
    "original_payload_length": "payload_data_length_characters",
    "decoder_selected_bytes_hash": "decoder_selected_bytes_sha256",
    "decoder_selected_bytes_base64": "decoder_selected_bytes_base64",
    "decoded_source_object_preserved": "decoded_source_object",
    "normalized_rows_preserved": "normalized_rows",
    "failure_stage_in_sample_record": '"failure_stage": decode_diag["failure_stage"]',
}

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    report = {
        "audit_schema": "WSH_V7_SOURCE_EVIDENCE_AUDIT_V1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "live_acquisition_performed": False,
        "candidate": {"path": CANDIDATE.name, "exists": CANDIDATE.exists()},
        "baseline_archive": {"path": BASELINE.name, "exists": BASELINE.exists()},
        "instrumentation_checks": {},
        "preserved_archive_inventory": {},
        "interpretation": [],
        "overall_status": "FAIL",
    }
    errors = []

    if not CANDIDATE.exists():
        errors.append(f"Missing instrumented candidate: {CANDIDATE.name}")
    else:
        raw = CANDIDATE.read_bytes()
        source = raw.decode("utf-8")
        compile(source, str(CANDIDATE), "exec")
        tree = ast.parse(source, filename=str(CANDIDATE))
        function_names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        report["candidate"].update({
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "top_level_functions": len(function_names),
        })
        for check, marker in REQUIRED_MARKERS.items():
            if check == "diagnostic_unpack_helper":
                passed = marker in function_names
            else:
                passed = marker in source
            report["instrumentation_checks"][check] = {
                "status": "PASS" if passed else "FAIL",
                "evidence": marker,
            }
            if not passed:
                errors.append(f"Instrumentation marker missing: {marker}")

        # Verify current source-evidence output fields separately from historical archive.
        for check, marker in CURRENT_EVIDENCE_MARKERS.items():
            passed = marker in source
            report["instrumentation_checks"][check] = {
                "status": "PASS" if passed else "FAIL",
                "evidence": marker,
            }
            if not passed:
                errors.append(f"Current source-evidence output marker missing: {marker}")

        # Confirm the original payload representation is recorded only on successful
        # decoder output. This is a known limitation for failed frames, which currently
        # retain hashes/lengths and failure stages rather than the full original payload.
        source_representation_attached_to_decoded = (
            'decoded["capture_boundary_evidence"]' in source
            and '"payload_data_representation": payload if isinstance(payload, str) else None' in source
        )
        report["instrumentation_checks"]["original_payload_preserved_for_successful_decodes"] = {
            "status": "PASS" if source_representation_attached_to_decoded else "FAIL",
            "evidence": "capture_boundary_evidence includes payload_data_representation for decoded frames",
        }
        if not source_representation_attached_to_decoded:
            errors.append("Could not verify original payload representation preservation for successful decoded frames.")
        report["instrumentation_checks"]["full_original_payload_preserved_for_failed_frames"] = {
            "status": "LIMITATION",
            "evidence": "Failed frames record diagnostic metadata but the current candidate does not preserve the full payloadData representation for each failed frame.",
        }

        # Verify that the candidate retains explicit non-production acceptance gates.
        guard_markers = [
            "institutional acceptance",
            "Identity and freshness remain separately gated",
            "raw values preserved",
        ]
        report["instrumentation_checks"]["acceptance_gates_retained"] = {
            "status": "PASS" if all(m.lower() in source.lower() for m in guard_markers) else "REVIEW",
            "evidence": guard_markers,
        }
        if report["instrumentation_checks"]["acceptance_gates_retained"]["status"] != "PASS":
            report["interpretation"].append(
                "Could not confirm every expected acceptance-gate phrase statically; inspect candidate diff manually."
            )

    if not BASELINE.exists():
        errors.append(f"Missing preserved baseline archive: {BASELINE.name}")
    else:
        members = []
        raw_evidence_matches = []
        candidate_payload_files = []
        try:
            with zipfile.ZipFile(BASELINE) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    low = name.lower()
                    members.append({"path": name, "bytes": info.file_size})
                    if any(token in low for token in ("source_evidence", "websocket_frame", "raw_frame", "ws_frame")):
                        candidate_payload_files.append(name)
                    if info.file_size > 10_000_000:
                        continue
                    if not low.endswith((".json", ".txt", ".jsonl")):
                        continue
                    try:
                        payload = zf.read(name)
                        text = payload.decode("utf-8")
                    except (UnicodeDecodeError, OSError, RuntimeError):
                        continue
                    # Search for evidence-key names only. Do not expose payload contents.
                    present = sorted(key for key in RAW_EVIDENCE_KEYS if key in text)
                    if present:
                        raw_evidence_matches.append({
                            "path": name,
                            "evidence_keys_found": present,
                            "content_sha256": sha256_bytes(payload),
                        })
        except (zipfile.BadZipFile, OSError) as exc:
            errors.append(f"Cannot read baseline archive: {type(exc).__name__}: {exc}")
        else:
            report["baseline_archive"].update({
                "sha256": sha256_bytes(BASELINE.read_bytes()),
                "member_count": len(members),
                "members": members,
            })
            report["preserved_archive_inventory"] = {
                "filename_hints_for_raw_source_evidence": candidate_payload_files,
                "structured_evidence_key_matches": raw_evidence_matches,
                "raw_source_frame_evidence_found": bool(raw_evidence_matches),
            }
            if raw_evidence_matches:
                report["interpretation"].append(
                    "The preserved archive contains files with one or more recognized source-evidence key names. This is only a key-presence finding; verify the selected frame bytes and source object are complete and mutually consistent."
                )
            else:
                report["interpretation"].append(
                    "No recognized raw-frame/source-object evidence keys were found in JSON/TXT/JSONL members of the preserved baseline archive. This is a historical evidence gap and does not identify the decoder as the root cause."
                )

    audit_source = pathlib.Path(__file__).read_text(encoding="utf-8")
    audit_tree = ast.parse(audit_source)
    imports_candidate = False
    for node in ast.walk(audit_tree):
        if isinstance(node, ast.Import):
            imports_candidate = imports_candidate or any(
                "wsh_cloud_automation_ws_diagnostics_v7_source_evidence_instrumented" in alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imports_candidate = imports_candidate or (
                node.module is not None
                and "wsh_cloud_automation_ws_diagnostics_v7_source_evidence_instrumented" in node.module
            )
    calls_collector = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "execute_wsh_institutional_pipeline"
        for node in ast.walk(audit_tree)
    )
    no_live_import = not imports_candidate and not calls_collector
    report["instrumentation_checks"]["audit_does_not_execute_collector"] = {
        "status": "PASS" if no_live_import else "FAIL",
        "evidence": "Audit script neither imports candidate module nor invokes collector entrypoint.",
    }
    if not no_live_import:
        errors.append("Audit script contains a candidate import or collector invocation.")

    report["interpretation"].extend([
        "Current candidate evidence fields are checked separately from historical archive contents.",
        "The current candidate preserves the original payloadData representation only for successfully decoded payloads; failed frames retain diagnostic metadata but not their complete payload representation.",
        "This audit checks static instrumentation markers and inventories preserved archive evidence; it does not prove live frame capture correctness.",
        "The candidate is compiled but never imported or executed. No Selenium session, browser, network request, or live market collection is started.",
        "Identity, freshness, quote-integrity, and institutional-acceptance decisions are not relaxed by this audit.",
    ])
    report["errors"] = errors
    report["overall_status"] = "PASS_WITH_LIMITATIONS" if not errors else "FAIL"
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"SOURCE_EVIDENCE_AUDIT_STATUS: {report['overall_status']}")
    print(f"LIVE_ACQUISITION_PERFORMED: {str(report['live_acquisition_performed']).lower()}")
    print(f"INSTRUMENTATION_CHECKS: {sum(x.get('status') == 'PASS' for x in report['instrumentation_checks'].values())} passed")
    print(f"RAW_SOURCE_FRAME_EVIDENCE_FOUND_IN_BASELINE: {str(report.get('preserved_archive_inventory', {}).get('raw_source_frame_evidence_found', False)).lower()}")
    for item in report["interpretation"]:
        print(f"NOTE: {item}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
