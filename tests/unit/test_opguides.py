import unittest

from tdflowmcp import opguides
from tdflowmcp.policy import APPROVABLE_CREATE_TYPES, SAFE_CREATE_TYPES


class OperatorGuideTests(unittest.TestCase):
    def test_coverage_matches_supported_operator_types(self):
        self.assertLessEqual(opguides.GUIDED_TYPES, SAFE_CREATE_TYPES | APPROVABLE_CREATE_TYPES)
        self.assertEqual(len(opguides.GUIDED_TYPES), 7)
        self.assertEqual(sum(len(opguides.guidance_for(name)["parameters"]) for name in opguides.GUIDED_TYPES), 27)
        for name in ("scriptCHOP", "scriptSOP", "scriptTOP", "unknown"):
            self.assertIsNone(opguides.guidance_for(name))

    def test_guidance_uses_runtime_names_and_returns_independent_data(self):
        first = opguides.guidance_for("mathCHOP")
        self.assertEqual(first["parameters"][0]["name"], "gain")
        self.assertEqual(first["parameters"][0]["label"], "Multiply")
        first["parameters"].clear()
        self.assertEqual(len(opguides.guidance_for("mathCHOP")["parameters"]), 5)
