#!/usr/bin/env python3
"""Hardened offline integrity and cross-session comparison for WSH V3.11."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from collections import Counter

SUPPORTED_SCHEMAS = {"WSH_V3_11_CAPTURE_1.0", "WSH_V3_11_CAPTURE_1.1"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed
    except (ValueError, TypeError, OverflowError):
        return None


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))

    if not isinstance(data, dict):
        raise ValueError(f"Capture root must be a JSON object: {path}")
    if data.get("schema") not in SUPPORTED_SCHEMAS:
        raise ValueError(f"Unsupported capture schema: {path}")
    if not isinstance(data.get("frames"), list):
        raise ValueError(f"frames is not a list: {path}")

    bad = []
    malformed = []

    for index, frame in enumerate(data["frames"]):
        if not isinstance(frame, dict):
            bad.append(index)
            malformed.append(index)
            continue

        payload = frame.get("payload_data")
        claimed_hash = frame.get("payload_sha256")

        if payload is None or not isinstance(claimed_hash, str) or not HASH_RE.fullmatch(claimed_hash):
            bad.append(index)
            malformed.append(index)
            continue

        actual_hash = hashlib.sha256(
            str(payload).encode("utf-8", errors="replace")
        ).hexdigest()

        if actual_hash != claimed_hash:
            bad.append(index)

    data["_hash_mismatch_indices"] = bad
    data["_malformed_frame_indices"] = malformed
    return data


def extract_identity(data):
    """Return the identity attestation in a version-aware, conservative form."""
    if data.get("schema") == "WSH_V3_11_CAPTURE_1.1":
        attestation = data.get("identity_attestation")
        if not isinstance(attestation, dict):
            return None, None, False
        underlying = attestation.get("expected_underlying")
        expiry = attestation.get("expected_expiry")
        entered_underlying = attestation.get("operator_entered_underlying")
        entered_expiry = attestation.get("operator_entered_expiry")
        complete = all(
            isinstance(value, str) and value.strip()
            for value in (underlying, expiry, entered_underlying, entered_expiry)
        )
        entries_consistent = (
            complete
            and underlying.strip().casefold() == entered_underlying.strip().casefold()
            and expiry.strip().casefold() == entered_expiry.strip().casefold()
        )
        return underlying, expiry, bool(entries_consistent)

    underlying = data.get("manually_verified_underlying")
    expiry = data.get("manually_verified_expiry")
    complete = (
        isinstance(underlying, str) and bool(underlying.strip())
        and isinstance(expiry, str) and bool(expiry.strip())
    )
    return underlying, expiry, bool(complete)


def summarize(data):
    frames = data["frames"]
    hashes = [
        frame.get("payload_sha256")
        for frame in frames
        if isinstance(frame, dict)
    ]
    counts = Counter(hashes)

    clocks = [
        frame.get("local_receipt_monotonic_ns")
        if isinstance(frame, dict) else None
        for frame in frames
    ]

    invalid_clocks = sum(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in clocks
    )

    regressions = sum(
        isinstance(a, int) and not isinstance(a, bool)
        and isinstance(b, int) and not isinstance(b, bool)
        and b < a
        for a, b in zip(clocks, clocks[1:])
    )

    start_raw = data.get("capture_start_local_utc")
    end_raw = data.get("capture_end_local_utc")
    start = parse_timestamp(start_raw)
    end = parse_timestamp(end_raw)
    timestamp_valid = bool(start and end and end > start)

    identity_underlying, identity_expiry, identity_entries_consistent = extract_identity(data)

    declared_frame_count = data.get("frame_count")
    declared_frame_count_valid = (
        isinstance(declared_frame_count, int)
        and not isinstance(declared_frame_count, bool)
        and declared_frame_count >= 0
    )
    declared_frame_count_matches = (
        declared_frame_count == len(frames)
        if declared_frame_count_valid else None
    )

    return {
        "session": data.get("session"),
        "run_id": data.get("run_id"),
        "frames": len(frames),
        "declared_frame_count": declared_frame_count,
        "declared_frame_count_valid": declared_frame_count_valid,
        "declared_frame_count_matches": declared_frame_count_matches,
        "unique_payload_hashes": len(set(hashes)),
        "duplicate_frames": sum(count - 1 for count in counts.values()),
        "hash_integrity_failures": len(data.get("_hash_mismatch_indices", [])),
        "malformed_frames": len(data.get("_malformed_frame_indices", [])),
        "invalid_or_missing_monotonic_clocks": invalid_clocks,
        "monotonic_clock_regressions": regressions,
        "capture_timestamps_valid": timestamp_valid,
        "capture_start_local_utc": start_raw,
        "capture_end_local_utc": end_raw,
        "socket_count_observed": data.get("socket_count_observed"),
        "identity_underlying": identity_underlying,
        "identity_expiry": identity_expiry,
        "identity_entries_consistent": identity_entries_consistent,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--out", default="WSH_V3_11_Cross_Session_Report.json")
    args = parser.parse_args()

    if len(args.captures) not in (2, 3):
        raise SystemExit("Provide exactly two or three capture JSON files (A/B or A/B/C).")

    try:
        data = [load(path) for path in args.captures]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    summaries = [summarize(item) for item in data]
    labels = [item.get("session") for item in data]

    expected = ["A", "B"] if len(data) == 2 else ["A", "B", "C_RELOAD"]
    labels_valid = labels == expected

    run_ids = [item.get("run_id") for item in data]
    run_ids_valid = (
        all(isinstance(value, str) and value.strip() for value in run_ids)
        and len(set(run_ids)) == len(run_ids)
    )

    identities = [
        (summary["identity_underlying"], summary["identity_expiry"])
        for summary in summaries
    ]
    identities_complete = all(
        summary["identity_entries_consistent"]
        and isinstance(underlying, str) and underlying.strip()
        and isinstance(expiry, str) and expiry.strip()
        for summary, (underlying, expiry) in zip(summaries, identities)
    )
    identities_match = identities_complete and len({
        (underlying.strip().casefold(), expiry.strip().casefold())
        for underlying, expiry in identities
    }) == 1

    nonempty_captures = all(item["frames"] for item in summaries)
    hashes_pass = all(
        item["hash_integrity_failures"] == 0
        and item["malformed_frames"] == 0
        for item in summaries
    )
    clocks_pass = all(
        item["invalid_or_missing_monotonic_clocks"] == 0
        and item["monotonic_clock_regressions"] == 0
        for item in summaries
    )
    timestamps_pass = all(item["capture_timestamps_valid"] for item in summaries)
    frame_counts_pass = all(
        summary["declared_frame_count_matches"] is True
        if item.get("schema") == "WSH_V3_11_CAPTURE_1.1"
        else summary["declared_frame_count_matches"] is not False
        for item, summary in zip(data, summaries)
    )

    common = set.intersection(*(
        {
            frame.get("payload_sha256")
            for frame in item["frames"]
            if isinstance(frame, dict)
            and isinstance(frame.get("payload_sha256"), str)
        }
        for item in data
    ))

    pairs = []
    for i in range(len(data)):
        for j in range(i + 1, len(data)):
            left = {
                frame.get("payload_sha256")
                for frame in data[i]["frames"]
                if isinstance(frame, dict)
                and isinstance(frame.get("payload_sha256"), str)
            }
            right = {
                frame.get("payload_sha256")
                for frame in data[j]["frames"]
                if isinstance(frame, dict)
                and isinstance(frame.get("payload_sha256"), str)
            }
            shared = left & right
            pairs.append({
                "session_a": labels[i],
                "session_b": labels[j],
                "shared_unique_hashes": len(shared),
                "shared_fraction_a": round(len(shared) / len(left), 6) if left else None,
                "shared_fraction_b": round(len(shared) / len(right), 6) if right else None,
            })

    automated_integrity_pass = hashes_pass and clocks_pass and nonempty_captures
    acceptance_pass = (
        automated_integrity_pass
        and labels_valid
        and run_ids_valid
        and identities_match
        and timestamps_pass
        and frame_counts_pass
    )

    report = {
        "schema": "WSH_V3_11_CROSS_SESSION_REPORT_3.0",
        "sessions": summaries,
        "pairwise_comparisons": pairs,
        "session_labels_valid": labels_valid,
        "run_ids_unique_and_present": run_ids_valid,
        "manual_identity_entries_complete_and_consistent": identities_complete,
        "all_manual_identity_entries_match": identities_match,
        "capture_timestamps_valid": timestamps_pass,
        "declared_frame_counts_match": frame_counts_pass,
        "nonempty_captures": nonempty_captures,
        "shared_hashes_across_all_sessions": len(common),
        "automated_integrity_gate_pass": automated_integrity_pass,
        "overall_acceptance_gate_pass": acceptance_pass,
        "interpretation": (
            "Manual underlying/expiry entries are operator attestations, not "
            "machine-verified identity. Payload hash overlap is descriptive only; "
            "it does not prove freshness, provider sequence continuity, exchange "
            "authenticity or source timestamps."
        ),
        "limitations": [
            "CDP payloadData is a browser-exposed representation, not guaranteed wire bytes.",
            "Local UTC/monotonic clocks measure browser-side observation only.",
            "CDP log timestamp semantics are browser instrumentation, not provider time.",
            "A successful integrity gate does not establish source authenticity or correct instrument selection.",
            "A manual identity match does not independently verify the selected instrument or expiry.",
        ],
    }

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if not acceptance_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
