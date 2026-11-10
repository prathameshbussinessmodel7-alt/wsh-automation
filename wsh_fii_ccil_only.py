"""WSH isolated NSE FII/DII + CCIL collector — R7.5.3.

R6 repairs CCIL table attribution: each market is identified by its own exact
header contract, not by page-wide fingerprints or unrelated page text. The
collector retains the raw page evidence and never infers publication from a
weekday or from the presence of another market's table.
"""
import os
import re
import json
import time
import hashlib
import datetime
from decimal import Decimal, InvalidOperation
from collections import Counter

from wsh_r7_5_1 import attach_capture_timestamp, extract_explicit_ccil_date

from bs4 import BeautifulSoup
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:  # Allows offline parser/regression tests without Selenium installed.
    webdriver = None
    Options = None
    WebDriverWait = None
    EC = None
    class By:
        CSS_SELECTOR = "css selector"
        TAG_NAME = "tag name"

PIPELINE_VERSION = "WSH_FII_CCIL_ISOLATED_R7.5.3_2026-11-11"
REPORT_DIR = "WSH_FII_CCIL_RND"
EVIDENCE_DIR = os.path.join(REPORT_DIR, "evidence")
CCIL_URL = "https://www.ccilindia.com/"
NSE_PAGE = "https://www.nseindia.com/reports/fii-dii"
NSE_ENDPOINT = "https://www.nseindia.com/api/fiidiiTradeReact"
CCIL_WAIT_SECONDS = 30
CCIL_NETWORK_EVENT_BUFFER = []

# Exact normalized header sequences observed in the supplied Run #1 evidence.
# These contracts deliberately preserve CCIL's native field names and units.
CCIL_CONTRACTS = {
    "Money Market": {
        "key": "ccil_money_market",
        "headers": ["market", "open", "high", "low", "ltr", "volumecrs", "war", "prevdaywar", "prevdayvolcrs"],
        "fields": ["Market", "Open", "High", "Low", "LTR", "Volume(₹ Crs.)", "WAR", "Prev. Day WAR", "Prev. Day Vol(₹ Crs.)"],
        "unit_notes": "Volume fields labelled ₹ Crs.; rate fields WAR/LTR have no explicit unit in captured header.",
    },
    "Gsec": {
        "key": "ccil_gsec",
        "headers": ["securitydescription", "trades", "ttacr", "ltp", "lty"],
        "fields": ["Security Description", "Trades", "TTA(₹ Cr.)", "LTP", "LTY"],
        "unit_notes": "TTA labelled ₹ Cr.; LTY appears as a yield-like field but source unit semantics require confirmation.",
    },
    "Forex": {
        "key": "ccil_forex",
        "headers": ["instruments", "open", "high", "low", "ltp", "prevclose", "netchange", "trades", "volusdinmio"],
        "fields": ["Instruments", "Open", "High", "Low", "LTP", "Prev Close", "Net Change", "Trades", "Vol(USD In Mio)"],
        "unit_notes": "Volume explicitly labelled USD in millions; price/rate quote convention requires source confirmation.",
    },
    "Derivatives": {
        "key": "ccil_derivatives",
        "headers": ["tenors", "trades", "volumecr", "high", "low", "ltr", "weightedaverage", "previousclose"],
        "fields": ["Tenors", "Trades", "Volume(₹ Cr.)", "High", "Low", "LTR", "Weighted Average", "Previous Close"],
        "unit_notes": "Volume labelled ₹ Cr.; tenor and rate semantics require source confirmation.",
    },
}


class QualityManifest:
    def __init__(self):
        self.data = {
            "execution_timestamp": datetime.datetime.now().astimezone().isoformat(),
            "pipeline_version": PIPELINE_VERSION,
            "scope": ["NSE_FII_DII", "CCIL"],
            "status_model": {
                "publication_status": "requires explicit source evidence; never inferred from weekday",
                "extraction_status": "per dataset/table, independent of page navigation status",
                "validation_status": "PARTIAL until date, units and independent reconciliation are established",
            },
            "datasets": {},
        }

    def record(self, key, source, publication, extraction, validation, fields, count,
               reasons=None, evidence=None, extra=None):
        item = {
            "source": source,
            "publication_status": publication,
            "extraction_status": extraction,
            "validation_status": validation,
            "expected_fields": fields,
            "records_extracted": int(count),
            "failure_reasons": reasons or [],
            "evidence": evidence or {},
        }
        if extra:
            item.update(extra)
        self.data["datasets"][key] = item

    def save(self):
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"WSH_Quality_Manifest_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False, default=str)
        return path


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def sha_bytes(value):
    if isinstance(value, bytes):
        payload = value
    else:
        payload = (value or "").encode("utf-8", "replace")
    return hashlib.sha256(payload).hexdigest()


