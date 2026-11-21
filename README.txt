# WSH Sensibull V3.11.1 - Capture and Comparator

## 1. Project purpose

This release captures browser-observed Sensibull option-chain WebSocket
payload representations through Chrome DevTools Protocol (CDP), records
capture metadata and integrity information, and performs hardened
cross-session comparison.

## 2. Validated environment

- Python: 3.15.0 (64-bit)
- Selenium: 4.51.0
- Chrome: 157.0.8103.79
- ChromeDriver: 157.0.8103.78
- Dependency declaration: requirements.txt

Install dependencies:

python -m pip install -r requirements.txt

Chrome and a compatible ChromeDriver are required for live capture.
Selenium Manager is used for driver resolution.

## 3. V3.11.1 release package contents

This release package contains exactly eight files:

- .gitignore
- README.txt
- requirements.txt
- wsh_v3_11_capture.py
- wsh_v3_11_compare_HARDENED_V311_COMPAT.py
- test_wsh_v3_11.py
- test_wsh_v3_11_compare_hardened_V311_COMPAT.py
- WSH_V3_11_1_RELEASE_SHA256.json

The SHA256 manifest records hashes for the seven release files other
than the manifest itself.

Historical test scripts, live-test orchestrators, audit files, browser
logs, raw captures and local evidence packages are not included.

## 4. November 20, 2026 local validation

Three canonical capture sessions were compared locally:

- Session A: 1,475 frames.
- Session B: 1,485 frames.
- Session C_RELOAD: 1,474 frames.

Recorded results:

- Hash integrity failures: zero in all sessions.
- Malformed frames: zero in all sessions.
- Missing or invalid monotonic clocks: zero.
- Monotonic clock regressions: zero.
- Capture timestamps: valid.
- Session labels: valid.
- Run IDs: unique and present.
- Manual identity entries: complete and consistent.
- Automated integrity gate: PASS.
- Overall comparator acceptance gate: PASS.

The C_RELOAD test included an operator-performed CTRL+R reload
approximately five minutes into capture. The associated browser log
records the reload navigation and a new WebSocket handshake.

These are locally recorded validation results, not independent
provider or exchange verification.

## 5. Regression testing

The local regression run completed 15 tests across two suites:

- Original capture and compatibility suite: 3/3 passed.
- Hardened comparator and V3.11.1 compatibility suite: 12/12 passed.
- Total: 15/15 passed; zero failures.

The included test files are:

- test_wsh_v3_11.py
- test_wsh_v3_11_compare_hardened_V311_COMPAT.py

The test results describe the local validation run. They should not be
interpreted as an independent CI execution or GitHub Actions result.

## 6. Historical local validation evidence

The canonical November 20 evidence was retained separately in the
original local workspace under WSH_V3_11_Captures.

The canonical files were:

- WSH_V3_11_Session_A_20261120_102957.json
- WSH_V3_11_Session_B_20261120_104537.json
- WSH_V3_11_Session_C_RELOAD_20261120_112237.json
- WSH_V3_11_ABC_Cross_Session_Report_20261120_LOCAL.json

Associated checkpoints and ChromeDriver logs were also retained.

The earlier C_RELOAD attempt at 11:01 is historical evidence and is
not the canonical C_RELOAD session.

These evidence files are not included in this eight-file release
package. Reproduction of the November 20 comparison requires access
to those separately retained local files.

## 7. Reproduce the comparator run

After making the separately retained canonical capture JSON files
available under WSH_V3_11_Captures, run the following from the
project directory in PowerShell:

python .\wsh_v3_11_compare_HARDENED_V311_COMPAT.py `
".\WSH_V3_11_Captures\WSH_V3_11_Session_A_20261120_102957.json" `
".\WSH_V3_11_Captures\WSH_V3_11_Session_B_20261120_104537.json" `
".\WSH_V3_11_Captures\WSH_V3_11_Session_C_RELOAD_20261120_112237.json" `
--out ".\WSH_V3_11_Captures\WSH_V3_11_ABC_Cross_Session_Report_20261120_LOCAL.json"

The capture JSON files are not supplied in this release package.

## 8. Evidence limitations

- CDP payloadData is a browser-exposed representation, not guaranteed
  original wire bytes.
- Local UTC and monotonic clocks measure browser-side observation,
  not provider timestamps.
- Underlying and expiry are operator attestations, not independent
  provider verification.
- Hash overlap is descriptive and does not prove freshness or
  provider sequence continuity.
- A successful integrity gate does not establish exchange authenticity.
- The comparator does not independently prove instrument selection.

## 9. Release status

- November 20 local capture and comparator acceptance criteria: PASS.
- Original capture and compatibility tests: 3/3 PASS.
- Hardened comparator and compatibility tests: 12/12 PASS.
- Combined local regression result: 15/15 PASS.
- Local acceptance report: generated and verified.
- GitHub publication: not yet completed.
- Workflow deployment: not yet completed.

The release SHA256 manifest is WSH_V3_11_1_RELEASE_SHA256.json.
The historical PACKAGE_SHA256.json is not part of this release.

Do not publish browser profiles, authentication cookies, credentials,
or other session secrets to GitHub.