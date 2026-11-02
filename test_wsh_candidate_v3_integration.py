import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

# Minimal Selenium import stubs: these tests exercise the real adapter/orchestrator
# functions without launching Chrome or making network requests.
selenium = types.ModuleType('selenium')
webdriver = types.ModuleType('selenium.webdriver')
webdriver.Chrome = lambda **kwargs: None
chrome = types.ModuleType('selenium.webdriver.chrome')
options_mod = types.ModuleType('selenium.webdriver.chrome.options')
class Options:
    def add_argument(self, *a): pass
    def set_capability(self, *a): pass
options_mod.Options = Options
common = types.ModuleType('selenium.webdriver.common')
by_mod = types.ModuleType('selenium.webdriver.common.by')
class By:
    TAG_NAME='tag name'; XPATH='xpath'
by_mod.By = By
support = types.ModuleType('selenium.webdriver.support')
ui_mod = types.ModuleType('selenium.webdriver.support.ui')
class WebDriverWait:
    def __init__(self, *a, **k): pass
    def until(self, *a, **k): return object()
ui_mod.WebDriverWait = WebDriverWait
ec_mod = types.ModuleType('selenium.webdriver.support.expected_conditions')
ec_mod.presence_of_element_located = lambda *a: object()
ec_mod.element_to_be_clickable = lambda *a: object()
for name, mod in {
    'selenium': selenium, 'selenium.webdriver': webdriver,
    'selenium.webdriver.chrome': chrome, 'selenium.webdriver.chrome.options': options_mod,
    'selenium.webdriver.common': common, 'selenium.webdriver.common.by': by_mod,
    'selenium.webdriver.support': support, 'selenium.webdriver.support.ui': ui_mod,
    'selenium.webdriver.support.expected_conditions': ec_mod,
}.items(): sys.modules[name] = mod
selenium.webdriver = webdriver

SOURCE = pathlib.Path(__file__).with_name('wsh_cloud_automation_isolation_manifest_candidate_v3.py')
spec = importlib.util.spec_from_file_location('wsh_candidate_v3', SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FailingNavigationDriver:
    def get(self, url): raise RuntimeError('synthetic navigation failure')

class CandidateIntegrationTests(unittest.TestCase):
    def test_fii_portal_level_failure_records_all_four_tabs(self):
        m = mod.InstitutionalQualityManifest()
        payload = mod.extract_sensibull_fii_dii_tabs(FailingNavigationDriver(), m)
        keys = [f'fii_dii_{x}' for x in ('summary','futures_and_options','cash_market','fii_history')]
        self.assertTrue('Portal Error' in payload)
        for key in keys:
            self.assertEqual(m.manifest['datasets'][key]['extraction_status'], 'FAILED')
            self.assertEqual(m.manifest['datasets'][key]['validation_status'], 'INVALID')

    def test_ccil_portal_level_failure_records_all_four_tabs(self):
        m = mod.InstitutionalQualityManifest()
        payload = mod.extract_ccil_multi_tabs(FailingNavigationDriver(), m)
        keys = [f'ccil_{x}' for x in ('money_market','gsec','forex','derivatives')]
        self.assertTrue('CCIL Portal General Error' in payload)
        for key in keys:
            self.assertEqual(m.manifest['datasets'][key]['extraction_status'], 'FAILED')
            self.assertEqual(m.manifest['datasets'][key]['validation_status'], 'INVALID')

    def test_escaped_exception_quarantines_partial_records_and_preserves_other_dataset(self):
        m = mod.InstitutionalQualityManifest()
        m.record_dataset('Other','unrelated','PUBLISHED','SUCCESS','VALID',['x'],1,[])
        def adapter():
            m.record_dataset('Synthetic','partial_output','PUBLISHED','SUCCESS','VALID',['a'],7,[])
            raise RuntimeError('after partial write')
        with tempfile.TemporaryDirectory() as d:
            out = mod._wsh_run_adapter('synthetic_adapter', adapter, m, d)
        item = m.manifest['datasets']['partial_output']
        self.assertEqual(item['extraction_status'], 'PARTIAL')
        self.assertEqual(item['validation_status'], 'QUARANTINED')
        self.assertEqual(item['records_extracted'], 7)
        self.assertEqual(m.manifest['datasets']['unrelated']['validation_status'], 'VALID')
        self.assertIn('synthetic_adapter', out)

    def test_non_string_result_fails_closed(self):
        m = mod.InstitutionalQualityManifest()
        with tempfile.TemporaryDirectory() as d:
            out = mod._wsh_run_adapter('bad_return', lambda: {'not':'text'}, m, d)
        self.assertIn('bad_return', out)
        self.assertEqual(m.manifest['datasets']['orchestrator_bad_return']['validation_status'], 'INVALID')

    def test_startup_failure_records_source_level_datasets_not_attempted(self):
        m = mod.InstitutionalQualityManifest()
        mod._wsh_record_source_datasets_not_attempted(m, "Chrome unavailable")
        keys = (
            "option_chain_nifty_discovery", "oi_vs_strike_nifty",
            "fii_dii_summary", "fii_dii_futures_and_options",
            "fii_dii_cash_market", "fii_dii_fii_history",
            "ccil_money_market", "ccil_gsec", "ccil_forex", "ccil_derivatives",
            "gsse_sovereign_bonds", "gsse_macro_calendar",
        )
        for key in keys:
            item = m.manifest["datasets"][key]
            self.assertEqual(item["extraction_status"], "NOT_ATTEMPTED")
            self.assertEqual(item["validation_status"], "NOT_RUN")

    def test_checkpoint_write_failure_is_reported_as_false(self):
        class BrokenManifest:
            manifest = {'datasets': {}}
        with tempfile.TemporaryDirectory() as d:
            # A regular file cannot be used as the checkpoint directory.
            blocker = pathlib.Path(d) / 'not_a_directory'
            blocker.write_text('x')
            self.assertFalse(mod._wsh_manifest_checkpoint(BrokenManifest(), str(blocker)))

if __name__ == '__main__': unittest.main(verbosity=2)
