"""Controlled runtime tests for the real V3 orchestrator.

All external sources, Selenium browser startup, and adapter outputs are replaced
with deterministic fixtures. No live website or market data is accessed.
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

# Minimal Selenium import stubs; the real V3 orchestrator is still executed.
selenium = types.ModuleType('selenium')
webdriver = types.ModuleType('selenium.webdriver')
webdriver.Chrome = lambda **kwargs: None
chrome = types.ModuleType('selenium.webdriver.chrome')
options_mod = types.ModuleType('selenium.webdriver.chrome.options')
class Options:
    def add_argument(self, *args): pass
    def set_capability(self, *args): pass
options_mod.Options = Options
common = types.ModuleType('selenium.webdriver.common')
by_mod = types.ModuleType('selenium.webdriver.common.by')
class By:
    TAG_NAME = 'tag name'
    XPATH = 'xpath'
by_mod.By = By
support = types.ModuleType('selenium.webdriver.support')
ui_mod = types.ModuleType('selenium.webdriver.support.ui')
class WebDriverWait:
    def __init__(self, *args, **kwargs): pass
    def until(self, *args, **kwargs): return object()
ui_mod.WebDriverWait = WebDriverWait
ec_mod = types.ModuleType('selenium.webdriver.support.expected_conditions')
ec_mod.presence_of_element_located = lambda *args: object()
ec_mod.element_to_be_clickable = lambda *args: object()
for name, module in {
    'selenium': selenium, 'selenium.webdriver': webdriver,
    'selenium.webdriver.chrome': chrome,
    'selenium.webdriver.chrome.options': options_mod,
    'selenium.webdriver.common': common,
    'selenium.webdriver.common.by': by_mod,
    'selenium.webdriver.support': support,
    'selenium.webdriver.support.ui': ui_mod,
    'selenium.webdriver.support.expected_conditions': ec_mod,
}.items():
    sys.modules[name] = module
selenium.webdriver = webdriver

SOURCE = pathlib.Path(__file__).with_name(
    'wsh_cloud_automation_isolation_manifest_candidate_v3.py')
spec = importlib.util.spec_from_file_location('wsh_candidate_v3_runtime', SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ADAPTERS = {
    'extract_sensibull_option_chains': ('sensibull_option_chains', 'PRIE'),
    'extract_sensibull_oi_vs_strike': ('sensibull_oi_vs_strike', 'PRIE'),
    'extract_sensibull_fii_dii_tabs': ('sensibull_fii_dii', 'PRIE'),
    'extract_ccil_multi_tabs': ('ccil_multi_tabs', 'GSSE'),
    'extract_tradingeconomics_page': ('tradingeconomics', 'GSSE'),
}

class FakeDriver:
    def __init__(self, quit_error=None, cdp_error=None):
        self.quit_error = quit_error
        self.cdp_error = cdp_error
        self.quit_called = False
    def execute_cdp_cmd(self, *args):
        if self.cdp_error:
            raise self.cdp_error
    def quit(self):
        self.quit_called = True
        if self.quit_error:
            raise self.quit_error

class RuntimeOrchestratorTests(unittest.TestCase):
    def run_pipeline(self, chrome_factory, adapter_behaviour=None, replace_hook=None):
        adapter_behaviour = adapter_behaviour or {}
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            output = io.StringIO()
            try:
                os.chdir(tmp)
                with contextlib.redirect_stdout(output):
                    with mock.patch.object(mod.webdriver, 'Chrome', side_effect=chrome_factory), \
                         mock.patch.object(mod, 'extract_sensibull_option_chains', side_effect=lambda d, m: adapter_behaviour.get('sensibull_option_chains', lambda: 'PRIE-CHAIN')()), \
                         mock.patch.object(mod, 'extract_sensibull_oi_vs_strike', side_effect=lambda d, m: adapter_behaviour.get('sensibull_oi_vs_strike', lambda: 'PRIE-OI')()), \
                         mock.patch.object(mod, 'extract_sensibull_fii_dii_tabs', side_effect=lambda d, m: adapter_behaviour.get('sensibull_fii_dii', lambda: 'PRIE-FII')()), \
                         mock.patch.object(mod, 'extract_ccil_multi_tabs', side_effect=lambda d, m: adapter_behaviour.get('ccil_multi_tabs', lambda: 'GSSE-CCIL')()), \
                         mock.patch.object(mod, 'extract_tradingeconomics_page', side_effect=lambda d, u, k, m: adapter_behaviour.get('tradingeconomics', lambda: 'GSSE-TE')()):
                        if replace_hook:
                            with mock.patch.object(mod.os, 'replace', side_effect=replace_hook):
                                mod.execute_wsh_institutional_pipeline()
                        else:
                            mod.execute_wsh_institutional_pipeline()
                folder = pathlib.Path(tmp) / 'WSH_Automated_Reports'
                files = list(folder.iterdir()) if folder.exists() else []
                manifests = [p for p in files if p.name.startswith('WSH_Quality_Manifest_') and 'CHECKPOINT' not in p.name]
                manifest = json.loads(manifests[0].read_text()) if manifests else None
                prie = [p.read_text() for p in files if p.name.startswith('WSH_PRIE_Data_Package_')]
                gsse = [p.read_text() for p in files if p.name.startswith('WSH_GSSE_Data_Package_')]
                return output.getvalue(), manifest, files, prie, gsse
            finally:
                os.chdir(previous)

    def test_normal_orchestrator_execution_writes_manifest_and_both_packages(self):
        driver = FakeDriver()
        out, manifest, files, prie, gsse = self.run_pipeline(lambda **kw: driver)
        self.assertTrue(driver.quit_called)
        self.assertIn('[PIPELINE COMPLETE]', out)
        self.assertIsNotNone(manifest)
        self.assertEqual(len(prie), 1)
        self.assertEqual(len(gsse), 1)
        self.assertIn('PRIE-CHAIN', prie[0])
        self.assertIn('GSSE-CCIL', gsse[0])
        self.assertTrue(any(p.name.startswith('WSH_Source_Fingerprint_') for p in files))

    def test_each_adapter_failure_is_isolated_and_later_adapters_continue(self):
        adapter_names = ('sensibull_option_chains', 'sensibull_oi_vs_strike',
                         'sensibull_fii_dii', 'ccil_multi_tabs', 'tradingeconomics')
        for failed in adapter_names:
            with self.subTest(adapter=failed):
                calls = []
                behaviours = {}
                for name in adapter_names:
                    def make(name=name):
                        def run():
                            calls.append(name)
                            if name == failed:
                                raise RuntimeError('synthetic adapter failure')
                            return name + '-OK'
                        return run
                    behaviours[name] = make()
                out, manifest, files, prie, gsse = self.run_pipeline(lambda **kw: FakeDriver(), behaviours)
                self.assertEqual(set(calls), set(adapter_names))
                failure_keys = ([f'orchestrator_{failed}_bonds', f'orchestrator_{failed}_calendar']
                                if failed == 'tradingeconomics' else [f'orchestrator_{failed}'])
                for failure_key in failure_keys:
                    self.assertIn(failure_key, manifest['datasets'])
                    self.assertEqual(manifest['datasets'][failure_key]['extraction_status'], 'FAILED')
                self.assertIn('[PIPELINE COMPLETE]', out)

    def test_partial_dataset_write_is_quarantined_by_real_orchestrator_boundary(self):
        def partial():
            # Access manifest through the bound closure is injected below by adapter mock.
            return 'placeholder'
        driver = FakeDriver()
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd(); os.chdir(tmp)
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output), \
                     mock.patch.object(mod.webdriver, 'Chrome', return_value=driver), \
                     mock.patch.object(mod, 'extract_sensibull_option_chains', side_effect=lambda d, m: (m.record_dataset('Synthetic','partial_dataset','PUBLISHED','SUCCESS','VALID',['x'],3,[]), (_ for _ in ()).throw(RuntimeError('after partial write')))[1]), \
                     mock.patch.object(mod, 'extract_sensibull_oi_vs_strike', return_value='ok'), \
                     mock.patch.object(mod, 'extract_sensibull_fii_dii_tabs', return_value='ok'), \
                     mock.patch.object(mod, 'extract_ccil_multi_tabs', return_value='ok'), \
                     mock.patch.object(mod, 'extract_tradingeconomics_page', return_value='ok'):
                    mod.execute_wsh_institutional_pipeline()
                folder = pathlib.Path(tmp)/'WSH_Automated_Reports'
                final = next(folder.glob('WSH_Quality_Manifest_*.json'))
                m = json.loads(final.read_text())
                self.assertEqual(m['datasets']['partial_dataset']['extraction_status'], 'PARTIAL')
                self.assertEqual(m['datasets']['partial_dataset']['validation_status'], 'QUARANTINED')
                self.assertEqual(m['datasets']['partial_dataset']['records_extracted'], 3)
            finally: os.chdir(previous)

    def test_chrome_startup_failure_records_source_datasets_and_still_writes_packages(self):
        def fail(**kwargs): raise RuntimeError('synthetic Chrome startup failure')
        out, manifest, files, prie, gsse = self.run_pipeline(fail)
        self.assertIn('driver_initialization', manifest['datasets'])
        self.assertEqual(manifest['datasets']['option_chain_nifty_discovery']['extraction_status'], 'NOT_ATTEMPTED')
        self.assertEqual(manifest['datasets']['ccil_gsec']['validation_status'], 'NOT_RUN')
        self.assertEqual(len(prie), 1); self.assertEqual(len(gsse), 1)
        self.assertIn('[PIPELINE COMPLETE]', out)

    def test_chrome_shutdown_failure_is_recorded_and_checkpointed(self):
        driver = FakeDriver(quit_error=RuntimeError('synthetic quit failure'))
        out, manifest, files, prie, gsse = self.run_pipeline(lambda **kw: driver)
        self.assertTrue(driver.quit_called)
        self.assertEqual(manifest['datasets']['driver_shutdown']['validation_status'], 'QUARANTINED')
        self.assertTrue(any(p.name == 'WSH_Quality_Manifest_CHECKPOINT.json' for p in files))
        self.assertEqual(len(prie), 1); self.assertEqual(len(gsse), 1)

    def test_cdp_enable_failure_is_warning_and_does_not_stop_adapters(self):
        driver = FakeDriver(cdp_error=RuntimeError('synthetic CDP failure'))
        out, manifest, files, prie, gsse = self.run_pipeline(lambda **kw: driver)
        self.assertIn('[WEBSOCKET CAPTURE WARNING]', out)
        self.assertEqual(len(prie), 1); self.assertEqual(len(gsse), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