def save_evidence(key, raw, metadata, extension):
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", key)
    # Keep source payload and derived metadata in separate files.
    # In R5, a JSON source payload and JSON metadata shared one path,
    # causing metadata to overwrite the original NSE response body.
    raw_path = os.path.join(EVIDENCE_DIR, f"{safe}_{stamp}.raw.{extension}")
    meta_path = os.path.join(EVIDENCE_DIR, f"{safe}_{stamp}.meta.json")
    if isinstance(raw, bytes):
        with open(raw_path, "wb") as f:
            f.write(raw)
    else:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw or "")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)
    return {"raw": raw_path, "metadata": meta_path, "sha256": sha_bytes(raw)}


def parse_html_tables(html):
    soup = BeautifulSoup(html or "", "html.parser")
    tables = []
    for index, table in enumerate(soup.find_all("table"), 1):
        parsed_rows = []
        for tr in table.find_all("tr"):
            cells = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
                     for cell in tr.find_all(["th", "td"])]
            if cells:
                parsed_rows.append(cells)
        if not parsed_rows:
            continue
        headers = parsed_rows[0]
        data_rows = [row for row in parsed_rows[1:] if any(str(cell).strip() for cell in row)]
        tables.append({
            "index": index,
            "headers": headers,
            "normalized_headers": [norm(h) for h in headers],
            "rows": data_rows,
            "row_count": len(data_rows),
            "fingerprint": sha_bytes(json.dumps([headers, data_rows], ensure_ascii=False, separators=(",", ":"))),
        })
    return tables


def find_contract_table(html, market):
    contract = CCIL_CONTRACTS[market]
    matches = [t for t in parse_html_tables(html) if t["normalized_headers"] == contract["headers"]]
    if len(matches) == 1:
        table = matches[0]
        # Drop summary/footer rows that do not identify a market instrument/security.
        rows = [r for r in table["rows"] if r and str(r[0]).strip()]
        table = dict(table)
        table["rows"] = rows
        table["row_count"] = len(rows)
        return table, "EXACT_HEADER_MATCH"
    if len(matches) > 1:
        return None, "AMBIGUOUS_DUPLICATE_HEADER_CONTRACT"
    return None, "TARGET_HEADER_CONTRACT_NOT_FOUND"


def visible_controls(driver):
    controls = []
    for element in driver.find_elements(By.CSS_SELECTOR, "[role='tab'],button,a,[aria-controls],[role='button']"):
        try:
            if not element.is_displayed():
                continue
            label = (element.get_attribute("aria-label") or element.get_attribute("title")
                     or element.text or element.get_attribute("textContent") or "")
            label = re.sub(r"\s+", " ", label).strip()
            if label:
                controls.append((label, element))
        except Exception:
            continue
    return controls


def active_state(element):
    selected = (element.get_attribute("aria-selected") or "").lower()
    if selected in ("true", "false"):
        return selected == "true"
    cls = set((element.get_attribute("class") or "").lower().split())
    return bool(cls & {"active", "selected", "current", "tab-active"})


def select_ccil_market(driver, market):
    controls = visible_controls(driver)
    matches = [element for label, element in controls if label.casefold() == market.casefold()]
    diag = {"target": market, "exact_control_matches": len(matches), "clicked": False,
            "active_state_before": None, "active_state_after": None}
    if len(matches) != 1:
        diag["reason"] = "missing_or_ambiguous_exact_control"
        return diag
    element = matches[0]
    try:
        diag["active_state_before"] = active_state(element)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", element)
        diag["clicked"] = True
        # A click is recorded as an action only. It is not considered proof of data identity.
        time.sleep(0.8)
        diag["active_state_after"] = active_state(element)
        diag["active_state_changed"] = diag["active_state_before"] != diag["active_state_after"]
    except Exception as exc:
        diag["click_error"] = f"{type(exc).__name__}: {exc}"
    return diag


