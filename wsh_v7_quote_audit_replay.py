#!/usr/bin/env python3
"""Offline v7 quote-audit replay against the preserved WSH baseline ZIP.

This does not launch Selenium or change source payloads. It writes a JSON audit
report while retaining identity and freshness as unverified unless proven by
independent source evidence.
"""
from __future__ import annotations
import importlib.util
import json
import pathlib
import sys
import zipfile
from datetime import datetime, timezone

CANDIDATE = pathlib.Path("wsh_cloud_automation_ws_diagnostics_v6.py")
BASELINE_ZIP = pathlib.Path("WSH-collector-39948804037.zip")
OUTDIR = pathlib.Path("WSH_V7_Audit_Evidence")

def load_candidate():
    spec = importlib.util.spec_from_file_location("wsh_candidate_v7", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load candidate module: {CANDIDATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("_wsh_quote_and_field_diagnostics",):
        if not hasattr(module, name):
            raise RuntimeError(f"Candidate missing required diagnostic function: {name}")
    return module

def main():
    if not CANDIDATE.is_file():
        raise FileNotFoundError(f"Candidate not found: {CANDIDATE}")
    if not BASELINE_ZIP.is_file():
        raise FileNotFoundError(f"Baseline ZIP not found: {BASELINE_ZIP}")

    module = load_candidate()
    report = {
        "audit_schema_version": "WSH_V7_OFFLINE_REPLAY_1",
        "audit_created_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_file": CANDIDATE.name,
        "baseline_archive": BASELINE_ZIP.name,
        "mode": "offline_preserved_payload_replay",
        "live_acquisition_performed": False,
        "identity_status": "UNVERIFIED",
        "freshness_status": "UNVERIFIED",
        "institutional_acceptance": "NOT_GRANTED",
        "datasets": {},
        "errors": [],
    }

    with zipfile.ZipFile(BASELINE_ZIP) as archive:
        payload_names = [
            name for name in archive.namelist()
            if pathlib.PurePosixPath(name).name.startswith("WSH_Sensibull_WS_")
            and name.endswith(".json")
        ]
        if not payload_names:
            raise RuntimeError("No WSH_Sensibull_WS_*.json payloads found in baseline ZIP")

        for name in sorted(payload_names):
            try:
                payload = json.loads(archive.read(name).decode("utf-8"))
                rows = payload.get("rows")
                if not isinstance(rows, list):
                    raise ValueError("payload 'rows' is not a list")
                findings = module._wsh_quote_and_field_diagnostics(rows)
                dataset = str(payload.get("symbol") or pathlib.Path(name).name)
                report["datasets"][dataset] = {
                    "source_file": pathlib.Path(name).name,
                    "requested_expiry": payload.get("requested_expiry"),
                    "captured_at_utc": payload.get("captured_at_utc"),
                    "route_identity_check": payload.get("route_identity_check"),
                    "status": payload.get("status"),
                    "rows_examined": len(rows),
                    "diagnostics": findings,
                    "identity_status": "UNVERIFIED",
                    "freshness_status": "UNVERIFIED",
                    "institutional_acceptance": "NOT_GRANTED",
                }
            except Exception as exc:
                report["errors"].append({"source_file": pathlib.Path(name).name,
                                         "error": f"{type(exc).__name__}: {exc}"})

    OUTDIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTDIR / "WSH_V7_Quote_Audit_Report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "audit_schema_version": report["audit_schema_version"],
        "datasets_audited": len(report["datasets"]),
        "dataset_errors": len(report["errors"]),
        "live_acquisition_performed": False,
        "identity_status": "UNVERIFIED",
        "freshness_status": "UNVERIFIED",
        "institutional_acceptance": "NOT_GRANTED",
        "report": str(report_path),
    }
    (OUTDIR / "WSH_V7_Quote_Audit_Summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print("WSH_V7_OFFLINE_REPLAY_COMPLETE")
    print(json.dumps(summary, indent=2))
    if report["errors"] or len(report["datasets"]) != 4:
        print("AUDIT_RESULT=PARTIAL_OR_FAILED")
        sys.exit(2)
    print("AUDIT_RESULT=COMPLETE_OFFLINE_ONLY")

if __name__ == "__main__":
    main()
