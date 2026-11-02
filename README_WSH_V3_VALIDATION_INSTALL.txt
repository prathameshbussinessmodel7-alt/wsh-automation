WSH V3 CANDIDATE VALIDATION — READY-TO-APPLY PACKAGE
====================================================

PACKAGE CONTENTS (relative to the repository root)
- wsh_cloud_automation_isolation_manifest_candidate_v3.py
- test_wsh_candidate_v3_integration.py
- .github/workflows/wsh-v3-candidate-validation.yml

INSTALLATION
1. Extract this ZIP into the ROOT of the clean cloned repository:
   ...\wsh-v3-validation-clean\wsh-automation
2. Allow Windows to merge the .github/workflows folders if prompted.
3. The two Python files belong in the repository root.
4. The YAML workflow belongs ONLY at:
   .github\workflows\wsh-v3-candidate-validation.yml
5. If a duplicate named wsh-v3-candidate-validation.yml remains in the repository
   root, delete that root-level duplicate. GitHub Actions does not discover
   workflow files from the repository root.
6. Do NOT replace, rename, or execute wsh_cloud_automation.py for this validation.

VALIDATION WORKFLOW
- Manual trigger only (workflow_dispatch).
- Python 3.12.
- Compiles the V3 candidate and its test module.
- Runs the six offline/synthetic integration tests.
- Uploads source hash and test output as a GitHub Actions artifact.
- Does NOT run the production collector or perform live market-data collection.

LOCAL VALIDATION (from repository root, CMD)
python -m py_compile wsh_cloud_automation_isolation_manifest_candidate_v3.py test_wsh_candidate_v3_integration.py
python test_wsh_candidate_v3_integration.py

EXPECTED RESULT
Ran 6 tests ... OK

This package is a validation candidate, not production approval. A passing
offline workflow does not establish live source accuracy or production readiness.
