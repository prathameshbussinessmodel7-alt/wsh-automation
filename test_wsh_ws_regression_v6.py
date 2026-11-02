#!/usr/bin/env python3
"""Offline regression checks for WSH Sensibull WS diagnostics v6.

Loads the exact diagnostic functions from wsh_cloud_automation_ws_diagnostics_v6.py
without importing Selenium/browser dependencies, then replays the preserved baseline ZIP.
This test never edits or repairs payload values.
"""
from __future__ import annotations
import ast
import datetime
import json
import math
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
CANDIDATE = ROOT / "wsh_cloud_automation_ws_diagnostics_v6.py"
FIXTURE_ZIP = ROOT / "WSH-collector-39948804037.zip"
EXPECTED = {
    "BANKNIFTY": {"rows": 81, "one_sided": 0, "both_zero": 0, "nulls": 0},
    "FINNIFTY":  {"rows": 81, "one_sided": 5, "both_zero": 0, "nulls": 0},
    "NIFTY":     {"rows": 81, "one_sided": 0, "both_zero": 0, "nulls": 0},
    "SENSEX":    {"rows": 81, "one_sided": 0, "both_zero": 136, "nulls": 34},
}
EXPECTED_QUALITY = {
    "BANKNIFTY": "PASS",
    "FINNIFTY": "BLOCKED",
    "NIFTY": "PASS",
    "SENSEX": "BLOCKED",
}
REQUIRED_FIELDS = (
    "instrument_token", "last_price", "oi", "oi_change",
    "oi_change_quantity", "volume", "best_buy_price", "best_sell_price"
)


def load_exact_candidate_functions():
    source = CANDIDATE.read_text(encoding="utf-8")
    # Verify whole candidate parses before extracting functions.
    compile(source, str(CANDIDATE), "exec")
    tree = ast.parse(source, filename=str(CANDIDATE))
    wanted = {"_wsh_quote_and_field_diagnostics", "_wsh_identity_diagnostic",
              "_wsh_quote_freshness_diagnostic"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    found = {n.name for n in nodes}
    if found != wanted:
        raise AssertionError(f"Candidate diagnostic functions missing: {sorted(wanted-found)}")
    namespace = {
        "math": math,
        "re": __import__("re"),
        "datetime": datetime,
        "_WSH_REQUIRED_WS_FIELDS": REQUIRED_FIELDS,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CANDIDATE), "exec"), namespace)
    return namespace


def row_to_chain(rows):
    chain = {}
    for row in rows:
        strike = str(row.get("strike"))
        sides = {}
        for side in ("call", "put"):
            prefix = side + "_"
            sides[side] = {key[len(prefix):]: value for key, value in row.items()
                           if key.startswith(prefix)}
        chain[strike] = sides
    return {"chain": chain}


def main():
    if not CANDIDATE.exists():
        raise SystemExit(f"Candidate not found: {CANDIDATE}")
    if not FIXTURE_ZIP.exists():
        raise SystemExit(f"Baseline fixture ZIP not found: {FIXTURE_ZIP}")
    funcs = load_exact_candidate_functions()
    quote_diag = funcs["_wsh_quote_and_field_diagnostics"]
    identity_diag = funcs["_wsh_identity_diagnostic"]
    freshness_diag = funcs["_wsh_quote_freshness_diagnostic"]

    # Explicit identity-key regression cases: normalized variants must be recognized.
    identity_cases = [
        ({"underlying_symbol": "NIFTY"}, "NIFTY", "PASS"),
        ({"underlyingSymbol": "NIFTY"}, "NIFTY", "PASS"),
        ({"symbol": "BANKNIFTY"}, "BANKNIFTY", "PASS"),
        ({"underlying_symbol": "SENSEX"}, "NIFTY", "FAIL"),
        ({"last_price": 123.4}, "NIFTY", "UNVERIFIED"),
    ]
    for payload, requested, expected_status in identity_cases:
        got = identity_diag(payload, requested)["status"]
        if got != expected_status:
            raise AssertionError(
                f"Identity case {payload!r} requested={requested}: "
                f"expected {expected_status}, got {got}"
            )

    # Missing explicit source timestamps must never be promoted to a freshness pass.
    missing_time = freshness_diag({"chain": {"1": {"call": {}, "put": {}}}}, "2026-10-23T12:00:00+00:00")
    if missing_time.get("status") != "UNVERIFIED":
        raise AssertionError(
            f"Missing source timestamps must remain UNVERIFIED, got {missing_time}"
        )
    print("IDENTITY_REGRESSION_PASS: key variants, match, conflict, and missing identity tested.")
    print("FRESHNESS_GUARD_PASS: missing source timestamps remain UNVERIFIED.")

    observed = {}
    with zipfile.ZipFile(FIXTURE_ZIP) as archive:
        members = [n for n in archive.namelist() if pathlib.PurePosixPath(n).name.startswith("WSH_Sensibull_WS_") and n.endswith(".json")]
        for name in members:
            payload = json.loads(archive.read(name).decode("utf-8"))
            symbol = payload.get("symbol")
            if symbol not in EXPECTED:
                continue
            rows = payload.get("rows")
            if not isinstance(rows, list):
                raise AssertionError(f"{symbol}: rows is not a list")
            q = quote_diag(rows)
            explicit_nulls = q["required_field_defect_count"]
            # The saved wrapper's symbol and URL are capture metadata, not decoded-chain identity proof.
            chain_data = row_to_chain(rows)
            identity = identity_diag(chain_data, symbol)
            freshness = freshness_diag(chain_data, "2026-10-23T12:00:00+00:00")
            got = {"rows": len(rows), "one_sided": q["one_sided_zero_count"],
                   "both_zero": q["both_sides_zero_count"], "nulls": explicit_nulls,
                   "identity": identity["status"], "freshness": freshness["status"]}
            observed[symbol] = got
            expected = EXPECTED[symbol]
            for key in ("rows", "one_sided", "both_zero", "nulls"):
                if got[key] != expected[key]:
                    raise AssertionError(f"{symbol} {key}: expected {expected[key]}, got {got[key]}")
            if identity["status"] != "UNVERIFIED":
                raise AssertionError(f"{symbol}: missing independent identity must remain UNVERIFIED, got {identity['status']}")
            if freshness["status"] != "UNVERIFIED":
                raise AssertionError(f"{symbol}: missing per-quote source timestamps must remain UNVERIFIED, got {freshness['status']}")
            blockers = any(got[key] > 0 for key in ("one_sided", "both_zero", "nulls"))
            computed_quality = "BLOCKED" if blockers else "PASS"
            if computed_quality != EXPECTED_QUALITY[symbol]:
                raise AssertionError(
                    f"{symbol}: expected quote-quality disposition {EXPECTED_QUALITY[symbol]}, got {computed_quality}"
                )
            got["quote_quality"] = computed_quality

    if set(observed) != set(EXPECTED):
        raise AssertionError(f"Expected all four indices; found {sorted(observed)}")
    print("REGRESSION_PASS: candidate syntax valid; all four baseline payloads replayed.")
    for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
        print(f"{symbol}: {json.dumps(observed[symbol], sort_keys=True)}")
    print("QUOTE_QUALITY_GATE_PASS: FINNIFTY/SENSEX blocked; NIFTY/BANKNIFTY pass quote/field checks only.")
    print("ACCEPTANCE_GUARD_PASS: identity and freshness remain UNVERIFIED; no institutional acceptance granted.")

if __name__ == "__main__":
    main()
