#!/usr/bin/env python3
"""Decode and structurally validate a captured Sensibull WebSocket hex message."""
import argparse, csv, gzip, hashlib, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

MAGIC = b"\x1f\x8b\x08"
REQUIRED_SIDE_FIELDS = (
    "instrument_token", "last_price", "oi", "oi_change",
    "oi_change_quantity", "volume", "best_buy_price", "best_sell_price"
)

def decode_hex(path):
    text = re.sub(r"\s+", "", path.read_text(encoding="utf-8").strip())
    if not text or len(text) % 2:
        raise ValueError("Input must be non-empty, even-length hexadecimal text.")
    try:
        return bytes.fromhex(text)
    except ValueError as e:
        raise ValueError(f"Invalid hexadecimal input: {e}") from e

def decode(raw):
    offset = raw.find(MAGIC)
    if offset < 0:
        raise ValueError("GZIP signature 1f 8b 08 not found.")
    decoded = gzip.decompress(raw[offset:])
    data = json.loads(decoded.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("chain"), dict):
        raise ValueError("Decoded JSON does not contain an object at 'chain'.")
    m = re.search(rb"(20\d{6})$", raw[:offset])
    expiry = m.group(1).decode("ascii") if m else None
    frame = {
        "binary_message_bytes": len(raw), "gzip_offset_bytes": offset,
        "compressed_payload_bytes": len(raw[offset:]), "decompressed_json_bytes": len(decoded),
        "frame_prefix_hex": raw[:offset].hex(), "encoded_expiry": expiry,
        "sha256_binary_message": hashlib.sha256(raw).hexdigest(),
        "sha256_decompressed_json": hashlib.sha256(decoded).hexdigest()
    }
    return data, frame

def normalize(data, frame):
    chain = data["chain"]
    rows, issues = [], []
    for strike_text, sides in chain.items():
        try: strike = float(strike_text)
        except (TypeError, ValueError): continue
        row = {"strike": strike}
        for side in ("call", "put"):
            obj = sides.get(side, {}) if isinstance(sides, dict) else {}
            if not isinstance(obj, dict): obj = {}
            for key, value in obj.items():
                row[f"{side}_{key}"] = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
            missing = [field for field in REQUIRED_SIDE_FIELDS if field not in obj]
            if missing: issues.append({"strike": strike, "side": side, "missing": missing})
        rows.append(row)
    rows.sort(key=lambda x: x["strike"])
    strikes = [r["strike"] for r in rows]
    steps = sorted({round(strikes[i+1]-strikes[i], 8) for i in range(len(strikes)-1)})
    validation = {
        "status": "PASS" if rows and len(strikes) == len(set(strikes)) and not issues else "PARTIAL",
        "strike_records": len(rows), "unique_strikes": len(set(strikes)),
        "min_strike": min(strikes) if strikes else None, "max_strike": max(strikes) if strikes else None,
        "observed_strike_steps": steps, "encoded_expiry": frame["encoded_expiry"],
        "required_fields_checked": list(REQUIRED_SIDE_FIELDS),
        "missing_required_field_issue_count": len(issues), "missing_required_field_issues_sample": issues[:10],
        "json_fields": sorted(data.keys()),
        "chain_side_fields": sorted(chain[next(iter(chain))]["call"].keys()) if chain else [],
        "important_limitation": "Per-strike IV/Greeks are not present in this captured message; ATM IV is a top-level summary. Structural validation does not prove freshness or correct live underlying mapping."
    }
    result = {
        "source": "captured_sensibull_websocket_hex",
        "decoded_at_utc": datetime.now(timezone.utc).isoformat(),
        "frame": frame,
        "summary": {k: data.get(k) for k in ("atm_strike","atm_iv","atm_iv_change","atm_iv_percentile","pcr","max_pain_strike","atm_ivp_type","future_price")},
        "chain": rows, "validation": validation
    }
    return result, rows, validation

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_hex", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("sensibull_decoded"))
    args = ap.parse_args()
    data, frame = decode(decode_hex(args.input_hex))
    result, rows, validation = normalize(data, frame)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir/"sensibull_decoded.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.out_dir/"sensibull_validation.json").write_text(json.dumps({"frame":frame,"validation":validation}, indent=2), encoding="utf-8")
    cols = sorted({k for row in rows for k in row})
    with (args.out_dir/"sensibull_strikes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(json.dumps({"validation":validation,"output_directory":str(args.out_dir)}, indent=2))
    if validation["status"] != "PASS": sys.exit(2)

if __name__ == "__main__": main()
