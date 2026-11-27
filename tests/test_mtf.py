"""os-optics-kit mtf: the optical MTF table, document and figures of an os-optics imaging analysis.

Offline on saved analyses (tests/data/mtf-sonnar-imaging.json: the os-optics 0.14.0 analysis of the
sonnar-telephoto-lens case; tests/data/mtf-edge-imaging.json: synthetic edge cases), and one short engine test that runs
the analysis once on the catalog-free achromatic-imaging Project (about 10 s).
"""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
import math
import unittest
from unittest.mock import patch

from os_optics_kit import imaging_results as ir
from os_optics_kit import mtf
from tests.test_cli import run_blocked
from tests.workflow_fixtures import ROOT, TempDir, invoke, quiet_environment, requires_engine, requires_matplotlib

SONNAR = ROOT / "tests" / "data" / "mtf-sonnar-imaging.json"
EDGE = ROOT / "tests" / "data" / "mtf-edge-imaging.json"


def run_mtf(*argv):
    return invoke(mtf.main, [str(a) for a in argv])


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TableTests(unittest.TestCase):
    def test_csv_columns_rows_and_round_trip(self):
        with TempDir() as tmp:
            code, summary, err = run_mtf("--imaging", SONNAR, "--out", tmp / "out", "--no-figures")
            self.assertEqual(code, 0, err)
            self.assertEqual(summary["status"], "COMPLETED")
            path = tmp / "out" / "mtf.csv"
            header = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(header.split(","), list(mtf.CSV_COLUMNS))
            records = mtf.read_csv(path)
            self.assertEqual(records, mtf.rows(ir.load(SONNAR)))
            self.assertNotIn(b"\r", path.read_bytes())
        self.assertEqual(len(records), 3 * 2 * 8)  # lines x directions x frequencies of each line's axis
        arrays = read(SONNAR)["arrays"]
        for line in ("SRC0#0", "SRC1#0", "SRC2#0"):
            for direction in ("tangential", "sagittal"):
                rows = [r for r in records if r["field"] == line and r["direction"] == direction]
                self.assertEqual([r["frequency_cyc_per_mm"] for r in rows], arrays[f"{line}.frequencies_cyc_per_mm"])
                self.assertEqual([r["mtf"] for r in rows], arrays[f"{line}.mtf_{direction}"])
                self.assertEqual([r["diffraction_limited_mtf"] for r in rows],
                                 arrays[f"{line}.diffraction_limited_mtf_{direction}"])
                self.assertEqual({(r["mtf_status"], r["unit"], r["wavelength_nm"]) for r in rows},
                                 {("KNOWN", "1", 587.5618)})

    def test_a_declined_curve_is_never_written_even_when_its_array_exists(self):
        records = mtf.rows(ir.load(SONNAR))
        arrays = read(SONNAR)["arrays"]
        for line in ("SRC0#0", "SRC2#0"):  # geometric MTF outside its regime: the Engine keeps an estimate array
            self.assertIn(f"{line}.geometric_mtf_tangential", arrays)
            rows = [r for r in records if r["field"] == line]
            self.assertEqual({(r["geometric_mtf"], r["geometric_mtf_status"]) for r in rows},
                             {(None, "OUT_OF_MODEL_SCOPE")})
        middle = [r for r in records if r["field"] == "SRC1#0" and r["direction"] == "sagittal"]
        self.assertEqual([r["geometric_mtf"] for r in middle], arrays["SRC1#0.geometric_mtf_sagittal"])

    def test_edge_statuses_in_the_table(self):
        by = {(r["field"], r["direction"], r["frequency_cyc_per_mm"]): r for r in mtf.rows(ir.load(EDGE))}
        self.assertEqual({key[0] for key in by}, {"A#0", "B#0"})  # B#1 (no arrival) has no axis, so no rows
        unconverged = by[("A#0", "sagittal", 20.0)]
        self.assertEqual((unconverged["mtf"], unconverged["mtf_status"]), (0.78, "NOT_CONVERGED"))
        beyond = by[("A#0", "tangential", 40.0)]
        self.assertEqual((beyond["mtf"], beyond["diffraction_limited_mtf"], beyond["diffraction_limited_mtf_status"]),
                         (0.55, None, "BEYOND_NYQUIST"))
        self.assertEqual(by[("A#0", "sagittal", 0.0)]["geometric_mtf_status"], "ABSENT")
        stand_in = by[("B#0", "tangential", 5.0)]
        self.assertEqual((stand_in["mtf"], stand_in["mtf_status"]), (None, "UNKNOWN"))

    def test_read_csv_refuses_another_table(self):
        with TempDir() as tmp:
            (tmp / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                mtf.read_csv(tmp / "other.csv")


class DocumentTests(unittest.TestCase):
    def test_json_shape_of_a_complete_analysis(self):
        with TempDir() as tmp:
            code, summary, err = run_mtf("--imaging", SONNAR, "--out", tmp / "out", "--no-figures")
            self.assertEqual(code, 0, err)
            document = read(tmp / "out" / "mtf.json")
        self.assertEqual((document["schema"], document["schema_version"], document["status"]),
                         ("os-optics-kit.mtf", 1, "COMPLETED"))
        source = document["input"]
        self.assertEqual((source["mode"], source["monitor"], source["run"], source["outside_active_area_checked"]),
                         ("imaging", "image_plane", None, False))
        self.assertEqual(source["imaging_sha256"], ir.file_sha256(SONNAR))
        self.assertIsNone(document["identity"]["run"])
        self.assertEqual(document["identity"]["analysis"]["engine_input_hash"][:12], "a6e2023bcc4f")
        self.assertIn("no pixel aperture", document["conventions"]["quantity"])
        self.assertIn("not renormalized", document["conventions"]["normalization"])
        self.assertIn("tangential", document["conventions"]["engine_axes"])
        self.assertEqual(document["counts"], {"lines": 3, "curves": 18, "usable_curves": 14, "not_converged_curves": 0,
                                              "declined_curves": 4, "beyond_nyquist_values": 0, "rows": 48,
                                              "not_analysed": 0})
        line = document["lines"][2]
        self.assertEqual((line["line_id"], line["source_id"], line["spectral_index"], line["wavelength_nm"]),
                         ("SRC2#0", "SRC2", 0, 587.5618))
        self.assertEqual(line["image_point"]["reference"], "CENTROID")
        self.assertEqual(line["image_point"]["point_mm"][2], -21.1921651)
        self.assertEqual(line["psf"]["mtf_nyquist_cyc_per_mm"], 512.0)
        curves = {(c["kind"], c["direction"]): c for c in line["curves"]}
        self.assertEqual(curves[("mtf", "tangential")]["values"][0], 0.885361587)
        declined = curves[("geometric_mtf", "tangential")]
        self.assertEqual((declined["values"], declined["frequencies_cyc_per_mm"], declined["reason"]),
                         (None, None, "GEOMETRIC_REGIME_NOT_MET"))
        self.assertEqual([row["line"] for row in summary["lines"]], ["SRC0#0", "SRC1#0", "SRC2#0"])
        self.assertNotIn("curves", json.dumps(summary["lines"]))

    def test_incomplete_analysis_states_what_is_missing(self):
        with TempDir() as tmp:
            code, summary, err = run_mtf("--imaging", EDGE, "--out", tmp / "out", "--no-figures")
            self.assertEqual(code, 2, err)
            document = read(tmp / "out" / "mtf.json")
        self.assertEqual(summary["status"], "INCOMPLETE")
        self.assertEqual([(r["line"], r["tangential_status"], r["sagittal_status"]) for r in summary["lines"]],
                         [("A#0", "KNOWN", "NOT_CONVERGED"), ("B#0", "UNKNOWN", "UNKNOWN")])
        missing = {(e["line_id"], e["source_id"], e["reason"]) for e in document["not_analysed"]}
        self.assertEqual(missing, {(None, "NOPE", "FIELD_NOT_A_BEAM_SOURCE"), ("B#1", "B", "NO_ARRIVAL"),
                                   (None, None, "WAVELENGTH_NOT_ANALYSED")})
        self.assertEqual(next(e for e in document["not_analysed"] if e["reason"] == "WAVELENGTH_NOT_ANALYSED")
                         ["wavelength_nm"], 700.0)
        self.assertEqual(document["lines"][0]["image_point"]["reference"], "CHIEF")
        self.assertEqual(document["counts"]["beyond_nyquist_values"], 2)

    def test_a_requested_field_without_a_line_is_named(self):
        document = read(EDGE)
        document["settings"]["request"]["fields"].append("GONE")  # e.g. none of its lines matched wavelengths_nm
        missing = mtf.not_analysed(ir.load(document))
        self.assertIn(("GONE", "NO_LINE"), [(e["source_id"], e["reason"]) for e in missing])

    def test_clipped_fields_from_the_run_monitor(self):
        document = ir.load(SONNAR)
        monitor = {"outside_active_area": [{"path_id": "EDGE#0:p1", "crossing_index": 0, "uv_mm": [30.0, 0.0],
                                            "miss_distance_mm": 4.6}, {"path_id": "SRC0#0.1", "uv_mm": [26, 0]}]}
        clipped = mtf.clipped_fields(monitor, document)
        self.assertEqual([(c["line_id"], c["source_id"], c["reason"]) for c in clipped],
                         [("EDGE#0", "EDGE", "OUTSIDE_ACTIVE_AREA")])
        self.assertEqual(mtf.build(document, source={}, clipped=clipped)["status"], "INCOMPLETE")
        named = copy.deepcopy(document)
        named["settings"]["request"]["fields"] = ["SRC0"]  # explicit fields: the Engine analyses them wherever
        self.assertEqual(mtf.clipped_fields(monitor, named), [])


class CommandTests(unittest.TestCase):
    def test_refusals(self):
        with TempDir() as tmp:
            (tmp / "full").mkdir()
            (tmp / "full" / "x").write_text("x", encoding="utf-8")
            cases = {
                "BAD_ARGUMENT": [["--out", tmp / "a"], ["RUN", "--out", tmp / "a"],
                                 ["--imaging", SONNAR, "RUN", "--out", tmp / "a"],
                                 ["--imaging", SONNAR, "--request", SONNAR, "--out", tmp / "a"],
                                 ["RUN", "--monitor", "P", "--run", "RUN", "--out", tmp / "a"],
                                 ["--imaging", SONNAR, "--monitor", "other", "--out", tmp / "a", "--no-figures"],
                                 ["--imaging", SONNAR, "--configuration", "f0", "--out", tmp / "a", "--no-figures"],
                                 ["--imaging", SONNAR, "--out", tmp / "a", "--bogus"]],
                "IMAGING_INVALID": [["--imaging", ROOT / "baseline.json", "--out", tmp / "b", "--no-figures"]],
                "OUTPUT_EXISTS": [["--imaging", SONNAR, "--out", tmp / "full", "--no-figures"]],
            }
            for code_name, argvs in cases.items():
                for argv in argvs:
                    with self.subTest(code=code_name, argv=argv):
                        code, out, err = run_mtf(*argv)
                        self.assertEqual((code, out), (4, None))
                        self.assertEqual(json.loads(err)["error"]["code"], code_name, err)

    def test_overwrite_replaces_own_files_and_keeps_others(self):
        with TempDir() as tmp:
            out = tmp / "out"
            out.mkdir()
            for name in ("mtf-OLD-0.png", "mtf-OLD-0.svg", "notes.txt"):
                (out / name).write_text("old", encoding="utf-8")
            saved = out / "imaging.json"
            saved.write_bytes(SONNAR.read_bytes())  # the input inside the output directory is kept
            code, summary, err = run_mtf("--imaging", saved, "--out", out, "--no-figures", "--overwrite")
            self.assertEqual(code, 0, err)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["imaging.json", "mtf.csv", "mtf.json", "notes.txt"])
            self.assertEqual(read(out / "mtf.json")["files"]["imaging"], "imaging.json")

    def test_help_states_what_the_command_is_not(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(mtf.main(["--help"]), 0)
        text = " ".join(out.getvalue().split())
        for needle in ("Optical MTF only: no pixel aperture", "no slanted-edge SFR", "os-optics-kit sfr",
                       "JunLin615/os-optics#42", "no PASS/FAIL"):
            self.assertIn(needle, text)

    def test_saved_analysis_needs_no_engine_and_no_matplotlib_with_no_figures(self):
        with TempDir() as tmp:
            done = run_blocked(["os_optics", "matplotlib"], "mtf", "--imaging", str(SONNAR), "--out",
                               str(tmp / "out"), "--no-figures")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["status"], "COMPLETED")
            self.assertEqual(sorted(p.name for p in (tmp / "out").iterdir()), ["mtf.csv", "mtf.json"])
            done = run_blocked(["os_optics", "matplotlib"], "mtf", "--imaging", str(SONNAR), "--out",
                               str(tmp / "out2"))
            self.assertEqual(done.returncode, 4)
            self.assertEqual(json.loads(done.stderr)["error"]["code"], "PLOT_EXTRA_MISSING")

    @requires_matplotlib
    def test_one_figure_per_line(self):
        with TempDir() as tmp:
            code, summary, err = run_mtf("--imaging", EDGE, "--out", tmp / "out")
            self.assertEqual(code, 2, err)
            names = sorted(p.name for p in (tmp / "out").iterdir())
            self.assertEqual(names, ["mtf-A-0.png", "mtf-A-0.svg", "mtf-B-0.png", "mtf-B-0.svg", "mtf.csv", "mtf.json"])
            self.assertEqual([p.rsplit("/", 1)[1] for p in summary["files"]["figures"]], ["mtf-A-0.png", "mtf-B-0.png"])
            figures = {f["line_id"]: f for f in read(tmp / "out" / "mtf.json")["files"]["figures"]}
            self.assertEqual(len(figures["B#0"]["not_drawn"]), 4)  # stand-in pupil: nothing drawn, all named
            self.assertEqual(figures["A#0"]["not_drawn"], [
                "diffraction limit, tangential 40 cycles/mm (BEYOND_NYQUIST)",
                "diffraction limit, sagittal 40 cycles/mm (BEYOND_NYQUIST)"])
            self.assertEqual(read(tmp / "out" / "mtf.json")["lines"][0]["figure"], "mtf-A-0.png")
            code, _summary, err = run_mtf("--imaging", SONNAR, "--out", tmp / "zh", "--theme", "report",
                                          "--set", "language=zh-CN")
            self.assertEqual(code, 0, err)
            self.assertTrue((tmp / "zh" / "mtf-SRC2-0.png").stat().st_size > 10_000)

    def test_figure_names(self):
        self.assertEqual(mtf.figure_name("SRC1#2"), "mtf-SRC1-2")
        self.assertEqual(mtf.figure_name("a b/c#0"), "mtf-a_b_c-0")


