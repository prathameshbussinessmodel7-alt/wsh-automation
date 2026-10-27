import os
import time
import datetime
import json
import re
import hashlib
import base64
import gzip
import math
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================================
# WSH MULTI-DIMENSIONAL QUALITY MANIFEST & CONTRACT ENGINE
# ==========================================================
class InstitutionalQualityManifest:
    def __init__(self):
        self.manifest = {
            "execution_timestamp": datetime.datetime.now().isoformat(),
            "pipeline_version": "WSH_COLLECTOR_RND_2026-10-24_WS-CAPTURE_4_QUOTE_AUDIT_V7",
            "datasets": {}
        }

    def record_dataset(self, source, dataset_name, pub_state, ext_state, val_state, expected_fields, records_extracted, failure_reasons=None):
        self.manifest["datasets"][dataset_name] = {
            "source": source,
            "publication_status": pub_state,  # PUBLISHED, MARKET_CLOSED, NOT_DUE
            "extraction_status": ext_state,    # SUCCESS, PARTIAL, FAILED, NOT_ATTEMPTED
            "validation_status": val_state,    # VALID, QUARANTINED, INVALID, NOT_RUN
            "expected_fields": expected_fields,
            "records_extracted": records_extracted,
            "failure_reasons": failure_reasons or []
        }

    def save_manifest(self, folder_path):
        os.makedirs(folder_path, exist_ok=True)
        filename = f"WSH_Quality_Manifest_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json"
        filepath = os.path.join(folder_path, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=4)
        print(f"[MANIFEST SAVED] Multi-dimensional quality audit written to {filepath}")

def is_market_trading_day():
    """Returns False if today is Saturday or Sunday, preventing false extraction failures."""
    current_day = datetime.datetime.now().weekday() # 5 = Saturday, 6 = Sunday
    return current_day < 5

def _wsh_page_diagnostics(driver, soup):
    """Return visible diagnostics useful for detecting fallback/error pages."""
    body_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    markers = ("javascript is disabled", "enable javascript", "access denied", "captcha",
               "verify you are human", "temporarily unavailable", "something went wrong")
    found = [marker for marker in markers if marker in body_text.lower()]
    return {
        "current_url": getattr(driver, "current_url", ""),
        "title": getattr(driver, "title", ""),
        "body_text": body_text[:900],
        "fallback_markers": found,
    }


def _wsh_has_expected_table_schema(table, required_groups):
    """True only when table headers match each required field group."""
    rows = table.find_all("tr")
    if not rows:
        return False
    headers = {_wsh_norm_key(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])}
    return all(any(_wsh_norm_key(alias) in headers for alias in aliases) for aliases in required_groups)


# ==========================================================
# SENSIBULL WEBSOCKET BINARY FRAME CAPTURE + VALIDATION
# ==========================================================
_WSH_GZIP_MAGIC = b"\x1f\x8b\x08"
_WSH_REQUIRED_WS_FIELDS = (
    "instrument_token", "last_price", "oi", "oi_change",
    "oi_change_quantity", "volume", "best_buy_price", "best_sell_price"
)


