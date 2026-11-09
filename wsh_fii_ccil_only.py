"""WSH isolated NSE FII/DII + CCIL collector — R5.

R5 repairs CCIL table attribution: each market is identified by its exact
header contract, not by page-wide fingerprints or unrelated page text.
Raw page evidence is retained. Publication is never inferred from weekdays.
"""

import os
import re
import json
import time
import hashlib
import datetime
from decimal import Decimal, InvalidOperation
from collections import Counter

from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    webdriver = None
    Options = None
    WebDriverWait = None
    EC = None

    class By:
        CSS_SELECTOR = "css selector"
        TAG_NAME = "tag name"


PIPELINE_VERSION = "WSH_FII_CCIL_ISOLATED_R5_2026-11-10"
REPORT_DIR = "WSH_FII_CCIL_RND"
EVIDENCE_DIR = os.path.join(REPORT_DIR, "evidence")

CCIL_URL = "https://www.ccilindia.com/"
NSE_PAGE = "https://www.nseindia.com/reports/fii-dii"
NSE_ENDPOINT = "https://www.nseindia.com/api/fiidiiTradeReact"

CCIL_WAIT_SECONDS = 18


# Exact normalized header contracts observed in Run #1 evidence.
CCIL_CONTRACTS = {
    "Money Market": {
        "key": "ccil_money_market",
        "headers": [
            "market", "open", "high", "low", "ltr",
            "volumecrs", "war", "prevdaywar", "prevdayvolcrs"
        ],
        "fields": [
            "Market", "Open", "High", "Low", "LTR",
            "Volume(₹ Crs.)", "WAR", "Prev. Day WAR",
            "Prev. Day Vol(₹ Crs.)"
        ],
        "unit_notes": (
            "Volume fields labelled ₹ Crs.; rate fields WAR/LTR "
            "have no explicit unit in captured header."
        )
    },

    "Gsec": {
        "key": "ccil_gsec",
        "headers": [
            "securitydescription", "trades", "ttacr", "ltp", "lty"
        ],
        "fields": [
            "Security Description", "Trades", "TTA(₹ Cr.)",
            "LTP", "LTY"
        ],
        "unit_notes": (
            "TTA labelled ₹ Cr.; LTY appears yield-like but "
            "source unit semantics require confirmation."
        )
    },

    "Forex": {
        "key": "ccil_forex",
        "headers": [
            "instruments", "open", "high", "low", "ltp",
            "prevclose", "netchange", "trades", "volusdinmio"
        ],
        "fields": [
            "Instruments", "Open", "High", "Low", "LTP",
            "Prev Close", "Net Change", "Trades",
            "Vol(USD In Mio)"
        ],
        "unit_notes": (
            "Volume explicitly labelled USD in millions; "
            "price/rate quote convention requires confirmation."
        )
    },

    "Derivatives": {
        "key": "ccil_derivatives",
        "headers": [
            "tenors", "trades", "volumecr", "high",
            "low", "ltr", "weightedaverage", "previousclose"
        ],
        "fields": [
            "Tenors", "Trades", "Volume(₹ Cr.)", "High",
            "Low", "LTR", "Weighted Average", "Previous Close"
        ],
        "unit_notes": (
            "Volume labelled ₹ Cr.; tenor and rate semantics "
            "require source confirmation."
        )
    }
}


class QualityManifest:

    def __init__(self):
        self.data = {
            "execution_timestamp": (
                datetime.datetime.now().astimezone().isoformat()
            ),
            "pipeline_version": PIPELINE_VERSION,
            "scope": ["NSE_FII_DII", "CCIL"],
            "status_model": {
                "publication_status": (
                    "Requires explicit source evidence; "
                    "never inferred from weekday."
                ),
                "extraction_status": (
                    "Per dataset/table, independent of page navigation."
                ),
                "validation_status": (
                    "PARTIAL until date, units and independent "
                    "reconciliation are established."
                )
            },
            "datasets": {}
        }

    def record(
        self, key, source, publication, extraction,
        validation, fields, count, reasons=None,
        evidence=None, extra=None
    ):
        item = {
            "source": source,
            "publication_status": publication,
            "extraction_status": extraction,
            "validation_status": validation,
            "expected_fields": fields,
            "records_extracted": int(count),
            "failure_reasons": reasons or [],
            "evidence": evidence or {}
        }

        if extra:
            item.update(extra)

        self.data["datasets"][key] = item

    def save(self):
        os.makedirs(REPORT_DIR, exist_ok=True)

        path = os.path.join(
            REPORT_DIR,
            "WSH_Quality_Manifest_{}.json".format(
                datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            )
        )

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                self.data, f, indent=2,
                ensure_ascii=False, default=str
            )

        return path


