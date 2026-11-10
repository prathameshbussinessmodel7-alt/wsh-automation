"""R7.5.3 network-response diagnostics and RND-6 regression tests."""
import json, unittest, zipfile
from pathlib import Path
import wsh_r7_5_3 as r753
from wsh_fii_ccil_only import parse_chrome_network_events

ROOT = Path(__file__).parent
RND6 = ROOT / "WSH-FII-CCIL-RND-6.zip"


def entry(method, params):
    return {"message": json.dumps({"message": {"method": method, "params": params}})}


class NetworkParserTests(unittest.TestCase):
    URL = "https://www.ccilindia.com/home?p_p_resource_id=gsecTable"
    def test_response_status_correlated_to_exact_table_endpoint(self):
        events = [
            entry("Network.requestWillBeSent", {"requestId":"1", "request":{"url":self.URL,"method":"GET","initiator":{"type":"script"}}}),
            entry("Network.responseReceived", {"requestId":"1", "response":{"status":200,"statusText":"OK","mimeType":"application/json","protocol":"h2","headers":{"content-type":"application/json"}}}),
            entry("Network.loadingFinished", {"requestId":"1"}),
        ]
        result = parse_chrome_network_events(events, "Gsec")
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(result["requests"][0]["http_status"], 200)
        self.assertEqual(result["requests"][0]["network_verdict"], "HTTP_SUCCESS_BODY_PENDING")

    def test_non_target_endpoint_is_excluded(self):
        events = [entry("Network.requestWillBeSent", {"requestId":"2", "request":{"url":"https://www.ccilindia.com/home?p_p_resource_id=forexTable","method":"GET"}})]
        self.assertEqual(parse_chrome_network_events(events, "Gsec")["request_count"], 0)

    def test_http_failure_is_not_relabelled_empty_or_closed(self):
        events = [
            entry("Network.requestWillBeSent", {"requestId":"3", "request":{"url":self.URL,"method":"GET"}}),
            entry("Network.responseReceived", {"requestId":"3", "response":{"status":503,"statusText":"Service Unavailable","mimeType":"text/html"}}),
            entry("Network.loadingFinished", {"requestId":"3"}),
        ]
        result = parse_chrome_network_events(events, "Gsec")
        self.assertEqual(result["requests"][0]["network_verdict"], "HTTP_ERROR")

    def test_request_failure_retained(self):
        events = [
            entry("Network.requestWillBeSent", {"requestId":"4", "request":{"url":self.URL,"method":"GET"}}),
            entry("Network.loadingFailed", {"requestId":"4", "errorText":"net::ERR_TIMED_OUT"}),
        ]
        result = parse_chrome_network_events(events, "Gsec")
        self.assertEqual(result["requests"][0]["network_verdict"], "REQUEST_FAILED")


@unittest.skipUnless(RND6.exists(), "RND-6 fixture missing")
class RND6RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.audit = r753.audit(RND6)
    def test_four_markets_reported(self): self.assertEqual(self.audit["ccil_market_count"], 4)
    def test_legacy_fixture_not_claimed_to_have_http_status(self):
        for key in ("CCIL_GSEC", "CCIL_FOREX", "CCIL_DERIVATIVES"):
            self.assertEqual(self.audit["results"][key]["status"], "LEGACY_NO_HTTP_RESPONSE_CAPTURE")
            self.assertIsNone(self.audit["results"][key]["http_status"])
    def test_resource_url_not_mistaken_for_success(self):
        self.assertTrue(self.audit["policy"]["resource_url_does_not_prove_http_success"])
    def test_empty_not_market_closed(self):
        self.assertTrue(self.audit["policy"]["empty_does_not_mean_market_closed"])

if __name__ == "__main__": unittest.main(verbosity=2)
