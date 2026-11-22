import unittest

from tests.unit import SRC  # noqa: F401
from tdflowmcp.gate import GateConfig, decide
from tdflowmcp.net.bindverify import ListenerState

S = ListenerState.SERVING
CFG = GateConfig(port=9480)
HOST = {"Host": "127.0.0.1:9480"}
ORIGIN = {"Origin": "http://127.0.0.1:9480"}


def h(**kw):
    d = dict(HOST)
    d.update({k.replace("_", "-"): v for k, v in kw.items()})
    return d


class VerifyingTests(unittest.TestCase):
    def test_nothing_served_before_bind_verified(self):
        for st in (ListenerState.OFF, ListenerState.VERIFYING, ListenerState.CLOSED):
            for path in ("/mcp", "/setup/", "/setup/api/status", "/.well-known/tdflowmcp", "/x"):
                d = decide("GET", path, h(), st, CFG)
                self.assertEqual((d.allow, d.status, d.code), (False, 503, "TDFLOW_VERIFYING"), (st, path))


class HostAndPreflightTests(unittest.TestCase):
    def test_bad_host_rejected_dns_rebinding(self):
        d = decide("GET", "/setup/", {"Host": "evil.example:9480"}, S, CFG)
        self.assertEqual((d.status, d.code), (403, "BAD_HOST"))
        self.assertEqual(decide("GET", "/setup/", {}, S, CFG).code, "BAD_HOST")

    def test_preflight_never_granted(self):
        d = decide("OPTIONS", "/setup/api/status", h(Origin="http://127.0.0.1:9480"), S, CFG)
        self.assertEqual((d.allow, d.code), (False, "PREFLIGHT_DENIED"))

    def test_body_limit(self):
        self.assertEqual(decide("POST", "/mcp", h(Content_Length=str(2 * 1024 * 1024)), S, CFG).status, 413)

    def test_localhost_page_redirects_to_canonical(self):
        d = decide("GET", "/setup/", {"Host": "localhost:9480"}, S, CFG)
        self.assertEqual((d.status, d.location), (308, "http://127.0.0.1:9480/setup/"))
        self.assertEqual(decide("POST", "/setup/api/pair", {"Host": "localhost:9480", "Origin": "http://localhost:9480"},
                                S, CFG).code, "NON_CANONICAL_HOST")


class McpTests(unittest.TestCase):
    def test_non_browser_without_origin_allowed_then_needs_token(self):
        d = decide("POST", "/mcp", h(Content_Type="application/json"), S, CFG)
        self.assertEqual((d.allow, d.next), (True, "agent_token"))

    def test_foreign_and_other_port_origins_rejected(self):
        for o in ("http://evil.example", "http://127.0.0.1:5173", "http://localhost:9480"):
            d = decide("POST", "/mcp", h(Origin=o, Content_Type="application/json"), S, CFG)
            self.assertEqual((d.status, d.code), (403, "BAD_ORIGIN"), o)

    def test_method_and_content_type(self):
        self.assertEqual(decide("GET", "/mcp", h(), S, CFG).status, 405)
        self.assertEqual(decide("POST", "/mcp", h(Content_Type="text/plain"), S, CFG).status, 415)


class BrowserRouteTests(unittest.TestCase):
    def test_static_get_same_origin_or_navigation(self):
        self.assertTrue(decide("GET", "/setup/", h(), S, CFG).allow)
        self.assertTrue(decide("GET", "/setup/", h(Sec_Fetch_Site="none"), S, CFG).allow)
        self.assertFalse(decide("GET", "/setup/", h(Sec_Fetch_Site="cross-site"), S, CFG).allow)

    def test_api_read_needs_custom_header_not_origin(self):
        # Browsers omit Origin on same-origin GET (review 2, H1): must still work.
        d = decide("GET", "/setup/api/status", h(X_TDflow_Request="1", Sec_Fetch_Site="same-origin"), S, CFG)
        self.assertEqual((d.allow, d.next), (True, ""))
        self.assertEqual(decide("GET", "/setup/api/status", h(), S, CFG).code, "MISSING_REQUEST_HEADER")
        self.assertEqual(decide("GET", "/setup/api/status", h(X_TDflow_Request="1", Sec_Fetch_Site="same-site"),
                                S, CFG).code, "CROSS_SITE_FETCH")

    def test_dashboard_api_reads_require_admin(self):
        d = decide("GET", "/api/audit", h(X_TDflow_Request="1"), S, CFG)
        self.assertEqual(d.next, "admin_session")
        d = decide("GET", "/setup/api/status", h(X_TDflow_Request="1"), S, GateConfig(setup_reads_need_admin=True))
        self.assertEqual(d.next, "admin_session")

    def test_pair_route(self):
        ok = decide("POST", "/setup/api/pair", h(Origin="http://127.0.0.1:9480", X_TDflow_Request="1"), S, CFG)
        self.assertEqual((ok.allow, ok.route, ok.next), (True, "pair", "pair"))
        self.assertEqual(decide("POST", "/setup/api/pair", h(X_TDflow_Request="1"), S, CFG).code, "BAD_ORIGIN")
        self.assertEqual(decide("POST", "/setup/api/pair", h(Origin="http://127.0.0.1:9480"), S, CFG).code,
                         "MISSING_REQUEST_HEADER")
        st = decide("GET", "/setup/api/pair/status", h(X_TDflow_Request="1"), S, CFG)
        self.assertEqual((st.allow, st.next), (True, "pair"))
        self.assertEqual(decide("GET", "/setup/api/pair/status", h(X_TDflow_Request="1", Sec_Fetch_Site="cross-site"),
                                S, CFG).code, "CROSS_SITE_FETCH")
        self.assertEqual(decide("GET", "/setup/api/agents", h(X_TDflow_Request="1"), S, CFG).next, "admin_session+csrf")

    def test_mutations_need_exact_origin_and_csrf(self):
        ok = decide("POST", "/setup/api/tokens", h(Origin="http://127.0.0.1:9480", X_TDflow_CSRF="t"), S, CFG)
        self.assertEqual((ok.allow, ok.next), (True, "admin_session+csrf"))
        self.assertEqual(decide("POST", "/setup/api/tokens", h(X_TDflow_CSRF="t"), S, CFG).code, "BAD_ORIGIN")
        self.assertEqual(decide("POST", "/setup/api/tokens", h(Origin="http://127.0.0.1:5173", X_TDflow_CSRF="t"),
                                S, CFG).code, "BAD_ORIGIN")
        self.assertEqual(decide("DELETE", "/api/tokens/1", h(Origin="http://127.0.0.1:9480"), S, CFG).code,
                         "MISSING_CSRF")

    def test_events_websocket(self):
        d = decide("GET", "/api/events", h(Upgrade="websocket", Origin="http://127.0.0.1:9480"), S, CFG)
        self.assertEqual((d.allow, d.status, d.next), (True, 101, "admin_session+csrf"))
        self.assertEqual(decide("GET", "/api/events", h(Upgrade="websocket"), S, CFG).code, "BAD_ORIGIN")

    def test_unknown_and_well_known(self):
        self.assertEqual(decide("GET", "/nope", h(), S, CFG).status, 404)
        self.assertTrue(decide("GET", "/.well-known/tdflowmcp", h(), S, CFG).allow)
        self.assertEqual(decide("POST", "/.well-known/tdflowmcp", h(), S, CFG).status, 405)

    def test_header_lookup_is_case_insensitive(self):
        d = decide("POST", "/mcp", {"host": "127.0.0.1:9480", "content-type": "application/json; charset=utf-8",
                                    "ORIGIN": "http://evil.example"}, S, CFG)
        self.assertEqual(d.code, "BAD_ORIGIN")



