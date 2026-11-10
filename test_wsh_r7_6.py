import json
import tempfile
import unittest
from pathlib import Path

import wsh_r7_6


class R76ArchiveTests(unittest.TestCase):
    def make_input(self, root):
        evidence_dir = root / "evidence"
        evidence_dir.mkdir(parents=True)
        datasets = {}
        for key, code in wsh_r7_6.DATASETS.items():
            raw = evidence_dir / f"{key}.raw"
            meta = evidence_dir / f"{key}.meta.json"
            raw.write_text(f"raw-{key}", encoding="utf-8")
            metadata = {
                "captured_at": "2026-11-12T04:00:00+00:00",
                "capture_timestamp_source": "collector_runtime_clock",
            }
            if key == "fii_dii_cash_activity":
                metadata["records"] = [{"date": "12-Nov-2026"}]
            else:
                metadata["source_date_evidence"] = {
                    "status": "UNKNOWN",
                    "normalized_date": None,
                    "reason": "No explicit source date"
                }
            meta.write_text(json.dumps(metadata), encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            datasets[key] = {
                "source": "NSE" if key.startswith("fii") else "CCIL",
                "publication_status": "UNKNOWN",
                "extraction_status": "SUCCESS",
                "validation_status": "PARTIAL",
                "records_extracted": 1,
                "evidence": {"raw": str(raw.relative_to(root)),
                             "metadata": str(meta.relative_to(root)),
                             "sha256": digest},
            }
        manifest = {
            "pipeline_version": "TEST_R7.5.3",
            "execution_timestamp": "2026-11-12T04:00:00+00:00",
            "datasets": datasets,
        }
        (root / "WSH_Quality_Manifest_test.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_ingest_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "input"
            source.mkdir()
            archive = base / "archive"
            self.make_input(source)
            result = wsh_r7_6.ingest_directory(source, archive)
            self.assertEqual(result["results"][0]["status"], "INGESTED")
            audit = wsh_r7_6.audit_archive(archive)
            self.assertEqual(audit["run_count"], 1)
            self.assertEqual(len(audit["dataset_history"]), 5)
            nse = next(x for x in audit["dataset_history"]
                       if x["dataset_key"] == "fii_dii_cash_activity")
            self.assertEqual(nse["unknown_source_dates"], 0)

    def test_idempotent_manifest_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "input"
            source.mkdir()
            archive = base / "archive"
            self.make_input(source)
            first = wsh_r7_6.ingest_directory(source, archive)
            second = wsh_r7_6.ingest_directory(source, archive)
            self.assertEqual(first["results"][0]["status"], "INGESTED")
            self.assertEqual(second["results"][0]["status"], "ALREADY_INGESTED")
            self.assertEqual(wsh_r7_6.audit_archive(archive)["run_count"], 1)

    def test_naive_timestamp_rejected(self):
        self.assertIsNone(wsh_r7_6.parse_aware_timestamp("2026-11-12T04:00:00"))

    def test_unknown_ccil_date_not_inferred(self):
        value, status = wsh_r7_6.source_date_for(
            "ccil_gsec", {"captured_at": "2026-11-12T04:00:00+00:00"}
        )
        self.assertIsNone(value)
        self.assertEqual(status, "UNKNOWN")

    def test_conflicting_nse_dates_not_collapsed(self):
        value, status = wsh_r7_6.source_date_for(
            "fii_dii_cash_activity",
            {"records": [{"date": "12-Nov-2026"}, {"date": "13-Nov-2026"}]}
        )
        self.assertIsNone(value)
        self.assertEqual(status, "CONFLICT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
