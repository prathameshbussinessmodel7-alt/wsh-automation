
"""WSH R7.2 — source provenance and publication-date validator.

Standalone validation module. Does not modify or execute the R6 collector.
Does not infer publication dates from capture timestamps or weekdays.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


VERSION = "WSH_R7.2"

IST = ZoneInfo("Asia/Kolkata")

EXPECTED_SOURCES = {
    "NSE": {"nseindia.com", "www.nseindia.com"},
    "CCIL": {"ccilindia.com", "www.ccilindia.com"},
}

# Only explicit source-provided date fields are eligible.
PUBLICATION_DATE_FIELDS = (
    "publication_date",
    "published_date",
    "published_at",
    "publication_datetime",
)

AS_OF_DATE_FIELDS = (
    "source_as_of_date",
    "as_of_date",
    "market_date",
    "report_date",
    "trading_date",
)

CAPTURE_TIME_FIELDS = (
    "captured_at",
    "capture_timestamp",
    "execution_timestamp",
)


def parse_datetime(value):
    """Parse an explicit ISO-like timestamp. Return None if invalid."""
    if not isinstance(value, str) or not value.strip():
        return None

    candidate = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed


def parse_date(value):
    """Accept explicit ISO calendar dates only."""
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

    return parsed.isoformat()


def sha256_file(path):
    """Calculate SHA-256 for an evidence file."""
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def source_identity(source_name, url):
    """Validate source domain against the configured source registry."""
    expected = EXPECTED_SOURCES.get(str(source_name).upper())

    if not expected:
        return {
            "status": "UNKNOWN",
            "reason": "Source name is not in the configured source registry.",
            "host": None,
        }

    if not isinstance(url, str) or not url.strip():
        return {
            "status": "UNKNOWN",
            "reason": "Source URL is missing.",
            "host": None,
        }

    parsed = None
    host = ""

    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
    except Exception:
        parsed = None
        host = ""

    if parsed is None or parsed.scheme not in ("http", "https") or not host:
        return {
            "status": "FAIL",
            "reason": "Source URL is malformed or uses an unsupported scheme.",
            "host": host or None,
        }

    if host not in expected:
        return {
            "status": "FAIL",
            "reason": (
                "Source hostname does not match the configured source registry."
            ),
            "host": host,
        }

    return {
        "status": "PASS",
        "reason": "Source hostname matches the configured source registry.",
        "host": host,
    }


def first_explicit_value(metadata, fields):
    """Return the first non-empty explicit field and its field name."""
    for field in fields:
        value = metadata.get(field)

        if value not in (None, ""):
            return field, value

    return None, None


def validate_date_field(metadata, fields, label):
    """Validate a source-provided date without inferring missing values."""
    field, value = first_explicit_value(metadata, fields)

    if field is None:
        return {
            "status": "UNKNOWN",
            "date": None,
            "evidence_field": None,
            "reason": f"No explicit {label} field was captured.",
        }

    normalized = parse_date(value)

    if normalized is None:
        return {
            "status": "UNKNOWN",
            "date": None,
            "evidence_field": field,
            "reason": (
                f"{label} field exists but is not a valid YYYY-MM-DD date."
            ),
            "raw_value": str(value),
        }

    return {
        "status": "PASS",
        "date": normalized,
        "evidence_field": field,
        "reason": f"{label} accepted from explicit source metadata.",
    }


def validate_capture_time(metadata):
    """Normalize an explicit timezone-aware capture timestamp."""
    field, value = first_explicit_value(metadata, CAPTURE_TIME_FIELDS)

    if field is None:
        return {
            "status": "UNKNOWN",
            "source_field": None,
            "utc": None,
            "ist": None,
            "reason": "Capture timestamp is absent.",
        }

    parsed = parse_datetime(value)

    if parsed is None:
        return {
            "status": "UNKNOWN",
            "source_field": field,
            "utc": None,
            "ist": None,
            "reason": (
                "Capture timestamp is invalid or has no timezone offset."
            ),
            "raw_value": str(value),
        }

    return {
        "status": "PASS",
        "source_field": field,
        "utc": parsed.astimezone(timezone.utc).isoformat(),
        "ist": parsed.astimezone(IST).isoformat(),
        "reason": "Capture timestamp normalized to UTC and IST.",
    }


def validate_record_dates(records):
    """Check dates actually present in extracted records."""
    if not isinstance(records, list) or not records:
        return {
            "status": "UNKNOWN",
            "dates": [],
            "reason": (
                "No extracted records supplied for date consistency validation."
            ),
        }

    parsed_dates = []
    missing_count = 0
    invalid_count = 0

    for record in records:
        if not isinstance(record, dict):
            invalid_count += 1
            continue

        raw_date = record.get("date")

        if raw_date in (None, ""):
            missing_count += 1
            continue

        normalized = parse_date(raw_date)

        if normalized is None:
            invalid_count += 1
        else:
            parsed_dates.append(normalized)

    unique_dates = sorted(set(parsed_dates))

    if invalid_count or missing_count:
        return {
            "status": "PARTIAL",
            "dates": unique_dates,
            "missing_records": missing_count,
            "invalid_records": invalid_count,
            "reason": "One or more records have missing or invalid dates.",
        }

    if len(unique_dates) == 1:
        return {
            "status": "PASS",
            "dates": unique_dates,
            "reason": "All extracted records carry the same valid date.",
        }

    if len(unique_dates) > 1:
        return {
            "status": "FAIL",
            "dates": unique_dates,
            "reason": "Extracted records contain conflicting dates.",
        }

    return {
        "status": "UNKNOWN",
        "dates": [],
        "reason": "No valid record dates were found.",
    }


def inspect_evidence(metadata, source_name, records=None):
    """Validate provenance metadata without changing original evidence."""
    metadata = metadata if isinstance(metadata, dict) else {}

    url = (
        metadata.get("endpoint")
        or metadata.get("url")
        or metadata.get("page_url")
        or metadata.get("source_url")
    )

    identity = source_identity(source_name, url)

    publication = validate_date_field(
        metadata, PUBLICATION_DATE_FIELDS, "publication date"
    )

    as_of = validate_date_field(
        metadata, AS_OF_DATE_FIELDS, "market/as-of date"
    )

    capture = validate_capture_time(metadata)

    record_dates = validate_record_dates(records)

    if publication["status"] == "FAIL" or as_of["status"] == "FAIL":
        overall_date_status = "FAIL"
    elif (
        publication["status"] == "PASS"
        or as_of["status"] == "PASS"
    ):
        overall_date_status = "PARTIAL"
    else:
        overall_date_status = "UNKNOWN"

    return {
        "validator_version": VERSION,
        "source": str(source_name).upper(),
        "source_url": url,
        "source_identity": identity,
        "capture_timestamp": capture,
        "publication_date": publication,
        "market_as_of_date": as_of,
        "record_date_consistency": record_dates,
        "date_validation_status": overall_date_status,
        "inference_policy": {
            "capture_time_used_as_publication_date": False,
            "weekday_used_to_infer_publication": False,
            "record_date_used_as_publication_date": False,
        },
    }


def audit_metadata_file(path, source_name, output_path=None):
    """Read one metadata JSON file and create a provenance audit."""
    with open(path, "r", encoding="utf-8") as handle:
        metadata = json.load(handle)

    records = metadata.get("records", [])

    report = inspect_evidence(
        metadata,
        source_name,
        records,
    )

    report["input_metadata_file"] = os.path.basename(path)
    report["metadata_sha256"] = sha256_file(path)

    if output_path:
        output_directory = os.path.dirname(output_path)

        if output_directory:
            os.makedirs(output_directory, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(
                report,
                handle,
                indent=2,
                ensure_ascii=False,
            )

    return report
