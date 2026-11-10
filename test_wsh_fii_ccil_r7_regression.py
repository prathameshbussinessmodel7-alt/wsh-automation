
"""WSH R7.1 regression harness for the R6 NSE FII/DII + CCIL collector.

Run from repository root:
    python -m unittest -v test_wsh_fii_ccil_r7_regression.py

Offline and deterministic. Does not launch Selenium, access NSE/CCIL,
infer publication dates or units, or invoke Sensibull.
"""

import hashlib
import json
import tempfile
import unittest

import wsh_fii_ccil_only as collector


EXPECTED_MARKETS = {
    "Money Market": [
        "market", "open", "high", "low", "ltr", "volumecrs",
        "war", "prevdaywar", "prevdayvolcrs",
    ],
    "Gsec": ["securitydescription", "trades", "ttacr", "ltp", "lty"],
    "Forex": [
        "instruments", "open", "high", "low", "ltp", "prevclose",
        "netchange", "trades", "volusdinmio",
    ],
    "Derivatives": [
        "tenors", "trades", "volumecr", "high", "low", "ltr",
        "weightedaverage", "previousclose",
    ],
}


class R71BaselineContractTests(unittest.TestCase):

    def test_pipeline_scope_excludes_sensibull(self):
        scope = collector.QualityManifest().data["scope"]
        self.assertIn("NSE_FII_DII", scope)
        self.assertIn("CCIL", scope)
        self.assertNotIn("SENSIBULL", scope)

    def test_four_ccil_header_contracts_are_preserved(self):
        self.assertEqual(set(collector.CCIL_CONTRACTS), set(EXPECTED_MARKETS))
        for market, expected in EXPECTED_MARKETS.items():
            self.assertEqual(
                collector.CCIL_CONTRACTS[market]["headers"], expected
            )

    def test_each_ccil_contract_identifies_its_own_table(self):
        for market, headers in EXPECTED_MARKETS.items():
            header_cells = "".join(
                f"<th>{value}</th>" for value in headers
            )
            html = (
                "<html><body><table><tr>"
                + header_cells
                + "</tr><tr>"
                + "".join("<td>1</td>" for _ in headers)
                + "</tr></table></body></html>"
            )

            table, status = collector.find_contract_table(html, market)

            self.assertEqual(status, "EXACT_HEADER_MATCH", market)
            self.assertIsNotNone(table, market)
            self.assertEqual(table["normalized_headers"], headers, market)
            self.assertEqual(table["row_count"], 1, market)

    def test_incomplete_header_is_not_misidentified(self):
        html = (
            "<table><tr><th>Market</th><th>Open</th>"
            "<th>High</th><th>Low</th><th>LTR</th></tr>"
            "<tr><td>CALL</td></tr></table>"
        )

        for market in EXPECTED_MARKETS:
            table, status = collector.find_contract_table(html, market)
            self.assertIsNone(table, market)
            self.assertNotEqual(status, "EXACT_HEADER_MATCH", market)

    def test_raw_payload_and_metadata_are_separate_and_hashable(self):
        original_dir = collector.EVIDENCE_DIR

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                collector.EVIDENCE_DIR = temp_dir

                raw_payload = '{"data":[{"participant":"DII"}]}'
                metadata = {
                    "source": "NSE",
                    "capture_status": "SUCCESS",
                }

                saved = collector.save_evidence(
                    "r71_nse_test", raw_payload, metadata, "json"
                )

                self.assertNotEqual(saved["raw"], saved["metadata"])

                with open(saved["raw"], encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), raw_payload)

                with open(saved["metadata"], encoding="utf-8") as handle:
                    self.assertEqual(json.load(handle), metadata)

                expected_hash = hashlib.sha256(
                    raw_payload.encode("utf-8")
                ).hexdigest()

                self.assertEqual(saved["sha256"], expected_hash)

        finally:
            collector.EVIDENCE_DIR = original_dir

    def test_nse_date_parser_supported_formats(self):
        cases = {
            "09-Nov-2026": "2026-11-09",
            "09-November-2026": "2026-11-09",
            "2026-11-09": "2026-11-09",
            "09/11/2026": "2026-11-09",
        }

        for source_value, expected in cases.items():
            with self.subTest(source_value=source_value):
                self.assertEqual(
                    collector.parse_nse_date(source_value), expected
                )

    def test_missing_or_invalid_date_is_not_invented(self):
        self.assertIsNone(collector.parse_nse_date(None))
        self.assertIsNone(collector.parse_nse_date(""))
        self.assertIsNone(collector.parse_nse_date("not-a-date"))

    def test_nse_fii_dii_arithmetic_matches_r6_baseline(self):
        records = [
            {
                "participant": "DII",
                "buy": "12764.97",
                "sell": "19937.50",
                "net": "-7172.53",
            },
            {
                "participant": "FII/FPI",
                "buy": "20310.99",
                "sell": "13098.77",
                "net": "7212.22",
            },
        ]

        results = collector.decimal_reconciliation(records)

        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["matches"] for item in results))
        self.assertEqual(results[0]["calculated_net"], "-7172.53")
        self.assertEqual(results[1]["calculated_net"], "7212.22")

    def test_fii_dii_arithmetic_mismatch_is_detected(self):
        records = [
            {
                "participant": "DII",
                "buy": "100",
                "sell": "70",
                "net": "35",
            }
        ]

        result = collector.decimal_reconciliation(records)
        self.assertFalse(result[0]["matches"])

    def test_ccil_numeric_diagnostics_preserve_47_check_baseline(self):
        money_market = [
            ["CALL", "5.60", "5.70", "4.30", "5.50",
             "14551.04", "5.59", "5.20", "846.10"],
            ["TREP", "5.00", "6.00", "4.00", "5.20",
             "100.00", "5.30", "5.10", "50.00"],
            ["BASKET REPO", "5.00", "6.00", "4.00", "5.40",
             "200.00", "5.50", "5.20", "60.00"],
            ["SPECIAL REPO", "5.00", "6.00", "4.00", "5.30",
             "300.00", "5.40", "5.10", "70.00"],
        ]

        gsec = [
            ["GS A", "10", "100.00", "98.50", "7.10"],
            ["GS B", "20", "200.00", "99.00", "7.20"],
            ["SGS C", "30", "300.00", "97.50", "7.30"],
            ["SGS D", "40", "400.00", "96.50", "7.40"],
            ["GS E", "50", "500.00", "95.50", "7.50"],
        ]

        forex = [
            ["SPOT", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["C-SPOT", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["R-SPOT", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["C/S", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["S/NOV26", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["S/DEC26", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["F/NOV26", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["F/DEC26", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
            ["F/JAN27", "93.85", "94.11", "93.69", "93.77",
             "93.985", "-0.215", "775", "468.00"],
        ]

        derivatives = [
            ["OIS1Y", "10", "100", "6.50", "6.30",
             "6.40", "6.42", "6.35"],
            ["OIS2Y", "20", "200", "6.60", "6.30",
             "6.45", "6.46", "6.40"],
            ["OIS3Y", "30", "300", "6.70", "6.30",
             "6.50", "6.52", "6.45"],
            ["OIS4Y", "40", "400", "6.80", "6.30",
             "6.55", "6.56", "6.50"],
            ["OIS5Y", "50", "500", "6.90", "6.30",
             "6.60", "6.62", "6.55"],
        ]

        results = {
            "Money Market": collector.ccil_numeric_checks(
                "Money Market", money_market
            ),
            "Gsec": collector.ccil_numeric_checks("Gsec", gsec),
            "Forex": collector.ccil_numeric_checks("Forex", forex),
            "Derivatives": collector.ccil_numeric_checks(
                "Derivatives", derivatives
            ),
        }

        expected = {
            "Money Market": 8,
            "Gsec": 20,
            "Forex": 9,
            "Derivatives": 10,
        }

        for market, checks in expected.items():
            self.assertEqual(results[market]["checks_total"], checks)
            self.assertEqual(results[market]["checks_passed"], checks)
            self.assertTrue(results[market]["all_checks_pass"])

        self.assertEqual(sum(expected.values()), 47)

    def test_numeric_diagnostics_do_not_claim_independent_reconciliation(self):
        row = [[
            "SPOT", "93.85", "94.11", "93.69", "93.77",
            "93.985", "-0.215", "775", "468.00",
        ]]

        result = collector.ccil_numeric_checks("Forex", row)

        self.assertIn(
            "not independent source reconciliation",
            result["scope_note"],
        )

    def test_manifest_does_not_infer_publication_dates(self):
        manifest = collector.QualityManifest().data

        self.assertIn("datasets", manifest)
        self.assertIn(
            "never inferred from weekday",
            manifest["status_model"]["publication_status"],
        )
        self.assertEqual(manifest["datasets"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
