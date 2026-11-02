"""Outcome handling of edit-and-run and of sweeps: out of model wins over the run status, UNKNOWN is never feasible,
and an unknown target id is refused before anything runs."""

import unittest

from tests.workflow_fixtures import TempDir, invoke, quiet_environment

quiet_environment()

from examples import edit_project as ep  # noqa: E402
from examples import optical_designs as D  # noqa: E402
from examples import sweep_parameters as sp  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

WAVELENGTH = {"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": "$value"}}


class EditAndRunExitTests(unittest.TestCase):
    def test_out_of_band_run_exits_out_of_model(self):
        with TempDir() as tmp:
            base = wc.write_json(tmp / "base.json", D.expander_focus())
            edits = wc.write_json(tmp / "edits.json", [
                {"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": 1300}}])
            code, out, err = invoke(ep.main, [str(base), "--edits", str(edits), "--out", str(tmp / "edited.json"),
                                              "--run", str(tmp / "run")])
            self.assertEqual(out["run"]["outcome"], "OUT_OF_MODEL", err)
            self.assertTrue(out["run"]["model_scope_issues"])
            self.assertEqual((code, out["status"]), (3, "OUT_OF_MODEL_SCOPE"))


class SweepFeasibilityTests(unittest.TestCase):
    def test_unknown_target_id_is_refused_before_running(self):
        spec = {"parameters": [{"name": "wavelength", "unit": "nm", "values": [780.0], "ops": [WAVELENGTH]}],
                "targets": ["no_such_target"], "max_evaluations": 1}
        with TempDir() as tmp:
            with self.assertRaises(wc.WorkflowError) as raised:
                sp.run_sweep(spec, tmp / "study", base=D.expander_focus())
            self.assertEqual(raised.exception.code, "TARGET_UNKNOWN")
            self.assertFalse((tmp / "study" / "samples").exists())

    def test_unknown_sample_is_not_feasible(self):
        row = {"outcome": "UNKNOWN"}
        self.assertEqual(sp.constraint_failures(row, []), [])
        self.assertTrue(sp.constraint_failures(row, [{"outcome": ["PASS"]}]))
        rows = [{"sample_id": "s000", "outcome": "UNKNOWN"}, {"sample_id": "s001", "outcome": "PASS"}]
        _best, feasible = sp.select_best(rows, [], None)
        self.assertEqual(feasible, 2)
        self.assertEqual([r["feasible"] for r in rows], [True, True])


if __name__ == "__main__":
    unittest.main()