CCIL_TABLE_RESOURCE_IDS = {
    "Money Market": "moneyMarketTable",
    "Gsec": "gsecTable",
    "Forex": "forexTable",
    "Derivatives": "derivativeTable",
}


def parse_chrome_network_events(entries, market):
    """Reduce Chrome performance-log entries to target CCIL table request evidence."""
    from urllib.parse import urlparse, parse_qs
    target = CCIL_TABLE_RESOURCE_IDS[market]
    requests, responses, finished, failures = {}, {}, set(), {}
    for entry in entries or []:
        try:
            envelope = json.loads(entry["message"] if isinstance(entry, dict) else entry)
            message = envelope.get("message", envelope)
            method, params = message.get("method"), message.get("params", {})
            if method == "Network.requestWillBeSent":
                req = params.get("request", {})
                url = req.get("url", "")
                rid = params.get("requestId")
                resource_id = parse_qs(urlparse(url).query).get("p_p_resource_id", [None])[0]
                if rid and resource_id == target:
                    requests[rid] = {"request_id": rid, "url": url,
                                     "method": req.get("method"),
                                     "request_timestamp": params.get("timestamp"),
                                     "initiator_type": params.get("initiator", {}).get("type")}
            elif method == "Network.responseReceived":
                rid = params.get("requestId")
                if rid in requests:
                    response = params.get("response", {})
                    responses[rid] = {
                        "http_status": response.get("status"),
                        "status_text": response.get("statusText"),
                        "mime_type": response.get("mimeType"),
                        "protocol": response.get("protocol"),
                        "remote_ip_address": response.get("remoteIPAddress"),
                        "from_disk_cache": response.get("fromDiskCache", False),
                        "from_service_worker": response.get("fromServiceWorker", False),
                        "encoded_data_length": response.get("encodedDataLength"),
                        "response_headers": {k: v for k, v in (response.get("headers") or {}).items()
                                              if k.lower() in {"content-type", "content-length", "cache-control", "date", "server"}},
                    }
            elif method == "Network.loadingFinished" and params.get("requestId") in requests:
                finished.add(params["requestId"])
            elif method == "Network.loadingFailed" and params.get("requestId") in requests:
                failures[params["requestId"]] = {
                    "error_text": params.get("errorText"),
                    "canceled": params.get("canceled", False),
                    "blocked_reason": params.get("blockedReason"),
                }
        except Exception:
            continue

    results = []
    for rid, req in requests.items():
        response = responses.get(rid, {})
        failure = failures.get(rid)
        status = response.get("http_status")
        if failure:
            verdict = "REQUEST_FAILED"
        elif status is None:
            verdict = "RESPONSE_NOT_OBSERVED"
        elif not (200 <= int(status) < 300):
            verdict = "HTTP_ERROR"
        elif rid in finished:
            verdict = "HTTP_SUCCESS_BODY_PENDING"
        else:
            verdict = "RESPONSE_RECEIVED_NOT_FINISHED"
        results.append({**req, **response, "loading_finished": rid in finished,
                        "loading_failure": failure, "network_verdict": verdict})
    return {"target_resource_id": target, "request_count": len(results),
            "requests": results,
            "capture_note": "Only matching CCIL table-resource requests are included; absence is not proof that the source has no data."}


