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
            "pipeline_version": "WSH_COLLECTOR_RND_2026-10-31_ISOLATION-MANIFEST-CANDIDATE_2",
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
        """Persist a collision-resistant manifest using an atomic replace."""
        os.makedirs(folder_path, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"WSH_Quality_Manifest_{stamp}.json"
        filepath = os.path.join(folder_path, filename)
        temporary_path = filepath + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, filepath)
        print(f"[MANIFEST SAVED] Multi-dimensional quality audit written to {filepath}")
        return filepath

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
_WSH_WS_MAX_SOURCE_AGE_SECONDS = 120
_WSH_WS_FUTURE_CLOCK_TOLERANCE_SECONDS = 5
_WSH_WS_OBSERVATION_WINDOW_SECONDS = 8.0
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


def _wsh_normalize_identity(value):
    """Normalize an explicit source identity value for strict comparison."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _wsh_find_payload_identity(data):
    """Inspect metadata outside the strike chain for explicit underlying identity."""
    identity_keys = {
        "symbol", "underlying", "tradingsymbol", "underlying_symbol",
        "underlying_name", "instrument_name", "exchange_symbol",
    }
    found = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if key == "chain":
                    continue
                current_path = f"{path}.{key}" if path else str(key)
                if normalized_key in identity_keys and isinstance(value, (str, int)):
                    found.append({"path": current_path, "value": str(value)})
                elif isinstance(value, (dict, list)):
                    walk(value, current_path)
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                if isinstance(value, (dict, list)):
                    walk(value, f"{path}[{index}]")

    walk(data)
    # Stable de-duplication without losing source paths.
    unique = []
    seen = set()
    for item in found:
        marker = (item["path"], item["value"])
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    return unique


def _wsh_parse_source_timestamp(value):
    """Parse supported source timestamps; return UTC epoch seconds or None."""
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        numeric = float(value)
        if numeric >= 1e12:
            numeric /= 1000.0
        if numeric >= 1e9:
            return numeric
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            numeric = float(candidate)
            return _wsh_parse_source_timestamp(numeric)
        except ValueError:
            pass
        try:
            parsed = datetime.datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def _wsh_find_payload_timestamps(data):
    """Find explicit source timestamp fields outside option-chain strike records."""
    timestamp_keys = {
        "timestamp", "lastupdated", "updatedat", "lastupdatetime",
        "exchangetimestamp", "datatimestamp", "markettimestamp",
        "servertime", "updatedtimestamp",
    }
    found = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if key == "chain":
                    continue
                current_path = f"{path}.{key}" if path else str(key)
                if normalized_key in timestamp_keys:
                    epoch = _wsh_parse_source_timestamp(value)
                    found.append({
                        "path": current_path,
                        "value": value if isinstance(value, (str, int, float)) else str(value),
                        "epoch": epoch,
                    })
                elif isinstance(value, (dict, list)):
                    walk(value, current_path)
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                if isinstance(value, (dict, list)):
                    walk(value, f"{path}[{index}]")

    walk(data)
    return [item for item in found if item["epoch"] is not None]


def capture_sensibull_ws_option_chain(driver, symbol, expiry, manifest_tracker, output_folder="WSH_Automated_Reports"):
    """Capture and independently assess route, payload identity and source freshness.

    Route matching and encoded expiry are necessary but insufficient for VALID.
    Payload identity must explicitly match the requested underlying, a source
    timestamp must be fresh, and at least two distinct snapshots must be observed.
    """
    dataset_key = f"sensibull_ws_{symbol.lower()}_{expiry}"
    captured_at = datetime.datetime.now(datetime.timezone.utc)
    route_ok, current_url = _wsh_contract_route_matches(driver, symbol, expiry)
    result = {
        "symbol": symbol,
        "requested_expiry": expiry,
        "captured_at_utc": captured_at.isoformat(),
        "page_url_at_capture": current_url,
        "route_identity_check": "PASS" if route_ok else "FAIL",
        "payload_identity_status": "NOT_CHECKED",
        "source_freshness_status": "NOT_CHECKED",
        "status": "NOT_CAPTURED",
        "frames_seen": 0,
        "matching_expiry_frames": 0,
        "distinct_snapshot_count": 0,
        "rows": [],
        "summary": {},
        "reasons": [],
    }
    if not route_ok:
        reason = f"Current page route does not match requested contract {symbol} {expiry}: {current_url}"
        result["reasons"] = [reason]
        manifest_tracker.record_dataset("Sensibull-WebSocket", dataset_key, "PUBLISHED", "FAILED", "INVALID",
            ["symbol", "expiry", "strike", "call_oi", "put_oi"], 0, [reason])
        return result

    # Collect throughout a bounded observation window. A single frame is not
    # treated as proof of successive updates.
    observation_seconds = _WSH_WS_OBSERVATION_WINDOW_SECONDS
    deadline = time.monotonic() + observation_seconds
    snapshots = {}
    identity_evidence = []
    timestamp_evidence = []
    frame_receipts = []

    while time.monotonic() < deadline:
        try:
            logs = driver.get_log("performance")
        except Exception as exc:
            logs = []
            result["reasons"].append(
                f"Chrome performance log unavailable: {type(exc).__name__}: {exc}"
            )

        for entry in logs:
            try:
                envelope = json.loads(entry.get("message", "{}"))
                message = envelope.get("message", {})
                if message.get("method") != "Network.webSocketFrameReceived":
                    continue
                response = message.get("params", {}).get("response", {})
                result["frames_seen"] += 1
                if int(response.get("opcode", 0)) != 2:
                    continue
                decoded = _wsh_unpack_ws_payload(response.get("payloadData", ""))
                if not decoded:
                    continue

                data = decoded["data"]
                encoded_expiry_bytes = decoded["raw"][:decoded["gzip_offset"]]
                expiry_match = re.search(rb"20\d{6}", encoded_expiry_bytes)
                encoded_expiry = expiry_match.group(0).decode("ascii") if expiry_match else None
                if encoded_expiry != expiry.replace("-", ""):
                    continue

                rows, issues = _wsh_ws_chain_rows(data)
                if not rows or issues:
                    continue

                result["matching_expiry_frames"] += 1
                decompressed_hash = hashlib.sha256(
                    json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                received_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                frame_receipts.append(received_at)
                frame_identity = _wsh_find_payload_identity(data)
                frame_timestamps = _wsh_find_payload_timestamps(data)
                snapshots.setdefault(decompressed_hash, {
                    "data": data,
                    "rows": rows,
                    "encoded_expiry": encoded_expiry,
                    "binary_message_sha256": hashlib.sha256(decoded["raw"]).hexdigest(),
                    "decompressed_json_sha256": decompressed_hash,
                    "received_at_utc": received_at,
                    "identity_evidence": frame_identity,
                    "timestamp_evidence": frame_timestamps,
                })
                identity_evidence.extend(frame_identity)
                timestamp_evidence.extend(frame_timestamps)
            except (ValueError, TypeError, KeyError):
                continue

        if time.monotonic() < deadline:
            time.sleep(0.5)

    result["distinct_snapshot_count"] = len(snapshots)
    result["frame_receipts_utc"] = frame_receipts
    result["payload_identity_evidence"] = identity_evidence
    result["source_timestamp_evidence"] = timestamp_evidence

    if not snapshots:
        reason = result["reasons"] or [
            "No binary WebSocket frame decoded into a structurally valid chain with the requested encoded expiry."
        ]
        result["reasons"] = reason
        pub = "MARKET_CLOSED" if not is_market_trading_day() else "PUBLISHED"
        manifest_tracker.record_dataset("Sensibull-WebSocket", dataset_key, pub, "FAILED", "INVALID",
            ["symbol", "expiry", "strike", "call_oi", "put_oi"], 0, reason)
        return result

    # Select the latest distinct snapshot received in this observation window.
    selected = max(snapshots.values(), key=lambda item: item["received_at_utc"])
    data = selected["data"]
    rows = selected["rows"]
    result.update({
        "rows": rows,
        "encoded_expiry": selected["encoded_expiry"],
        "summary": {
            key: data.get(key) for key in (
                "atm_strike", "atm_iv", "atm_iv_change", "atm_iv_percentile",
                "pcr", "max_pain_strike", "atm_ivp_type", "future_price"
            )
        },
        "binary_message_sha256": selected["binary_message_sha256"],
        "decompressed_json_sha256": selected["decompressed_json_sha256"],
    })

    requested_identity = _wsh_normalize_identity(symbol)
    selected_identity_evidence = selected.get("identity_evidence", [])
    observed_identities = {
        _wsh_normalize_identity(item["value"])
        for item in selected_identity_evidence
    }
    if observed_identities:
        if observed_identities == {requested_identity}:
            result["payload_identity_status"] = "PASS"
        else:
            result["payload_identity_status"] = "MISMATCH"
            result["reasons"].append(
                f"Payload identity evidence does not match requested symbol {symbol}: {identity_evidence}"
            )
    else:
        result["payload_identity_status"] = "UNPROVEN"
        result["reasons"].append(
            "Decoded payload exposes no recognized underlying identity field; route identity alone is insufficient."
        )

    now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
    selected_timestamp_evidence = selected.get("timestamp_evidence", [])
    parsed_timestamps = [item for item in selected_timestamp_evidence if item.get("epoch") is not None]
    if parsed_timestamps:
        latest_source_timestamp = max(item["epoch"] for item in parsed_timestamps)
        source_age = now_epoch - latest_source_timestamp
        result["source_timestamp_latest_epoch"] = latest_source_timestamp
        result["source_timestamp_age_seconds"] = round(source_age, 3)
        if source_age < -_WSH_WS_FUTURE_CLOCK_TOLERANCE_SECONDS:
            result["source_freshness_status"] = "FUTURE_TIMESTAMP"
            result["reasons"].append("Source timestamp is more than five seconds in the future.")
        elif source_age > _WSH_WS_MAX_SOURCE_AGE_SECONDS:
            result["source_freshness_status"] = "STALE"
            result["reasons"].append(
                f"Latest source timestamp is {source_age:.1f}s old; configured freshness limit is {_WSH_WS_MAX_SOURCE_AGE_SECONDS}s."
            )
        else:
            result["source_freshness_status"] = "FRESH"
    else:
        result["source_freshness_status"] = "UNPROVEN"
        result["reasons"].append(
            "No recognized source timestamp was exposed; receipt time cannot certify market-data freshness."
        )

    if len(snapshots) >= 2:
        result["successive_snapshot_status"] = "MULTIPLE_DISTINCT_SNAPSHOTS"
    else:
        result["successive_snapshot_status"] = "SINGLE_DISTINCT_SNAPSHOT"
        result["reasons"].append(
            "Only one distinct decoded snapshot was observed during the 8-second window."
        )

    if result["payload_identity_status"] == "MISMATCH":
        validation_status = "INVALID"
    elif (
        result["payload_identity_status"] == "PASS"
        and result["source_freshness_status"] == "FRESH"
        and len(snapshots) >= 2
    ):
        validation_status = "VALID"
    else:
        validation_status = "PARTIAL"

    result["status"] = validation_status
    result["observation_window_seconds"] = observation_seconds
    result["reasons"] = list(dict.fromkeys(result["reasons"]))

    os.makedirs(output_folder, exist_ok=True)
    safe_expiry = expiry.replace("-", "")
    outfile = os.path.join(
        output_folder,
        f"WSH_Sensibull_WS_{symbol}_{safe_expiry}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    with open(outfile, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    result["output_file"] = outfile

    pub = "MARKET_CLOSED" if not is_market_trading_day() else "PUBLISHED"
    manifest_tracker.record_dataset(
        "Sensibull-WebSocket",
        dataset_key,
        pub,
        "SUCCESS",
        validation_status,
        ["symbol", "expiry", "strike", "call_oi", "put_oi", "call_change_oi", "put_change_oi"],
        len(rows),
        result["reasons"],
    )
    print(
        f"[WSH WEBSOCKET RESULT] symbol={symbol} expiry={expiry} "
        f"validation={validation_status} payload_identity={result['payload_identity_status']} "
        f"freshness={result['source_freshness_status']} distinct_snapshots={len(snapshots)} "
        f"rows={len(rows)} reasons={json.dumps(result['reasons'], ensure_ascii=False)}"
    )
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
                # Emit evidence directly to Actions logs; package-only diagnostics are easy to miss.
                try:
                    checkbox_evidence = driver.execute_script("""return Array.from(document.querySelectorAll('input[type=checkbox]')).map((el)=>({checked:!!el.checked, text:((el.labels&&el.labels.length)?Array.from(el.labels).map(x=>x.innerText||'').join(' '):((el.closest('label')||el.parentElement||{}).innerText||'')).replace(/\\s+/g,' ').trim()})).slice(0,40);""") or []
                except Exception as diag_exc:
                    checkbox_evidence = [{'diagnostic_error': f'{type(diag_exc).__name__}: {diag_exc}'}]
                print(f"[WSH ACQUISITION DIAGNOSTIC] adapter=oi_vs_strike symbol={symbol} requested_url={url!r} resolved_url={diagnostics['current_url']!r} title={diagnostics['title']!r} fallback_markers={diagnostics['fallback_markers']!r} checkbox_count={len(checkbox_evidence)} checkbox_evidence={json.dumps(checkbox_evidence, ensure_ascii=False)[:2500]} body_excerpt={diagnostics['body_text'][:700]!r}")
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

    Expiry candidates are discovered from the option-chain page's rendered UI rather than
    hard-coded. The page is still marked invalid unless structured numeric
    strike records can be extracted and the symbol/expiry route is preserved.
    """
    symbols=['NIFTY','BANKNIFTY','FINNIFTY','SENSEX']
    payload='\n\n==================================================\nWSH PRIE: SENSIBULL OPTION CHAIN (SEPARATE EXPIRY-SPECIFIC SOURCE)\n==================================================\n'
    pub_state='PUBLISHED' if is_market_trading_day() else 'MARKET_CLOSED'
    for symbol in symbols:
        try:
            discover_url=f'https://web.sensibull.com/option-chain?view=all&tradingsymbol={symbol}'
            driver.get(discover_url)
            # Non-invasive browser-runtime probe: distinguish JS execution from app boot/access failures.
            try:
                runtime_probe = driver.execute_script("""return {
                    user_agent: (navigator && navigator.userAgent) || '',
                    webdriver: !!(navigator && navigator.webdriver),
                    window_type: typeof window,
                    document_type: typeof document,
                    ready_state: document ? document.readyState : 'unavailable',
                    script_count: document ? document.scripts.length : -1,
                    script_sources: document ? Array.from(document.scripts).map(s => s.src || '[inline]').slice(0,20) : [],
                    resource_scripts: (performance && performance.getEntriesByType) ? performance.getEntriesByType('resource').filter(r => /\\.js([?#]|$)|javascript/i.test(r.name)).map(r => ({name:r.name,duration_ms:Math.round(r.duration),transfer_size:r.transferSize||0})).slice(-20) : [],
                    title: document ? document.title : '',
                    body_text: document && document.body ? (document.body.innerText || '').slice(0,900) : '',
                    url: location ? location.href : '',
                    js_execution_probe: (function(){ try { return (1+1===2) ? 'PASS' : 'FAIL'; } catch(e) { return 'ERROR'; } })()
                };""")
                print(f"[WSH BROWSER RUNTIME] symbol={symbol} requested_url={discover_url!r} resolved_url={getattr(driver, 'current_url', '')!r} probe={json.dumps(runtime_probe, ensure_ascii=False)[:3500]}")
            except Exception as runtime_exc:
                print(f"[WSH BROWSER RUNTIME ERROR] symbol={symbol} requested_url={discover_url!r} resolved_url={getattr(driver, 'current_url', '')!r} error={type(runtime_exc).__name__}: {runtime_exc}")
            WebDriverWait(driver,20).until(EC.presence_of_element_located((By.TAG_NAME,'body')))
            time.sleep(4)
            expiry_candidates=_wsh_discover_expiry_checkboxes(driver)
            expiry_discovery_source='checkboxes'
            if not expiry_candidates:
                # Fallback: Sensibull may expose the current option-chain snapshot as
                # server-rendered text even when its expiry checkbox React UI did not boot.
                # Discover only dates actually present in the page; never inject a fixed ladder.
                try:
                    visible_text = driver.execute_script("return (document.body && document.body.innerText) || ''") or ''
                except Exception:
                    visible_text = BeautifulSoup(driver.page_source, 'html.parser').get_text(' ', strip=True)
                month_map={m.lower():i for i,m in enumerate(
                    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
                today=datetime.date.today()
                seen_dates=set()
                for match in re.finditer(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(?:\s+(20\d{2}))?\b', visible_text, re.I):
                    day=int(match.group(1)); month=month_map[match.group(2).lower()]
                    year=int(match.group(3)) if match.group(3) else today.year
                    try:
                        expiry_date=datetime.date(year, month, day)
                    except ValueError:
                        continue
                    if not match.group(3) and expiry_date < today - datetime.timedelta(days=2):
                        expiry_date=datetime.date(year+1, month, day)
                    if expiry_date < today - datetime.timedelta(days=2):
                        continue
                    iso=expiry_date.isoformat()
                    if iso not in seen_dates:
                        seen_dates.add(iso)
                        expiry_candidates.append({'date':iso,'label':match.group(0),'index':-1,'checked':False})
                if expiry_candidates:
                    expiry_discovery_source='visible_page_text_fallback'
                    print(f"[WSH EXPIRY FALLBACK] symbol={symbol} source={expiry_discovery_source} candidates={json.dumps(expiry_candidates, ensure_ascii=False)}")
            if not expiry_candidates:
                reason='Could not discover expiry dates from either rendered expiry checkboxes or visible page text.'
                try:
                    checkbox_evidence = driver.execute_script("""return Array.from(document.querySelectorAll('input[type=checkbox]')).map((el)=>({checked:!!el.checked, text:((el.labels&&el.labels.length)?Array.from(el.labels).map(x=>x.innerText||'').join(' '):((el.closest('label')||el.parentElement||{}).innerText||'')).replace(/\\s+/g,' ').trim()})).slice(0,40);""") or []
                except Exception as diag_exc:
                    checkbox_evidence = [{'diagnostic_error': f'{type(diag_exc).__name__}: {diag_exc}'}]
                current_url = str(getattr(driver, 'current_url', ''))
                current_title = str(getattr(driver, 'title', ''))
                body_excerpt = re.sub(r'\s+', ' ', BeautifulSoup(driver.page_source, 'html.parser').get_text(' ', strip=True))[:700]
                print(f"[WSH ACQUISITION DIAGNOSTIC] adapter=option_chain_discovery symbol={symbol} requested_url={discover_url!r} resolved_url={current_url!r} title={current_title!r} checkbox_count={len(checkbox_evidence)} checkbox_evidence={json.dumps(checkbox_evidence, ensure_ascii=False)[:2500]} body_excerpt={body_excerpt!r}")
                manifest_tracker.record_dataset('Sensibull',f'option_chain_{symbol.lower()}_discovery',pub_state,'FAILED','INVALID',
                    ['symbol','expiry','strike','call_oi','put_oi'],0,[reason])
                payload+=f'\n--- OPTION CHAIN {symbol} ---\n[STATUS: FAILED / INVALID] {reason}\n'
                continue
            payload+=f'\n--- OPTION CHAIN INDEX: {symbol} ---\n[EXPIRY DISCOVERY SOURCE]: {expiry_discovery_source}\n[DISCOVERED EXPIRIES]: '+', '.join(x['date'] for x in expiry_candidates)+'\n'
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
                print(f"[WSH WEBSOCKET RESULT] symbol={symbol} expiry={expiry['date']} status={ws_result['status']} route_identity={ws_result.get('route_identity_check')} frames_seen={ws_result.get('frames_seen')} rows={len(ws_result.get('rows', []))} reasons={json.dumps(ws_result.get('reasons', []), ensure_ascii=False)}")
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
        reason = f"FII/DII portal-level failure: {type(e).__name__}: {e}"
        payload += f"\n[Portal Error: {reason}]\n"
        # A page/navigation failure occurs outside the per-tab boundary. Explicitly
        # fail every tab not already represented; never let a text payload masquerade
        # as a successful adapter result.
        for tab_name in tabs:
            dataset_key = f"fii_dii_{tab_name.lower().replace(' ', '_')}"
            manifest_tracker.record_dataset(
                "Sensibull", dataset_key, pub_state, "FAILED", "INVALID",
                ["participant", "net_flow", "date"], 0, [reason]
            )

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
        reason = f"CCIL portal-level failure: {type(e).__name__}: {e}"
        payload += f"\n[CCIL Portal General Error: {reason}]\n"
        # The outer handler covers navigation/body failures before individual tab
        # handlers can run. Record every affected market explicitly.
        for tab_name in tabs:
            dataset_key = f"ccil_{tab_name.lower().replace(' ', '_')}"
            manifest_tracker.record_dataset(
                "CCIL", dataset_key, pub_state, "FAILED", "INVALID",
                ["instrument", "rate"], 0, [reason]
            )
        
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
def _wsh_manifest_checkpoint(manifest_tracker, folder_path):
    """Atomically refresh a stable checkpoint after each adapter boundary."""
    try:
        os.makedirs(folder_path, exist_ok=True)
        path = os.path.join(folder_path, "WSH_Quality_Manifest_CHECKPOINT.json")
        temporary_path = path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(manifest_tracker.manifest, handle, indent=4, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        print(f"[MANIFEST CHECKPOINT] {path}", flush=True)
        return True
    except Exception as exc:
        # A checkpoint failure must be visible but must not suppress other adapters.
        print(f"[MANIFEST CHECKPOINT ERROR] {type(exc).__name__}: {exc}", flush=True)
        return False


def _wsh_adapter_failure(manifest_tracker, adapter_key, exc, before=None):
    """Record escaped failure and quarantine any dataset records changed by this adapter."""
    pub_state = "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED"
    reason = f"Unhandled adapter exception: {type(exc).__name__}: {exc}"
    before = before or {}
    datasets = manifest_tracker.manifest.get("datasets", {})
    affected = []
    for key, item in list(datasets.items()):
        if key == f"orchestrator_{adapter_key}":
            continue
        if key not in before or item != before.get(key):
            affected.append(key)
            prior_reasons = list(item.get("failure_reasons", []))
            item["extraction_status"] = "PARTIAL" if item.get("records_extracted", 0) else "FAILED"
            item["validation_status"] = "QUARANTINED"
            item["failure_reasons"] = prior_reasons + [reason, "Adapter raised after writing this dataset record; output quarantined pending review."]
    manifest_tracker.record_dataset(
        "WSH-Orchestrator", f"orchestrator_{adapter_key}", pub_state,
        "FAILED", "INVALID", [], 0, [reason] + ([f"Affected dataset records quarantined: {', '.join(affected)}"] if affected else [])
    )
    print(f"[WSH ADAPTER ISOLATION] adapter={adapter_key} status=FAILED affected={affected} reason={reason}", flush=True)
    return f"\n[ORCHESTRATOR ADAPTER FAILURE: {adapter_key}] {reason}\n"


def _wsh_run_adapter(adapter_key, adapter_callable, manifest_tracker, folder_path):
    """Run one adapter behind an independent failure boundary and checkpoint."""
    before = {k: dict(v) for k, v in manifest_tracker.manifest.get("datasets", {}).items()}
    try:
        result = adapter_callable()
        if not isinstance(result, str):
            raise TypeError(f"Adapter returned {type(result).__name__}; expected str payload")
        return result
    except Exception as exc:
        return _wsh_adapter_failure(manifest_tracker, adapter_key, exc, before)
    finally:
        _wsh_manifest_checkpoint(manifest_tracker, folder_path)


def _wsh_record_not_attempted(manifest_tracker, adapter_key, reason):
    pub_state = "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED"
    manifest_tracker.record_dataset(
        "WSH-Orchestrator", f"orchestrator_{adapter_key}", pub_state,
        "NOT_ATTEMPTED", "NOT_RUN", [], 0, [reason]
    )


def _wsh_record_source_datasets_not_attempted(manifest_tracker, reason):
    """Preserve source-level dataset visibility when Chrome never initializes."""
    pub_state = "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED"
    entries = [
        ("Sensibull", "option_chain_nifty_discovery", ["symbol", "expiry", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "option_chain_banknifty_discovery", ["symbol", "expiry", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "option_chain_finnifty_discovery", ["symbol", "expiry", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "option_chain_sensex_discovery", ["symbol", "expiry", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "oi_vs_strike_nifty", ["expiry_selection", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "oi_vs_strike_banknifty", ["expiry_selection", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "oi_vs_strike_finnifty", ["expiry_selection", "strike", "call_oi", "put_oi"]),
        ("Sensibull", "oi_vs_strike_sensex", ["expiry_selection", "strike", "call_oi", "put_oi"]),
        *[("Sensibull", f"fii_dii_{key}", ["participant", "net_flow", "date"])
          for key in ("summary", "futures_and_options", "cash_market", "fii_history")],
        *[("CCIL", f"ccil_{key}", ["instrument", "rate"])
          for key in ("money_market", "gsec", "forex", "derivatives")],
        ("TradingEconomics", "gsse_sovereign_bonds", ["indicator", "actual", "previous"]),
        ("TradingEconomics", "gsse_macro_calendar", ["indicator", "actual", "previous"]),
    ]
    for source, dataset_key, fields in entries:
        manifest_tracker.record_dataset(
            source, dataset_key, pub_state, "NOT_ATTEMPTED", "NOT_RUN",
            fields, 0, [reason]
        )


def execute_wsh_institutional_pipeline():
    timestamp_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    folder_path = "WSH_Automated_Reports"
    os.makedirs(folder_path, exist_ok=True)
    manifest = InstitutionalQualityManifest()
    run_date_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    prie_payload = f"=== WSH PRIE DATA PACKAGE [Multi-Dimensional Audit] - {timestamp_str} ===\n"
    gsse_payload = f"=== WSH GSSE DATA PACKAGE [Calendar-Aware Governance] - {timestamp_str} ===\n"
    driver = None

    # Recovery boundary: ordinary adapter/driver exceptions are recorded and final
    # artifacts are attempted. KeyboardInterrupt/SystemExit are intentionally not
    # swallowed; driver.quit runs via finally, but package/final-manifest persistence
    # is not guaranteed for process termination, power loss, or an unhandled BaseException.
    # The stable checkpoint is best-effort and cannot be guaranteed if storage is unavailable.
    # Chrome startup is isolated so startup failure still produces packages and a manifest.
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as exc:
        reason = f"Chrome WebDriver initialization failed: {type(exc).__name__}: {exc}"
        print(f"[WSH DRIVER INITIALIZATION FAILED] {reason}", flush=True)
        manifest.record_dataset("WSH-Orchestrator", "driver_initialization", "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED",
                                "FAILED", "INVALID", ["webdriver"], 0, [reason])
        startup_reason = "Chrome driver unavailable; adapter was not attempted."
        for key in ("sensibull_option_chains", "sensibull_oi_vs_strike", "sensibull_fii_dii", "ccil_multi_tabs", "tradingeconomics_bonds", "tradingeconomics_calendar"):
            _wsh_record_not_attempted(manifest, key, startup_reason)
        _wsh_record_source_datasets_not_attempted(manifest, startup_reason)
        _wsh_manifest_checkpoint(manifest, folder_path)

    if driver is not None:
        try:
            try:
                driver.execute_cdp_cmd("Network.enable", {})
            except Exception as exc:
                print(f"[WEBSOCKET CAPTURE WARNING] CDP Network.enable failed: {type(exc).__name__}: {exc}", flush=True)

            # Each adapter is isolated. An escaped failure cannot prevent later adapters.
            prie_payload += _wsh_run_adapter(
                "sensibull_option_chains",
                lambda: extract_sensibull_option_chains(driver, manifest), manifest, folder_path)
            prie_payload += _wsh_run_adapter(
                "sensibull_oi_vs_strike",
                lambda: extract_sensibull_oi_vs_strike(driver, manifest), manifest, folder_path)
            prie_payload += _wsh_run_adapter(
                "sensibull_fii_dii",
                lambda: extract_sensibull_fii_dii_tabs(driver, manifest), manifest, folder_path)
            gsse_payload += _wsh_run_adapter(
                "ccil_multi_tabs",
                lambda: extract_ccil_multi_tabs(driver, manifest), manifest, folder_path)
            gsse_payload += _wsh_run_adapter(
                "tradingeconomics_bonds",
                lambda: extract_tradingeconomics_page(driver, "https://tradingeconomics.com/bonds", "gsse_sovereign_bonds", manifest), manifest, folder_path)
            gsse_payload += _wsh_run_adapter(
                "tradingeconomics_calendar",
                lambda: extract_tradingeconomics_page(driver, "https://tradingeconomics.com/calendar", "gsse_macro_calendar", manifest), manifest, folder_path)
        finally:
            try:
                driver.quit()
            except Exception as exc:
                reason = f"Chrome driver shutdown failed: {type(exc).__name__}: {exc}"
                print(f"[WSH DRIVER SHUTDOWN WARNING] {reason}", flush=True)
                manifest.record_dataset("WSH-Orchestrator", "driver_shutdown", "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED",
                                        "PARTIAL", "QUARANTINED", ["webdriver_shutdown"], 0, [reason])
                _wsh_manifest_checkpoint(manifest, folder_path)

    # Always attempt package persistence, even if driver startup/extraction failed.
    package_errors = []
    for filename, content in (
        (f"WSH_PRIE_Data_Package_{run_date_str}.txt", prie_payload),
        (f"WSH_GSSE_Data_Package_{run_date_str}.txt", gsse_payload),
    ):
        path = os.path.join(folder_path, filename)
        try:
            temporary_path = path + ".tmp"
            with open(temporary_path, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            print(f"[WSH PACKAGE SAVED] {path}", flush=True)
        except Exception as exc:
            package_errors.append(f"{filename}: {type(exc).__name__}: {exc}")
            print(f"[WSH PACKAGE SAVE ERROR] {package_errors[-1]}", flush=True)

    try:
        with open(__file__, "rb") as source_file:
            source_hash = hashlib.sha256(source_file.read()).hexdigest()
        fingerprint_path = os.path.join(folder_path, f"WSH_Source_Fingerprint_{run_date_str}.txt")
        with open(fingerprint_path, "w", encoding="utf-8") as fp:
            fp.write("pipeline_version=WSH_COLLECTOR_RND_2026-10-31_ISOLATION-MANIFEST-CANDIDATE_2\n")
            fp.write(f"source_file={os.path.basename(__file__)}\nsha256={source_hash}\n")
    except Exception as exc:
        print(f"[PROVENANCE WARNING] Could not write source fingerprint: {type(exc).__name__}: {exc}", flush=True)

    if package_errors:
        manifest.record_dataset("WSH-Orchestrator", "package_persistence", "PUBLISHED" if is_market_trading_day() else "MARKET_CLOSED",
                                "PARTIAL", "QUARANTINED", ["prie_package", "gsse_package"], 0, package_errors)

    try:
        manifest.save_manifest(folder_path)
    except Exception as exc:
        print(f"[MANIFEST FINAL SAVE ERROR] {type(exc).__name__}: {exc}", flush=True)
        _wsh_manifest_checkpoint(manifest, folder_path)

    from collections import Counter
    validation_counts = Counter(item.get('validation_status', 'UNKNOWN') for item in manifest.manifest.get('datasets', {}).values())
    print(f"[WSH QUALITY SUMMARY] dataset_count={len(manifest.manifest.get('datasets', {}))} validation_counts={json.dumps(dict(validation_counts), sort_keys=True)}", flush=True)
    for dataset_name, item in manifest.manifest.get('datasets', {}).items():
        if item.get('validation_status') != 'VALID':
            print(f"[WSH DATASET NONPASS] dataset={dataset_name} extraction={item.get('extraction_status')} validation={item.get('validation_status')} records={item.get('records_extracted')} reasons={json.dumps(item.get('failure_reasons', []), ensure_ascii=False)}", flush=True)
    print("\n[PIPELINE COMPLETE] Collection completed; inspect per-dataset validation states before using data.", flush=True)

if __name__ == "__main__":
    execute_wsh_institutional_pipeline()