def norm(value):
    return re.sub(
        r"[^a-z0-9]", "",
        str(value or "").lower()
    )


def sha_bytes(value):
    if isinstance(value, bytes):
        payload = value
    else:
        payload = (value or "").encode("utf-8", "replace")

    return hashlib.sha256(payload).hexdigest()


def save_evidence(key, raw, metadata, extension):

    os.makedirs(EVIDENCE_DIR, exist_ok=True)

    stamp = datetime.datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", key)

    raw_path = os.path.join(
        EVIDENCE_DIR,
        "{}_{}.{}".format(safe, stamp, extension)
    )

    meta_path = os.path.join(
        EVIDENCE_DIR,
        "{}_{}.json".format(safe, stamp)
    )

    if isinstance(raw, bytes):
        with open(raw_path, "wb") as f:
            f.write(raw)
    else:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw or "")

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            metadata, f, indent=2,
            ensure_ascii=False, default=str
        )

    return {
        "raw": raw_path,
        "metadata": meta_path,
        "sha256": sha_bytes(raw)
    }


def parse_html_tables(html):

    soup = BeautifulSoup(html or "", "html.parser")
    tables = []

    for index, table in enumerate(soup.find_all("table"), 1):

        parsed_rows = []

        for tr in table.find_all("tr"):
            cells = [
                re.sub(
                    r"\s+", " ",
                    cell.get_text(" ", strip=True)
                ).strip()
                for cell in tr.find_all(["th", "td"])
            ]

            if cells:
                parsed_rows.append(cells)

        if not parsed_rows:
            continue

        headers = parsed_rows[0]

        data_rows = [
            row for row in parsed_rows[1:]
            if any(str(cell).strip() for cell in row)
        ]

        tables.append({
            "index": index,
            "headers": headers,
            "normalized_headers": [norm(h) for h in headers],
            "rows": data_rows,
            "row_count": len(data_rows),
            "fingerprint": sha_bytes(
                json.dumps(
                    [headers, data_rows],
                    ensure_ascii=False,
                    separators=(",", ":")
                )
            )
        })

    return tables


def find_contract_table(html, market):

    contract = CCIL_CONTRACTS[market]

    matches = [
        table for table in parse_html_tables(html)
        if table["normalized_headers"] == contract["headers"]
    ]

    if len(matches) == 1:

        table = dict(matches[0])

        # Exclude blank-key footer and summary rows.
        rows = [
            row for row in table["rows"]
            if row and str(row[0]).strip()
        ]

        table["rows"] = rows
        table["row_count"] = len(rows)

        return table, "EXACT_HEADER_MATCH"

    if len(matches) > 1:
        return None, "AMBIGUOUS_DUPLICATE_HEADER_CONTRACT"

    return None, "TARGET_HEADER_CONTRACT_NOT_FOUND"


def visible_controls(driver):

    controls = []

    elements = driver.find_elements(
        By.CSS_SELECTOR,
        "[role='tab'],button,a,[aria-controls],[role='button']"
    )

    for element in elements:

        try:
            if not element.is_displayed():
                continue

            label = (
                element.get_attribute("aria-label")
                or element.get_attribute("title")
                or element.text
                or element.get_attribute("textContent")
                or ""
            )

            label = re.sub(r"\s+", " ", label).strip()

            if label:
                controls.append((label, element))

        except Exception:
            continue

    return controls


def active_state(element):

    selected = (
        element.get_attribute("aria-selected") or ""
    ).lower()

    if selected in ("true", "false"):
        return selected == "true"

    cls = set(
        (element.get_attribute("class") or "").lower().split()
    )

    return bool(
        cls & {"active", "selected", "current", "tab-active"}
    )