class HardeningTests(unittest.TestCase):
    """Review 3: path normalisation bypass (H2), body limits and duplicate headers (M9)."""

    def test_traversal_and_encoded_paths_rejected_before_classification(self):
        for p in ("/setup/api/../../api/x", "/setup/api/./status", "//api/x", "/api" + chr(92) + "x", "/setup/api/%2e%2e/x",
                  "/setup/api/st%61tus", "/setup/\u00e9", "/setup/api/a" + chr(0) + "b", "", "setup/"):
            d = decide("GET", p, h(X_TDflow_Request="1"), S, CFG)
            self.assertEqual((d.allow, d.status, d.code), (False, 400, "BAD_PATH"), p)

    def test_query_string_is_stripped_and_validated_path_returned(self):
        d = decide("GET", "/setup/api/status?x=%2e%2e", h(X_TDflow_Request="1"), S, CFG)
        self.assertEqual((d.allow, d.path), (True, "/setup/api/status"))

    def test_bad_content_lengths(self):
        for v in ("-1", "abc", "1e3", " 5"):
            self.assertEqual(decide("POST", "/mcp", h(Content_Length=v, Content_Type="application/json"), S, CFG).code,
                             "BAD_CONTENT_LENGTH", v)

    def test_transfer_encoding_requires_actual_body_length(self):
        hdr = h(Transfer_Encoding="chunked", Content_Type="application/json")
        self.assertEqual(decide("POST", "/mcp", hdr, S, CFG).code, "TRANSFER_ENCODING_UNSUPPORTED")
        self.assertEqual(decide("POST", "/mcp", hdr, S, CFG, body_len=2 * 1024 * 1024).status, 413)
        self.assertTrue(decide("POST", "/mcp", hdr, S, CFG, body_len=100).allow)

    def test_declared_length_must_match_actual(self):
        hdr = h(Content_Length="10", Content_Type="application/json")
        self.assertEqual(decide("POST", "/mcp", hdr, S, CFG, body_len=11).code, "CONTENT_LENGTH_MISMATCH")
        self.assertTrue(decide("POST", "/mcp", hdr, S, CFG, body_len=10).allow)

    def test_conflicting_duplicate_sensitive_headers_rejected(self):
        d = decide("GET", "/setup/", {"Host": "127.0.0.1:9480", "host": "evil.example:9480"}, S, CFG)
        self.assertEqual((d.status, d.code), (400, "DUPLICATE_HEADER:host"))
        d = decide("POST", "/mcp", {"Host": "127.0.0.1:9480", "Origin": "http://127.0.0.1:9480",
                                    "origin": "http://evil.example", "Content-Type": "application/json"}, S, CFG)
        self.assertEqual(d.code, "DUPLICATE_HEADER:origin")
        # identical duplicates are harmless
        self.assertTrue(decide("GET", "/setup/", {"Host": "127.0.0.1:9480", "host": "127.0.0.1:9480"}, S, CFG).allow)

    def test_null_origin_rejected(self):
        d = decide("POST", "/mcp", h(Origin="null", Content_Type="application/json"), S, CFG)
        self.assertEqual(d.code, "BAD_ORIGIN")


if __name__ == "__main__":
    unittest.main()
