# WSH Institutional Data Collector

## Files
- `wsh_cloud_automation.py` — governed source collection and validation.
- `requirements.txt` — Python dependencies.
- `.github/workflows/wsh-collector.yml` — manual and hourly GitHub Actions workflow.

## Install into an existing repository
Copy the three files/folders to the repository root. In GitHub, enable Actions and ensure the workflow is permitted to write repository contents:
**Settings → Actions → General → Workflow permissions → Read and write permissions**.

Run it manually from **Actions → WSH Institutional Data Collector → Run workflow**. It also runs hourly at minute 17 UTC.

## Output
`WSH_Automated_Reports/` contains:
- `WSH_Quality_Manifest_<run_id>.json`
- `WSH_PRIE_Data_Package_<run_id>.txt`
- `WSH_GSSE_Data_Package_<run_id>.txt`
- `WSH_Run_Summary_<run_id>.json`
- `raw/` immutable HTML evidence
- `normalized/` JSON record extracts
- `evidence/` per-dataset validation evidence

## Governance behavior
Publication status, extraction status, and validation status are separate. `VALID` requires dataset-specific checks. Cross-tab duplicates are quarantined. Table count is not treated as record count. CCIL weekend non-publication is applied only to the four explicitly configured CCIL datasets. The run-level release status remains `HOLD` if any dataset is invalid/quarantined or otherwise violates release controls.

## Important limits
This is a conservative collector, not a guarantee that every site will expose machine-readable data. Sensibull may require JavaScript/session access or change its markup; CCIL tab labels/layout may change; TradingEconomics may throttle automation. A failed or unsupported adapter is reported rather than filled with fabricated values. Do not use output for trading or capital allocation until the source-specific schema and observation timestamps are validated against actual source data.

### Source contracts requiring final confirmation
The code intentionally does not grant execution-grade `VALID` to FII/DII non-summary tabs or CCIL tabs without explicit source-specific column contracts. Option chains are not `VALID` unless expiry and source timestamp are actually populated from a verified source field. This prevents false-positive passes; add confirmed selectors/contracts only after inspecting the live DOM or a documented API.