def capture_ccil_network_evidence(driver, market):
    """Drain Chrome network events and preserve matching response metadata/body when available."""
    try:
        entries = driver.get_log("performance")
        CCIL_NETWORK_EVENT_BUFFER.extend(entries)
    except Exception as exc:
        return {"target_resource_id": CCIL_TABLE_RESOURCE_IDS[market], "request_count": 0,
                "requests": [], "log_capture_error": f"{type(exc).__name__}: {exc}"}
    # Keep a page-session buffer: CCIL may request all four table endpoints during initial load.
    # Do not discard GSEC/Forex/Derivatives events merely because Money Market is audited first.
    result = parse_chrome_network_events(CCIL_NETWORK_EVENT_BUFFER, market)
    body_files = []
    for item in result["requests"]:
        if not item.get("loading_finished") or item.get("loading_failure"):
            item["body_capture_status"] = "NOT_AVAILABLE_REQUEST_NOT_FINISHED"
            continue
        try:
            body_result = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": item["request_id"]})
            body = body_result.get("body", "")
            if body_result.get("base64Encoded"):
                import base64
                raw = base64.b64decode(body)
            else:
                raw = body.encode("utf-8", "replace")
            digest = hashlib.sha256(raw).hexdigest()
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            filename = f"ccil_network_{market.lower().replace(' ', '_')}_{stamp}.response.raw"
            os.makedirs(EVIDENCE_DIR, exist_ok=True)
            path = os.path.join(EVIDENCE_DIR, filename)
            # Preserve full response up to 5 MiB; larger payloads are explicitly truncated.
            limit = 5 * 1024 * 1024
            stored = raw[:limit]
            with open(path, "wb") as handle:
                handle.write(stored)
            item.update({"body_capture_status": "CAPTURED" if len(raw) <= limit else "TRUNCATED_5_MIB",
                         "body_length_bytes": len(raw), "body_sha256": digest,
                         "body_saved_path": path, "body_saved_length_bytes": len(stored),
                         "body_base64_encoded_by_browser": bool(body_result.get("base64Encoded"))})
            body_files.append(path)
        except Exception as exc:
            item["body_capture_status"] = "CAPTURE_FAILED"
            item["body_capture_error"] = f"{type(exc).__name__}: {exc}"
    result["response_body_files"] = body_files
    result["captured_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def ccil_page_diagnostics(driver, market):
    """Capture observable page state without inferring publication or market status."""
    diagnostics = {"target_market": market, "current_url": getattr(driver, "current_url", None)}
    try:
        diagnostics["ready_state"] = driver.execute_script("return document.readyState")
    except Exception as exc:
        diagnostics["ready_state_error"] = type(exc).__name__
    try:
        diagnostics["active_controls"] = [
            {"label": label, "active": active_state(element),
             "aria_selected": element.get_attribute("aria-selected"),
             "aria_controls": element.get_attribute("aria-controls"),
             "role": element.get_attribute("role")}
            for label, element in visible_controls(driver)
        ]
    except Exception as exc:
        diagnostics["active_controls_error"] = type(exc).__name__
    try:
        diagnostics["loading_indicators"] = driver.execute_script("""
          const selectors=['[aria-busy=true]','.loading','.loader','.spinner',
                           '[class*=loading i]','[class*=spinner i]'];
          return selectors.flatMap(s=>Array.from(document.querySelectorAll(s)))
            .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0})
            .slice(0,30).map(e=>({selector:e.tagName.toLowerCase(),className:String(e.className||''),
              text:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,160)}));
        """)
    except Exception as exc:
        diagnostics["loading_indicators_error"] = type(exc).__name__
    try:
        diagnostics["performance_resources"] = driver.execute_script("""
          return performance.getEntriesByType('resource').map(x=>x.name)
            .filter(x=>/ccil|market|gsec|forex|deriv|money|api/i.test(x))
            .slice(-80);
        """)
    except Exception as exc:
        diagnostics["performance_resources_error"] = type(exc).__name__
    try:
        diagnostics["iframe_count"] = len(driver.find_elements(By.TAG_NAME, 'iframe'))
    except Exception:
        diagnostics["iframe_count"] = None
    return diagnostics


def wait_for_market_table(driver, market, timeout=CCIL_WAIT_SECONDS):
    """Poll the exact target table and retain state transitions for diagnosis."""
    end = time.monotonic() + timeout
    last_html = driver.page_source
    last_table, last_reason = find_contract_table(last_html, market)
    observations = []
    previous_signature = None
    while time.monotonic() < end:
        last_html = driver.page_source
        last_table, last_reason = find_contract_table(last_html, market)
        signature = (last_table["row_count"], last_table["fingerprint"]) if last_table else (0, None)
        observations.append({
            "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "contract_status": last_reason,
            "rows": last_table["row_count"] if last_table else 0,
            "table_fingerprint": last_table["fingerprint"] if last_table else None,
            "table_state_changed": signature != previous_signature,
        })
        previous_signature = signature
        if last_table and last_table["row_count"] > 0:
            return last_html, last_table, observations, "TARGET_TABLE_POPULATED"
        time.sleep(0.75)
    status = "TARGET_TABLE_EMPTY_AFTER_WAIT" if last_table else last_reason
    return last_html, last_table, observations, status


def decimal_reconciliation(records):
    results = []
    for record in records:
        try:
            buy = Decimal(str(record["buy"]).replace(",", ""))
            sell = Decimal(str(record["sell"]).replace(",", ""))
            net = Decimal(str(record["net"]).replace(",", ""))
            calculated = buy - sell
            results.append({"participant": record["participant"], "calculated_net": str(calculated),
                            "reported_net": str(net), "matches": calculated == net})
        except (KeyError, InvalidOperation, TypeError) as exc:
            results.append({"participant": record.get("participant"), "matches": False,
                            "reason": f"reconciliation_unavailable: {type(exc).__name__}"})
    return results


def parse_nse_date(value):
    """Parse observed NSE date strings without guessing missing dates."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def ccil_numeric_checks(market, rows):
    """Run conservative row-level checks; never promote dataset to VALID."""
    checks = []

    def dec(value):
        return Decimal(str(value).replace(",", "").strip())

    for index, row in enumerate(rows, 1):
        result = {"row": index, "key": row[0] if row else None, "checks": []}
        try:
            if market == "Forex":
                # LTP - Prev Close should equal Net Change, subject to displayed precision.
                ltp, prev, net = dec(row[4]), dec(row[5]), dec(row[6])
                ok = (ltp - prev).quantize(Decimal("0.0001")) == net.quantize(Decimal("0.0001"))
                result["checks"].append({"name": "ltp_minus_prev_close_equals_net_change", "pass": ok})
            elif market == "Money Market":
                low, high, ltr, war = dec(row[3]), dec(row[2]), dec(row[4]), dec(row[6])
                result["checks"].append({"name": "ltr_within_low_high", "pass": low <= ltr <= high})
                result["checks"].append({"name": "war_within_low_high", "pass": low <= war <= high})
            elif market == "Derivatives":
                low, high, ltr, weighted = dec(row[4]), dec(row[3]), dec(row[5]), dec(row[6])
                result["checks"].append({"name": "ltr_within_low_high", "pass": low <= ltr <= high})
                result["checks"].append({"name": "weighted_average_within_low_high", "pass": low <= weighted <= high})
            elif market == "Gsec":
                trades, tta, ltp, lty = dec(row[1]), dec(row[2]), dec(row[3]), dec(row[4])
                result["checks"].append({"name": "trades_non_negative_integer", "pass": trades >= 0 and trades == trades.to_integral_value()})
                result["checks"].append({"name": "tta_non_negative", "pass": tta >= 0})
                result["checks"].append({"name": "ltp_positive", "pass": ltp > 0})
                result["checks"].append({"name": "lty_positive", "pass": lty > 0})
        except (IndexError, InvalidOperation, TypeError) as exc:
            result["checks"].append({"name": "numeric_parse", "pass": False, "reason": type(exc).__name__})
        checks.append(result)

    total = sum(len(item["checks"]) for item in checks)
    passed = sum(1 for item in checks for check in item["checks"] if check.get("pass"))
    return {"rows_checked": len(rows), "checks_total": total, "checks_passed": passed,
            "all_checks_pass": bool(total) and passed == total, "row_results": checks,
            "scope_note": "Arithmetic/range diagnostics only; not independent source reconciliation or publication validation."}


def extract_nse_fii_dii(driver, manifest, output):
    key = "fii_dii_cash_activity"
    lines = ["WSH FII/DII — OFFICIAL NSE CASH ACTIVITY", f"Source page: {NSE_PAGE}", f"Data endpoint: {NSE_ENDPOINT}"]
    try:
        driver.get("https://www.nseindia.com/")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        driver.get(NSE_PAGE)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        result = driver.execute_async_script("""
          const url=arguments[0], done=arguments[arguments.length-1];
          fetch(url,{credentials:'include',headers:{'accept':'application/json,text/plain,*/*'}})
            .then(async r=>({http_status:r.status,content_type:r.headers.get('content-type'),text:await r.text()}))
            .then(done).catch(e=>done({error:String(e)}));
        """, NSE_ENDPOINT)
        raw = result.get("text", "")
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = None
        source_rows = parsed if isinstance(parsed, list) else []
        if isinstance(parsed, dict):
            source_rows = next((v for v in parsed.values() if isinstance(v, list)), [])
        records = []
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            mapped = {norm(k): v for k, v in row.items()}
            participant = mapped.get("category") or mapped.get("participant") or mapped.get("clienttype") or mapped.get("name")
            date = mapped.get("date") or mapped.get("tradedate") or mapped.get("asofdate")
            buy = mapped.get("buyvalue") or mapped.get("buy") or mapped.get("purchasevalue")
            sell = mapped.get("sellvalue") or mapped.get("sell") or mapped.get("salevalue")
            net = mapped.get("netvalue") or mapped.get("net") or mapped.get("netbuyvalue")
            if participant is not None:
                records.append({"participant": str(participant), "date": date, "buy": buy, "sell": sell, "net": net})
        reconciliation = decimal_reconciliation(records)
        all_reconciled = bool(records) and all(x.get("matches") for x in reconciliation)
        observed_dates = sorted({parse_nse_date(r.get("date")) for r in records if parse_nse_date(r.get("date"))})
        date_parse_complete = bool(records) and all(parse_nse_date(r.get("date")) for r in records)
        date_consistent = len(observed_dates) == 1
        nse_metadata = attach_capture_timestamp({
            "page_url": driver.current_url, "endpoint": NSE_ENDPOINT,
            "http_status": result.get("http_status"), "content_type": result.get("content_type"),
            "fetch_error": result.get("error"), "raw_length": len(raw),
            "raw_response_sha256": sha_bytes(raw),
            "records": records, "reconciliation": reconciliation,
            "observed_source_dates": observed_dates,
            "date_parse_complete": date_parse_complete,
            "date_consistent": date_consistent,
            "units": "UNVERIFIED — source response does not state unit in the captured record schema",
        })
        evidence = save_evidence(key, raw, nse_metadata, "json")
        lines += [f"HTTP status: {result.get('http_status')}", f"Records: {len(records)}",
                  f"Arithmetic reconciliation: {json.dumps(reconciliation, ensure_ascii=False)}",
                  "Units: UNVERIFIED", f"Evidence: {json.dumps(evidence)}"]
        complete_fields = bool(records) and all(all(r.get(k) not in (None, "") for k in ("date", "buy", "sell", "net")) for r in records)
        if result.get("http_status") == 200 and records and complete_fields:
            reasons = ["Cash activity acquired from official NSE endpoint.",
                       "Reported buy/sell/net arithmetic checked locally.",
                       "Unit semantics and independent source reconciliation remain outstanding."]
            if not all_reconciled:
                reasons.append("One or more reported net values do not equal buy minus sell; investigate before use.")
            manifest.record(key, "NSE", "UNKNOWN", "SUCCESS", "PARTIAL", ["participant", "date", "buy", "sell", "net"],
                            len(records), reasons, evidence,
                            {"unit_status": "UNVERIFIED", "arithmetic_reconciliation": reconciliation,
                             "all_arithmetic_checks_pass": all_reconciled,
                             "observed_source_dates": observed_dates,
                             "date_parse_complete": date_parse_complete,
                             "date_consistent": date_consistent,
                             "raw_response_preserved": True})
            lines += ["STATUS: UNKNOWN / SUCCESS / PARTIAL", json.dumps(records, indent=2, ensure_ascii=False)]
        else:
            reasons = [f"HTTP={result.get('http_status')}", "NSE response missing required cash activity fields or records."]
            manifest.record(key, "NSE", "UNKNOWN", "FAILED", "INVALID", ["participant", "date", "buy", "sell", "net"], 0, reasons, evidence)
            lines += ["STATUS: UNKNOWN / FAILED / INVALID", "REASONS: " + "; ".join(reasons)]
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        manifest.record(key, "NSE", "UNKNOWN", "FAILED", "INVALID", ["participant", "date", "buy", "sell", "net"], 0, [reason])
        lines += ["STATUS: UNKNOWN / FAILED / INVALID", reason]

    # These remain explicitly unimplemented rather than being inferred from the cash API.
    for dataset, reason in [
        ("fii_dii_derivatives_participant", "Participant-wise derivatives source mapping is not implemented in R7.5.2; no data fabricated."),
        ("fii_dii_history", "Historical archive source mapping is not implemented in R7.5.2; no data fabricated."),
    ]:
        manifest.record(dataset, "NSE", "UNKNOWN", "NOT_ATTEMPTED", "NOT_RUN",
                        ["source-specific fields", "report date", "units"], 0, [reason])
        lines += ["", f"{dataset}: NOT_ATTEMPTED — {reason}"]
    return "\n".join(lines) + "\n"


def extract_ccil(driver, manifest):
    lines = ["WSH CCIL FOUR-MARKET TABLE EXTRACTION — R7.5.2", f"Source: {CCIL_URL}",
             "Identity rule: exact market-specific header contract; page-wide fingerprints are diagnostic only."]
    driver.get(CCIL_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Capture the initial page once; then select each market and wait on that market's own table.
    # The initial page can already contain the default Money Market data.
    for market, contract in CCIL_CONTRACTS.items():
        key = contract["key"]
        lines += ["", f"--- {market} ---"]
        transition = {"target": market, "clicked": False, "note": "initial page capture"}
        try:
            if market != "Money Market":
                transition = select_ccil_market(driver, market)
            html, table, observations, wait_status = wait_for_market_table(driver, market)
            page_state_diagnostics = ccil_page_diagnostics(driver, market)
            network_response_diagnostics = capture_ccil_network_evidence(driver, market)
            all_tables = parse_html_tables(html)
            page_table_count = len(all_tables)
            page_fingerprint = sha_bytes("|".join(sorted(t["fingerprint"] for t in all_tables)))
            rows = table["rows"] if table else []
            row_count = len(rows)
            target_fp = table["fingerprint"] if table else None
            table_diag = {
                "market": market,
                "contract_headers_expected": contract["fields"],
                "contract_headers_normalized": contract["headers"],
                "contract_match": bool(table),
                "target_table_index": table["index"] if table else None,
                "target_table_headers_observed": table["headers"] if table else None,
                "target_table_rows": row_count,
                "target_table_fingerprint": target_fp,
                "page_table_count": page_table_count,
                "page_fingerprint_diagnostic_only": page_fingerprint,
                "wait_status": wait_status,
                "poll_observations": observations,
                "poll_duration_seconds": round((len(observations) - 1) * 0.75, 2),
                "page_state_diagnostics": page_state_diagnostics,
                "network_response_diagnostics": network_response_diagnostics,
                "unit_notes": contract["unit_notes"],
                "row_policy": "rows with blank first-column key excluded as footer/summary rows",
            }
            numeric_checks = ccil_numeric_checks(market, rows) if table else {"rows_checked": 0, "checks_total": 0, "checks_passed": 0, "all_checks_pass": False, "row_results": []}
            ccil_date = extract_explicit_ccil_date({}, html)
            ccil_metadata = attach_capture_timestamp({"url": driver.current_url,
                                                  "source_as_of_date": ccil_date.get("normalized_date") if ccil_date.get("status") == "PASS" else "UNKNOWN_NOT_EXPLICIT_IN_CAPTURE",
                                                  "source_date_evidence": ccil_date,
                                                  "transition_diagnostic_only": transition, "table_diagnostic": table_diag,
                                                  "numeric_checks": numeric_checks, "rows": rows})
            evidence = save_evidence(key, html, ccil_metadata, "html")
            if table and row_count > 0:
                extraction = "SUCCESS"
                validation = "PARTIAL"
                publication = "UNKNOWN"
                reasons = ["Target-specific exact header contract matched and non-empty rows captured.",
                           "Publication date/status is not explicit in captured evidence.",
                           "Field units/semantics and independent source reconciliation remain pending."]
                manifest.record(key, "CCIL", publication, extraction, validation, contract["fields"], row_count,
                                reasons, evidence,
                                {"target_table_fingerprint": target_fp, "target_table_index": table["index"],
                                 "unit_notes": contract["unit_notes"], "navigation_transition": transition,
                                 "table_identity_method": "EXACT_NORMALIZED_HEADER_SEQUENCE",
                                 "source_as_of_date": "UNKNOWN_NOT_EXPLICIT_IN_CAPTURE",
                                 "numeric_diagnostic_checks": numeric_checks,
                                 "numeric_checks_all_pass": numeric_checks["all_checks_pass"]})
                lines += [f"STATUS: {publication} / {extraction} / {validation}",
                          f"Rows: {row_count}", f"Headers: {' | '.join(table['headers'])}",
                          f"Evidence: {json.dumps(evidence)}"]
                for row in rows:
                    lines.append(" | ".join(row))
            elif table and row_count == 0:
                reasons = ["Target-specific table and header contract were found, but no data rows appeared within the wait window.",
                           "This is an EMPTY capture, not evidence that the market was closed or unpublished.",
                           "Page readiness, active controls, loading indicators, iframe count, resource URLs and matching HTTP response/body evidence were captured where available."]
                manifest.record(key, "CCIL", "UNKNOWN", "EMPTY", "INVALID", contract["fields"], 0,
                                reasons, evidence,
                                {"target_table_fingerprint": target_fp, "target_table_index": table["index"],
                                 "navigation_transition": transition, "table_identity_method": "EXACT_NORMALIZED_HEADER_SEQUENCE",
                                 "empty_capture_diagnostics": page_state_diagnostics,
                                 "poll_observation_count": len(observations)})
                lines += ["STATUS: UNKNOWN / EMPTY / INVALID", "REASONS: " + "; ".join(reasons),
                          f"Evidence: {json.dumps(evidence)}"]
            else:
                reasons = ["Exact target-specific header contract not found or ambiguous.", wait_status]
                manifest.record(key, "CCIL", "UNKNOWN", "FAILED", "INVALID", contract["fields"], 0, reasons, evidence,
                                {"navigation_transition": transition, "table_identity_method": "EXACT_NORMALIZED_HEADER_SEQUENCE"})
                lines += ["STATUS: UNKNOWN / FAILED / INVALID", "REASONS: " + "; ".join(reasons),
                          f"Evidence: {json.dumps(evidence)}"]
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            manifest.record(key, "CCIL", "UNKNOWN", "FAILED", "INVALID", contract["fields"], 0, [reason])
            lines += [f"STATUS: UNKNOWN / FAILED / INVALID — {reason}"]
    return "\n".join(lines) + "\n"


def main():
    if webdriver is None:
        raise RuntimeError("Selenium is required for live collection. Install requirements.txt first.")
    os.makedirs(REPORT_DIR, exist_ok=True)
    manifest = QualityManifest()
    options = Options()
    options.add_argument("--headless=new")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"})
    options.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True, "enablePage": True})
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        CCIL_NETWORK_EVENT_BUFFER.clear()
        fii_report = extract_nse_fii_dii(driver, manifest, None)
        ccil_report = extract_ccil(driver, manifest)
    finally:
        driver.quit()

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(REPORT_DIR, f"WSH_PRIE_FII_DII_{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(fii_report)
    with open(os.path.join(REPORT_DIR, f"WSH_GSSE_CCIL_{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(ccil_report)
    manifest_path = manifest.save()
    source_path = os.path.abspath(__file__)
    with open(os.path.join(REPORT_DIR, f"WSH_Source_Fingerprint_{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(f"pipeline_version={PIPELINE_VERSION}\nsource_file={os.path.basename(__file__)}\nsha256={sha_bytes(open(source_path, 'rb').read())}\n")
    counts = Counter(item["validation_status"] for item in manifest.data["datasets"].values())
    print(f"[FII-CCIL R7.5.3 COMPLETE] datasets={len(manifest.data['datasets'])} validation={dict(counts)} manifest={manifest_path}")


if __name__ == "__main__":
    main()
