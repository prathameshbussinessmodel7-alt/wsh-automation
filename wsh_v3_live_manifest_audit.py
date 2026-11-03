
from __future__ import annotations

import glob
import json
import os
from collections import Counter

REPORT_DIR = "WSH_Automated_Reports"
OUT = "WSH_LIVE_VALIDATION_SUMMARY.txt"
CHECKPOINT_NAME = "WSH_Quality_Manifest_CHECKPOINT.json"
START_TIME_FILE = "WSH_LIVE_RUN_START_EPOCH.txt"


def get_run_start_epoch() -> float | None:
    """Read the timestamp recorded by the current GitHub Actions run."""
    try:
        with open(START_TIME_FILE, "r", encoding="utf-8") as handle:
            return float(handle.read().strip())
    except (OSError, ValueError):
        return None


def select_current_run_manifest(
    report_dir: str,
    run_start_epoch: float | None,
) -> tuple[str | None, str]:
    """Select only a manifest created during the current workflow run."""
    if run_start_epoch is None:
        return None, "RUN_TIMESTAMP_MISSING"

    final_manifests = sorted(
        glob.glob(
            os.path.join(
                report_dir,
                "WSH_Quality_Manifest_????????_??????_??????.json",
            )
        ),
        key=os.path.getmtime,
        reverse=True,
    )

    for path in final_manifests:
        if os.path.getmtime(path) >= run_start_epoch:
            return path, "CURRENT_RUN_FINAL_MANIFEST"

    checkpoint = os.path.join(report_dir, CHECKPOINT_NAME)

    if (
        os.path.isfile(checkpoint)
        and os.path.getmtime(checkpoint) >= run_start_epoch
    ):
        return checkpoint, "CURRENT_RUN_CHECKPOINT_FALLBACK"

    return None, "CURRENT_RUN_MANIFEST_NOT_FOUND"


lines = [
    "WSH V3 LIVE BROWSER VALIDATION - MANIFEST AUDIT",
    "Only manifests created during the current workflow run are eligible.",
    "Older manifests are never accepted as current-run evidence.",
    "",
]

run_start_epoch = get_run_start_epoch()

if run_start_epoch is None:
    lines.append("RUN_TIMESTAMP_STATUS=MISSING_OR_INVALID")
else:
    lines.append(f"RUN_START_EPOCH={run_start_epoch}")
    lines.append("RUN_TIMESTAMP_STATUS=VALID")

manifest_path, selection_status = select_current_run_manifest(
    REPORT_DIR,
    run_start_epoch,
)

lines.append(f"MANIFEST_SELECTION={selection_status}")

if manifest_path is None:
    lines += [
        "MANIFEST_STATUS=NOT_FOUND",
        "No eligible current-run final manifest or checkpoint was found.",
        "",
    ]
else:
    lines += [
        "MANIFEST_STATUS=FOUND",
        f"MANIFEST_FILE={manifest_path}",
        f"MANIFEST_MODIFIED_EPOCH={os.path.getmtime(manifest_path)}",
    ]

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        if not isinstance(manifest, dict):
            raise TypeError("Manifest root is not a JSON object")

        datasets = manifest.get("datasets", {})

        if not isinstance(datasets, dict):
            raise TypeError("manifest.datasets is not a JSON object")

        lines.append(
            f"EXECUTION_TIMESTAMP={manifest.get('execution_timestamp', 'UNKNOWN')}"
        )
        lines.append(
            f"PIPELINE_VERSION={manifest.get('pipeline_version', 'UNKNOWN')}"
        )
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

            extraction_counts[
                str(item.get("extraction_status", "MISSING"))
            ] += 1
            validation_counts[
                str(item.get("validation_status", "MISSING"))
            ] += 1
            publication_counts[
                str(item.get("publication_status", "MISSING"))
            ] += 1

        lines.append(
            f"EXTRACTION_COUNTS={dict(sorted(extraction_counts.items()))}"
        )
        lines.append(
            f"VALIDATION_COUNTS={dict(sorted(validation_counts.items()))}"
        )
        lines.append(
            f"PUBLICATION_COUNTS={dict(sorted(publication_counts.items()))}"
        )
        lines += ["", "DATASET_DETAIL:"]

        for name, item in sorted(datasets.items()):
            if not isinstance(item, dict):
                lines.append(
                    f"- {name} | MALFORMED_RECORD="
                    f"{json.dumps(item, ensure_ascii=False)}"
                )
                continue

            reasons = item.get("failure_reasons", [])

            lines.append(
                f"- {name}"
                f" | source={item.get('source')}"
                f" | publication={item.get('publication_status')}"
                f" | extraction={item.get('extraction_status')}"
                f" | validation={item.get('validation_status')}"
                f" | records={item.get('records_extracted')}"
                f" | reasons={json.dumps(reasons, ensure_ascii=False)}"
            )

    except Exception as exc:
        lines += [
            "MANIFEST_STATUS=UNREADABLE",
            f"MANIFEST_ERROR={type(exc).__name__}: {exc}",
        ]

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
