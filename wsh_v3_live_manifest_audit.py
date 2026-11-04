from __future__ import annotations

import glob
import json
import os
from collections import Counter

REPORT_DIR = "WSH_Automated_Reports"
OUT = "WSH_LIVE_VALIDATION_SUMMARY.txt"
CHECKPOINT_NAME = "WSH_Quality_Manifest_CHECKPOINT.json"


def select_manifest(report_dir: str) -> tuple[str | None, str]:
    """Prefer the final timestamped manifest; use checkpoint only as fallback."""
    final_manifests = sorted(
        glob.glob(os.path.join(report_dir, "WSH_Quality_Manifest_????????_??????_??????.json"))
    )
    if final_manifests:
        return final_manifests[-1], "FINAL_TIMESTAMPED_MANIFEST"

    checkpoint = os.path.join(report_dir, CHECKPOINT_NAME)
    if os.path.isfile(checkpoint):
        return checkpoint, "CHECKPOINT_FALLBACK_ONLY"

    return None, "NOT_FOUND"


lines = [
    "WSH V3 LIVE BROWSER VALIDATION — MANIFEST AUDIT",
    "This report summarizes actual candidate output; it does not convert failed or missing datasets into passes.",
    "Manifest selection prefers the final timestamped manifest; checkpoint is fallback only.",
    "",
]

manifest_path, selection_status = select_manifest(REPORT_DIR)
lines.append(f"MANIFEST_SELECTION={selection_status}")

if manifest_path is None:
    lines += ["MANIFEST_STATUS=NOT_FOUND", "No final manifest or checkpoint was generated.", ""]
else:
    lines += [f"MANIFEST_STATUS=FOUND", f"MANIFEST_FILE={manifest_path}"]
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        datasets = manifest.get("datasets", {})
        if not isinstance(datasets, dict):
            raise TypeError("manifest.datasets is not a JSON object")

        lines.append(f"EXECUTION_TIMESTAMP={manifest.get('execution_timestamp', 'UNKNOWN')}")
        lines.append(f"PIPELINE_VERSION={manifest.get('pipeline_version', 'UNKNOWN')}")
        lines.append(f"DATASET_COUNT={len(datasets)}")

        extraction_counts = Counter()
        validation_counts = Counter()
        publication_counts = Counter()
        for item in datasets.values():
            if not isinstance(item, dict):
                extraction_counts["MALFORMED_RECORD"] += 1
                validation_counts["MALFORMED_RECORD"] += 1
                publication_counts["MALFORMED_RECORD"] += 1
                continue
            extraction_counts[str(item.get("extraction_status", "MISSING"))] += 1
            validation_counts[str(item.get("validation_status", "MISSING"))] += 1
            publication_counts[str(item.get("publication_status", "MISSING"))] += 1

        lines.append(f"EXTRACTION_COUNTS={dict(sorted(extraction_counts.items()))}")
        lines.append(f"VALIDATION_COUNTS={dict(sorted(validation_counts.items()))}")
        lines.append(f"PUBLICATION_COUNTS={dict(sorted(publication_counts.items()))}")
        lines += ["", "DATASET_DETAIL:"]

        for name, item in sorted(datasets.items()):
            if not isinstance(item, dict):
                lines.append(f"- {name} | MALFORMED_RECORD={json.dumps(item, ensure_ascii=False)}")
                continue
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

with open(OUT, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")

print("\n".join(lines), flush=True)
