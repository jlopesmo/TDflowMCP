"""Phase 4: network summary and optimization hints (pure)."""
import unittest

from tdflowmcp import insights as I

ROWS = [
    {"path": "/p/noise1", "type": "noiseTOP", "family": "TOP", "inputs": [], "cook_count": 10, "cook_ms": 9.5,
     "resolution": [3840, 2160]},
    {"path": "/p/level1", "type": "levelTOP", "family": "TOP", "inputs": ["/p/noise1"], "cook_count": 10, "cook_ms": 8.0},
    {"path": "/p/out", "type": "nullTOP", "family": "TOP", "inputs": ["/p/level1"], "cook_count": 10, "cook_ms": 0.1,
     "display": True},
    {"path": "/p/left", "type": "blurTOP", "family": "TOP", "inputs": [], "cook_count": 3, "cook_ms": 0.5},
    {"path": "/p/bad", "type": "selectTOP", "family": "TOP", "inputs": [], "cook_count": 0, "errors": "no source"},
]


class InsightTests(unittest.TestCase):
    def test_static_reference_accepts_exact_parameter_and_channel_forms(self):
        self.assertEqual(I.literal_reference(" op('../controls').par.Fade "),
                         ("../controls", "parameter", "Fade"))
        self.assertEqual(I.literal_reference('op("/p/mod")["chan1"]'), ("/p/mod", "channel", "chan1"))

    def test_static_reference_omits_dynamic_or_unbounded_source(self):
        for expression in ("op(name).par.Fade", "op('controls').par.Fade.eval()",
                           "op('a').par.Fade + op('b').par.Fade", "op('x')['bad/path']",
                           "getattr(op('x').par,'Fade')", "1 / 0", "x" * 513, "\x00"):
            with self.subTest(expression=expression[:24]):
                self.assertIsNone(I.literal_reference(expression))

    def test_summary(self):
        s = I.summarize(ROWS, total=7, filtered=2)
        self.assertEqual(s["edges"], [["/p/noise1", "/p/level1"], ["/p/level1", "/p/out"]])
        self.assertEqual((s["by_family"], s["with_errors"], s["never_cooked"]), ({"TOP": 5}, ["/p/bad"], ["/p/bad"]))
        self.assertEqual((s["total"], s["shown"], s["truncated"], s["filtered"]), (7, 5, True, 2))

    def test_hints_are_ranked_and_explained(self):
        h = I.hints(ROWS)
        kinds = [(x["kind"], x["path"]) for x in h]
        self.assertEqual(kinds[:2], [("slow_cook", "/p/noise1"), ("slow_cook", "/p/level1")])
        self.assertIn(("frame_budget", None), kinds)
        self.assertIn(("error", "/p/bad"), kinds)
        self.assertIn(("large_texture", "/p/noise1"), kinds)
        self.assertIn(("dead_end", "/p/left"), kinds)
        self.assertNotIn(("dead_end", "/p/out"), kinds)                  # displayed and a null
        self.assertTrue(all(x["why"] for x in h))
        self.assertEqual(I.hints([]), [])

    def test_diagnostics_trace_visible_wiring_and_label_observations(self):
        rows = [
            dict(path="/p/out", inputs=["/p/up"], errors="input failed", cook_count=2, cook_ms=0.5),
            dict(path="/p/up", inputs=["/p/out", "/private/hidden"], warnings="stale", cook_count=1, cook_ms=4.0),
            dict(path="/p/cold", inputs=[], cook_count=0, cook_ms=10.0),
            dict(path="/p/invalid", inputs=[], cook_count=1, cook_ms=float("nan")),
        ]
        result = I.diagnostics(rows, partial=True, input_notes={"/p/out": {"minimum_inputs": 2, "connected_inputs": 1}})
        self.assertTrue(result["partial_view"])
        top = result["findings"][0]
        self.assertEqual(top["observations"], ["confirmed_error", "missing_required_inputs"])
        self.assertEqual(top["upstream_leads"], [
            {"path": "/p/up", "hops": 1, "observations": ["observed_warning"]}])
        self.assertNotIn("/private/hidden", repr(result))
        self.assertEqual(result["performance"]["sample_count"], 2)
        self.assertEqual([entry["path"] for entry in result["performance"]["top"]], ["/p/up", "/p/out"])


if __name__ == "__main__":
    unittest.main()
