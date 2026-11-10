"""WSH R7.5.3 CCIL network-response evidence audit.

Audits collector metadata without inferring market closure, publication, or data absence.
Accepts a live output directory or a ZIP evidence package.
"""
from __future__ import annotations
import argparse, json, zipfile
from pathlib import Path

VERSION = "WSH_R7.5.3"
MARKETS = {
    "ccil_money_market": "Money Market",
    "ccil_gsec": "Gsec",
    "ccil_forex": "Forex",
    "ccil_derivatives": "Derivatives",
}


def _read_metadata(source):
    items = []
    path = Path(source)
    if path.is_dir():
        for f in sorted(path.rglob("*.meta.json")):
            try: items.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
            except Exception as exc: items.append((f.name, {"metadata_read_error": str(exc)}))
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(".meta.json"):
                    try: items.append((Path(name).name, json.loads(z.read(name))))
                    except Exception as exc: items.append((name, {"metadata_read_error": str(exc)}))
    else:
        raise ValueError(f"Input is neither a directory nor a ZIP package: {source}")
    return items


def audit(source):
    metadata = _read_metadata(source)
    results = {}
    for key, market in MARKETS.items():
        candidates = [(name, meta) for name, meta in metadata if key in name]
        if not candidates:
            results[key.upper()] = {"market": market, "status": "NOT_FOUND"}
            continue
        name, meta = candidates[-1]
        table = meta.get("table_diagnostic", {})
        network = table.get("network_response_diagnostics")
        if network is None:
            resources = (table.get("page_state_diagnostics") or {}).get("performance_resources") or []
            results[key.upper()] = {
                "market": market, "metadata_file": name,
                "status": "LEGACY_NO_HTTP_RESPONSE_CAPTURE",
                "target_resource_url_observed": any(f"p_p_resource_id={_resource_id(market)}" in u for u in resources),
                "http_status": None, "response_body_status": "NOT_CAPTURED",
                "interpretation": "A browser resource URL may be present, but no HTTP response status/body evidence was retained.",
            }
            continue
        requests = network.get("requests", [])
        results[key.upper()] = {
            "market": market, "metadata_file": name,
            "status": "NETWORK_EVIDENCE_CAPTURED" if requests else "NO_MATCHING_REQUEST_OBSERVED",
            "request_count": network.get("request_count", len(requests)),
            "requests": [{k: r.get(k) for k in ("url", "method", "http_status", "status_text", "mime_type", "protocol", "remote_ip_address", "encoded_data_length", "loading_finished", "loading_failure", "network_verdict", "body_capture_status", "body_length_bytes", "body_sha256", "body_saved_path", "body_capture_error")} for r in requests],
            "log_capture_error": network.get("log_capture_error"),
        }
    observed = [v for v in results.values() if v.get("status") == "NETWORK_EVIDENCE_CAPTURED"]
    return {
        "validator_version": VERSION,
        "input": str(source),
        "ccil_market_count": len(MARKETS),
        "network_response_capture_markets": len(observed),
        "results": results,
        "policy": {
            "empty_does_not_mean_market_closed": True,
            "resource_url_does_not_prove_http_success": True,
            "http_2xx_does_not_prove_valid_market_data": True,
            "no_request_observed_does_not_prove_source_unavailable": True,
        },
        "overall_status": "DIAGNOSTIC_ONLY",
    }


def _resource_id(market):
    return {"Money Market":"moneyMarketTable", "Gsec":"gsecTable", "Forex":"forexTable", "Derivatives":"derivativeTable"}[market]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Live output directory or evidence ZIP")
    parser.add_argument("--output", help="Optional JSON audit output path")
    args = parser.parse_args()
    result = audit(args.input)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

if __name__ == "__main__": main()
