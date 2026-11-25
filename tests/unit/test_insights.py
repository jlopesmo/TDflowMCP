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


if __name__ == "__main__":
    unittest.main()
