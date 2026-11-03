from __future__ import annotations
import glob
import json
import os
from collections import Counter

REPORT_DIR = "WSH_Automated_Reports"
OUT = "WSH_LIVE_VALIDATION_SUMMARY.txt"

lines = [
    "WSH V3 LIVE BROWSER VALIDATION — MANIFEST AUDIT",
    "This report summarizes actual candidate output; it does not convert failed or missing datasets into passes.",
    "",
]
files = sorted(glob.glob(os.path.join(REPORT_DIR, "WSH_Quality_Manifest_*.json")))
if not files:
    lines += ["MANIFEST_STATUS=NOT_FOUND", "No WSH_Quality_Manifest_*.json file was generated.", ""]
else:
    path = files[-1]
    lines += [f"MANIFEST_STATUS=FOUND", f"MANIFEST_FILE={path}"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        datasets = manifest.get("datasets", {})
        lines.append(f"EXECUTION_TIMESTAMP={manifest.get('execution_timestamp', 'UNKNOWN')}")
        lines.append(f"PIPELINE_VERSION={manifest.get('pipeline_version', 'UNKNOWN')}")
        lines.append(f"DATASET_COUNT={len(datasets)}")
        extraction_counts = Counter()
        validation_counts = Counter()
        publication_counts = Counter()
        for item in datasets.values():
            extraction_counts[str(item.get("extraction_status", "MISSING"))] += 1
            validation_counts[str(item.get("validation_status", "MISSING"))] += 1
            publication_counts[str(item.get("publication_status", "MISSING"))] += 1
        lines.append(f"EXTRACTION_COUNTS={dict(sorted(extraction_counts.items()))}")
        lines.append(f"VALIDATION_COUNTS={dict(sorted(validation_counts.items()))}")
        lines.append(f"PUBLICATION_COUNTS={dict(sorted(publication_counts.items()))}")
        lines += ["", "DATASET_DETAIL:"]
        for name, item in sorted(datasets.items()):
            reasons = item.get("failure_reasons", [])
            lines.append(
                f"- {name} | source={item.get('source')} | publication={item.get('publication_status')}"
                f" | extraction={item.get('extraction_status')} | validation={item.get('validation_status')}"
                f" | records={item.get('records_extracted')} | reasons={json.dumps(reasons, ensure_ascii=False)}"
            )
    except Exception as exc:
        lines += ["MANIFEST_STATUS=UNREADABLE", f"MANIFEST_ERROR={type(exc).__name__}: {exc}"]

lines += ["", "PACKAGE_FILES:"]
if os.path.isdir(REPORT_DIR):
    for path in sorted(glob.glob(os.path.join(REPORT_DIR, "*"))):
        if os.path.isfile(path):
            lines.append(f"- {path} ({os.path.getsize(path)} bytes)")
else:
    lines.append(f"- {REPORT_DIR}/ not created")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines), flush=True)