def select_ccil_market(driver, market):

    controls = visible_controls(driver)

    matches = [
        element
        for label, element in controls
        if label.casefold() == market.casefold()
    ]

    diag = {
        "target": market,
        "exact_control_matches": len(matches),
        "clicked": False,
        "active_state_before": None,
        "active_state_after": None
    }

    if len(matches) != 1:
        diag["reason"] = "missing_or_ambiguous_exact_control"
        return diag

    element = matches[0]

    try:

        diag["active_state_before"] = active_state(element)

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});"
            "arguments[0].click();",
            element
        )

        diag["clicked"] = True

        # A click is not proof of successful data acquisition.
        time.sleep(0.8)

        diag["active_state_after"] = active_state(element)

        diag["active_state_changed"] = (
            diag["active_state_before"]
            != diag["active_state_after"]
        )

    except Exception as exc:

        diag["click_error"] = (
            "{}: {}".format(type(exc).__name__, exc)
        )

    return diag


def wait_for_market_table(
    driver, market, timeout=CCIL_WAIT_SECONDS
):

    end = time.monotonic() + timeout

    last_html = driver.page_source
    last_table, last_reason = find_contract_table(
        last_html, market
    )

    observations = []

    while time.monotonic() < end:

        last_html = driver.page_source

        last_table, last_reason = find_contract_table(
            last_html, market
        )

        observations.append({
            "at": datetime.datetime.now().astimezone().isoformat(),
            "contract_status": last_reason,
            "rows": (
                last_table["row_count"]
                if last_table else 0
            ),
            "table_fingerprint": (
                last_table["fingerprint"]
                if last_table else None
            )
        })

        if last_table and last_table["row_count"] > 0:

            return (
                last_html,
                last_table,
                observations,
                "TARGET_TABLE_POPULATED"
            )

        time.sleep(0.75)

    return (
        last_html,
        last_table,
        observations,
        (
            "TARGET_TABLE_EMPTY_AFTER_WAIT"
            if last_table else last_reason
        )
    )


def decimal_reconciliation(records):

    results = []

    for record in records:

        try:

            buy = Decimal(
                str(record["buy"]).replace(",", "")
            )

            sell = Decimal(
                str(record["sell"]).replace(",", "")
            )

            net = Decimal(
                str(record["net"]).replace(",", "")
            )

            calculated = buy - sell

            results.append({
                "participant": record["participant"],
                "calculated_net": str(calculated),
                "reported_net": str(net),
                "matches": calculated == net
            })

        except (
            KeyError, InvalidOperation, TypeError
        ) as exc:

            results.append({
                "participant": record.get("participant"),
                "matches": False,
                "reason": (
                    "reconciliation_unavailable: {}".format(
                        type(exc).__name__
                    )
                )
            })

    return results


