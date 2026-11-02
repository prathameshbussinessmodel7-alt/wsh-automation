#!/usr/bin/env python3
"""WSH WebSocket payload validation layer v1.1.

Audits Sensibull WebSocket JSON artifacts without changing the collector or promoting
records to institutional VALID. It deliberately separates structural checks from
identity and freshness checks, which require independent source evidence.

Usage:
  python wsh_payload_validation_v1.py <artifact-directory-or-zip> [--max-age-minutes 15]

Outputs WSH_Payload_Validation_Audit.json and .txt in the chosen directory (for ZIP,
in the current working directory). No network access or credentials are used.
"""
import argparse, datetime as dt, hashlib, json, math, os, sys, zipfile
from pathlib import Path

REQUIRED = ("instrument_token", "last_price", "oi", "oi_change", "oi_change_quantity",
            "volume", "best_buy_price", "best_sell_price")


def parse_time(value):
    try:
        x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if x.tzinfo is None:
            return None
        return x.astimezone(dt.timezone.utc)
    except Exception:
        return None


def finite_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def audit_payload(d, filename, max_age_minutes):
    checks, blockers, warnings = {}, [], []
    symbol = str(d.get("symbol", "")).upper()
    expiry = str(d.get("requested_expiry", ""))
    encoded = str(d.get("encoded_expiry", ""))
    rows = d.get("rows") if isinstance(d.get("rows"), list) else []
    captured = parse_time(d.get("captured_at_utc", ""))
    now = dt.datetime.now(dt.timezone.utc)
    age_min = (now-captured).total_seconds()/60 if captured else None

    checks["capture_timestamp_parseable"] = captured is not None
    if captured is None: blockers.append("Missing/invalid timezone-aware capture timestamp")
    checks["capture_age_within_limit"] = age_min is not None and -1 <= age_min <= max_age_minutes
    if age_min is None: blockers.append("Freshness cannot be evaluated")
    elif age_min < -1: blockers.append("Capture timestamp is in the future")
    elif age_min > max_age_minutes: blockers.append(f"Artifact age {age_min:.1f} min exceeds configured {max_age_minutes} min limit")

    checks["symbol_present"] = symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
    if not checks["symbol_present"]: blockers.append("Missing or unrecognized requested symbol")
    checks["expiry_format_valid"] = bool(len(expiry)==10 and expiry[4:5]=='-' and expiry[7:8]=='-')
    if not checks["expiry_format_valid"]: blockers.append("Requested expiry is not YYYY-MM-DD")
    checks["encoded_expiry_matches"] = bool(expiry and encoded == expiry.replace("-", ""))
    if not checks["encoded_expiry_matches"]: blockers.append("Encoded WebSocket expiry does not match requested expiry")
    checks["capture_status_pass"] = d.get("status") == "EXPIRY_MATCHED_STRUCTURE_PASS"
    if not checks["capture_status_pass"]: blockers.append("Collector did not report expiry-matched structure pass")
    checks["route_identity_flag_pass"] = d.get("route_identity_check") in (True, "PASS", "pass", {"status":"PASS"})
    if not checks["route_identity_flag_pass"]:
        warnings.append("Route/instrument identity is not independently proven by a recognized PASS flag")

    checks["minimum_strike_rows"] = len(rows) >= 8
    if not checks["minimum_strike_rows"]: blockers.append(f"Only {len(rows)} rows; minimum is 8")
    strikes, call_tokens, put_tokens = [], [], []
    missing_fields, bad_values, duplicate_strikes = [], [], []
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            bad_values.append(f"row {i}: not an object"); continue
        strike = row.get("strike")
        if not finite_num(strike) or strike <= 0:
            bad_values.append(f"row {i}: invalid strike")
        else:
            strikes.append(float(strike))
            if float(strike) in seen: duplicate_strikes.append(float(strike))
            seen.add(float(strike))
        for side, tokens in (("call",call_tokens),("put",put_tokens)):
            for field in REQUIRED:
                key=f"{side}_{field}"
                if key not in row:
                    missing_fields.append(f"row {i}: {key} (key absent)")
                elif row[key] is None:
                    missing_fields.append(f"row {i}: {key} (explicit null)")
            token=row.get(f"{side}_instrument_token")
            if token is not None: tokens.append(str(token))
            for field in ("last_price","oi","volume","best_buy_price","best_sell_price"):
                v=row.get(f"{side}_{field}")
                if not finite_num(v) or v < 0:
                    bad_values.append(f"row {i}: invalid {side}_{field}={v!r}")
            for field in ("oi_change","oi_change_quantity"):
                v=row.get(f"{side}_{field}")
                if v is not None and not finite_num(v):
                    bad_values.append(f"row {i}: invalid {side}_{field}={v!r}; expected finite number or null")
            bid=row.get(f"{side}_best_buy_price"); ask=row.get(f"{side}_best_sell_price")
            if finite_num(bid) and finite_num(ask):
                if bid > 0 and ask == 0:
                    bad_values.append(f"row {i}: {side} ask is zero while bid is positive (quote unavailable/invalid; not a valid two-sided quote)")
                elif bid > ask:
                    bad_values.append(f"row {i}: {side} bid exceeds ask")
    checks["strike_grid_sorted"] = len(strikes)==len(rows) and strikes==sorted(strikes)
    if not checks["strike_grid_sorted"]: blockers.append("Strike grid is not fully numeric and ascending")
    checks["unique_strikes"] = not duplicate_strikes
    if duplicate_strikes: blockers.append(f"Duplicate strikes: {sorted(set(duplicate_strikes))[:10]}")
    absent_fields = [x for x in missing_fields if "(key absent)" in x]
    null_fields = [x for x in missing_fields if "(explicit null)" in x]
    checks["required_side_fields_present_and_nonnull"] = not missing_fields
    checks["required_field_keys_absent_count"] = len(absent_fields)
    checks["required_field_explicit_null_count"] = len(null_fields)
    if absent_fields: blockers.append(f"Required field keys absent ({len(absent_fields)}); examples: {absent_fields[:5]}")
    if null_fields: blockers.append(f"Required fields explicitly null ({len(null_fields)}); examples: {null_fields[:5]}")
    checks["numeric_market_values_valid"] = not bad_values
    checks["zero_ask_with_positive_bid_count"] = sum("ask is zero while bid is positive" in x for x in bad_values)
    if bad_values: blockers.append(f"Invalid numeric values/quotes ({len(bad_values)}); examples: {bad_values[:5]}")
    checks["call_tokens_unique"] = len(call_tokens)==len(set(call_tokens)) and len(call_tokens)==len(rows)
    checks["put_tokens_unique"] = len(put_tokens)==len(set(put_tokens)) and len(put_tokens)==len(rows)
    if not checks["call_tokens_unique"]: blockers.append("Call instrument tokens missing or duplicated")
    if not checks["put_tokens_unique"]: blockers.append("Put instrument tokens missing or duplicated")

    # Identity cannot be certified merely because URL parameters match the requested symbol.
    warnings.append("This offline audit cannot independently prove that the captured frame belongs to the requested underlying.")
    warnings.append("This offline audit cannot verify live freshness against an independent market source.")
    # Structural pass is not equivalent to institutional VALID.
    status = "STRUCTURE_PASS_PENDING_EXTERNAL_IDENTITY_AND_FRESHNESS" if not blockers else "FAIL_OR_STALE"
    return {"file":filename,"symbol":symbol,"requested_expiry":expiry,"captured_at_utc":d.get("captured_at_utc"),
            "artifact_age_minutes_at_audit":round(age_min,2) if age_min is not None else None,
            "rows":len(rows),"status":status,"checks":checks,"blockers":blockers,"warnings":warnings,
            "sha256_file_content":None}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("source", help="Artifact directory or ZIP downloaded from GitHub Actions")
    ap.add_argument("--max-age-minutes", type=float, default=15)
    args=ap.parse_args()
    source=Path(args.source)
    items=[]
    if source.is_dir():
        for p in sorted(source.glob("*.json")):
            try:
                d=json.loads(p.read_text(encoding="utf-8"))
                if "rows" in d and "requested_expiry" in d: items.append((p.name,d,hashlib.sha256(p.read_bytes()).hexdigest()))
            except Exception: pass
        outdir=source
    elif source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            for n in z.namelist():
                if n.lower().endswith(".json") and "sensibull_ws_" in n.lower():
                    try:
                        raw=z.read(n); d=json.loads(raw)
                        items.append((n,d,hashlib.sha256(raw).hexdigest()))
                    except Exception: pass
        outdir=Path.cwd()
    else:
        print("ERROR: source must be a directory or ZIP artifact",file=sys.stderr); return 2
    results=[]
    for name,d,sha in items:
        r=audit_payload(d,name,args.max_age_minutes); r["sha256_file_content"]=sha; results.append(r)
    report={"audit_version":"WSH_PAYLOAD_VALIDATION_V1_1","audit_time_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "max_age_minutes":args.max_age_minutes,"payloads_found":len(results),"institutional_valid_count":0,
            "note":"No payload is promoted to institutional VALID by this offline layer. Independent underlying identity and external freshness reconciliation are required.","results":results}
    (outdir/"WSH_Payload_Validation_Audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    lines=["WSH PAYLOAD VALIDATION AUDIT v1.1",f"Audit UTC: {report['audit_time_utc']}",f"Payloads: {len(results)}", "Institutional VALID: 0 (external identity/freshness not proven)",""]
    for r in results:
        lines += [f"{r['symbol']} {r['requested_expiry']} | rows={r['rows']} | {r['status']} | age_min={r['artifact_age_minutes_at_audit']}"]
        lines += [f"  BLOCKER: {x}" for x in r['blockers']]
        lines += [f"  WARNING: {x}" for x in r['warnings']]
    (outdir/"WSH_Payload_Validation_Audit.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("\n".join(lines))
    return 0

if __name__=="__main__": raise SystemExit(main())
