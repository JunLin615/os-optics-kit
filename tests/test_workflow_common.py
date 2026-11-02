"""Outcome classes of examples/workflow_common.classify (no Engine run needed)."""

import unittest

from examples import workflow_common as wc


def row(target_id, status, value=1.0):
    return {"id": target_id, "status": status, "value": value, "assessment": {"PASS": "MET", "WARNING": "MET", "FAIL": "UNMET", "UNKNOWN": "INDETERMINATE"}.get(status)}


class ClassifyTests(unittest.TestCase):
    def test_catalog_identity_uses_saved_results_only(self):
        self.assertEqual(wc.run_catalog_identity([{"result_hash": "a", "catalog_version": None},
                                                  {"result_hash": None, "catalog_version": "installed-only"}]),
                         {"catalog_version": None, "catalog_versions": [None]})
        mixed = wc.run_catalog_identity([{"result_hash": "a", "catalog_version": "release-a"},
                                         {"result_hash": "b", "catalog_version": "release-b"}])
        self.assertEqual(mixed, {"catalog_version": None, "catalog_versions": ["release-a", "release-b"]})

    def test_precedence(self):
        self.assertEqual(wc.classify(None, [], []), "ERROR")
        self.assertEqual(wc.classify("FAIL", ["MODEL_WAVELENGTH_OUTSIDE_BAND"], [row("a", "FAIL")]), "FAIL")
        self.assertEqual(wc.classify("PASS", ["MODEL_WAVELENGTH_OUTSIDE_BAND"], [row("a", "PASS")]), "PASS")
        self.assertEqual(wc.classify("FAIL", [], [row("a", "PASS"), row("b", "UNKNOWN", None)]), "UNKNOWN")
        self.assertEqual(wc.classify("FAIL", [], [row("a", "PASS"), row("b", "FAIL")]), "FAIL")
        self.assertEqual(wc.classify("WARNING", [], [row("a", "WARNING")]), "PASS")

    def test_selection_decides(self):
        rows = [row("a", "PASS"), row("b", "FAIL")]
        self.assertEqual(wc.classify("FAIL", [], rows, selected=["a"]), "PASS")
        self.assertEqual(wc.classify("FAIL", [], rows, selected=["c"]), "UNKNOWN")

    def test_nothing_evaluated_is_never_pass(self):
        for status in ("PASS", "WARNING", "NOT_APPLICABLE", "FAIL", "UNKNOWN"):
            self.assertEqual(wc.classify(status, [], []), "NOT_APPLICABLE", status)
        self.assertEqual(wc.classify("PASS", [], [row("a", "PASS")], selected=[]), "NOT_APPLICABLE")


if __name__ == "__main__":
    unittest.main()
