"""WSH R7.5.1 targeted evidence corrections.

Scope: NSE FII/DII cash + CCIL Money Market, GSEC, Forex, Derivatives.
No Sensibull, no collector redesign, and no inference of dates, units or definitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import hashlib
import json
import re
import zipfile

VERSION = "WSH_R7.5.1"
IST = ZoneInfo("Asia/Kolkata")

DATASET_FILES = {
    "NSE_FII_DII_CASH": "fii_dii_cash_activity",
    "CCIL_MONEY_MARKET": "ccil_money_market",
    "CCIL_GSEC": "ccil_gsec",
    "CCIL_FOREX": "ccil_forex",
    "CCIL_DERIVATIVES": "ccil_derivatives",
}

# Only source-visible labels and explicit unit annotations from RND-4 are recorded.
# A null unit/definition means the source capture does not establish it.
SEMANTIC_CONTRACT_REGISTRY = {
    "NSE_FII_DII_CASH": {
        "source": "NSE",
        "fields": {
            "participant": {"source_label": "category", "definition": "Participant category as returned by NSE.", "unit": "not_applicable", "evidence": "NSE API response key: category"},
            "date": {"source_label": "date", "definition": "Date string returned by NSE.", "unit": "calendar_date", "evidence": "NSE API response key: date"},
            "buy": {"source_label": "buyValue", "definition": None, "unit": None, "evidence": "RND-4 response key only; authoritative field definition/unit not present"},
            "sell": {"source_label": "sellValue", "definition": None, "unit": None, "evidence": "RND-4 response key only; authoritative field definition/unit not present"},
            "net": {"source_label": "netValue", "definition": None, "unit": None, "evidence": "RND-4 response key only; authoritative field definition/unit not present"},
        },
        "notes": "RND-4 explicitly marks monetary units UNVERIFIED.",
    },
    "CCIL_MONEY_MARKET": {
        "source": "CCIL",
        "fields": {
            "Market": {"source_label": "Market", "definition": None, "unit": None, "evidence": "Header observed; no authoritative definition"},
            "Open": {"source_label": "Open", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "High": {"source_label": "High", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Low": {"source_label": "Low", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "LTR": {"source_label": "LTR", "definition": None, "unit": None, "evidence": "Header observed; unit not explicit"},
            "Volume(₹ Crs.)": {"source_label": "Volume(₹ Crs.)", "definition": None, "unit": "INR crore", "evidence": "Explicit unit in source header"},
            "WAR": {"source_label": "WAR", "definition": None, "unit": None, "evidence": "Header observed; unit not explicit"},
            "Prev. Day WAR": {"source_label": "Prev. Day WAR", "definition": None, "unit": None, "evidence": "Header observed; unit not explicit"},
            "Prev. Day Vol(₹ Crs.)": {"source_label": "Prev. Day Vol(₹ Crs.)", "definition": None, "unit": "INR crore", "evidence": "Explicit unit in source header"},
        },
    },
    "CCIL_GSEC": {
        "source": "CCIL",
        "fields": {
            "Security Description": {"source_label": "Security Description", "definition": None, "unit": "not_applicable", "evidence": "Header observed; full source definition absent"},
            "Trades": {"source_label": "Trades", "definition": None, "unit": "count", "evidence": "Header label indicates count; formal source definition not supplied"},
            "TTA(₹ Cr.)": {"source_label": "TTA(₹ Cr.)", "definition": None, "unit": "INR crore", "evidence": "Explicit unit in source header"},
            "LTP": {"source_label": "LTP", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "LTY": {"source_label": "LTY", "definition": None, "unit": None, "evidence": "Header observed; yield unit/definition not documented"},
        },
    },
    "CCIL_FOREX": {
        "source": "CCIL",
        "fields": {
            "Instruments": {"source_label": "Instruments", "definition": None, "unit": "not_applicable", "evidence": "Header observed; formal definition absent"},
            "Open": {"source_label": "Open", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "High": {"source_label": "High", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Low": {"source_label": "Low", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "LTP": {"source_label": "LTP", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Prev Close": {"source_label": "Prev Close", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Net Change": {"source_label": "Net Change", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Trades": {"source_label": "Trades", "definition": None, "unit": "count", "evidence": "Header label indicates count; formal source definition not supplied"},
            "Vol(USD In Mio)": {"source_label": "Vol(USD In Mio)", "definition": None, "unit": "USD million", "evidence": "Explicit unit in source header"},
        },
    },
    "CCIL_DERIVATIVES": {
        "source": "CCIL",
        "fields": {
            "Tenors": {"source_label": "Tenors", "definition": None, "unit": None, "evidence": "Header observed; tenor convention not documented"},
            "Trades": {"source_label": "Trades", "definition": None, "unit": "count", "evidence": "Header label indicates count; formal source definition not supplied"},
            "Volume(₹ Cr.)": {"source_label": "Volume(₹ Cr.)", "definition": None, "unit": "INR crore", "evidence": "Explicit unit in source header"},
            "High": {"source_label": "High", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Low": {"source_label": "Low", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "LTR": {"source_label": "LTR", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Weighted Average": {"source_label": "Weighted Average", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
            "Previous Close": {"source_label": "Previous Close", "definition": None, "unit": None, "evidence": "Header observed; quote convention/unit not documented"},
        },
    },
}


def parse_source_date(value: object) -> dict:
    """Parse ISO or explicitly observed DD-Mon-YYYY; retain exact raw value."""
    if not isinstance(value, str) or not value.strip():
        return {"status": "UNKNOWN", "raw_value": value, "normalized_date": None}
    raw = value
    candidate = value.strip()
    parsed = None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            parsed = datetime.strptime(candidate, fmt).date()
            break
        except ValueError:
            pass
    if parsed is None:
        return {"status": "UNKNOWN", "raw_value": raw, "normalized_date": None}
    return {"status": "PASS", "raw_value": raw, "normalized_date": parsed.isoformat()}


def create_capture_timestamp(now: datetime | None = None) -> str:
    """Create an aware timestamp at collection time; never consult file mtime."""
    stamp = now if now is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Capture timestamp must be timezone-aware.")
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def attach_capture_timestamp(metadata: dict, now: datetime | None = None) -> dict:
    """Return metadata with collection-time capture timestamp and its provenance."""
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dictionary")
    result = dict(metadata)
    result["captured_at"] = create_capture_timestamp(now)
    result["capture_timestamp_source"] = "collector_runtime_clock"
    result["capture_timestamp_timezone"] = "UTC"
    return result


_DATE_LABEL_RE = re.compile(
    r"(?i)\b(?:publication\s+date|published\s+on|as\s+of|as\s+on|market\s+date|business\s+date|report\s+date)\b\s*[:\-]?\s*(\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{4}|\d{4}-\d{2}-\d{2})"
)


def extract_explicit_ccil_date(metadata: dict, raw_html: str | bytes | None = None) -> dict:
    """Accept only explicit source date keys/labels; never use capture or file dates."""
    keys = ("publication_date", "published_date", "published_at", "source_as_of_date", "as_of_date", "market_date", "report_date")
    for key in keys:
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if value and str(value).strip().upper() not in {"UNKNOWN", "UNKNOWN_NOT_EXPLICIT_IN_CAPTURE", "NONE"}:
            result = parse_source_date(str(value))
            if result["status"] == "PASS":
                return {**result, "evidence_field": key, "evidence_type": "source_metadata"}
            return {**result, "evidence_field": key, "evidence_type": "source_metadata"}
    if raw_html is not None:
        html = raw_html.decode("utf-8", errors="replace") if isinstance(raw_html, bytes) else str(raw_html)
        for match in _DATE_LABEL_RE.finditer(html):
            result = parse_source_date(match.group(1))
            if result["status"] == "PASS":
                return {**result, "evidence_field": None, "evidence_type": "explicit_html_label", "label_context": match.group(0)}
    return {"status": "UNKNOWN", "raw_value": None, "normalized_date": None, "evidence_field": None, "evidence_type": None, "reason": "No explicit source publication/as-of date found."}


def semantic_contract_status(dataset: str) -> dict:
    """Summarize source-documentation completeness without filling missing units."""
    contract = SEMANTIC_CONTRACT_REGISTRY.get(dataset)
    if contract is None:
        return {"status": "UNKNOWN", "reason": "Dataset has no semantic registry entry."}
    fields = contract["fields"]
    undocumented = [name for name, spec in fields.items() if not spec.get("definition") or not spec.get("unit")]
    documented = [name for name, spec in fields.items() if spec.get("definition") and spec.get("unit")]
    unit_documented = [name for name, spec in fields.items() if spec.get("unit")]
    definition_documented = [name for name, spec in fields.items() if spec.get("definition")]
    return {
        "status": "PASS" if not undocumented else "PARTIAL",
        "dataset": dataset,
        "field_count": len(fields),
        "documented_field_count": len(documented),
        "unit_documented_field_count": len(unit_documented),
        "definition_documented_field_count": len(definition_documented),
        "undocumented_fields": undocumented,
        "documented_fields": documented,
        "unit_documented_fields": unit_documented,
        "reason": "All field definitions and units are documented." if not undocumented else "RND-4 headers establish some units, but authoritative definitions and/or units remain missing; semantic PASS is withheld.",
    }


def inspect_rnd4_package(zip_path: str | Path) -> dict:
    """Run R7.5.1 targeted checks against the actual RND-4 evidence ZIP."""
    path = Path(zip_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        manifest_name = next((n for n in names if n.endswith("WSH_Quality_Manifest_20261109_183706.json")), None)
        if not manifest_name:
            raise ValueError("RND-4 quality manifest not found")
        manifest = json.loads(archive.read(manifest_name))
        results = {}
        for dataset, stem in DATASET_FILES.items():
            meta_name = next((n for n in names if Path(n).name.startswith(stem + "_") and n.endswith(".meta.json")), None)
            if not meta_name:
                results[dataset] = {"status": "UNKNOWN", "reason": "Dataset metadata missing."}
                continue
            meta = json.loads(archive.read(meta_name))
            source = meta.get("page_url") or meta.get("url") or meta.get("endpoint")
            host = (urlparse(source).hostname or "").lower() if source else ""
            expected_host = "nseindia.com" if dataset.startswith("NSE_") else "ccilindia.com"
            source_status = "PASS" if host == expected_host or host.endswith("." + expected_host) else "FAIL"
            capture = meta.get("captured_at")
            capture_valid = False
            if isinstance(capture, str):
                try:
                    parsed_capture = datetime.fromisoformat(capture.replace("Z", "+00:00"))
                    capture_valid = parsed_capture.tzinfo is not None and parsed_capture.utcoffset() is not None
                except ValueError:
                    pass
            date_result = parse_source_date(meta.get("records", [{}])[0].get("date")) if dataset.startswith("NSE_") and meta.get("records") else extract_explicit_ccil_date(meta)
            sem = semantic_contract_status(dataset)
            manifest_key = "fii_dii_cash_activity" if dataset.startswith("NSE_") else stem
            manifest_dataset = manifest.get("datasets", {}).get(manifest_key, {})
            raw_name = next((n for n in names if Path(n).name.startswith(stem + "_") and (n.endswith(".raw.json") if dataset.startswith("NSE_") else n.endswith(".raw.html"))), None)
            raw_digest = hashlib.sha256(archive.read(raw_name)).hexdigest() if raw_name else None
            expected_digest = manifest_dataset.get("evidence", {}).get("sha256") or meta.get("raw_response_sha256")
            raw_integrity_status = "UNKNOWN" if not raw_name or not expected_digest else ("PASS" if raw_digest == expected_digest else "FAIL")
            results[dataset] = {
                "source_identity_status": source_status,
                "capture_timestamp_status": "PASS" if capture_valid else "UNKNOWN",
                "capture_timestamp": capture,
                "source_date": date_result,
                "semantic_contract": sem,
                "raw_payload_integrity_status": raw_integrity_status,
                "raw_payload_sha256": raw_digest,
                "acquisition_status": manifest_dataset.get("extraction_status", "UNKNOWN"),
                "records_extracted": manifest_dataset.get("records_extracted", 0),
            }
            layer_statuses = [source_status, "PASS" if capture_valid else "UNKNOWN", date_result["status"], sem["status"], raw_integrity_status]
            results[dataset]["r7_5_1_status"] = "FAIL" if "FAIL" in layer_statuses else ("PARTIAL" if "PARTIAL" in layer_statuses or "UNKNOWN" in layer_statuses else "PASS")
        return {
            "validator_version": VERSION,
            "package": path.name,
            "dataset_count": len(results),
            "results": results,
            "overall_status": "FAIL" if any(v.get("r7_5_1_status") == "FAIL" for v in results.values()) else ("PARTIAL" if any(v.get("r7_5_1_status") != "PASS" for v in results.values()) else "PASS"),
            "policy": {"file_mtime_used": False, "capture_time_substitutes_for_publication_date": False, "units_inferred": False, "definitions_inferred": False, "sensibull_in_scope": False},
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WSH R7.5.1 RND-4 evidence audit")
    parser.add_argument("package", help="Path to WSH-FII-CCIL-RND-4.zip")
    parser.add_argument("--output", default="WSH_R7_5_1_RND4_AUDIT.json")
    args = parser.parse_args()
    report = inspect_rnd4_package(args.package)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