def extract_nse_fii_dii(driver, manifest):

    key = "fii_dii_cash_activity"

    lines = [
        "WSH FII/DII — OFFICIAL NSE CASH ACTIVITY",
        "Source page: {}".format(NSE_PAGE),
        "Data endpoint: {}".format(NSE_ENDPOINT)
    ]

    try:

        driver.get("https://www.nseindia.com/")

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.TAG_NAME, "body")
            )
        )

        driver.get(NSE_PAGE)

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.TAG_NAME, "body")
            )
        )

        result = driver.execute_async_script("""
            const url = arguments[0];
            const done = arguments[arguments.length - 1];

            fetch(url, {
                credentials: 'include',
                headers: {
                    'accept': 'application/json,text/plain,*/*'
                }
            })
            .then(async r => ({
                http_status: r.status,
                content_type: r.headers.get('content-type'),
                text: await r.text()
            }))
            .then(done)
            .catch(e => done({error: String(e)}));
        """, NSE_ENDPOINT)

        raw = result.get("text", "")

        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = None

        source_rows = (
            parsed if isinstance(parsed, list) else []
        )

        if isinstance(parsed, dict):

            source_rows = next(
                (
                    value for value in parsed.values()
                    if isinstance(value, list)
                ),
                []
            )

        records = []

        for row in source_rows:

            if not isinstance(row, dict):
                continue

            mapped = {
                norm(k): v for k, v in row.items()
            }

            participant = (
                mapped.get("category")
                or mapped.get("participant")
                or mapped.get("clienttype")
                or mapped.get("name")
            )

            date = (
                mapped.get("date")
                or mapped.get("tradedate")
                or mapped.get("asofdate")
            )

            buy = (
                mapped.get("buyvalue")
                or mapped.get("buy")
                or mapped.get("purchasevalue")
            )

            sell = (
                mapped.get("sellvalue")
                or mapped.get("sell")
                or mapped.get("salevalue")
            )

            net = (
                mapped.get("netvalue")
                or mapped.get("net")
                or mapped.get("netbuyvalue")
            )

            if participant is not None:

                records.append({
                    "participant": str(participant),
                    "date": date,
                    "buy": buy,
                    "sell": sell,
                    "net": net
                })

        reconciliation = decimal_reconciliation(records)

        all_reconciled = (
            bool(records)
            and all(
                item.get("matches")
                for item in reconciliation
            )
        )

        evidence = save_evidence(
            key,
            raw,
            {
                "page_url": driver.current_url,
                "endpoint": NSE_ENDPOINT,
                "http_status": result.get("http_status"),
                "content_type": result.get("content_type"),
                "fetch_error": result.get("error"),
                "raw_length": len(raw),
                "records": records,
                "reconciliation": reconciliation,
                "units": (
                    "UNVERIFIED — source response does not "
                    "state unit in captured record schema."
                )
            },
            "json"
        )

        lines += [
            "HTTP status: {}".format(
                result.get("http_status")
            ),
            "Records: {}".format(len(records)),
            "Arithmetic reconciliation: {}".format(
                json.dumps(reconciliation, ensure_ascii=False)
            ),
            "Units: UNVERIFIED",
            "Evidence: {}".format(
                json.dumps(evidence)
            )
        ]

        complete_fields = (
            bool(records)
            and all(
                all(
                    record.get(field) not in (None, "")
                    for field in ("date", "buy", "sell", "net")
                )
                for record in records
            )
        )

        if (
            result.get("http_status") == 200
            and records
            and complete_fields
        ):

            reasons = [
                "Cash activity acquired from official NSE endpoint.",
                "Reported buy/sell/net arithmetic checked locally.",
                "Unit semantics and independent source reconciliation remain outstanding."
            ]

            if not all_reconciled:
                reasons.append(
                    "One or more reported net values do not equal "
                    "buy minus sell; investigate before use."
                )

            manifest.record(
                key,
                "NSE",
                "UNKNOWN",
                "SUCCESS",
                "PARTIAL",
                ["participant", "date", "buy", "sell", "net"],
                len(records),
                reasons,
                evidence,
                {
                    "unit_status": "UNVERIFIED",
                    "arithmetic_reconciliation": reconciliation,
                    "all_arithmetic_checks_pass": all_reconciled
                }
            )

            lines += [
                "STATUS: UNKNOWN / SUCCESS / PARTIAL",
                json.dumps(
                    records, indent=2, ensure_ascii=False
                )
            ]

        else:

            reasons = [
                "HTTP={}".format(result.get("http_status")),
                "NSE response missing required cash activity fields or records."
            ]

            manifest.record(
                key,
                "NSE",
                "UNKNOWN",
                "FAILED",
                "INVALID",
                ["participant", "date", "buy", "sell", "net"],
                0,
                reasons,
                evidence
            )

            lines += [
                "STATUS: UNKNOWN / FAILED / INVALID",
                "REASONS: " + "; ".join(reasons)
            ]

    except Exception as exc:

        reason = "{}: {}".format(
            type(exc).__name__, exc
        )

        manifest.record(
            key,
            "NSE",
            "UNKNOWN",
            "FAILED",
            "INVALID",
            ["participant", "date", "buy", "sell", "net"],
            0,
            [reason]
        )

        lines += [
            "STATUS: UNKNOWN / FAILED / INVALID",
            reason
        ]

    # These sources are explicitly not implemented in R5.
    # No historical or derivatives records are fabricated.

    for dataset, reason in [
        (
            "fii_dii_derivatives_participant",
            "Participant-wise derivatives source mapping is not implemented in R5."
        ),
        (
            "fii_dii_history",
            "Historical archive source mapping is not implemented in R5."
        )
    ]:

        manifest.record(
            dataset,
            "NSE",
            "UNKNOWN",
            "NOT_ATTEMPTED",
            "NOT_RUN",
            ["source-specific fields", "report date", "units"],
            0,
            [reason]
        )

        lines += [
            "",
            "{}: NOT_ATTEMPTED — {}".format(
                dataset, reason
            )
        ]

    return "\n".join(lines) + "\n"


