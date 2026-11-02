"""Offline checks of examples/workflow_common.py units and examples/edit_project.py (catalog-free Projects).

Run from the repository root: python -m unittest discover -s tests -v
"""

import copy
import json
import math
from pathlib import Path
import unittest

from tests.workflow_fixtures import TempDir, by_id, expander, invoke, quiet_environment

quiet_environment()

from examples import edit_project as ep  # noqa: E402
from examples import workflow_common as wc  # noqa: E402


def qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return [w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2]


def qrot(q, v):
    w, x, y, z = q
    p = qmul(qmul(q, [0.0, *v]), [w, -x, -y, -z])
    return p[1:]


def axis_angle(axis, degrees):
    n = math.sqrt(sum(a * a for a in axis))
    half = math.radians(degrees) / 2.0
    return [math.cos(half), *[math.sin(half) * a / n for a in axis]]


class UnitTests(unittest.TestCase):
    def test_length_angle_power_conversions(self):
        self.assertEqual(wc.convert(500, "um", "mm"), 0.5)
        self.assertEqual(wc.convert(0.532, "um", "nm"), 532.0)
        self.assertEqual(wc.convert(5, "mW", "W"), 0.005)
        self.assertEqual(wc.convert(2, "m", "mm"), 2000.0)
        self.assertAlmostEqual(wc.convert(1, "mrad", "deg"), 0.057295779513082, places=12)
        self.assertAlmostEqual(wc.convert(90, "deg", "rad"), math.pi / 2, places=15)
        self.assertEqual(wc.convert(3, "mm", "mm"), 3)

    def test_unknown_and_incompatible_units_are_refused(self):
        with self.assertRaises(wc.WorkflowError) as unknown:
            wc.convert(1, "furlong", "mm")
        self.assertEqual(unknown.exception.code, "UNIT_UNKNOWN")
        with self.assertRaises(wc.WorkflowError) as incompatible:
            wc.convert(1, "deg", "mm")
        self.assertEqual(incompatible.exception.code, "UNIT_INCOMPATIBLE")

    def test_normalize_quantities_by_field_suffix(self):
        out = wc.normalize_quantities({"position_mm": [{"value": 1, "unit": "m"}, 0, 0],
                                       "pivot": {"point_mm": {"value": [1, 2, 3], "unit": "um"}},
                                       "power_w": {"value": 2, "unit": "mW"}, "m2": {"value": 1.1, "unit": "1"}})
        self.assertEqual(out["position_mm"], [1000.0, 0, 0])
        self.assertEqual(out["pivot"]["point_mm"], [0.001, 0.002, 0.003])
        self.assertEqual(out["power_w"], 0.002)
        self.assertEqual(out["m2"], 1.1)
        with self.assertRaises(wc.WorkflowError):
            wc.normalize_quantities({"quaternion_wxyz": {"value": [1, 0, 0, 0], "unit": "deg"}})
        with self.assertRaises(wc.WorkflowError):
            wc.normalize_quantities({"m2": {"value": 1.1, "unit": "mm"}})


class EditOpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = expander()

    def edit(self, ops):
        before = copy.deepcopy(self.base)
        result = ep.apply_edits(self.base, ops)
        self.assertEqual(self.base, before, "the input document must not change")
        return result

    def refused(self, ops, code="PROJECT_OP_INVALID"):
        with self.assertRaises(ep.EditError) as caught:
            ep.apply_edits(self.base, ops)
        self.assertEqual(caught.exception.code, code)
        return caught.exception.details

    def test_field_ops_with_units_and_change_record(self):
        result = self.edit([
            {"op": "set_source_model", "instance_id": "SRC",
             "changes": {"wavelength_nm": {"value": 0.532, "unit": "um"}, "power_w": {"value": 2, "unit": "mW"},
                         "m2": 1.2}},
            {"op": "set_component_pose", "instance_id": "L3", "position_mm": [{"value": 0.301, "unit": "m"}, 0, 0]},
            {"op": "set_observation_pose", "observation_id": "focus", "position_mm": [401.0, 0, 0]},
            {"op": "set_observation", "observation_id": "focus", "changes": {"absorbing": True}},
            {"op": "set_target", "target_id": "spot_radius", "changes": {"maximum": {"value": 15, "unit": "um"}}},
            {"op": "set_retained_dof", "instance_id": "L2", "axis": "Tz", "control": "MANUAL"},
            {"op": "set_retained_dofs", "instance_id": "L1", "retained_dofs": [{"axis": "Rz", "control": "MANUAL"},
                                                                              {"axis": "Tx", "control": "NUMERICAL"}]},
            {"op": "set_design_intent", "design_intent": "OPTICAL_SYSTEM"},
        ])
        project, record = result["project"], result["record"]
        source = by_id(project["components"], "instance_id", "SRC")["source_model"]
        self.assertEqual(source["wavelength_nm"], 532.0)
        self.assertEqual(source["power_w"], 0.002)
        self.assertEqual(by_id(project["components"], "instance_id", "L3")["nominal_pose"]["position_mm"][0], 301.0)
        self.assertEqual(by_id(project["targets"], "target_id", "spot_radius")["maximum"], 0.015)
        self.assertEqual([d["axis"] for d in by_id(project["components"], "instance_id", "L1")["retained_dofs"]],
                         ["Tx", "Rz"])
        self.assertEqual(project["design_intent"], "OPTICAL_SYSTEM")
        self.assertEqual(len(record["changes"]), 8)
        fields = {f["path"]: f for f in record["changes"][0]["affected"][0]["fields"]}
        self.assertEqual(fields["source_model.wavelength_nm"]["before"], 780.0)
        self.assertEqual(fields["source_model.wavelength_nm"]["after"], 532.0)
        self.assertEqual(record["normalized_ops"][4]["changes"]["maximum"], 0.015)
        self.assertEqual(record["ops"][4]["changes"]["maximum"], {"value": 15, "unit": "um"})
        self.assertEqual(record["ops_digest"], wc.canonical_digest(wc.canonical_numbers(record["normalized_ops"])))
        self.assertNotEqual(record["base"]["project_hash"], record["result"]["project_hash"])
        self.assertEqual(record["result"]["project_hash"], wc.check(project)["project_hash"])

    def test_add_and_remove_objects(self):
        new_plane = copy.deepcopy(by_id(self.base["observations"], "observation_id", "focus"))
        new_plane["observation_id"] = "far"
        new_plane["pose"]["position_mm"] = [450.0, 0.0, 0.0]
        result = self.edit([
            {"op": "add_observation", "observation": new_plane},
            {"op": "add_target", "target": {"target_id": "far_radius", "metric": "beam_radius_mm",
                                            "observation_id": "far", "unit": "mm", "parameters": [],
                                            "maximum": {"value": 2, "unit": "mm"}}},
            {"op": "remove_target", "target_id": "waist_on_plane"},
        ])
        project = result["project"]
        self.assertIn("far", [o["observation_id"] for o in project["observations"]])
        self.assertEqual(by_id(project["targets"], "target_id", "far_radius")["maximum"], 2)
        self.assertNotIn("waist_on_plane", [t["target_id"] for t in project["targets"]])
        kinds = [(a["kind"], a.get("added"), a.get("removed")) for c in result["record"]["changes"]
                 for a in c["affected"]]
        self.assertIn(("observation", True, None), kinds)
        self.assertIn(("target", None, True), kinds)
        component = copy.deepcopy(by_id(self.base["components"], "instance_id", "L3"))
        component["instance_id"] = "L4"
        component["nominal_pose"]["position_mm"] = [420.0, 0.0, 0.0]
        added = self.edit([{"op": "add_component", "component": component},
                           {"op": "remove_component", "instance_id": "L4"}])
        self.assertEqual([c["instance_id"] for c in added["project"]["components"]],
                         [c["instance_id"] for c in self.base["components"]])

    def test_model_ops_follow_designer_rules(self):
        model = copy.deepcopy(by_id(self.base["optical_models"], "model_id", "thin-L3"))
        model["model_id"] = "thin-alt"
        model["model"]["focal_length_mm"] = "90"
        result = self.edit([{"op": "add_optical_model", "optical_model": model},
                            {"op": "set_component_model", "instance_id": "L3", "optical_model_id": "thin-alt"},
                            {"op": "remove_optical_model", "model_id": "thin-L3"}])
        l3 = by_id(result["project"]["components"], "instance_id", "L3")
        self.assertEqual(l3["optical_model_id"], "thin-alt")
        self.assertEqual(self.refused([{"op": "remove_optical_model", "model_id": "thin-L1"}])["op_code"], "IN_USE")
        same = self.edit([{"op": "add_optical_model", "optical_model": by_id(
            self.base["optical_models"], "model_id", "thin-L1")}])
        self.assertFalse(same["record"]["engine_update"]["changed"])
        self.assertTrue(same["record"]["engine_update"]["already_present"])

    def test_null_removes_a_field(self):
        doc = ep.apply_edits(self.base, [{"op": "set_source_model", "instance_id": "SRC",
                                        "changes": {"m2": None, "waist_radius_y_mm": 0.6}}], validate=False)["project"]
        source = by_id(doc["components"], "instance_id", "SRC")["source_model"]
        self.assertNotIn("m2", source)
        self.assertEqual(source["waist_radius_y_mm"], 0.6)

    def test_set_component_product_replaces_declared_model(self):
        doc = ep.apply_edits(self.base, [{"op": "set_component_product", "instance_id": "L3",
                                        "optical_sku": "OS-OPT-EXAMPLE", "catalog_version": "2026-10-15.1"}], validate=False)["project"]
        l3 = by_id(doc["components"], "instance_id", "L3")
        self.assertEqual(l3["product"], {"optical_sku": "OS-OPT-EXAMPLE", "catalog_version": "2026-10-15.1"})
        self.assertNotIn("optical_model_id", l3)
        self.assertNotIn("custom_part", l3)

    def test_transform_local_axis_and_design_axis(self):
        local = self.edit([{"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [0, 0, 5],
                            "frame": {"component": "L2"}}])["project"]
        design = self.edit([{"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [0, 0, 5],
                             "frame": "design"}])["project"]
        self.assertEqual(by_id(local["components"], "instance_id", "L2")["nominal_pose"]["position_mm"],
                         [205.0, 0.0, 0.0])  # local +Z is design +X for this lens
        self.assertEqual(by_id(design["components"], "instance_id", "L2")["nominal_pose"]["position_mm"],
                         [200.0, 0.0, 5.0])

    def test_transform_rotation_about_local_pivot_matches_closed_form(self):
        pose = by_id(self.base["components"], "instance_id", "L2")["nominal_pose"]
        pivot = [0.0, 0.0, 0.5]  # mid-thickness of the 1 mm thin-lens body, in L2's frame
        result = self.edit([{"op": "transform", "selector": {"ids": ["L2"]},
                             "rotation": {"axis": [0, 1, 0], "angle_deg": {"value": 17.453292519943297,
                                                                           "unit": "mrad"}},
                             "frame": {"component": "L2"}, "pivot": {"point_mm": pivot}}])
        after = by_id(result["project"]["components"], "instance_id", "L2")["nominal_pose"]
        qa = axis_angle([0, 1, 0], 1.0)  # 17.4533 mrad = 1 deg
        expected_q = qmul(pose["quaternion_wxyz"], qa)
        shift = [p - r for p, r in zip(pivot, qrot(qa, pivot))]
        expected_p = [t + d for t, d in zip(pose["position_mm"], qrot(pose["quaternion_wxyz"], shift))]
        for got, want in zip(after["quaternion_wxyz"], expected_q):
            self.assertAlmostEqual(got, want, places=12)
        for got, want in zip(after["position_mm"], expected_p):
            self.assertAlmostEqual(got, want, places=12)
        self.assertEqual(result["record"]["changes"][0]["affected"][0]["id"], "L2")

    def test_refusals_name_the_op(self):
        self.assertEqual(self.refused([{"op": "set_target", "target_id": "spot_radius", "changes": {}},
                                       {"op": "nope"}])["op_index"], 1)
        details = self.refused([{"op": "set_target", "target_id": "missing", "changes": {}}])
        self.assertEqual((details["op_code"], details["op_index"]), ("NOT_FOUND", 0))
        details = self.refused([{"op": "set_source_model", "instance_id": "SRC",
                                 "changes": {"wavelength_nm": {"value": 1, "unit": "deg"}}}])
        self.assertEqual(details["op_code"], "UNIT_INCOMPATIBLE")
        self.assertEqual(self.refused([{"op": "set_component_pose", "instance_id": "L1"}])["op_code"],
                         "BAD_ARGUMENT")
        self.assertEqual(self.refused([{"op": "set_source_model", "instance_id": "L1", "changes": {}}])["op_code"],
                         "BAD_ARGUMENT")
        self.assertEqual(self.refused([{"op": "set_retained_dof", "instance_id": "L1", "axis": "Q",
                                        "control": "MANUAL"}])["op_code"], "BAD_ARGUMENT")
        details = self.refused([{"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [-200, 0, 0]}],
                               code="PROJECT_REJECTED")
        self.assertTrue(details["problems"])
        details = self.refused([{"op": "set_source_model", "instance_id": "SRC", "changes": {"waist_radius_mm": -1}}],
                               code="PROJECT_REJECTED")
        self.assertEqual(details["problems"][0]["code"], "PROJECT_INVALID")

    def test_target_unit_mismatch_is_reported(self):
        result = self.edit([{"op": "set_target", "target_id": "spot_radius", "changes": {"unit": "um",
                                                                                       "maximum": 20}}])
        self.assertEqual(result["record"]["warnings"][0]["code"], "TARGET_UNIT_MISMATCH")


class EditFileTests(unittest.TestCase):
    OPS = [{"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [0, 0, 1], "frame": {"component": "L2"}}]

    def setUp(self):
        self._tmp = TempDir()
        self.dir = self._tmp.__enter__()
        self.base = self.dir / "base.json"
        wc.write_json(self.base, expander())
        self.base_bytes = self.base.read_bytes()
        self.edits = wc.write_json(self.dir / "edits.json", self.OPS)
        self.out = self.dir / "new.json"

    def tearDown(self):
        self.assertEqual(self.base.read_bytes(), self.base_bytes, "the base file must never change")
        self._tmp.__exit__(None, None, None)

    def cli(self, *extra):
        return invoke(ep.main, [str(self.base), "--edits", str(self.edits), "--out", str(self.out), *extra])

    def test_edit_writes_project_and_record_and_replays(self):
        code, out, _err = self.cli()
        self.assertEqual((code, out["status"], out["already_applied"]), (0, "EDITED", False))
        record = wc.read_json(self.dir / "new.changes.json")
        self.assertEqual(record["output"]["sha256"], wc.sha256_file(self.out))
        self.assertEqual(record["base"]["project_hash"], out["base_project_hash"])
        moved = by_id(wc.read_json(self.out)["components"], "instance_id", "L2")["nominal_pose"]["position_mm"]
        self.assertEqual(moved, [201.0, 0.0, 0.0])
        written = self.out.read_bytes()
        code, again, _err = self.cli()  # the reply was lost: the same command is answered without a second move
        self.assertEqual((code, again["status"], again["already_applied"]), (0, "ALREADY_APPLIED", True))
        self.assertEqual(self.out.read_bytes(), written)

    def test_interrupted_attempt_without_record_is_refused(self):
        self.cli()
        (self.dir / "new.changes.json").unlink()
        code, out, err = self.cli()
        self.assertEqual((code, out), (4, None))
        error = json.loads(err)["error"]
        self.assertEqual((error["code"], error["mismatch"]), ("OUTPUT_EXISTS", ["RECORD_MISSING"]))
        code, out, _err = self.cli("--overwrite")
        self.assertEqual((code, out["status"]), (0, "EDITED"))

    def test_changed_base_or_ops_is_refused(self):
        self.cli()
        other = self.dir / "other_edits.json"
        wc.write_json(other, [{**self.OPS[0], "translation_mm": [0, 0, 2]}])
        code, _out, err = invoke(ep.main, [str(self.base), "--edits", str(other), "--out", str(self.out)])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(err)["error"]["mismatch"], ["OPS_CHANGED"])
        changed = expander(wavelength_nm=785.0)
        base2 = wc.write_json(self.dir / "base2.json", changed)
        code, _out, err = invoke(ep.main, [str(base2), "--edits", str(self.edits), "--out", str(self.out)])
        self.assertEqual(json.loads(err)["error"]["mismatch"], ["BASE_CHANGED"])

    def test_expect_base_hash(self):
        current = wc.check(wc.read_json(self.base))["project_hash"]
        code, out, _err = self.cli("--expect-base-hash", current)
        self.assertEqual(code, 0)
        self.out.unlink()
        code, out, err = self.cli("--expect-base-hash", "0" * 64)
        error = json.loads(err)["error"]
        self.assertEqual((code, error["code"], error["current_hash"]), (4, "BASE_CHANGED", current))
        self.assertFalse(self.out.exists())

    def test_refused_edit_writes_nothing(self):
        wc.write_json(self.edits, [{"op": "set_target", "target_id": "spot_radius", "changes": {"maximum": 0.01}},
                                   {"op": "set_source_model", "instance_id": "SRC",
                                    "changes": {"wavelength_nm": {"value": 1, "unit": "parsec"}}}])
        code, out, err = self.cli()
        error = json.loads(err)["error"]
        self.assertEqual((code, out, error["op_index"], error["op_code"]), (4, None, 1, "UNIT_UNKNOWN"))
        self.assertFalse(self.out.exists())
        self.assertFalse((self.dir / "new.changes.json").exists())

    def test_run_option_runs_and_reuses_current_run(self):
        wc.write_json(self.edits, [{**self.OPS[0], "translation_mm": [0, 0, 2]}])
        code, out, _err = self.cli("--run", str(self.dir / "run"))
        self.assertEqual(code, 1)  # moving L2 by 2 mm defocuses the spot: spot_radius FAIL
        self.assertEqual((out["run"]["outcome"], out["run"]["reused"]), ("FAIL", False))
        code, again, _err = self.cli("--run", str(self.dir / "run"))
        self.assertEqual((again["already_applied"], again["run"]["reused"]), (True, True))

    def test_describe_index_and_details(self):
        code, out, _err = invoke(ep.main, [str(self.base), "--describe"])
        self.assertEqual(code, 0)
        self.assertEqual({o["kind"] for o in out["objects"]}, {"component", "observation", "target"})
        code, out, _err = invoke(ep.main, [str(self.base), "--describe", "--ids", "L2,SRC,spot_radius,focus,zz"])
        objects = {o["id"]: o for o in out["objects"]}
        self.assertEqual(objects["L2"]["pose"]["local_axes"]["z"], [1.0, 0.0, 0.0])
        self.assertEqual(objects["L2"]["applicability"]["model_valid_band_nm"]["max_nm"], "20000")
        self.assertEqual(objects["SRC"]["source_model"]["wavelength_nm"], {"value": 780.0, "unit": "nm"})
        self.assertIn("set_source_model", objects["SRC"]["ops"])
        self.assertEqual(objects["spot_radius"]["metric_unit"], "mm")
        self.assertEqual(out["missing"], ["zz"])
        self.assertIn("pivots", out["conventions"]["transform"])
        self.assertLess(len(json.dumps(out)), 6000)


class HelpTests(unittest.TestCase):
    def test_every_script_prints_english_help_on_stdout(self):
        import subprocess
        import sys

        from tests.workflow_fixtures import ROOT

        for name in ("edit_project", "sweep_parameters", "compare_runs", "plot_results", "build_report",
                     "catalog_candidates"):
            done = subprocess.run([sys.executable, str(ROOT / "examples" / f"{name}.py"), "--help"],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, name)
            self.assertIn("usage:", done.stdout, name)
            self.assertIn("Exit", done.stdout, name)


if __name__ == "__main__":
    unittest.main()