def _wsh_unpack_ws_payload(payload_text):
    """Decode Chrome DevTools WebSocket payloadData candidates safely.

    Chrome versions may expose binary payloadData as base64 or a text-like
    representation. Only bytes containing a valid GZIP member are accepted.
    """
    if not isinstance(payload_text, str) or not payload_text:
        return None
    candidates = []
    try:
        candidates.append(base64.b64decode(payload_text, validate=False))
    except Exception:
        pass
    try:
        compact = re.sub(r"\s+", "", payload_text)
        if compact and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact):
            candidates.append(bytes.fromhex(compact))
    except Exception:
        pass
    candidates.append(payload_text.encode("latin-1", errors="ignore"))
    for raw in candidates:
        offset = raw.find(_WSH_GZIP_MAGIC)
        if offset < 0:
            continue
        try:
            decoded = gzip.decompress(raw[offset:])
            data = json.loads(decoded.decode("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("chain"), dict):
                return {"raw": raw, "gzip_offset": offset, "data": data}
        except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None



def _wsh_record_failed_frame_evidence(evidence, payload, opcode, decode_diag):
    """Record bounded evidence for failed frames without retaining unbounded payloads.

    Exact payload hashes/lengths are always retained when the payload is a string.
    Up to three failed-frame samples retain the complete representation only when
    it is at most 16 KiB; larger representations are truncated and explicitly marked.
    """
    samples = evidence.setdefault("failed_frame_samples", [])
    if len(samples) >= 3:
        evidence["failed_frame_samples_omitted"] = evidence.get("failed_frame_samples_omitted", 0) + 1
        return
    is_text = isinstance(payload, str)
    encoded = payload.encode("utf-8") if is_text else b""
    max_chars = 16 * 1024
    representation = payload if is_text and len(payload) <= max_chars else (payload[:max_chars] if is_text else None)
    samples.append({
        "frame_id": f"ws-failed-{evidence.get('websocket_events', 0)}",
        "source": "Chrome DevTools Network.webSocketFrameReceived.params.response.payloadData",
        "opcode": opcode,
        "payload_data_type": type(payload).__name__,
        "payload_data_length_characters": len(payload) if is_text else None,
        "payload_data_utf8_length_bytes": len(encoded) if is_text else None,
        "payload_data_sha256_utf8": hashlib.sha256(encoded).hexdigest() if is_text else None,
        "payload_data_representation_sample": representation,
        "payload_data_representation_complete": bool(is_text and len(payload) <= max_chars),
        "payload_data_truncated": bool(is_text and len(payload) > max_chars),
        "decoder_failure_stage": decode_diag.get("failure_stage"),
        "decoder_failure_reason": decode_diag.get("failure_reason") or decode_diag.get("failure_stage"),
        "candidate_representations_tried": decode_diag.get("candidate_representations_tried", []),
    })


def _wsh_unpack_ws_payload_with_diagnostics(payload_text):
    """Decode one frame and report stage outcomes without logging its contents."""
    diag = {
        "payload_type": type(payload_text).__name__,
        "payload_length": len(payload_text) if isinstance(payload_text, (str, bytes, bytearray)) else None,
        "payload_sha256": hashlib.sha256(
            payload_text if isinstance(payload_text, bytes)
            else str(payload_text).encode("utf-8", errors="replace")
        ).hexdigest(),
        "candidate_representations_tried": [],
        "gzip_signature_found": False,
        "gzip_decompression": "NOT_ATTEMPTED",
        "utf8_decode": "NOT_ATTEMPTED",
        "json_parse": "NOT_ATTEMPTED",
        "chain_schema": "NOT_ATTEMPTED",
        "failure_stage": None,
    }
    if not isinstance(payload_text, str) or not payload_text:
        diag["failure_stage"] = "PAYLOAD_TYPE_OR_EMPTY"
        return None, diag

    candidates = []
    try:
        candidates.append(("base64", base64.b64decode(payload_text, validate=False)))
    except Exception as exc:
        diag["candidate_representations_tried"].append(
            {"representation": "base64", "status": "FAILED", "error_type": type(exc).__name__}
        )
    compact = re.sub(r"\s+", "", payload_text)
    if compact and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact):
        try:
            candidates.append(("hex", bytes.fromhex(compact)))
        except Exception as exc:
            diag["candidate_representations_tried"].append(
                {"representation": "hex", "status": "FAILED", "error_type": type(exc).__name__}
            )
    try:
        candidates.append(("latin1_text", payload_text.encode("latin-1", errors="strict")))
    except UnicodeEncodeError:
        candidates.append(("latin1_text_ignore", payload_text.encode("latin-1", errors="ignore")))

    saw_gzip = False
    saw_gzip_failure = False
    saw_utf8_failure = False
    saw_json_failure = False
    saw_schema_failure = False
    for representation, raw in candidates:
        offset = raw.find(_WSH_GZIP_MAGIC)
        candidate_status = {"representation": representation, "byte_length": len(raw),
                            "gzip_signature_found": offset >= 0}
        diag["candidate_representations_tried"].append(candidate_status)
        if offset < 0:
            continue
        saw_gzip = True
        try:
            decompressed = gzip.decompress(raw[offset:])
            candidate_status["gzip_decompression"] = "PASS"
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            saw_gzip_failure = True
            candidate_status["gzip_decompression"] = "FAILED"
            candidate_status["error_type"] = type(exc).__name__
            continue
        try:
            decoded_text = decompressed.decode("utf-8")
            candidate_status["utf8_decode"] = "PASS"
        except UnicodeDecodeError as exc:
            saw_utf8_failure = True
            candidate_status["utf8_decode"] = "FAILED"
            candidate_status["error_type"] = type(exc).__name__
            continue
        try:
            data = json.loads(decoded_text)
            candidate_status["json_parse"] = "PASS"
        except json.JSONDecodeError as exc:
            saw_json_failure = True
            candidate_status["json_parse"] = "FAILED"
            candidate_status["error_type"] = type(exc).__name__
            continue
        if isinstance(data, dict) and isinstance(data.get("chain"), dict):
            candidate_status["chain_schema"] = "PASS"
            diag.update({
                "gzip_signature_found": True,
                "gzip_decompression": "PASS",
                "utf8_decode": "PASS",
                "json_parse": "PASS",
                "chain_schema": "PASS",
                "failure_stage": None,
                "selected_representation": representation,
                "gzip_offset": offset,
                "decompressed_length": len(decompressed),
                "decompressed_sha256": hashlib.sha256(decompressed).hexdigest(),
            })
            return {"raw": raw, "gzip_offset": offset, "data": data}, diag
        saw_schema_failure = True
        candidate_status["chain_schema"] = "FAILED"
        candidate_status["decoded_top_level_type"] = type(data).__name__
        candidate_status["top_level_keys_sample"] = list(data.keys())[:20] if isinstance(data, dict) else []

    diag["gzip_signature_found"] = saw_gzip
    if not saw_gzip:
        diag["failure_stage"] = "GZIP_SIGNATURE_NOT_FOUND"
    elif saw_gzip_failure and not (saw_utf8_failure or saw_json_failure or saw_schema_failure):
        diag.update({"gzip_decompression": "FAILED", "failure_stage": "GZIP_DECOMPRESSION"})
    elif saw_utf8_failure and not (saw_json_failure or saw_schema_failure):
        diag.update({"gzip_decompression": "PASS", "utf8_decode": "FAILED", "failure_stage": "UTF8_DECODE"})
    elif saw_json_failure and not saw_schema_failure:
        diag.update({"gzip_decompression": "PASS", "utf8_decode": "PASS", "json_parse": "FAILED", "failure_stage": "JSON_PARSE"})
    else:
        diag.update({"gzip_decompression": "PASS", "utf8_decode": "PASS", "json_parse": "PASS",
                     "chain_schema": "FAILED", "failure_stage": "CHAIN_SCHEMA"})
    return None, diag

def _wsh_ws_chain_rows(data):
    """Normalize a decoded Sensibull option-chain message and validate fields."""
    chain = data.get("chain")
    if not isinstance(chain, dict):
        return [], ["Decoded message has no chain object"]
    rows, issues, seen = [], [], set()
    for strike_text, sides in chain.items():
        try:
            strike = float(strike_text)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(strike) or strike <= 0 or not isinstance(sides, dict):
            continue
        row = {"strike": strike}
        for side in ("call", "put"):
            obj = sides.get(side)
            if not isinstance(obj, dict):
                issues.append(f"strike {strike:g}: missing {side} object")
                continue
            missing = [field for field in _WSH_REQUIRED_WS_FIELDS if field not in obj]
            if missing:
                issues.append(f"strike {strike:g} {side}: missing {','.join(missing)}")
            for key, value in obj.items():
                row[f"{side}_{key}"] = value
        if strike not in seen:
            seen.add(strike)
            rows.append(row)
    rows.sort(key=lambda x: x["strike"])
    if len(rows) < 8:
        issues.append(f"Only {len(rows)} unique strike rows; minimum acceptance threshold is 8")
    return rows, issues




def _wsh_quote_and_field_diagnostics(rows):
    """Audit quote integrity and preserve row/field evidence without repairing source values.

    v7 adds per-field quote-state counts and raw-value evidence so a zero quote,
    explicit null, absent key, malformed value, and negative value cannot be
    conflated in downstream debugging.
    """
    findings = {"one_sided_zero_quotes": [], "both_sides_zero": [],
                "crossed_quotes": [], "negative_value_fields": [],
                "explicit_null_counts": {}, "absent_field_counts": {},
                "invalid_numeric_fields": [], "required_field_defects": [],
                "quote_field_audit": {}, "rows_examined": len(rows),
                "diagnostic_schema_version": "WSH_QUOTE_AUDIT_V7"}
    numeric_fields = ("last_price", "oi", "oi_change", "oi_change_quantity", "volume",
                      "best_buy_price", "best_sell_price")
    nonnegative_fields = {"last_price", "oi", "volume", "best_buy_price", "best_sell_price"}
    quote_fields = ("best_buy_price", "best_sell_price")

    def bump(field_key, state):
        bucket = findings["quote_field_audit"].setdefault(
            field_key, {"present": 0, "absent": 0, "explicit_null": 0,
                        "zero": 0, "positive": 0, "negative": 0,
                        "malformed_or_nonfinite": 0})
        bucket[state] += 1

    for index, row in enumerate(rows):
        strike = row.get("strike")
        for side in ("call", "put"):
            # Required contract fields must be present and non-null; zero is never substituted.
            for field in _WSH_REQUIRED_WS_FIELDS:
                key = f"{side}_{field}"
                if key not in row:
                    findings["absent_field_counts"][key] = findings["absent_field_counts"].get(key, 0) + 1
                    findings["required_field_defects"].append({"row": index, "strike": strike, "field": key, "defect": "absent"})
                elif row[key] is None:
                    findings["explicit_null_counts"][key] = findings["explicit_null_counts"].get(key, 0) + 1
                    findings["required_field_defects"].append({"row": index, "strike": strike, "field": key, "defect": "explicit_null"})

            bid_key, ask_key = f"{side}_best_buy_price", f"{side}_best_sell_price"
            bid, ask = row.get(bid_key), row.get(ask_key)
            # Keep state counts separate for every raw quote field.
            for field, key, value in (("best_buy_price", bid_key, bid), ("best_sell_price", ask_key, ask)):
                if key not in row:
                    bump(key, "absent")
                elif value is None:
                    bump(key, "present")
                    bump(key, "explicit_null")
                elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    bump(key, "present")
                    bump(key, "malformed_or_nonfinite")
                    findings["invalid_numeric_fields"].append({"row": index, "strike": strike, "field": key, "value_repr": repr(value)[:120]})
                else:
                    bump(key, "present")
                    if value == 0:
                        bump(key, "zero")
                    elif value > 0:
                        bump(key, "positive")
                    else:
                        bump(key, "negative")
                        findings["negative_value_fields"].append({"row": index, "strike": strike, "field": key, "value": value})

            bid_numeric = isinstance(bid, (int, float)) and not isinstance(bid, bool) and math.isfinite(bid)
            ask_numeric = isinstance(ask, (int, float)) and not isinstance(ask, bool) and math.isfinite(ask)
            if bid_numeric and ask_numeric:
                if bid > 0 and ask == 0:
                    findings["one_sided_zero_quotes"].append({"row": index, "strike": strike, "side": side, "bid": bid, "ask": ask, "classification": "positive_bid_zero_ask"})
                elif bid == 0 and ask > 0:
                    findings["one_sided_zero_quotes"].append({"row": index, "strike": strike, "side": side, "bid": bid, "ask": ask, "classification": "zero_bid_positive_ask"})
                elif bid == 0 and ask == 0:
                    findings["both_sides_zero"].append({"row": index, "strike": strike, "side": side, "bid": bid, "ask": ask, "classification": "both_quotes_zero"})
                if bid > 0 and ask > 0 and bid > ask:
                    findings["crossed_quotes"].append({"row": index, "strike": strike, "side": side, "bid": bid, "ask": ask, "classification": "crossed_bid_above_ask"})
            else:
                # A quote pair that cannot be evaluated is explicitly represented, not treated as clean.
                if (bid_key not in row or bid is None or not bid_numeric or
                        ask_key not in row or ask is None or not ask_numeric):
                    findings.setdefault("unevaluable_quote_pairs", []).append({
                        "row": index, "strike": strike, "side": side,
                        "bid_present": bid_key in row, "bid_raw": repr(bid)[:120],
                        "ask_present": ask_key in row, "ask_raw": repr(ask)[:120],
                        "classification": "missing_null_or_non_numeric_quote"})

        for side in ("call", "put"):
            for field in numeric_fields:
                key = f"{side}_{field}"
                if field in quote_fields or key not in row or row[key] is None:
                    continue
                value = row[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    findings["invalid_numeric_fields"].append({"row": index, "strike": strike, "field": key, "value_repr": repr(value)[:120]})
                elif field in nonnegative_fields and value < 0:
                    findings["negative_value_fields"].append({"row": index, "strike": strike, "field": key, "value": value})
    findings["one_sided_zero_count"] = len(findings["one_sided_zero_quotes"])
    findings["both_sides_zero_count"] = len(findings["both_sides_zero"])
    findings["crossed_quote_count"] = len(findings["crossed_quotes"])
    findings["negative_value_count"] = len(findings["negative_value_fields"])
    findings["invalid_numeric_count"] = len(findings["invalid_numeric_fields"])
    findings["required_field_defect_count"] = len(findings["required_field_defects"])
    findings["unevaluable_quote_pair_count"] = len(findings.get("unevaluable_quote_pairs", []))
    return findings


def _wsh_identity_diagnostic(data, requested_symbol):
    """Require explicit payload identity evidence; URL routing alone is not identity proof."""
    # Store identity keys in canonical form: lowercase alphanumeric only.
    # This matches normalized_key below, so "underlying_symbol" and
    # "underlyingSymbol" are both recognized as "underlyingsymbol".
    identity_keys = {
        re.sub(r"[^a-z0-9]", "", key.lower())
        for key in (
            "symbol", "tradingsymbol", "underlying", "underlying_symbol",
            "index", "index_name", "instrument_name", "underlying_name",
            "exchange_instrument",
        )
    }
    wanted = re.sub(r"[^A-Z0-9]", "", str(requested_symbol).upper())
    observed = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized_key in identity_keys and isinstance(child, (str, int)):
                    candidate = re.sub(r"[^A-Z0-9]", "", str(child).upper())
                    if candidate:
                        observed.append({"field": str(key), "value": str(child), "normalized": candidate})
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)
    matches = [x for x in observed if x["normalized"] == wanted]
    conflicts = [x for x in observed if x["normalized"] != wanted]
    if matches and not conflicts:
        status = "PASS"
    elif not observed:
        status = "UNVERIFIED"
    else:
        status = "FAIL"
    return {"status": status, "requested_symbol": requested_symbol,
            "identity_fields_observed": observed[:50],
            "reason": ("Explicit payload identity matches requested symbol." if status == "PASS"
                       else "Payload exposes no recognized identity field." if status == "UNVERIFIED"
                       else "Payload identity fields conflict with requested symbol.")}


def _wsh_parse_source_timestamp(value):
    """Parse only explicit source timestamps; do not substitute capture time."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        # Accept plausible Unix seconds or milliseconds only.
        if number > 1e12:
            number /= 1000.0
        if number < 1e8 or number > 4e9:
            return None
        return datetime.datetime.fromtimestamp(number, tz=datetime.timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            return None
    return None


def _wsh_quote_freshness_diagnostic(data, checked_at_utc, max_age_seconds=120):
    """Require a source timestamp on every call/put quote and compare it with validation time."""
    chain = data.get("chain") if isinstance(data, dict) else None
    if not isinstance(chain, dict):
        return {"status": "UNVERIFIED", "reason": "No decoded chain object.", "quotes_checked": 0,
                "max_age_seconds": max_age_seconds, "quote_timestamps": []}
    timestamp_keys = {"timestamp", "quotetimestamp", "exchangetimestamp", "lasttradetime",
                       "lasttradetimestamp", "updatedat", "lastupdated", "sourcetimestamp",
                       "markettimestamp", "tradetime"}
    try:
        checked_at = datetime.datetime.fromisoformat(checked_at_utc.replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            raise ValueError("checked_at_utc has no timezone")
        checked_at = checked_at.astimezone(datetime.timezone.utc)
    except (ValueError, AttributeError):
        return {"status": "UNVERIFIED", "reason": "Validation timestamp is not timezone-aware.",
                "quotes_checked": 0, "max_age_seconds": max_age_seconds, "quote_timestamps": []}
    records, missing = [], []
    for strike, sides in chain.items():
        if not isinstance(sides, dict):
            continue
        for side in ("call", "put"):
            quote = sides.get(side)
            if not isinstance(quote, dict):
                continue
            found = None
            for key, value in quote.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized in timestamp_keys:
                    parsed = _wsh_parse_source_timestamp(value)
                    if parsed is not None:
                        found = (str(key), parsed)
                        break
            if found is None:
                missing.append({"strike": str(strike), "side": side})
                continue
            key, stamp = found
            age = (checked_at - stamp).total_seconds()
            records.append({"strike": str(strike), "side": side, "timestamp_field": key,
                            "source_timestamp_utc": stamp.isoformat(), "age_seconds": round(age, 3)})
    stale = [x for x in records if x["age_seconds"] < -5 or x["age_seconds"] > max_age_seconds]
    if not records or missing:
        status = "UNVERIFIED"
    elif stale:
        status = "FAIL"
    else:
        status = "PASS"
    return {"status": status, "reason": (
                "All quote objects have source timestamps within the freshness threshold." if status == "PASS"
                else "One or more quote objects lack a parseable source timestamp." if status == "UNVERIFIED"
                else "One or more source timestamps are stale or materially in the future."),
            "quotes_checked": len(records), "quotes_missing_timestamp": len(missing),
            "missing_timestamp_examples": missing[:25], "stale_examples": stale[:25],
            "max_age_seconds": max_age_seconds, "quote_timestamps": records[:100]}

def _wsh_clear_performance_logs(driver):
    """Drain old performance events so a new contract capture starts clean."""
    try:
        driver.get_log("performance")
        return True
    except Exception:
        return False


def _wsh_contract_route_matches(driver, symbol, expiry):
    """Require the browser to remain on the requested option-chain route."""
    current = str(getattr(driver, "current_url", "")).lower()
    wanted_symbol = f"tradingsymbol={symbol.lower()}"
    wanted_expiry = f"expiry={expiry.lower()}"
    return wanted_symbol in current and wanted_expiry in current, current


def capture_sensibull_ws_option_chain(driver, symbol, expiry, manifest_tracker, output_folder="WSH_Automated_Reports"):
    # v7 root-cause evidence: retain the selected source message separately from normalized rows.
    """Capture option-chain frames emitted by the authenticated Sensibull page.

    This does not perform or bypass Zerodha authentication. If the GitHub runner
    is not authenticated, or the market is closed and no suitable frame arrives,
    it records an explicit failure/partial status instead of substituting stale
    or fabricated values.
    """
    dataset_key = f"sensibull_ws_{symbol.lower()}_{expiry}"
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    route_ok, current_url = _wsh_contract_route_matches(driver, symbol, expiry)
    result = {"symbol": symbol, "requested_expiry": expiry, "captured_at_utc": captured_at,
              "page_url_at_capture": current_url, "route_identity_check": "PASS" if route_ok else "FAIL",
              "status": "NOT_CAPTURED", "frames_seen": 0, "rows": [], "summary": {}, "reasons": [],
              "frame_evidence": {"websocket_events": 0, "binary_frames": 0, "text_frames": 0,
                                 "other_opcode_frames": 0, "payload_missing_or_wrong_type": 0,
                                 "decode_successes": 0, "decode_failures_by_stage": {},
                                 "sample_frames": [], "failed_frame_samples": [],
                                 "failed_frame_samples_omitted": 0}}
    if not route_ok:
        reason = f"Current page route does not match requested contract {symbol} {expiry}: {current_url}"
        result["reasons"] = [reason]
        manifest_tracker.record_dataset("Sensibull-WebSocket", dataset_key, "PUBLISHED", "FAILED", "INVALID",
            ["symbol", "expiry", "strike", "call_oi", "put_oi"], 0, [reason])
        return result
    try:
        logs = driver.get_log("performance")
    except Exception as exc:
        logs = []
        result["reasons"].append(f"Chrome performance log unavailable: {type(exc).__name__}: {exc}")
    parsed = []
    for entry in logs:
        try:
            envelope = json.loads(entry.get("message", "{}"))
            message = envelope.get("message", {})
            if message.get("method") != "Network.webSocketFrameReceived":
                continue
            params = message.get("params", {})
            response = params.get("response", {})
            result["frames_seen"] += 1
            evidence = result["frame_evidence"]
            evidence["websocket_events"] += 1
            opcode = response.get("opcode", 0)
            payload = response.get("payloadData", "")
            if opcode == 2:
                evidence["binary_frames"] += 1
            elif opcode == 1:
                evidence["text_frames"] += 1
                continue
            else:
                evidence["other_opcode_frames"] += 1
                continue
            if not isinstance(payload, str) or not payload:
                evidence["payload_missing_or_wrong_type"] += 1
            decoded, decode_diag = _wsh_unpack_ws_payload_with_diagnostics(payload)
            if decoded:
                decoded["capture_boundary_evidence"] = {
                    "source": "Chrome DevTools Network.webSocketFrameReceived.params.response.payloadData",
                    "opcode": opcode,
                    "payload_data_type": type(payload).__name__,
                    "payload_data_length_characters": len(payload) if isinstance(payload, str) else None,
                    "payload_data_utf8_length_bytes": len(payload.encode("utf-8")) if isinstance(payload, str) else None,
                    "payload_data_sha256_utf8": hashlib.sha256(payload.encode("utf-8")).hexdigest() if isinstance(payload, str) else None,
                    "payload_data_representation": payload if isinstance(payload, str) else None,
                    "decoder_selected_representation": decode_diag.get("selected_representation"),
                    "decoder_selected_bytes_length": len(decoded.get("raw", b"")),
                    "decoder_selected_bytes_sha256": hashlib.sha256(decoded.get("raw", b"")).hexdigest(),
                }
            if len(evidence["sample_frames"]) < 20:
                evidence["sample_frames"].append({
                    "opcode": opcode,
                    "payload_type": decode_diag["payload_type"],
                    "payload_length": decode_diag["payload_length"],
                    "payload_sha256": decode_diag["payload_sha256"],
                    "decode_status": "PASS" if decoded else "FAILED",
                    "failure_stage": decode_diag["failure_stage"],
                    "candidate_representations_tried": decode_diag["candidate_representations_tried"],
                })
            if decoded:
                evidence["decode_successes"] += 1
                parsed.append(decoded)
            else:
                stage = decode_diag["failure_stage"] or "UNKNOWN"
                evidence["decode_failures_by_stage"][stage] = evidence["decode_failures_by_stage"].get(stage, 0) + 1
                _wsh_record_failed_frame_evidence(evidence, payload, opcode, decode_diag)
        except (ValueError, TypeError, KeyError):
            continue
    # Some browser versions deliver frames after the first log drain. Wait and drain once more.
    deadline = time.time() + 3
    while time.time() < deadline and not parsed:
        time.sleep(0.5)
        try:
            logs = driver.get_log("performance")
        except Exception:
            logs = []
        for entry in logs:
            try:
                envelope = json.loads(entry.get("message", "{}"))
                message = envelope.get("message", {})
                if message.get("method") != "Network.webSocketFrameReceived":
                    continue
                response = message.get("params", {}).get("response", {})
                result["frames_seen"] += 1
                evidence = result["frame_evidence"]
                evidence["websocket_events"] += 1
                opcode = response.get("opcode", 0)
                payload = response.get("payloadData", "")
                if opcode == 2:
                    evidence["binary_frames"] += 1
                elif opcode == 1:
                    evidence["text_frames"] += 1
                    continue
                else:
                    evidence["other_opcode_frames"] += 1
                    continue
                if not isinstance(payload, str) or not payload:
                    evidence["payload_missing_or_wrong_type"] += 1
                decoded, decode_diag = _wsh_unpack_ws_payload_with_diagnostics(payload)
                if decoded:
                    decoded["capture_boundary_evidence"] = {
                        "source": "Chrome DevTools Network.webSocketFrameReceived.params.response.payloadData",
                        "opcode": opcode,
                        "payload_data_type": type(payload).__name__,
                        "payload_data_length_characters": len(payload) if isinstance(payload, str) else None,
                        "payload_data_utf8_length_bytes": len(payload.encode("utf-8")) if isinstance(payload, str) else None,
                        "payload_data_sha256_utf8": hashlib.sha256(payload.encode("utf-8")).hexdigest() if isinstance(payload, str) else None,
                        "payload_data_representation": payload if isinstance(payload, str) else None,
                        "decoder_selected_representation": decode_diag.get("selected_representation"),
                        "decoder_selected_bytes_length": len(decoded.get("raw", b"")),
                        "decoder_selected_bytes_sha256": hashlib.sha256(decoded.get("raw", b"")).hexdigest(),
                    }
                if len(evidence["sample_frames"]) < 20:
                    evidence["sample_frames"].append({
                        "opcode": opcode,
                        "payload_type": decode_diag["payload_type"],
                        "payload_length": decode_diag["payload_length"],
                        "payload_sha256": decode_diag["payload_sha256"],
                        "decode_status": "PASS" if decoded else "FAILED",
                        "failure_stage": decode_diag["failure_stage"],
                        "candidate_representations_tried": decode_diag["candidate_representations_tried"],
                    })
                if decoded:
                    evidence["decode_successes"] += 1
                    parsed.append(decoded)
                else:
                    stage = decode_diag["failure_stage"] or "UNKNOWN"
                    evidence["decode_failures_by_stage"][stage] = evidence["decode_failures_by_stage"].get(stage, 0) + 1
                    _wsh_record_failed_frame_evidence(evidence, payload, opcode, decode_diag)
            except (ValueError, TypeError, KeyError):
                continue
    evidence = result["frame_evidence"]
    evidence["decoded_payloads"] = len(parsed)
    if not parsed:
        if result["frames_seen"] == 0:
            result["reasons"].append("No Network.webSocketFrameReceived events observed in drained Chrome performance logs.")
        elif evidence["binary_frames"] == 0:
            result["reasons"].append("WebSocket events observed, but no binary opcode-2 frames were available to the current decoder.")
        else:
            result["reasons"].append(
                "Binary frames observed but none decoded as GZIP-compressed JSON with a dict-valued chain; "
                f"failure stages={json.dumps(evidence['decode_failures_by_stage'], sort_keys=True)}"
            )
    matching = []
    for item in parsed:
        data = item["data"]
        encoded_expiry = item["raw"][:item["gzip_offset"]]
        expiry_match = re.search(rb"20\d{6}", encoded_expiry)
        encoded = expiry_match.group(0).decode("ascii") if expiry_match else None
        # Contract acceptance is strict: an absent expiry is not a match.
        if not encoded or encoded != expiry.replace("-", ""):
            continue
        rows, issues = _wsh_ws_chain_rows(data)
        if rows and not issues:
            matching.append((item, data, rows, encoded))
    if matching:
        item, data, rows, encoded = max(matching, key=lambda x: len(x[2]))
        checked_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        quote_diagnostics = _wsh_quote_and_field_diagnostics(rows)
        identity_diagnostics = _wsh_identity_diagnostic(data, symbol)
        freshness_diagnostics = _wsh_quote_freshness_diagnostic(data, checked_at_utc)
        has_quote_defects = bool(
            quote_diagnostics["one_sided_zero_count"]
            or quote_diagnostics["both_sides_zero_count"]
            or quote_diagnostics["invalid_numeric_count"]
            or quote_diagnostics["required_field_defect_count"]
            or quote_diagnostics["crossed_quote_count"]
            or quote_diagnostics["negative_value_count"]
        )
        # Route/expiry/structure success must never imply instrument identity or freshness success.
        result.update({"status": "STRUCTURAL_DATA_ISSUES" if has_quote_defects else "EXPIRY_MATCHED_STRUCTURE_PASS",
                       "rows": rows, "validated_at_utc": checked_at_utc,
                       "quote_integrity_diagnostics": quote_diagnostics,
                       "identity_diagnostics": identity_diagnostics,
                       "freshness_diagnostics": freshness_diagnostics,
                       "institutional_acceptance": "NOT_VALID" if identity_diagnostics["status"] != "PASS" or freshness_diagnostics["status"] != "PASS" or has_quote_defects else "REQUIRES_SEPARATE_ACCEPTANCE_TEST",
                       "encoded_expiry": encoded,
                       "identity_limitations": ["URL route and encoded expiry are not independent proof of underlying identity.",
                           "Identity passes only when recognized explicit payload identity fields match the requested symbol.",
                           "Freshness passes only when every call/put quote exposes a parseable source timestamp within 120 seconds of validation time."],
                       "summary": {key: data.get(key) for key in ("atm_strike", "atm_iv", "atm_iv_change", "atm_iv_percentile", "pcr", "max_pain_strike", "atm_ivp_type", "future_price")},
                       "binary_message_sha256": hashlib.sha256(item["raw"]).hexdigest(),
                       "decompressed_json_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                       "reasons": []})
        os.makedirs(output_folder, exist_ok=True)
        safe_expiry = expiry.replace("-", "")
        outfile = os.path.join(output_folder, f"WSH_Sensibull_WS_{symbol}_{safe_expiry}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        result["output_file"] = outfile
        # Preserve the exact decoded upstream object and source frame bytes so a later audit can
        # distinguish source-supplied zero/null values from normalization/mapping defects.
        # This is diagnostic evidence only; it does not change values or acceptance gates.
        raw_evidence = {
            "evidence_schema_version": "WSH_WS_SOURCE_TO_ROW_V1",
            "symbol_requested": symbol,
            "expiry_requested": expiry,
            "captured_at_utc": captured_at,
            "validated_at_utc": checked_at_utc,
            "page_url_at_capture": current_url,
            "encoded_expiry": encoded,
            "frames_seen": result["frames_seen"],
            "frame_evidence": result["frame_evidence"],
            "capture_boundary_evidence": item.get("capture_boundary_evidence", {}),
            "decoder_selected_bytes_sha256": hashlib.sha256(item["raw"]).hexdigest(),
            "decompressed_source_json_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "decoder_selected_bytes_base64": base64.b64encode(item["raw"]).decode("ascii"),
            "decoded_source_object": data,
            "normalized_rows": rows,
            "note": "Source object and normalized rows are both preserved without repairs. Compare chain[strike][call|put] fields against normalized rows to locate any mapping loss. Identity and freshness remain separately gated."
        }
        evidence_file = os.path.join(output_folder, f"WSH_Sensibull_WS_SOURCE_EVIDENCE_{symbol}_{safe_expiry}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(evidence_file, "w", encoding="utf-8") as f:
            json.dump(raw_evidence, f, indent=2, ensure_ascii=False)
        result["source_evidence_file"] = evidence_file
        pub = "MARKET_CLOSED" if not is_market_trading_day() else "PUBLISHED"
        val = "INVALID" if has_quote_defects else "PARTIAL"
        reason = ["Payload structure and encoded expiry validated; institutional acceptance remains blocked unless independent identity and per-quote freshness checks pass."]
        if quote_diagnostics["one_sided_zero_count"]:
            reason.append(f"Quote integrity blocker: {quote_diagnostics['one_sided_zero_count']} one-sided zero bid/ask anomalies; raw values preserved.")
        if quote_diagnostics["both_sides_zero_count"]:
            reason.append(f"Quote integrity blocker: {quote_diagnostics['both_sides_zero_count']} both-sided zero bid/ask cases; raw values preserved.")
        if quote_diagnostics["required_field_defect_count"]:
            reason.append(f"Required-field blocker: {quote_diagnostics['required_field_defect_count']} missing or explicit-null required fields; raw values preserved.")
        if quote_diagnostics["invalid_numeric_count"]:
            reason.append(f"Quote integrity blocker: {quote_diagnostics['invalid_numeric_count']} invalid numeric field values; raw values preserved.")
        if quote_diagnostics["crossed_quote_count"]:
            reason.append(f"Quote integrity blocker: {quote_diagnostics['crossed_quote_count']} crossed bid/ask quotes (bid greater than ask); raw values preserved.")
        if quote_diagnostics["negative_value_count"]:
            reason.append(f"Quote integrity blocker: {quote_diagnostics['negative_value_count']} negative values in fields expected to be non-negative; raw values preserved.")
        reason.append(f"Underlying identity diagnostic: {identity_diagnostics['status']} — {identity_diagnostics['reason']}")
        reason.append(f"Quote freshness diagnostic: {freshness_diagnostics['status']} — {freshness_diagnostics['reason']}")
        manifest_tracker.record_dataset("Sensibull-WebSocket", dataset_key, pub, "SUCCESS", val,
            ["symbol", "expiry", "strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"], len(rows), reason)
    else:
        reason = result["reasons"] or ["No binary WebSocket frame decoded into a matching GZIP-compressed option-chain payload. This can occur without an authenticated Sensibull session or when the market is closed."]
        result["reasons"] = reason
        pub = "MARKET_CLOSED" if not is_market_trading_day() else "PUBLISHED"
        manifest_tracker.record_dataset("Sensibull-WebSocket", dataset_key, pub, "FAILED", "INVALID",
            ["symbol", "expiry", "strike", "call_oi", "put_oi"], 0, reason)
    return result


# ==========================================================
# 1. SENSIBULL OPTION CHAIN ADAPTER (PRIE)
# ==========================================================

def _wsh_norm_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _wsh_number(value):
    """Parse a numeric value without treating blanks/dashes as zero."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "").replace("₹", "")
    if not text or text.lower() in {"-", "--", "—", "na", "n/a", "null", "none"}:
        return None
    multiplier = 1.0
    if text.endswith(("K", "k")):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith(("M", "m")):
        multiplier, text = 1_000_000.0, text[:-1]
    elif text.endswith(("B", "b")):
        multiplier, text = 1_000_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return None


def _wsh_first_value(mapping, aliases):
    if not isinstance(mapping, dict):
        return None
    normalized = {_wsh_norm_key(k): v for k, v in mapping.items()}
    for alias in aliases:
        key = _wsh_norm_key(alias)
        if key in normalized:
            return normalized[key]
    return None


def _wsh_records_from_json(value):
    """Yield dict records from embedded JSON without inventing values."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _wsh_records_from_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _wsh_records_from_json(child)


def _wsh_json_option_rows(soup):
    strike_aliases = ("strike", "strikePrice", "strike_price", "strikeRate")
    call_oi_aliases = ("callOI", "call_oi", "ceOI", "ce_oi", "callOpenInterest")
    put_oi_aliases = ("putOI", "put_oi", "peOI", "pe_oi", "putOpenInterest")
    call_change_aliases = ("callChangeOI", "call_change_oi", "ceChangeOI", "changeInCallOI",
                           "callOIChange", "ce_oi_change")
    put_change_aliases = ("putChangeOI", "put_change_oi", "peChangeOI", "changeInPutOI",
                          "putOIChange", "pe_oi_change")
    output, seen = [], set()

    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        candidates = []
        try:
            candidates.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            # Next.js / app state can wrap JSON in an assignment. Only parse
            # complete JSON objects/arrays; never regex-scrape numeric values.
            match = re.search(r"(\{.*\}|\[.*\])", raw.strip(), flags=re.S)
            if match:
                try:
                    candidates.append(json.loads(match.group(1)))
                except (json.JSONDecodeError, TypeError):
                    pass

        for candidate in candidates:
            for obj in _wsh_records_from_json(candidate):
                strike_raw = _wsh_first_value(obj, strike_aliases)
                call_raw = _wsh_first_value(obj, call_oi_aliases)
                put_raw = _wsh_first_value(obj, put_oi_aliases)
                # Also support nested call/put objects when their field names
                # are explicit in the source payload.
                if isinstance(obj.get("call"), dict):
                    call_raw = _wsh_first_value(obj["call"], ("oi", "openInterest", "open_interest"))
                    call_change_raw = _wsh_first_value(obj["call"], ("changeOI", "changeInOI", "oiChange"))
                else:
                    call_change_raw = _wsh_first_value(obj, call_change_aliases)
                if isinstance(obj.get("put"), dict):
                    put_raw = _wsh_first_value(obj["put"], ("oi", "openInterest", "open_interest"))
                    put_change_raw = _wsh_first_value(obj["put"], ("changeOI", "changeInOI", "oiChange"))
                else:
                    put_change_raw = _wsh_first_value(obj, put_change_aliases)

                strike = _wsh_number(strike_raw)
                call_oi = _wsh_number(call_raw)
                put_oi = _wsh_number(put_raw)
                call_change = _wsh_number(call_change_raw)
                put_change = _wsh_number(put_change_raw)
                if None in (strike, call_oi, put_oi, call_change, put_change):
                    continue
                # Strike prices should be positive and finite. OI can be zero,
                # but cannot be negative.
                if strike <= 0 or call_oi < 0 or put_oi < 0:
                    continue
                record = {
                    "strike": strike,
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "call_change_oi": call_change,
                    "put_change_oi": put_change,
                    "source_format": "embedded_json"
                }
                key = (strike, call_oi, put_oi, call_change, put_change)
                if key not in seen:
                    seen.add(key)
                    output.append(record)
    return output


def _wsh_html_option_rows(soup):
    """Extract only actual table rows whose headers map to required fields."""
    output, seen = [], set()
    for table in soup.find_all("table"):
        all_rows = table.find_all("tr")
        if not all_rows:
            continue
        headers = [_wsh_norm_key(c.get_text(" ", strip=True))
                   for c in all_rows[0].find_all(["th", "td"])]
        if not headers:
            continue

        def index_for(aliases):
            normalized_aliases = {_wsh_norm_key(x) for x in aliases}
            for i, header in enumerate(headers):
                if header in normalized_aliases:
                    return i
            return None

        idx = {
            "strike": index_for(("strike", "strikeprice", "strikepriceinr")),
            "call_oi": index_for(("calloi", "ceoi", "callopeninterest")),
            "put_oi": index_for(("putoi", "peoi", "putopeninterest")),
            "call_change_oi": index_for(("callchangeoi", "cechangeoi", "changeincalloi", "calloichange")),
            "put_change_oi": index_for(("putchangeoi", "pechangeoi", "changeinputoi", "putoichange"))
        }
        if any(v is None for v in idx.values()):
            continue

        for row in all_rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
            if not cells or max(idx.values()) >= len(cells):
                continue
            record = {name: _wsh_number(cells[position]) for name, position in idx.items()}
            if any(v is None for v in record.values()):
                continue
            if record["strike"] <= 0 or record["call_oi"] < 0 or record["put_oi"] < 0:
                continue
            key = tuple(record[name] for name in ("strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"))
            if key not in seen:
                seen.add(key)
                record["source_format"] = "html_table"
                output.append(record)
    return output


def _wsh_discover_expiry_checkboxes(driver):
    """Discover expiry checkboxes from the live Sensibull OI-vs-Strike UI.

    This intentionally uses visible UI labels rather than assuming a fixed
    weekly/monthly expiry ladder. Returned dates are candidates and must still
    be verified against the actual page after selection.
    """
    elements = driver.execute_script("""
      return Array.from(document.querySelectorAll('input[type=checkbox]')).map((el, i) => {
        const label = el.labels && el.labels.length
          ? Array.from(el.labels).map(x => x.innerText || '').join(' ')
          : '';
        const parent = el.closest('label') || el.parentElement;
        return {index:i, checked:!!el.checked,
                text:(label || (parent && parent.innerText) || '').replace(/\\s+/g,' ').trim()};
      }).filter(x => x.text);
    """) or []
    found=[]
    seen=set()
    month_map={m.lower():i for i,m in enumerate(
        ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
    today=datetime.date.today()
    for item in elements:
        text=str(item.get('text','')).strip()
        match=re.search(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b(?:\s+(\d{4}))?',text,re.I)
        if not match:
            continue
        day=int(match.group(1)); month=month_map[match.group(2).lower()]
        year=int(match.group(3)) if match.group(3) else today.year
        try:
            expiry=datetime.date(year,month,day)
        except ValueError:
            continue
        if not match.group(3) and expiry < today - datetime.timedelta(days=2):
            expiry=datetime.date(year+1,month,day)
        key=expiry.isoformat()
        if key not in seen:
            seen.add(key)
            found.append({'date':key,'label':text,'index':int(item.get('index',-1)),'checked':bool(item.get('checked'))})
    return found


def _wsh_select_single_expiry_checkbox(driver, expiry_date):
    """Select one discovered expiry and verify the actual checkbox state."""
    result=driver.execute_script("""
      const wanted=arguments[0];
      const inputs=Array.from(document.querySelectorAll('input[type=checkbox]'));
      function labelText(el) {
        const label=el.labels && el.labels.length
          ? Array.from(el.labels).map(x=>x.innerText||'').join(' ')
          : '';
        const parent=el.closest('label') || el.parentElement;
        return (label || (parent && parent.innerText) || '').replace(/\\s+/g,' ').trim();
      }
      function dateKey(s) {
        const m=s.match(/\\b(\\d{1,2})\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\b(?:\\s+(\\d{4}))?/i);
        if(!m) return null;
        const months={jan:'01',feb:'02',mar:'03',apr:'04',may:'05',jun:'06',jul:'07',aug:'08',sep:'09',oct:'10',nov:'11',dec:'12'};
        const year=m[3] || new Date().getFullYear();
        return String(year)+'-'+months[m[2].toLowerCase()]+'-'+String(m[1]).padStart(2,'0');
      }
      let target=null;
      for(const el of inputs) if(dateKey(labelText(el))===wanted) {target=el;break;}
      if(!target) return {ok:false, reason:'No checkbox matched expiry '+wanted};
      // Click only labels/inputs as required to cause the UI's React handlers.
      for(const el of inputs) {
        if(el!==target && el.checked) {
          const lab=(el.labels && el.labels[0]) || el;
          lab.click();
        }
      }
      if(!target.checked) {
        const lab=(target.labels && target.labels[0]) || target;
        lab.click();
      }
      return {ok:target.checked, selected:inputs.filter(x=>x.checked).map(x=>labelText(x)),
              targetLabel:labelText(target)};
    """, expiry_date)
    time.sleep(2)
    return result or {'ok':False,'reason':'Checkbox selection returned no result'}


def _wsh_extract_option_rows_from_current_page(driver):
    soup=BeautifulSoup(driver.page_source,'html.parser')
    rows=_wsh_json_option_rows(soup)
    if not rows:
        rows=_wsh_html_option_rows(soup)
    return rows, soup, _wsh_page_diagnostics(driver,soup)


def extract_sensibull_oi_vs_strike(driver, manifest_tracker):
    """Collect OI-vs-Strike separately, discovering expiry checkboxes dynamically.

    If the chart does not expose a validated numeric series in embedded JSON or
    semantic tables, this adapter records a diagnostic and marks the dataset
    invalid/partial rather than manufacturing values from bar heights.
    """
    symbols=['NIFTY','BANKNIFTY','FINNIFTY','SENSEX']
    payload='\n\n==================================================\nWSH PRIE: SENSIBULL OI VS STRIKE (DYNAMIC EXPIRY SELECTION)\n==================================================\n'
    pub_state='PUBLISHED' if is_market_trading_day() else 'MARKET_CLOSED'
    for symbol in symbols:
        url=f'https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}'
        try:
            driver.get(url)
            WebDriverWait(driver,20).until(EC.presence_of_element_located((By.TAG_NAME,'body')))
            time.sleep(5)
            initial_soup=BeautifulSoup(driver.page_source,'html.parser')
            diagnostics=_wsh_page_diagnostics(driver,initial_soup)
            payload+=f'\n\n--- OI VS STRIKE: {symbol} ---\nRequested URL: {url}\nResolved URL: {diagnostics["current_url"]}\nPage title: {diagnostics["title"]}\n'
            if diagnostics['fallback_markers'] or symbol.lower() not in diagnostics['current_url'].lower():
                reasons=[f'Fallback/error page or symbol route mismatch: {diagnostics["fallback_markers"]}']
                manifest_tracker.record_dataset('Sensibull',f'oi_vs_strike_{symbol.lower()}','PUBLISHED','FAILED','INVALID',
                    ['expiry_selection','strike','call_oi','put_oi'],0,reasons)
                payload+='[STATUS: FAILED / INVALID]\n[REASON]: '+ '; '.join(reasons)+'\n'
                continue
            expiries=_wsh_discover_expiry_checkboxes(driver)
            if not expiries:
                reasons=['No expiry checkbox labels with parseable dates were discovered in the rendered page.']
                manifest_tracker.record_dataset('Sensibull',f'oi_vs_strike_{symbol.lower()}',pub_state,'FAILED','INVALID',
                    ['expiry_selection','strike','call_oi','put_oi'],0,reasons)
                payload+='[STATUS: FAILED / INVALID]\n[REASON]: '+reasons[0]+'\n'
                payload+='[DIAGNOSTIC]: '+diagnostics['body_text'][:600]+'\n'
                continue
            payload+='[DISCOVERED EXPIRIES]: '+', '.join(f"{x['date']} ({x['label']})" for x in expiries)+'\n'
            all_rows=0
            for expiry in expiries:
                select_result=_wsh_select_single_expiry_checkbox(driver,expiry['date'])
                time.sleep(2)
                rows,soup,diag=_wsh_extract_option_rows_from_current_page(driver)
                payload+=f"\n[EXPIRY TARGET: {expiry['date']}] selection={json.dumps(select_result,ensure_ascii=False)}\n"
                if select_result.get('ok') and len(rows)>=8:
                    all_rows+=len(rows)
                    payload+=f'[STRUCTURED SERIES ROWS]: {len(rows)}\nstrike | call_oi | put_oi | call_change_oi | put_change_oi\n'
                    for r in rows[:100]:
                        payload+=f"{r['strike']:g} | {r['call_oi']:g} | {r['put_oi']:g} | {r['call_change_oi']:g} | {r['put_change_oi']:g}\n"
                    state='PARTIAL' # UI selection is verified; semantics/source payload still need live acceptance testing.
                    reasons=['Structured rows found after expiry selection; chart totals and source semantics still require independent cross-check.']
                else:
                    state='INVALID'
                    reasons=[select_result.get('reason') or f'No validated numeric strike series found for {expiry["date"]}; chart may render as SVG/canvas without semantic rows.']
                    payload+=f'[DATA STATUS: INVALID] {reasons[0]}\n[PAGE DIAGNOSTIC]: {diag["body_text"][:500]}\n'
                manifest_tracker.record_dataset('Sensibull',f'oi_vs_strike_{symbol.lower()}_{expiry["date"]}',pub_state,
                    'SUCCESS' if rows else 'PARTIAL',state,['expiry_selection','strike','call_oi','put_oi'],len(rows),reasons)
            manifest_tracker.record_dataset('Sensibull',f'oi_vs_strike_{symbol.lower()}_index',pub_state,
                'SUCCESS' if all_rows else 'PARTIAL','PARTIAL' if all_rows else 'INVALID',
                ['expiry_selection','strike','call_oi','put_oi'],all_rows,
                [] if all_rows else ['Expiry controls were discovered, but no structured numeric OI series was validated.'])
        except Exception as e:
            reasons=[f'{type(e).__name__}: {e}']
            payload+=f'\n[OI VS STRIKE ERROR: {symbol}] {reasons[0]}\n'
            manifest_tracker.record_dataset('Sensibull',f'oi_vs_strike_{symbol.lower()}',pub_state,'FAILED','INVALID',
                ['expiry_selection','strike','call_oi','put_oi'],0,reasons)
    return payload


def extract_sensibull_option_chains(driver, manifest_tracker):
    """Collect the separate expiry-specific Option Chain page.

    Expiry candidates are discovered from the OI-vs-Strike expiry UI rather than
    hard-coded. The page is still marked invalid unless structured numeric
    strike records can be extracted and the symbol/expiry route is preserved.
    """
    symbols=['NIFTY','BANKNIFTY','FINNIFTY','SENSEX']
    payload='\n\n==================================================\nWSH PRIE: SENSIBULL OPTION CHAIN (SEPARATE EXPIRY-SPECIFIC SOURCE)\n==================================================\n'
    pub_state='PUBLISHED' if is_market_trading_day() else 'MARKET_CLOSED'
    for symbol in symbols:
        try:
            discover_url=f'https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}'
            driver.get(discover_url)
            WebDriverWait(driver,20).until(EC.presence_of_element_located((By.TAG_NAME,'body')))
            time.sleep(4)
            expiry_candidates=_wsh_discover_expiry_checkboxes(driver)
            if not expiry_candidates:
                reason='Could not discover expiry dates from the rendered Sensibull expiry checkboxes.'
                manifest_tracker.record_dataset('Sensibull',f'option_chain_{symbol.lower()}_discovery',pub_state,'FAILED','INVALID',
                    ['symbol','expiry','strike','call_oi','put_oi'],0,[reason])
                payload+=f'\n--- OPTION CHAIN {symbol} ---\n[STATUS: FAILED / INVALID] {reason}\n'
                continue
            payload+=f'\n--- OPTION CHAIN INDEX: {symbol} ---\n[DISCOVERED EXPIRIES]: '+', '.join(x['date'] for x in expiry_candidates)+'\n'
            for expiry in expiry_candidates:
                url=f'https://web.sensibull.com/option-chain?view=all&tradingsymbol={symbol}&expiry={expiry["date"]}'
                # Prevent frames from a previously visited index/expiry contaminating this capture.
                _wsh_clear_performance_logs(driver)
                driver.get(url)
                WebDriverWait(driver,20).until(EC.presence_of_element_located((By.TAG_NAME,'body')))
                time.sleep(5)
                # Capture binary WebSocket payloads from this page in addition to HTML.
                ws_result = capture_sensibull_ws_option_chain(driver, symbol, expiry["date"], manifest_tracker)
                payload += (f"\n[WEBSOCKET CAPTURE]: status={ws_result['status']} "
                            f"frames_seen={ws_result['frames_seen']} rows={len(ws_result['rows'])} "
                            f"reasons={'; '.join(ws_result['reasons'])}\n")
                rows,soup,diag=_wsh_extract_option_rows_from_current_page(driver)
                current=diag['current_url'].lower()
                route_ok=(f'tradingsymbol={symbol.lower()}' in current and f'expiry={expiry["date"]}' in current)
                payload+=f'\n[REQUESTED CONTRACT]: {symbol} {expiry["date"]}\n[REQUESTED URL]: {url}\n[RESOLVED URL]: {diag["current_url"]}\n[PAGE TITLE]: {diag["title"]}\n'
                if diag['fallback_markers'] or not route_ok:
                    state='INVALID'; ext='FAILED'
                    reasons=[f'Fallback/error page or symbol/expiry route mismatch; markers={diag["fallback_markers"]}; route_ok={route_ok}.']
                    rows=[]
                elif len(rows)>=8:
                    # Do not overstate VALID until cross-checks against displayed totals are implemented.
                    state='PARTIAL'; ext='SUCCESS'
                    reasons=['Structured strike rows found; expiry totals, lot-size semantics and live-page reconciliation still require acceptance testing.']
                    payload+='strike | call_oi | put_oi | call_change_oi | put_change_oi\n'
                    for r in rows[:100]:
                        payload+=f"{r['strike']:g} | {r['call_oi']:g} | {r['put_oi']:g} | {r['call_change_oi']:g} | {r['put_change_oi']:g}\n"
                else:
                    state='INVALID'; ext='PARTIAL' if rows else 'FAILED'
                    reasons=[f'Only {len(rows)} structured strike rows found; at least 8 required.']
                    payload+='[DIAGNOSTIC]: '+diag['body_text'][:600]+'\n'
                payload+=f'[STATUS: EXT={ext} | VAL={state} | ROWS={len(rows)}]\n[REASON]: '+ '; '.join(reasons)+'\n'
                manifest_tracker.record_dataset('Sensibull',f'option_chain_{symbol.lower()}_{expiry["date"]}',pub_state,ext,state,
                    ['symbol','expiry','strike','call_oi','put_oi','call_change_oi','put_change_oi'],len(rows),reasons)
        except Exception as e:
            reasons=[f'{type(e).__name__}: {e}']
            payload+=f'\n[OPTION CHAIN ERROR: {symbol}] {reasons[0]}\n'
            manifest_tracker.record_dataset('Sensibull',f'option_chain_{symbol.lower()}_adapter',pub_state,'FAILED','INVALID',
                ['symbol','expiry','strike','call_oi','put_oi'],0,reasons)
    return payload

# ==========================================================
# 2. SENSIBULL FII/DII PARTICIPANT POSITIONING ADAPTER (PRIE)
# ==========================================================
def extract_sensibull_fii_dii_tabs(driver, manifest_tracker):
    payload = "\n\n==================================================\n"
    payload += "WSH PRIE: FII/DII PARTICIPANT POSITIONING (TAB FINGERPRINTING)\n"
    payload += "==================================================\n"

    tabs = ["Summary", "Futures and Options", "Cash Market", "FII History"]
    seen_content_signatures = set()
    pub_state = "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED"

    try:
        driver.get("https://web.sensibull.com/fii-dii-data")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)

        for tab_name in tabs:
            payload += f"\n\n--- PRIE FII/DII TAB TARGET: {tab_name} ---\n"
            dataset_key = f"fii_dii_{tab_name.lower().replace(' ', '_')}"
            try:
                tab_xpath = (
                    f"//*[self::button or self::a or self::div or self::span]"
                    f"[normalize-space(text())='{tab_name}']"
                )
                tab_element = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, tab_xpath))
                )
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)

                soup = BeautifulSoup(driver.page_source, "html.parser")
                tables = soup.find_all("table")
                rows_captured = 0
                data_rows = 0
                signature_parts = []

                for tbl in tables:
                    signature_parts.append(re.sub(r"\s+", " ", tbl.get_text(" ", strip=True)).strip())
                table_signature = "||".join(signature_parts)

                if not tables or not table_signature.strip("| "):
                    ext_state, val_state = "FAILED", "INVALID"
                    reasons = ["No non-empty tables rendered."]
                elif table_signature in seen_content_signatures and tab_name != "Summary":
                    ext_state, val_state = "SUCCESS", "QUARANTINED"
                    reasons = ["Cross-tab fingerprint match: all rendered table content duplicates an earlier tab."]
                else:
                    seen_content_signatures.add(table_signature)
                    for idx, tbl in enumerate(tables):
                        payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                        for row_index, row in enumerate(tbl.find_all("tr")):
                            cols = row.find_all(["td", "th"])
                            r_data = [c.get_text(" ", strip=True) for c in cols]
                            if r_data:
                                payload += " | ".join(r_data) + "\n"
                                rows_captured += 1
                                if row_index > 0 and any(value.strip() for value in r_data):
                                    data_rows += 1
                    header_text = " ".join(
                        _wsh_norm_key(c.get_text(" ", strip=True))
                        for tbl in tables[:3]
                        for row in tbl.find_all("tr")[:1]
                        for c in row.find_all(["th", "td"])
                    )
                    has_participant = any(k in header_text for k in ("participant", "clienttype", "category", "participanttype"))
                    has_flow = any(k in header_text for k in ("net", "long", "short", "buy", "sell", "oi", "openinterest", "value"))
                    has_date = any(k in header_text for k in ("date", "day", "expiry"))
                    if data_rows and has_participant and has_flow and has_date:
                        ext_state, val_state, reasons = "SUCCESS", "PARTIAL", [
                            "Recognized participant/flow/date-like headers; source-specific semantics and units still require review."
                        ]
                    elif data_rows:
                        ext_state, val_state, reasons = "SUCCESS", "INVALID", [
                            "Rows exist, but required participant, flow and date-like headers were not all detected."
                        ]
                    else:
                        ext_state, val_state, reasons = "PARTIAL", "INVALID", [
                            "Tables rendered but no data rows were found."
                        ]

                payload += (
                    f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | "
                    f"VAL: {val_state} | Rows: {rows_captured}]\n"
                )
                if reasons:
                    payload += "[REASON]: " + "; ".join(reasons) + "\n"

                manifest_tracker.record_dataset(
                    "Sensibull", dataset_key, pub_state, ext_state, val_state,
                    ["participant", "net_flow", "date"], rows_captured, reasons
                )
            except Exception as e:
                reasons = [f"{type(e).__name__}: {e}"]
                payload += f"[STATUS -> PUB: {pub_state} | EXT: FAILED | VAL: INVALID | REASON: {reasons[0]}]\n"
                manifest_tracker.record_dataset(
                    "Sensibull", dataset_key, pub_state, "FAILED", "INVALID",
                    ["participant", "net_flow", "date"], 0, reasons
                )
    except Exception as e:
        payload += f"\n[Portal Error: {type(e).__name__}: {e}]\n"

    return payload

# ==========================================================
# 3. CCIL MULTI-MARKET CALENDAR-AWARE ADAPTER (GSSE)
# ==========================================================
def extract_ccil_multi_tabs(driver, manifest_tracker):
    payload = f"\n\n==================================================\n"
    payload += f"WSH GSSE: CCIL MULTI-MARKET ISOLATION (CALENDAR-AWARE)\n"
    payload += f"==================================================\n"
    
    tabs = ["Money Market", "Gsec", "Forex", "Derivatives"]
    is_trading = is_market_trading_day()
    pub_state = "PUBLISHED" if is_trading else "MARKET_CLOSED"

    try:
        driver.get("https://www.ccilindia.com/")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(6)
        
        for tab_name in tabs:
            payload += f"\n\n--- GSSE CCIL SUB-MARKET TAB: {tab_name} ---\n"
            
            # If weekend, handle as expected non-publication rather than script failure
            if not is_trading:
                payload += f"\n[STATUS -> PUB: MARKET_CLOSED | EXT: NOT_ATTEMPTED | VAL: NOT_RUN | Note: Weekend non-publication window]\n"
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", "MARKET_CLOSED", "NOT_ATTEMPTED", "NOT_RUN", ["instrument", "rate"], 0, ["Expected weekend non-publication"])
                continue

            try:
                tab_xpath = f"//*[self::a or self::button or self::span][contains(normalize-space(text()), '{tab_name}')]"
                tab_element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, tab_xpath)))
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(4)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                tables = soup.find_all('table')
                
                if tables:
                    table_text = tables[0].get_text(strip=True)
                    if tab_name == "Gsec" and any(k in table_text for k in ["CALL", "TREP", "SPECIAL REPO", "BASKET REPO"]):
                        ext_state, val_state = "SUCCESS", "QUARANTINED"
                        reasons = ["Contamination detected: Gsec table contains Money Market instruments."]
                    elif len(table_text) < 20:
                        ext_state, val_state = "SUCCESS", "INVALID"
                        reasons = ["Extracted table is empty."]
                    elif not any(_wsh_has_expected_table_schema(tbl, (("instrument", "security", "tenor", "product", "currency", "market"), ("rate", "yield", "price", "value", "amount", "quote"))) for tbl in tables):
                        ext_state, val_state = "SUCCESS", "INVALID"
                        reasons = ["Table content exists, but no table header matched expected instrument/market and rate/value fields; not accepted as valid CCIL data."]
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Diagnostic table #{idx + 1} for {tab_name}; NOT VALIDATED]\n"
                            payload += re.sub(r"\s+", " ", tbl.get_text(" ", strip=True))[:1200] + "\n"
                    else:
                        ext_state, val_state, reasons = "SUCCESS", "PARTIAL", ["Header pattern matched, but source-specific CCIL market isolation and units still require verification."]
                        for idx, tbl in enumerate(tables):
                            payload += f"\n[Table #{idx + 1} for {tab_name}]\n"
                            for row in tbl.find_all('tr'):
                                cols = row.find_all(['td', 'th'])
                                r_data = [c.get_text(strip=True) for c in cols]
                                if r_data:
                                    payload += " | ".join(r_data) + "\n"
                    
                    payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                else:
                    ext_state, val_state = "FAILED", "INVALID"
                    reasons = ["No table elements found."]
                    payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state}]\n"
                
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", pub_state, ext_state, val_state, ["instrument", "rate"], 1 if val_state=="VALID" else 0, reasons)

            except Exception as e:
                manifest_tracker.record_dataset("CCIL", f"ccil_{tab_name.lower().replace(' ', '_')}", pub_state, "FAILED", "INVALID", ["instrument", "rate"], 0, [str(e)])
                
    except Exception as e:
        payload += f"\n[CCIL Portal General Error: {str(e)}]\n"
        
    return payload

# ==========================================================
# 4. TRADINGECONOMICS ADAPTER (GSSE)
# ==========================================================
def extract_tradingeconomics_page(driver, url, dataset_key, manifest_tracker):
    payload = "\n\n==================================================\n"
    payload += f"WSH GSSE: TRADINGECONOMICS EXTRACTION: {url}\n"
    payload += "==================================================\n"
    pub_state = "PUBLISHED"
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        tables = soup.find_all("table")
        rows_captured = 0
        data_rows = 0

        for idx, tbl in enumerate(tables):
            payload += f"\n[Table #{idx + 1}]\n"
            rows = tbl.find_all("tr")
            for row_index, row in enumerate(rows):
                cols = row.find_all(["td", "th"])
                r_data = [c.get_text(" ", strip=True) for c in cols]
                if r_data:
                    payload += " | ".join(r_data) + "\n"
                    rows_captured += 1
                    if row_index > 0 and any(value.strip() for value in r_data):
                        data_rows += 1

        if data_rows > 0:
            ext_state, val_state = "SUCCESS", "PARTIAL"
            reasons = [
                "Table rows extracted; indicator/actual/previous fields and source-specific record schema still require validation."
            ]
            payload += (
                f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state} | "
                f"Tables: {len(tables)} | Data rows: {data_rows}]\n"
            )
        else:
            ext_state, val_state = ("FAILED", "INVALID") if not tables else ("PARTIAL", "INVALID")
            reasons = ["No usable table data rows found."]
            payload += f"\n[STATUS -> PUB: {pub_state} | EXT: {ext_state} | VAL: {val_state} | Data rows: 0]\n"

        manifest_tracker.record_dataset(
            "TradingEconomics", dataset_key, pub_state, ext_state, val_state,
            ["indicator", "actual", "previous"], rows_captured, reasons
        )
    except Exception as e:
        reasons = [f"{type(e).__name__}: {e}"]
        payload += f"\n[STATUS -> PUB: {pub_state} | EXT: FAILED | VAL: INVALID | REASON: {reasons[0]}]\n"
        manifest_tracker.record_dataset(
            "TradingEconomics", dataset_key, pub_state, "FAILED", "INVALID",
            ["indicator", "actual", "previous"], 0, reasons
        )
    return payload

# ==========================================================
# 5. ORCHESTRATOR & PACKAGE WRITER
# ==========================================================
def execute_wsh_institutional_pipeline():
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    
    manifest = InstitutionalQualityManifest()
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    # Required for Chrome DevTools Network.webSocketFrameReceived events.
    chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=chrome_options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception as exc:
        print(f"[WEBSOCKET CAPTURE WARNING] CDP Network.enable failed: {exc}")
    try:
        prie_payload = f"=== WSH PRIE DATA PACKAGE [Multi-Dimensional Audit] - {timestamp_str} ===\n"
        prie_payload += extract_sensibull_option_chains(driver, manifest)
        prie_payload += extract_sensibull_oi_vs_strike(driver, manifest)
        prie_payload += extract_sensibull_fii_dii_tabs(driver, manifest)

        gsse_payload = f"=== WSH GSSE DATA PACKAGE [Calendar-Aware Governance] - {timestamp_str} ===\n"
        gsse_payload += extract_ccil_multi_tabs(driver, manifest)
        gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/bonds", "gsse_sovereign_bonds", manifest)
        gsse_payload += extract_tradingeconomics_page(driver, "https://tradingeconomics.com/calendar", "gsse_macro_calendar", manifest)
    finally:
        driver.quit()

    run_date_str = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    
    with open(os.path.join(folder_path, f"WSH_PRIE_Data_Package_{run_date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(prie_payload)
        
    with open(os.path.join(folder_path, f"WSH_GSSE_Data_Package_{run_date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(gsse_payload)
        
    # Record the exact script fingerprint in a small provenance file for run-to-code auditing.
    try:
        with open(__file__, "rb") as source_file:
            source_hash = hashlib.sha256(source_file.read()).hexdigest()
        with open(os.path.join(folder_path, f"WSH_Source_Fingerprint_{run_date_str}.txt"), "w", encoding="utf-8") as fp:
            fp.write(f"pipeline_version=WSH_COLLECTOR_RND_2026-10-24_WS-CAPTURE_4_QUOTE_AUDIT_V7\n")
            fp.write(f"source_file={os.path.basename(__file__)}\nsha256={source_hash}\n")
    except Exception as e:
        print(f"[PROVENANCE WARNING] Could not write source fingerprint: {e}")

    manifest.save_manifest(folder_path)
    print(f"\n[PIPELINE COMPLETE] Collection completed; inspect per-dataset validation states before using data.")

if __name__ == "__main__":
    execute_wsh_institutional_pipeline()

