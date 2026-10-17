#!/usr/bin/env python3
"""
WSH Cloud Data Collector — governed extraction, normalization and release control.

Design principles:
- Publication state is independent from extraction and validation state.
- Never mark a dataset VALID merely because HTML exists or a browser request succeeded.
- Keep immutable raw HTML evidence and normalized JSON/CSV-style records per run.
- Quarantine cross-tab duplicates and schema/identity mismatches.
- Do not infer that a source is closed solely because a different source is closed.
- On a closed-market day, record MARKET_CLOSED only for sources explicitly configured
  with a weekend closure rule. Holidays/publication windows must be maintained in config.
- Missing/blocked/unsupported data remain visible as failures; no synthetic data is created.

This collector covers the source adapters currently identified in WSH's pipeline:
Sensibull option chains + FII/DII tabs, CCIL market tabs, TradingEconomics bonds/calendar.
Websites may change markup, require JavaScript/login, or restrict automated access.
The code intentionally fails closed when source structure cannot be verified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ----------------------------- Configuration -----------------------------

ROOT = Path(os.getenv("WSH_OUTPUT_DIR", "WSH_Automated_Reports"))
RAW_DIR = ROOT / "raw"
NORMALIZED_DIR = ROOT / "normalized"
EVIDENCE_DIR = ROOT / "evidence"
TIMEOUT_SECONDS = int(os.getenv("WSH_TIMEOUT_SECONDS", "20"))
WAIT_AFTER_LOAD = float(os.getenv("WSH_WAIT_AFTER_LOAD", "3"))
MIN_OPTION_ROWS = int(os.getenv("WSH_MIN_OPTION_ROWS", "8"))
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

SENSIBULL_OPTION_URL = (
    "https://web.sensibull.com/open-interest/oi-vs-strike?tradingsymbol={symbol}"
)
SENSIBULL_FII_URL = "https://web.sensibull.com/fii-dii-data"
CCIL_HOME_URL = "https://www.ccilindia.com/"
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_CALENDAR_URL = "https://tradingeconomics.com/calendar"

# Calendar policy is source-specific. This default expresses only the known
# weekend closure expectation for the four CCIL tabs; it does not infer holidays.
# Add verified source-specific holidays/publication windows here as ISO dates.
SOURCE_CALENDAR = {
    "ccil_money_market": {"weekend_closed": True, "holidays": []},
    "ccil_gsec": {"weekend_closed": True, "holidays": []},
    "ccil_forex": {"weekend_closed": True, "holidays": []},
    "ccil_derivatives": {"weekend_closed": True, "holidays": []},
    # Sensibull pages are still inspected on weekends because historical data may
    # be displayed. Do not mark them NOT_ATTEMPTED simply because the market is closed.
    "option_chain_nifty": {"weekend_closed": False, "holidays": []},
    "option_chain_banknifty": {"weekend_closed": False, "holidays": []},
    "option_chain_finnifty": {"weekend_closed": False, "holidays": []},
    "option_chain_sensex": {"weekend_closed": False, "holidays": []},
    "fii_dii_summary": {"weekend_closed": False, "holidays": []},
    "fii_dii_futures_and_options": {"weekend_closed": False, "holidays": []},
    "fii_dii_cash_market": {"weekend_closed": False, "holidays": []},
    "fii_dii_fii_history": {"weekend_closed": False, "holidays": []},
    "gsse_sovereign_bonds": {"weekend_closed": False, "holidays": []},
    "gsse_macro_calendar": {"weekend_closed": False, "holidays": []},
}

EXPECTED_FIELDS = {
    "option_chain": ["index", "expiry", "strike", "call_oi", "put_oi",
                     "call_change_oi", "put_change_oi", "source_timestamp"],
    "fii_dii_summary": ["segment", "bearish", "bullish", "net_oi", "change"],
    "fii_dii_futures_and_options": ["source_tab", "row_values"],
    "fii_dii_cash_market": ["source_tab", "row_values"],
    "fii_dii_fii_history": ["date", "row_values"],
    "ccil": ["instrument", "rate"],
    "gsse_sovereign_bonds": ["country", "yield", "change", "date"],
    "gsse_macro_calendar": ["event_date", "event_name", "country_code", "time"],
}

logging.basicConfig(
    level=os.getenv("WSH_LOG_LEVEL", "INFO"),
    format="%(asctime)sZ %(levelname)s %(message)s",
)
log = logging.getLogger("wsh-collector")


# ----------------------------- Common utilities -----------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_market_date() -> str:
    # GitHub runners may use UTC; WSH market-calendar checks use India date.
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_").lower()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_number(value: Any) -> Optional[float]:
    """Parse a numeric cell without guessing units such as L/Cr or percentages."""
    text = clean_text(value).replace(",", "").replace("−", "-")
    if not text or text in {"-", "—", "N/A", "NA", "None", "null"}:
        return None
    text = text.replace("%", "").strip()
    # Do not convert lakh/crore suffixes without a dataset-specific unit contract.
    if re.search(r"(?i)(cr|crore|lakh|lac|[0-9]l)$", text):
        return None
    match = re.fullmatch(r"\(?[-+]?\d+(?:\.\d+)?\)?", text)
    if not match:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def ensure_dirs() -> None:
    for folder in (ROOT, RAW_DIR, NORMALIZED_DIR, EVIDENCE_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def rows_from_table(table) -> tuple[list[str], list[list[str]]]:
    rows = []
    for tr in table.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return [], []
    # Header is the first row with TH cells; otherwise first row is a candidate header.
    th_rows = table.find_all("tr")
    header_index = 0
    for idx, tr in enumerate(th_rows):
        if tr.find_all("th"):
            header_index = idx
            break
    raw = []
    for tr in th_rows[header_index + 1:]:
        cells = [clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            raw.append(cells)
    header = rows[header_index]
    return header, raw


def norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", clean_text(value).lower()).strip("_")


def table_signature(table) -> str:
    header, rows = rows_from_table(table)
    canonical = json.dumps(
        {"header": [norm_header(x) for x in header],
         "rows": [[clean_text(x) for x in row] for row in rows]},
        sort_keys=True, ensure_ascii=False,
    )
    return sha256_text(canonical)


def publication_state(dataset_key: str) -> tuple[str, str]:
    """Return only calendar-derived states here; never assume a source is published."""
    cfg = SOURCE_CALENDAR.get(dataset_key, {})
    today = local_market_date()
    if today in cfg.get("holidays", []):
        return "NOT_DUE", f"Configured source-specific holiday: {today}"
    if cfg.get("weekend_closed") and datetime.fromisoformat(today).weekday() >= 5:
        return "MARKET_CLOSED", f"Configured weekend closure rule for {dataset_key}; date={today}"
    return "UNKNOWN", f"No closure rule applies; publication must be established from source evidence"


def is_numeric_like(value: str) -> bool:
    return parse_number(value) is not None


@dataclass
class DatasetAudit:
    source: str
    source_url: str
    publication_status: str = "UNKNOWN"
    publication_rule: str = ""
    extraction_status: str = "NOT_ATTEMPTED"
    validation_status: str = "NOT_RUN"
    expected_fields: list[str] | None = None
    raw_table_count: int = 0
    records_extracted: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    source_observation_date: Optional[str] = None
    collected_at_utc: str = ""
    raw_sha256: Optional[str] = None
    normalized_artifact: Optional[str] = None
    evidence_artifact: Optional[str] = None
    validation_checks: dict[str, Any] | None = None
    failure_reasons: list[str] | None = None

    def __post_init__(self):
        self.collected_at_utc = self.collected_at_utc or utc_now()
        self.expected_fields = self.expected_fields or []
        self.validation_checks = self.validation_checks or {}
        self.failure_reasons = self.failure_reasons or []


class Manifest:
    def __init__(self):
        self.data = {
            "schema_version": "2.1",
            "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
            "execution_timestamp_utc": utc_now(),
            "market_date_ist": local_market_date(),
            "timezone_policy": "UTC collection timestamp; Asia/Kolkata market date",
            "release_status": "HOLD",
            "release_blockers": [],
            "datasets": {},
        }

    def add(self, key: str, audit: DatasetAudit):
        self.data["datasets"][key] = asdict(audit)

    def finalize(self):
        required = (
            [f"option_chain_{x}" for x in ("nifty", "banknifty", "finnifty", "sensex")]
            + [f"fii_dii_{x}" for x in ("summary", "futures_and_options", "cash_market", "fii_history")]
            + [f"ccil_{x}" for x in ("money_market", "gsec", "forex", "derivatives")]
            + ["gsse_sovereign_bonds", "gsse_macro_calendar"]
        )
        blockers = [f"{key}:MISSING_DATASET_AUDIT" for key in required
                    if key not in self.data["datasets"]]
        for key, item in self.data["datasets"].items():
            if item["validation_status"] in {"INVALID", "QUARANTINED"}:
                blockers.append(f"{key}:{item['validation_status']}")
            elif item["validation_status"] == "NOT_RUN" and item["publication_status"] not in {
                "MARKET_CLOSED", "NOT_DUE"
            }:
                blockers.append(f"{key}:VALIDATION_NOT_RUN")
            elif item["validation_status"] == "VALID" and item["records_accepted"] <= 0:
                blockers.append(f"{key}:VALID_WITH_ZERO_ACCEPTED_RECORDS")
        self.data["release_blockers"] = blockers
        self.data["release_status"] = "CLEARED" if not blockers else "HOLD"


def save_raw(dataset_key: str, body: str) -> tuple[str, str]:
    name = f"{dataset_key}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.html"
    path = RAW_DIR / name
    write_text(path, body)
    return str(path), sha256_text(body)


def save_normalized(dataset_key: str, records: list[dict[str, Any]]) -> str:
    path = NORMALIZED_DIR / f"{dataset_key}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(path, records)
    return str(path)


def save_evidence(dataset_key: str, evidence: dict[str, Any]) -> str:
    path = EVIDENCE_DIR / f"{dataset_key}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(path, evidence)
    return str(path)


# ----------------------------- Browser lifecycle -----------------------------

def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument("--lang=en-US")
    # Selenium Manager resolves the matching driver on hosted runners.
    return webdriver.Chrome(options=options)


def load_page(driver, url: str) -> str:
    driver.get(url)
    WebDriverWait(driver, TIMEOUT_SECONDS).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    time.sleep(WAIT_AFTER_LOAD)
    return driver.page_source


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return clean_text(soup.get_text(" ", strip=True))


# ----------------------------- PRIE: option chains -----------------------------

def normalize_option_table(symbol: str, soup: BeautifulSoup) -> tuple[list[dict], int, list[str]]:
    accepted = []
    tables = soup.find_all("table")
    reasons = []
    expected_aliases = {
        "strike": {"strike", "strike_price", "strikeprice"},
        "call_oi": {"call_oi", "calloi", "ce_oi", "ceoi", "call_open_interest"},
        "put_oi": {"put_oi", "putoi", "pe_oi", "peoi", "put_open_interest"},
        "call_change_oi": {"call_change_oi", "change_in_call_oi", "ce_change_oi", "change_ce_oi"},
        "put_change_oi": {"put_change_oi", "change_in_put_oi", "pe_change_oi", "change_pe_oi"},
    }
    for table in tables:
        header, rows = rows_from_table(table)
        hnorm = [norm_header(x) for x in header]
        index = {}
        for field, aliases in expected_aliases.items():
            found = next((i for i, h in enumerate(hnorm) if h in aliases), None)
            if found is not None:
                index[field] = found
        if not {"strike", "call_oi", "put_oi"}.issubset(index):
            continue
        for row in rows:
            try:
                strike = parse_number(row[index["strike"]]) if index["strike"] < len(row) else None
                call_oi = parse_number(row[index["call_oi"]]) if index["call_oi"] < len(row) else None
                put_oi = parse_number(row[index["put_oi"]]) if index["put_oi"] < len(row) else None
                if strike is None or strike <= 0 or call_oi is None or put_oi is None:
                    continue
                call_chg = parse_number(row[index["call_change_oi"]]) if "call_change_oi" in index and index["call_change_oi"] < len(row) else None
                put_chg = parse_number(row[index["put_change_oi"]]) if "put_change_oi" in index and index["put_change_oi"] < len(row) else None
                accepted.append({
                    "index": symbol, "expiry": None, "strike": strike,
                    "call_oi": call_oi, "put_oi": put_oi,
                    "call_change_oi": call_chg, "put_change_oi": put_chg,
                    "source_timestamp": None,
                    "source_note": "Expiry/timestamp must be supplied by a verified source field or API before this dataset is execution-grade.",
                })
            except (IndexError, TypeError):
                continue
    if not accepted:
        reasons.append("No table yielded numeric strike + call OI + put OI records under the required schema.")
    if accepted and any(x["expiry"] is None for x in accepted):
        reasons.append("Expiry not verified in normalized strike records.")
    return accepted, len(tables), reasons


def collect_option_chain(driver, manifest: Manifest, symbol: str):
    key = f"option_chain_{symbol.lower()}"
    url = SENSIBULL_OPTION_URL.format(symbol=quote(symbol))
    pub, rule = publication_state(key)
    audit = DatasetAudit("Sensibull", url, pub, rule, expected_fields=EXPECTED_FIELDS["option_chain"])
    try:
        html = load_page(driver, url)
        raw_path, digest = save_raw(key, html)
        soup = BeautifulSoup(html, "html.parser")
        records, table_count, reasons = normalize_option_table(symbol, soup)
        audit.extraction_status = "SUCCESS"
        if records:
            audit.publication_status = "PUBLISHED"
            audit.publication_rule = "Source page returned records matching the candidate option-chain schema"
        audit.raw_table_count = table_count
        audit.records_extracted = len(records)
        audit.raw_sha256 = digest
        audit.evidence_artifact = raw_path
        audit.validation_checks = {
            "index_identity": symbol in page_text(html).upper(),
            "numeric_strike_oi_schema": len(records) > 0,
            "minimum_rows": len(records) >= MIN_OPTION_ROWS,
            "expiry_verified": bool(records) and all(r["expiry"] for r in records),
            "source_timestamp_verified": bool(records) and all(r["source_timestamp"] for r in records),
        }
        # No execution-grade VALID without expiry and source timestamp.
        if len(records) >= MIN_OPTION_ROWS and all(
            r["expiry"] and r["source_timestamp"] for r in records
        ):
            audit.validation_status = "VALID"
            audit.records_accepted = len(records)
        else:
            audit.validation_status = "INVALID"
            audit.records_rejected = len(records)
            audit.failure_reasons = reasons + [
                "Strict release gate: valid strike rows, expiry, timestamp and index identity are required."
            ]
        norm_path = save_normalized(key, records)
        audit.normalized_artifact = norm_path
        audit.evidence_artifact = raw_path
        evidence_path = save_evidence(key, {
            "url": url, "raw_sha256": digest, "raw_html": raw_path,
            "page_text_excerpt": page_text(html)[:1500],
            "validation_checks": audit.validation_checks,
        })
        audit.evidence_artifact = evidence_path
    except Exception as exc:
        audit.extraction_status = "FAILED"
        audit.validation_status = "INVALID"
        audit.failure_reasons = [f"{type(exc).__name__}: {exc}"]
        log.exception("Option chain failed: %s", key)
    manifest.add(key, audit)


# ----------------------------- PRIE: FII/DII tabs -----------------------------

FII_TABS = [
    ("Summary", "fii_dii_summary"),
    ("Futures and Options", "fii_dii_futures_and_options"),
    ("Cash Market", "fii_dii_cash_market"),
    ("FII History", "fii_dii_fii_history"),
]


def locate_tab(driver, tab_name: str) -> bool:
    xpath = (
        "//*[self::button or self::a or @role='tab']"
        f"[normalize-space(.)={json.dumps(tab_name)}]"
    )
    try:
        element = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].click();", element)
        time.sleep(WAIT_AFTER_LOAD)
        return True
    except TimeoutException:
        return False


def collect_fii_dii(driver, manifest: Manifest):
    url = SENSIBULL_FII_URL
    tab_bodies: dict[str, str] = {}
    tab_signatures: dict[str, str] = {}
    try:
        html = load_page(driver, url)
        write_text(RAW_DIR / f"fii_dii_landing_{manifest.data['run_id']}.html", html)
    except Exception as exc:
        for tab_name, key in FII_TABS:
            pub, rule = publication_state(key)
            audit = DatasetAudit("Sensibull", url, pub, rule,
                                 extraction_status="FAILED", validation_status="INVALID",
                                 expected_fields=EXPECTED_FIELDS.get(key, []),
                                 failure_reasons=[f"Landing page failed: {type(exc).__name__}: {exc}"])
            manifest.add(key, audit)
        return

    for tab_name, key in FII_TABS:
        pub, rule = publication_state(key)
        audit = DatasetAudit("Sensibull", url, pub, rule, expected_fields=EXPECTED_FIELDS.get(key, []))
        try:
            clicked = locate_tab(driver, tab_name)
            current_html = driver.page_source
            soup = BeautifulSoup(current_html, "html.parser")
            tables = soup.find_all("table")
            if not clicked:
                audit.extraction_status = "FAILED"
                audit.validation_status = "INVALID"
                audit.failure_reasons = [f"Could not verify/click target tab: {tab_name}"]
                audit.raw_table_count = len(tables)
                manifest.add(key, audit)
                continue

            # Fingerprint the whole set of data tables, not just the first 150 chars.
            signatures = sorted(table_signature(t) for t in tables if rows_from_table(t)[0])
            signature = sha256_text("|".join(signatures)) if signatures else ""
            tab_bodies[key] = current_html
            tab_signatures[key] = signature
            raw_path, digest = save_raw(key, current_html)
            audit.raw_sha256 = digest
            audit.evidence_artifact = raw_path
            audit.raw_table_count = len(tables)
            audit.extraction_status = "SUCCESS"

            duplicates = [
                other_key for other_key, other_sig in tab_signatures.items()
                if other_key != key and signature and other_sig == signature
            ]
            # Also compare with all already-seen tabs; a duplicate is never silently accepted.
            if duplicates:
                audit.validation_status = "QUARANTINED"
                audit.failure_reasons = [f"Cross-tab fingerprint collision with: {duplicates}"]
                audit.validation_checks = {"tab_clicked": clicked, "signature": signature, "duplicate_tabs": duplicates}
                manifest.add(key, audit)
                continue

            records = []
            for table in tables:
                header, rows = rows_from_table(table)
                for row in rows:
                    if not any(clean_text(v) for v in row):
                        continue
                    records.append({"source_tab": tab_name, "header": header, "row_values": row})

            # Dataset-specific schema gates. Summary has a recognizable segment/position table.
            if key == "fii_dii_summary":
                headers = {norm_header(x) for t in tables for x in rows_from_table(t)[0]}
                has_segment = any("segment" in h for h in headers)
                has_net_oi = any(h in {"net_oi", "net_open_interest", "netoi"} for h in headers)
                has_change = any(h in {"change", "change_oi", "change_in_oi"} for h in headers)
                semantically_valid = has_segment and has_net_oi and has_change and len(records) >= 2
                audit.validation_checks = {
                    "tab_clicked": clicked, "fingerprint": signature,
                    "has_segment": has_segment, "has_net_oi": has_net_oi,
                    "has_change": has_change, "record_count": len(records),
                }
            elif key == "fii_dii_fii_history":
                text = page_text(current_html).lower()
                has_date_signal = bool(re.search(r"\b(20\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", text))
                semantically_valid = len(records) >= 2 and has_date_signal
                audit.validation_checks = {"tab_clicked": clicked, "fingerprint": signature,
                                           "date_signal": has_date_signal, "record_count": len(records)}
            else:
                semantically_valid = len(records) >= 2
                audit.validation_checks = {"tab_clicked": clicked, "fingerprint": signature,
                                           "record_count": len(records),
                                           "note": "Raw rows retained; tab-specific numeric business schema still needs source contract."}

            audit.records_extracted = len(records)
            if records:
                audit.publication_status = "PUBLISHED"
                audit.publication_rule = "Source tab returned table rows; semantic validity evaluated separately"
            norm_path = save_normalized(key, records)
            audit.normalized_artifact = norm_path
            if semantically_valid:
                audit.validation_status = "VALID" if key == "fii_dii_summary" else "QUARANTINED"
                if key != "fii_dii_summary":
                    audit.failure_reasons = [
                        "Rows were extracted, but a verified tab-specific business schema is not configured; raw-only quarantine."
                    ]
                else:
                    audit.records_accepted = len(records)
            else:
                audit.validation_status = "INVALID"
                audit.records_rejected = len(records)
                audit.failure_reasons = ["Tab content does not satisfy the dataset-specific semantic schema."]
            evidence_path = save_evidence(key, {
                "url": url, "tab": tab_name, "tab_clicked": clicked,
                "raw_sha256": digest, "fingerprint": signature,
                "page_text_excerpt": page_text(current_html)[:1200],
                "validation_checks": audit.validation_checks,
            })
            audit.evidence_artifact = evidence_path
        except Exception as exc:
            audit.extraction_status = "FAILED"
            audit.validation_status = "INVALID"
            audit.failure_reasons = [f"{type(exc).__name__}: {exc}"]
            log.exception("FII/DII tab failed: %s", tab_name)
        manifest.add(key, audit)


# ----------------------------- GSSE: CCIL tabs -----------------------------

CCIL_TABS = ["Money Market", "Gsec", "Forex", "Derivatives"]
# Contamination markers are rejection signals, not a substitute for a positive Gsec schema.
GSEC_CONTAMINATION = [
    "TREP", "TREPS", "CALL MONEY", "SPECIAL REPO", "BASKET REPO",
    "NOTICE MONEY", "MONEY MARKET", "CALL/NOTICE",
]


def click_text_tab(driver, tab_name: str) -> bool:
    xpath = (
        "//*[self::a or self::button or @role='tab']"
        f"[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        f"'abcdefghijklmnopqrstuvwxyz'), {json.dumps(tab_name.lower())})]"
    )
    try:
        el = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].click();", el)
        time.sleep(WAIT_AFTER_LOAD)
        return True
    except TimeoutException:
        return False


def collect_ccil(driver, manifest: Manifest):
    for tab_name in CCIL_TABS:
        key = "ccil_" + slug(tab_name)
        pub, rule = publication_state(key)
        audit = DatasetAudit("CCIL", CCIL_HOME_URL, pub, rule, expected_fields=EXPECTED_FIELDS["ccil"])

        if pub in {"MARKET_CLOSED", "NOT_DUE"}:
            audit.extraction_status = "NOT_ATTEMPTED"
            audit.validation_status = "NOT_RUN"
            audit.failure_reasons = [rule]
            audit.validation_checks = {
                "calendar_rule_configured": True,
                "calendar_date_ist": local_market_date(),
                "source_specific_rule": key,
            }
            manifest.add(key, audit)
            continue

        try:
            html = load_page(driver, CCIL_HOME_URL)
            clicked = click_text_tab(driver, tab_name)
            current_html = driver.page_source
            soup = BeautifulSoup(current_html, "html.parser")
            tables = soup.find_all("table")
            raw_path, digest = save_raw(key, current_html)
            audit.raw_sha256 = digest
            audit.evidence_artifact = raw_path
            audit.raw_table_count = len(tables)
            audit.extraction_status = "SUCCESS"
            candidates = []
            for table in tables:
                header, rows = rows_from_table(table)
                h = " ".join(norm_header(x) for x in header)
                table_text = clean_text(table.get_text(" ", strip=True)).upper()
                if tab_name == "Gsec":
                    contamination = [marker for marker in GSEC_CONTAMINATION if marker in table_text]
                    # Positive identity requires sovereign-security identifiers, not generic table text.
                    positive_gsec = any(token in h for token in ["security", "isin", "maturity", "coupon", "yield", "gsec"])
                    if contamination:
                        audit.validation_status = "QUARANTINED"
                        audit.failure_reasons.append(f"Gsec contamination markers detected: {contamination}")
                        continue
                    if not positive_gsec:
                        continue
                if rows:
                    candidates.append((header, rows, table))

            normalized = []
            if clicked and candidates:
                for header, rows, _ in candidates:
                    for row in rows:
                        if len(row) < 2 or not any(row):
                            continue
                        # Preserve source columns without pretending all CCIL tables share one schema.
                        normalized.append({"market_tab": tab_name, "header": header, "row_values": row})
            audit.records_extracted = len(normalized)
            if normalized:
                audit.publication_status = "PUBLISHED"
                audit.publication_rule = "CCIL target tab returned candidate rows; market schema evaluated separately"
            audit.validation_checks = {
                "target_tab_clicked": clicked,
                "tables_found": len(tables),
                "candidate_tables": len(candidates),
                "positive_market_schema": len(candidates) > 0,
                "record_count": len(normalized),
            }
            norm_path = save_normalized(key, normalized)
            audit.normalized_artifact = norm_path
            if audit.validation_status == "QUARANTINED":
                audit.records_rejected = len(normalized)
            elif not clicked:
                audit.validation_status = "INVALID"
                audit.failure_reasons.append("Could not verify/click target CCIL market tab.")
            elif not normalized:
                audit.validation_status = "INVALID"
                audit.failure_reasons.append("No records passed positive market-specific schema checks.")
            else:
                # Conservative: CCIL market-specific normalized contract is not yet configured.
                audit.validation_status = "QUARANTINED"
                audit.records_rejected = len(normalized)
                audit.failure_reasons.append(
                    "Raw rows retained; no verified CCIL tab-specific column contract is configured for VALID status."
                )
            evidence_path = save_evidence(key, {
                "url": CCIL_HOME_URL, "tab": tab_name, "target_tab_clicked": clicked,
                "raw_sha256": digest, "validation_checks": audit.validation_checks,
                "page_text_excerpt": page_text(current_html)[:1200],
            })
            audit.evidence_artifact = evidence_path
        except Exception as exc:
            audit.extraction_status = "FAILED"
            audit.validation_status = "INVALID"
            audit.failure_reasons.append(f"{type(exc).__name__}: {exc}")
            log.exception("CCIL segment failed: %s", tab_name)
        manifest.add(key, audit)


# ----------------------------- GSSE: TradingEconomics bonds -----------------------------

def collect_te_bonds(driver, manifest: Manifest):
    key, url = "gsse_sovereign_bonds", TE_BONDS_URL
    pub, rule = publication_state(key)
    audit = DatasetAudit("TradingEconomics", url, pub, rule, expected_fields=EXPECTED_FIELDS[key])
    try:
        html = load_page(driver, url)
        soup = BeautifulSoup(html, "html.parser")
        raw_path, digest = save_raw(key, html)
        audit.raw_sha256, audit.evidence_artifact = digest, raw_path
        tables = soup.find_all("table")
        audit.raw_table_count = len(tables)
        records = []
        for table in tables:
            header, rows = rows_from_table(table)
            hnorm = [norm_header(x) for x in header]
            country_i = next((i for i, h in enumerate(hnorm) if h in {"major10y", "country", "name"}), None)
            yield_i = next((i for i, h in enumerate(hnorm) if h in {"yield", "rate", "last"}), None)
            change_i = next((i for i, h in enumerate(hnorm) if h in {"chg", "change", "change_value"}), None)
            date_i = next((i for i, h in enumerate(hnorm) if h == "date"), None)
            if country_i is None or yield_i is None or date_i is None:
                continue
            for row in rows:
                if max(country_i, yield_i, date_i) >= len(row):
                    continue
                country = clean_text(row[country_i])
                yld = parse_number(row[yield_i])
                obs_date = clean_text(row[date_i])
                change = parse_number(row[change_i]) if change_i is not None and change_i < len(row) else None
                if not country or yld is None or not obs_date:
                    continue
                records.append({
                    "country": country, "yield": yld, "change": change,
                    "weekly": row[hnorm.index("weekly")] if "weekly" in hnorm and hnorm.index("weekly") < len(row) else None,
                    "monthly": row[hnorm.index("monthly")] if "monthly" in hnorm and hnorm.index("monthly") < len(row) else None,
                    "ytd": row[hnorm.index("ytd")] if "ytd" in hnorm and hnorm.index("ytd") < len(row) else None,
                    "yoy": row[hnorm.index("yoy")] if "yoy" in hnorm and hnorm.index("yoy") < len(row) else None,
                    "date": obs_date,
                })
        # Regional tables repeat countries; deduplicate by country/date.
        dedup = {}
        for rec in records:
            dedup[(rec["country"].casefold(), rec["date"])] = rec
        records = list(dedup.values())
        audit.extraction_status = "SUCCESS"
        if records:
            audit.publication_status = "PUBLISHED"
            audit.publication_rule = "Sovereign-yield records matched the dedicated bond schema"
        audit.records_extracted = len(records)
        audit.validation_checks = {
            "yield_schema": len(records) > 0,
            "country_date_unique": len({(r["country"].casefold(), r["date"]) for r in records}) == len(records),
            "normalized_record_count": len(records),
            "raw_table_count_not_used_as_record_count": True,
        }
        norm_path = save_normalized(key, records)
        audit.normalized_artifact = norm_path
        if records and all(r["yield"] >= 0 for r in records):
            audit.validation_status = "VALID"
            audit.records_accepted = len(records)
            audit.source_observation_date = max((r["date"] for r in records), default=None)
        else:
            audit.validation_status = "INVALID"
            audit.records_rejected = len(records)
            audit.failure_reasons = ["No sovereign-yield records passed the dedicated bond schema."]
        evidence_path = save_evidence(key, {
            "url": url, "raw_sha256": digest, "tables_found": len(tables),
            "normalized_record_count": len(records),
            "page_text_excerpt": page_text(html)[:1500],
        })
        audit.evidence_artifact = evidence_path
    except Exception as exc:
        audit.extraction_status = "FAILED"
        audit.validation_status = "INVALID"
        audit.failure_reasons = [f"{type(exc).__name__}: {exc}"]
        log.exception("TradingEconomics bonds failed")
    manifest.add(key, audit)


# ----------------------------- GSSE: TradingEconomics calendar -----------------------------

DATE_HEADING = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\s+\d{4}\b",
    re.I,
)
TIME_RE = re.compile(r"^(?:\d{1,2}:\d{2}\s*(?:AM|PM)?|All Day)$", re.I)
COUNTRY_RE = re.compile(r"^[A-Z]{2,3}$")


def collect_te_calendar(driver, manifest: Manifest):
    key, url = "gsse_macro_calendar", TE_CALENDAR_URL
    pub, rule = publication_state(key)
    audit = DatasetAudit("TradingEconomics", url, pub, rule, expected_fields=EXPECTED_FIELDS[key])
    try:
        html = load_page(driver, url)
        soup = BeautifulSoup(html, "html.parser")
        raw_path, digest = save_raw(key, html)
        audit.raw_sha256, audit.evidence_artifact = digest, raw_path
        tables = soup.find_all("table")
        audit.raw_table_count = len(tables)
        records = []
        for table in tables:
            header, rows = rows_from_table(table)
            # Reject the filter/navigation table: it has no date + event rows.
            if not rows:
                continue
            current_date = None
            for row in rows:
                joined = " | ".join(row)
                date_match = DATE_HEADING.search(joined)
                if date_match:
                    current_date = date_match.group(0)
                    # Some tables repeat date heading as a row; don't count it as event.
                    continue
                if len(row) < 4:
                    continue
                # Common TradingEconomics calendar shape: time, country code, repeated
                # country code/category, event label, actual/previous/consensus/forecast.
                time_value = clean_text(row[0])
                country = clean_text(row[1]).upper() if len(row) > 1 else ""
                event_candidates = [clean_text(x) for x in row[2:] if clean_text(x)]
                event_name = ""
                for candidate in event_candidates:
                    if (not COUNTRY_RE.fullmatch(candidate)
                            and not TIME_RE.fullmatch(candidate)
                            and not DATE_HEADING.fullmatch(candidate)
                            and candidate.lower() not in {"actual", "previous", "consensus", "forecast", "download"}):
                        event_name = candidate
                        break
                if not current_date or not event_name:
                    continue
                if country and not COUNTRY_RE.fullmatch(country):
                    # Do not invent a country code when source columns shift.
                    continue
                if time_value and not TIME_RE.fullmatch(time_value) and not DATE_HEADING.fullmatch(time_value):
                    # Time may be blank for unscheduled releases; allow blank only.
                    if time_value not in {"", "-"}:
                        continue
                records.append({
                    "event_date": current_date, "time": time_value or None,
                    "country_code": country or None, "event_name": event_name,
                    "raw_row": row,
                })
        # Deduplicate repeated headers/fragments by event identity.
        dedup = {}
        for rec in records:
            identity = (rec["event_date"], rec["time"], rec["country_code"], rec["event_name"])
            dedup[identity] = rec
        records = list(dedup.values())
        audit.extraction_status = "SUCCESS"
        if records:
            audit.publication_status = "PUBLISHED"
            audit.publication_rule = "Economic-calendar event rows were normalized from source tables"
        audit.records_extracted = len(records)
        audit.validation_checks = {
            "event_rows_found": len(records) > 0,
            "event_identity_fields": all(r["event_date"] and r["event_name"] for r in records) if records else False,
            "deduplicated": True,
            "raw_table_count_not_used_as_record_count": True,
            "calendar_dates_are_source_labels": True,
        }
        norm_path = save_normalized(key, records)
        audit.normalized_artifact = norm_path
        if len(records) >= 5 and all(r["event_date"] and r["event_name"] for r in records):
            audit.validation_status = "VALID"
            audit.records_accepted = len(records)
        else:
            audit.validation_status = "INVALID"
            audit.records_rejected = len(records)
            audit.failure_reasons = [
                "Insufficient normalized event records or missing event identity; raw HTML table count is not validity."
            ]
        evidence_path = save_evidence(key, {
            "url": url, "raw_sha256": digest, "tables_found": len(tables),
            "normalized_event_count": len(records),
            "page_text_excerpt": page_text(html)[:1500],
        })
        audit.evidence_artifact = evidence_path
    except Exception as exc:
        audit.extraction_status = "FAILED"
        audit.validation_status = "INVALID"
        audit.failure_reasons = [f"{type(exc).__name__}: {exc}"]
        log.exception("TradingEconomics calendar failed")
    manifest.add(key, audit)


# ----------------------------- Orchestration / output -----------------------------

def write_text_packages(manifest: Manifest):
    run_id = manifest.data["run_id"]
    datasets = manifest.data["datasets"]
    prie_keys = [k for k in datasets if k.startswith("option_chain_") or k.startswith("fii_dii_")]
    gsse_keys = [k for k in datasets if k.startswith("ccil_") or k.startswith("gsse_")]

    def package(title: str, keys: list[str]) -> str:
        lines = [f"=== {title} | RUN {run_id} | UTC {manifest.data['execution_timestamp_utc']} ===", ""]
        for key in keys:
            d = datasets[key]
            lines.extend([
                "-" * 78,
                f"DATASET: {key}",
                f"SOURCE: {d['source']} | URL: {d['source_url']}",
                f"PUBLICATION: {d['publication_status']} | RULE: {d['publication_rule']}",
                f"EXTRACTION: {d['extraction_status']} | VALIDATION: {d['validation_status']}",
                f"RECORDS: extracted={d['records_extracted']} accepted={d['records_accepted']} rejected={d['records_rejected']}",
                f"RAW_TABLES: {d['raw_table_count']} | SHA256: {d['raw_sha256']}",
                f"NORMALIZED: {d['normalized_artifact']}",
                f"EVIDENCE: {d['evidence_artifact']}",
                f"CHECKS: {json.dumps(d['validation_checks'], ensure_ascii=False)}",
                f"FAILURES: {json.dumps(d['failure_reasons'], ensure_ascii=False)}",
                "",
            ])
        return "\n".join(lines)

    write_text(ROOT / f"WSH_PRIE_Data_Package_{run_id}.txt", package("WSH PRIE DATA PACKAGE", prie_keys))
    write_text(ROOT / f"WSH_GSSE_Data_Package_{run_id}.txt", package("WSH GSSE DATA PACKAGE", gsse_keys))


def main() -> int:
    ensure_dirs()
    manifest = Manifest()
    driver = None
    try:
        driver = make_driver()
        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
            collect_option_chain(driver, manifest, symbol)
        collect_fii_dii(driver, manifest)
        collect_ccil(driver, manifest)
        collect_te_bonds(driver, manifest)
        collect_te_calendar(driver, manifest)
    except Exception as exc:
        log.exception("Pipeline-level failure")
        manifest.data.setdefault("pipeline_errors", []).append(f"{type(exc).__name__}: {exc}")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                log.exception("Browser cleanup failed")

    manifest.finalize()
    run_id = manifest.data["run_id"]
    manifest_path = ROOT / f"WSH_Quality_Manifest_{run_id}.json"
    write_json(manifest_path, manifest.data)
    write_text_packages(manifest)

    summary = {
        "run_id": run_id,
        "release_status": manifest.data["release_status"],
        "dataset_count": len(manifest.data["datasets"]),
        "release_blockers": manifest.data["release_blockers"],
        "manifest": str(manifest_path),
    }
    write_json(ROOT / f"WSH_Run_Summary_{run_id}.json", summary)
    log.info("WSH pipeline finished: %s", json.dumps(summary))
    # Exit 0 means artifacts were generated, not that data passed release gates.
    # CI should use manifest release_status for release decisions.
    return 0


if __name__ == "__main__":
    sys.exit(main())