def extract_ccil(driver, manifest):

    lines = [
        "WSH CCIL FOUR-MARKET TABLE EXTRACTION — R5",
        "Source: {}".format(CCIL_URL),
        (
            "Identity rule: exact market-specific header contract; "
            "page-wide fingerprints are diagnostic only."
        )
    ]

    driver.get(CCIL_URL)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )

    for market, contract in CCIL_CONTRACTS.items():

        key = contract["key"]

        lines += [
            "",
            "--- {} ---".format(market)
        ]

        transition = {
            "target": market,
            "clicked": False,
            "note": "initial page capture"
        }

        try:

            if market != "Money Market":
                transition = select_ccil_market(
                    driver, market
                )

            html, table, observations, wait_status = (
                wait_for_market_table(driver, market)
            )

            all_tables = parse_html_tables(html)

            page_table_count = len(all_tables)

            page_fingerprint = sha_bytes(
                "|".join(
                    sorted(
                        item["fingerprint"]
                        for item in all_tables
                    )
                )
            )

            rows = table["rows"] if table else []

            row_count = len(rows)

            target_fp = (
                table["fingerprint"] if table else None
            )

            table_diag = {
                "market": market,
                "contract_headers_expected": contract["fields"],
                "contract_headers_normalized": contract["headers"],
                "contract_match": bool(table),
                "target_table_index": (
                    table["index"] if table else None
                ),
                "target_table_headers_observed": (
                    table["headers"] if table else None
                ),
                "target_table_rows": row_count,
                "target_table_fingerprint": target_fp,
                "page_table_count": page_table_count,
                "page_fingerprint_diagnostic_only": page_fingerprint,
                "wait_status": wait_status,
                "poll_observations": observations,
                "unit_notes": contract["unit_notes"],
                "row_policy": (
                    "Rows with blank first-column key excluded "
                    "as footer/summary rows."
                )
            }

            evidence = save_evidence(
                key,
                html,
                {
                    "url": driver.current_url,
                    "captured_at": (
                        datetime.datetime.now()
                        .astimezone()
                        .isoformat()
                    ),
                    "transition_diagnostic_only": transition,
                    "table_diagnostic": table_diag,
                    "rows": rows
                },
                "html"
            )

            if table and row_count > 0:

                extraction = "SUCCESS"
                validation = "PARTIAL"
                publication = "UNKNOWN"

                reasons = [
                    "Target-specific exact header contract matched and non-empty rows captured.",
                    "Publication date/status is not explicit in captured evidence.",
                    "Field units/semantics and independent source reconciliation remain pending."
                ]

                manifest.record(
                    key,
                    "CCIL",
                    publication,
                    extraction,
                    validation,
                    contract["fields"],
                    row_count,
                    reasons,
                    evidence,
                    {
                        "target_table_fingerprint": target_fp,
                        "target_table_index": table["index"],
                        "unit_notes": contract["unit_notes"],
                        "navigation_transition": transition,
                        "table_identity_method": (
                            "EXACT_NORMALIZED_HEADER_SEQUENCE"
                        )
                    }
                )

                lines += [
                    "STATUS: {} / {} / {}".format(
                        publication, extraction, validation
                    ),
                    "Rows: {}".format(row_count),
                    "Headers: {}".format(
                        " | ".join(table["headers"])
                    ),
                    "Evidence: {}".format(
                        json.dumps(evidence)
                    )
                ]

                for row in rows:
                    lines.append(" | ".join(row))

            elif table and row_count == 0:

                reasons = [
                    "Target-specific table and header contract were found, but no data rows appeared within the wait window.",
                    "This is an EMPTY capture, not evidence that the market was closed or unpublished."
                ]

                manifest.record(
                    key,
                    "CCIL",
                    "UNKNOWN",
                    "EMPTY",
                    "INVALID",
                    contract["fields"],
                    0,
                    reasons,
                    evidence,
                    {
                        "target_table_fingerprint": target_fp,
                        "target_table_index": table["index"],
                        "navigation_transition": transition,
                        "table_identity_method": (
                            "EXACT_NORMALIZED_HEADER_SEQUENCE"
                        )
                    }
                )

                lines += [
                    "STATUS: UNKNOWN / EMPTY / INVALID",
                    "REASONS: " + "; ".join(reasons),
                    "Evidence: {}".format(
                        json.dumps(evidence)
                    )
                ]

            else:

                reasons = [
                    "Exact target-specific header contract not found or ambiguous.",
                    wait_status
                ]

                manifest.record(
                    key,
                    "CCIL",
                    "UNKNOWN",
                    "FAILED",
                    "INVALID",
                    contract["fields"],
                    0,
                    reasons,
                    evidence,
                    {
                        "navigation_transition": transition,
                        "table_identity_method": (
                            "EXACT_NORMALIZED_HEADER_SEQUENCE"
                        )
                    }
                )

                lines += [
                    "STATUS: UNKNOWN / FAILED / INVALID",
                    "REASONS: " + "; ".join(reasons),
                    "Evidence: {}".format(
                        json.dumps(evidence)
                    )
                ]

        except Exception as exc:

            reason = "{}: {}".format(
                type(exc).__name__, exc
            )

            manifest.record(
                key,
                "CCIL",
                "UNKNOWN",
                "FAILED",
                "INVALID",
                contract["fields"],
                0,
                [reason]
            )

            lines += [
                "STATUS: UNKNOWN / FAILED / INVALID — {}".format(
                    reason
                )
            ]

    return "\n".join(lines) + "\n"