@requires_engine
class EngineTests(unittest.TestCase):
    """One run of the achromatic-imaging Project (catalog-free, 3 s) plus a turned field the plane's aperture clips."""

    REQUEST = {"fields": ["SRC"], "launch_grid": 40, "frequencies_cyc_per_mm": [0, 10, 20, 40], "psf_samples": 128,
               "psf_half_width_um": 80}

    @classmethod
    def setUpClass(cls):
        from os_optics.local import run_optical_project

        quiet_environment()
        cls.tmp = TempDir()
        cls.root = cls.tmp.__enter__()
        document = read(ROOT / "cases" / "achromatic-imaging" / "project.json")
        source = next(c for c in document["components"] if c.get("optical_role") == "source")
        field = copy.deepcopy(source)  # 2.5 deg about the design z axis: lands 3.3 mm off axis, outside the 2 mm plane
        half = math.radians(2.5) / 2
        w, x, y, z = source["nominal_pose"]["quaternion_wxyz"]
        c, s = math.cos(half), math.sin(half)
        field["instance_id"] = "FIELD"
        field["nominal_pose"] = {"position_mm": [-10.0, -10.0 * math.tan(2 * half), 0.0],
                                 "quaternion_wxyz": [c * w - s * z, c * x - s * y, c * y + s * x, c * z + s * w]}
        document["components"].append(field)
        cls.run_dir = cls.root / "run"
        run_optical_project(document, output_dir=cls.run_dir)
        (cls.root / "request.json").write_text(json.dumps(cls.REQUEST), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_one_analysis_equals_the_engine_and_a_rerun_from_the_saved_document(self):
        from os_optics.results import RunResults, load_run

        calls = []
        original = RunResults.imaging

        def counted(self, monitor, request=None):
            calls.append((monitor, request))
            return original(self, monitor, request)

        out = self.root / "out"
        with patch.object(RunResults, "imaging", counted):
            code, summary, err = run_mtf(self.run_dir, "--monitor", "image", "--request", self.root / "request.json",
                                         "--out", out, "--no-figures")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [("image", self.REQUEST)])  # exactly one Engine analysis
        self.assertEqual(summary["status"], "COMPLETED")
        results = load_run(self.run_dir)
        direct = ir.load(results.imaging("image", self.REQUEST))
        saved = read(out / "imaging.json")
        self.assertEqual(saved, direct)
        document = read(out / "mtf.json")
        self.assertEqual(document["identity"]["run"], results.identity())
        self.assertEqual(document["input"]["request_sha256"], ir.file_sha256(self.root / "request.json"))
        records = mtf.read_csv(out / "mtf.csv")
        self.assertEqual(records, mtf.rows(direct))
        tangential = [r["mtf"] for r in records if r["field"] == "SRC#1" and r["direction"] == "tangential"]
        self.assertEqual(tangential, direct["arrays"]["SRC#1.mtf_tangential"])
        self.assertEqual(len({r["field"] for r in records}), 3)  # one curve per spectral line of SRC

        rerun = self.root / "rerun"
        code, again, err = run_mtf("--imaging", out / "imaging.json", "--run", self.run_dir, "--out", rerun, "--no-figures")
        self.assertEqual(code, 0, err)
        self.assertEqual(mtf.read_csv(rerun / "mtf.csv"), records)
        self.assertEqual(read(rerun / "mtf.json")["identity"]["run"]["result_hash"], results.identity()["result_hash"])

        tampered = copy.deepcopy(saved)
        tampered["identity"]["physical"]["run_result_hash"] = "0" * 64
        (self.root / "tampered.json").write_text(json.dumps(tampered), encoding="utf-8")
        code, _out, err = run_mtf("--imaging", self.root / "tampered.json", "--run", self.run_dir, "--out",
                                  self.root / "t", "--no-figures")
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "IMAGING_RUN_MISMATCH"))

    def test_default_fields_name_the_line_the_plane_clips(self):
        out = self.root / "clipped"
        request = {k: v for k, v in self.REQUEST.items() if k != "fields"}
        (self.root / "default-fields.json").write_text(json.dumps(request), encoding="utf-8")
        code, summary, err = run_mtf(self.run_dir, "--monitor", "image", "--request", self.root / "default-fields.json",
                                     "--out", out, "--no-figures")
        self.assertEqual(code, 2, err)
        self.assertEqual([r["line"] for r in summary["lines"]], ["SRC#0", "SRC#1", "SRC#2"])
        self.assertEqual([(e["line_id"], e["reason"]) for e in summary["not_analysed"]],
                         [("FIELD#0", "OUTSIDE_ACTIVE_AREA"), ("FIELD#1", "OUTSIDE_ACTIVE_AREA"),
                          ("FIELD#2", "OUTSIDE_ACTIVE_AREA")])

    def test_engine_refusals(self):
        bad = self.root / "bad-request.json"
        bad.write_text(json.dumps({"psf_samples": 17}), encoding="utf-8")
        cases = [("MONITOR_NOT_FOUND", [self.run_dir, "--monitor", "nope"]),
                 ("ANALYSIS_REFUSED", [self.run_dir, "--monitor", "image", "--request", bad]),
                 ("RUN_UNREADABLE", [self.root, "--monitor", "image"]),
                 ("BAD_ARGUMENT", [self.run_dir, "--monitor", "image", "--configuration", "f0"])]
        for index, (code_name, argv) in enumerate(cases):
            with self.subTest(code=code_name):
                code, _out, err = run_mtf(*argv, "--out", self.root / f"refused-{index}", "--no-figures")
                self.assertEqual((code, json.loads(err)["error"]["code"]), (4, code_name), err)
        error = json.loads(run_mtf(self.run_dir, "--monitor", "image", "--request", bad, "--out", self.root / "r",
                                   "--no-figures")[2])["error"]
        self.assertEqual(error["engine_code"], "IMAGING_INVALID")

    def test_a_configurations_run_needs_a_configuration(self):
        top = self.root / "configured"
        (top / "configurations").mkdir(parents=True)
        manifest = {"schema": "opensci.local_configurations_run",
                    "configurations": [{"configuration_id": "f0", "directory": "configurations/f0",
                                        "manifest_sha256": "x"},
                                       {"configuration_id": "f2", "directory": "configurations/f2",
                                        "manifest_sha256": "x"}]}
        (top / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for argv, ids in (([], ["f0", "f2"]), (["--configuration", "f9"], ["f0", "f2"])):
            code, _out, err = run_mtf(top, "--monitor", "image", *argv, "--out", self.root / "c", "--no-figures")
            error = json.loads(err)["error"]
            self.assertEqual((code, error["code"], error["configurations"]), (4, "CONFIGURATION_REQUIRED", ids))
        self.assertEqual(mtf.resolve_run(top, "f2"), top / "configurations/f2")


if __name__ == "__main__":
    unittest.main()