def main():

    if webdriver is None:
        raise RuntimeError(
            "Selenium is required for live collection. "
            "Install requirements.txt first."
        )

    os.makedirs(REPORT_DIR, exist_ok=True)

    manifest = QualityManifest()

    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    options.add_argument(
        "--user-agent=Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)

    try:

        fii_report = extract_nse_fii_dii(
            driver, manifest
        )

        ccil_report = extract_ccil(
            driver, manifest
        )

    finally:
        driver.quit()

    stamp = datetime.datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    with open(
        os.path.join(
            REPORT_DIR,
            "WSH_PRIE_FII_DII_{}.txt".format(stamp)
        ),
        "w",
        encoding="utf-8"
    ) as f:
        f.write(fii_report)

    with open(
        os.path.join(
            REPORT_DIR,
            "WSH_GSSE_CCIL_{}.txt".format(stamp)
        ),
        "w",
        encoding="utf-8"
    ) as f:
        f.write(ccil_report)

    manifest_path = manifest.save()

    source_path = os.path.abspath(__file__)

    with open(
        os.path.join(
            REPORT_DIR,
            "WSH_Source_Fingerprint_{}.txt".format(stamp)
        ),
        "w",
        encoding="utf-8"
    ) as f:

        with open(source_path, "rb") as source_file:
            source_hash = sha_bytes(source_file.read())

        f.write(
            "pipeline_version={}\n".format(PIPELINE_VERSION)
        )

        f.write(
            "source_file={}\n".format(
                os.path.basename(__file__)
            )
        )

        f.write(
            "sha256={}\n".format(source_hash)
        )

    counts = Counter(
        item["validation_status"]
        for item in manifest.data["datasets"].values()
    )

    print(
        "[FII-CCIL R5 COMPLETE] datasets={} validation={} manifest={}".format(
            len(manifest.data["datasets"]),
            dict(counts),
            manifest_path
        )
    )


if __name__ == "__main__":
    main()
