"""Checks of the case collection and of ``os-optics-kit cases`` (list, show, copy, verify, index).

The format, index and copy checks need only the standard library. The verify checks and the project consistency
checks run the engine and are skipped when ``os_optics`` is not importable.
Run from the repository root: python -m unittest tests.test_cases -v
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib import import_module
from importlib.util import find_spec, module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from os_optics_kit import cases as C

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
HAS_ENGINE = find_spec("os_optics") is not None
CATALOG_FREE = "ideal-lens-focus"


def call(argv):
    """Run ``os-optics-kit cases ARGV`` in process; return (exit code, stdout text, stderr text)."""
    out, err = io.BytesIO(), io.StringIO()
    stdout = io.TextIOWrapper(out, encoding="utf-8")
    with redirect_stdout(stdout), redirect_stderr(err):
        code = C.main(argv)
        stdout.flush()
    return code, out.getvalue().decode("utf-8"), err.getvalue()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def copy_tree(names, dest):
    """A kit-checkout-like tree (pyproject.toml + cases/) with copies of the named cases."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "pyproject.toml").write_text("", encoding="utf-8")
    for name in names:
        shutil.copytree(CASES / name, dest / "cases" / name, ignore=shutil.ignore_patterns("__pycache__"))
    return dest / "cases"


class IndexTests(unittest.TestCase):
    def test_every_case_passes_the_format_checks(self):
        self.assertEqual(C.check_all(), [])

    def test_index_is_current_and_complete(self):
        self.assertEqual((ROOT / "cases.json").read_text(encoding="utf-8"), C.index_text(C.build_index()))
        index = load(ROOT / "cases.json")
        self.assertEqual((index["schema"], index["schema_version"]), ("os-optics-kit.cases", 1))
        names = [c["name"] for c in index["cases"]]
        self.assertEqual(names, sorted(p.name for p in CASES.iterdir() if p.is_dir() and p.name != "__pycache__"))
        self.assertGreaterEqual(len(names), 10)
        for entry in index["cases"]:
            self.assertEqual(entry["path"], f"cases/{entry['name']}")
            for required in C.REQUIRED_FILES:
                self.assertIn(required, entry["files"], entry["name"])
            self.assertEqual(len(entry["project_sha256"]), 64)
        code, out, _err = call(["index", "--check"])
        self.assertEqual((code, json.loads(out)["status"]), (0, "OK"))

    def test_baselines_are_declared(self):
        for entry in load(ROOT / "cases.json")["cases"]:
            expected = load(CASES / entry["name"] / "expected.json")
            self.assertEqual((expected["engine"], expected["catalog"]), (entry["engine"], entry["catalog"]))
        catalog = {e["name"]: e["catalog"] for e in load(ROOT / "cases.json")["cases"]}
        self.assertEqual(catalog["beam-expander"], "2026-10-28.1")
        self.assertIsNone(catalog[CATALOG_FREE])

    def test_list_show_and_tags(self):
        code, out, _err = call(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), load(ROOT / "cases.json"))
        self.assertNotIn("\n", out.strip(), "compact JSON")
        code, out, _err = call(["list", "--tag", "interop"])
        self.assertEqual(sorted(c["name"] for c in json.loads(out)["cases"]),
                         ["interop-diffraction", "interop-metasurface", "interop-optimization", "interop-quantum"])
        code, out, _err = call(["show", CATALOG_FREE])
        self.assertEqual((code, out), (0, (CASES / CATALOG_FREE / "CASE.md").read_text(encoding="utf-8")))
        code, _out, err = call(["show", "no-such-case"])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "UNKNOWN_CASE"))


class FormatCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = copy_tree([CATALOG_FREE], Path(self._tmp.name))
        self.case = self.root / CATALOG_FREE

    def tearDown(self):
        self._tmp.cleanup()

    def codes(self):
        return sorted({p["code"] for p in C.check_case(self.case)})

    def edit(self, name, old, new):
        path = self.case / name
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")

    def test_the_copy_is_valid(self):
        self.assertEqual(self.codes(), [])

    def test_front_matter_parser(self):
        meta, body = C.parse_front_matter("---\nname: a\ntitle: B: c\n---\n\n## X\n")
        self.assertEqual(meta, {"name": "a", "title": "B: c"})
        self.assertIn("## X", body)
        for bad in ("name: a\n", "---\nname: a\n", "---\nname a\n---\n", "---\nname: a\nname: b\n---\n",
                    "---\n  - list\n---\n"):
            with self.assertRaises(ValueError, msg=bad):
                C.parse_front_matter(bad)

    def test_sections_keys_and_budget(self):
        self.edit("CASE.md", "## Baseline", "## Base line")
        self.edit("CASE.md", "license: MIT", "license: GPL")
        self.edit("CASE.md", "tools: summary", "tools: summary, frobnicate")
        self.assertEqual(self.codes(), ["FRONT_MATTER_LICENSE", "FRONT_MATTER_TOOLS", "SECTIONS"])
        self.edit("CASE.md", "## Files", "## Files\n\n" + "x" * 2000)
        self.assertIn("TOKEN_BUDGET", self.codes())

    def test_project_and_expected(self):
        project = load(self.case / "project.json")
        (self.case / "project.json").write_text(json.dumps(project), encoding="utf-8")
        self.assertEqual(self.codes(), ["PROJECT_NOT_NORMALIZED"])
        project["targets"] = []
        (self.case / "project.json").write_text(C.normalized_json(project), encoding="utf-8", newline="\n")
        self.assertIn("PROJECT_TARGETS", self.codes())
        expected = load(self.case / "expected.json")
        expected["targets"]["spot_radius"] = "PASSED"
        (self.case / "expected.json").write_text(C.normalized_json(expected), encoding="utf-8", newline="\n")
        self.assertIn("EXPECTED_SCHEMA", self.codes())

    def test_baseline_and_catalog_pins_agree(self):
        self.edit("CASE.md", "catalog: none", "catalog: 2026-10-28.1")
        self.assertEqual(self.codes(), ["BASELINE_MISMATCH", "CATALOG_PIN"])

    def test_images_sizes_paths_and_generated_files(self):
        (self.case / "images" / "extra.png").write_bytes(C.PNG_SIGNATURE + b"\0" * (C.IMAGE_MAX_BYTES + 1))
        self.assertEqual(self.codes(), ["IMAGES"])
        (self.case / "images" / "extra.png").unlink()
        (self.case / "notes.txt").write_text("run dir: C:\\Users\\someone\\run\n", encoding="utf-8")
        (self.case / "scene.glb").write_bytes(b"glTF")
        (self.case / "big.csv").write_text("x\n" * (C.FILE_MAX_BYTES // 2 + 1), encoding="utf-8")
        self.assertEqual(self.codes(), ["FILE_TOO_LARGE", "GENERATED_FILE", "LOCAL_PATH"])

    def test_local_path_pattern(self):
        for text in ("C:\\Users\\x", "D:/cache/x", "/home/user/x", "\\\\server\\share", "see /tmp/run"):
            self.assertIsNotNone(C.LOCAL_PATH.search(text), text)
        for text in ("https://github.com/x/y", "cases/a/b.json", "~/.opensci/token", "1:2", "a/b/c", "N:1"):
            self.assertIsNone(C.LOCAL_PATH.search(text), text)

    def test_index_write_and_check_on_a_tree(self):
        index = C.build_index(self.root)
        self.assertEqual([c["name"] for c in index["cases"]], [CATALOG_FREE])
        self.assertEqual(index["cases"][0]["catalog"], None)
        self.assertNotIn("__pycache__", " ".join(index["cases"][0]["files"]))


class CopyTests(unittest.TestCase):
    def test_copy_works_and_refuses_to_overwrite_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            code, out, err = call(["copy", "mirror-alignment", "--dest", str(dest)])
            self.assertEqual(code, 0, err)
            summary = json.loads(out)
            self.assertEqual(summary["status"], "COPIED")
            target = dest / "mirror-alignment"
            self.assertEqual(summary["files"], C.case_files(CASES / "mirror-alignment"))
            for name in summary["files"]:
                self.assertEqual((target / name).read_bytes(), (CASES / "mirror-alignment" / name).read_bytes())
            code, out, _err = call(["copy", "mirror-alignment", "--dest", str(dest)])
            self.assertEqual((code, json.loads(out)["written"]), (0, []))
            (target / "CASE.md").write_text("changed", encoding="utf-8")
            code, _out, err = call(["copy", "mirror-alignment", "--dest", str(dest)])
            self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "DESTINATION_EXISTS"))
            self.assertEqual((target / "CASE.md").read_text(encoding="utf-8"), "changed")
            code, out, _err = call(["copy", "mirror-alignment", "--dest", str(dest), "--force"])
            self.assertEqual((code, json.loads(out)["written"]), (0, ["CASE.md"]))

    def test_without_the_engine(self):
        """list / show / copy / index never import os_optics; verify refuses with exit 4."""
        script = ("import sys; sys.modules['os_optics'] = None\n"
                  "from os_optics_kit import cases\n"
                  "codes = [cases.main(['list']), cases.main(['show', 'ideal-lens-focus']),\n"
                  "         cases.main(['copy', 'ideal-lens-focus', '--dest', sys.argv[1]]),\n"
                  "         cases.main(['index', '--check'])]\n"
                  "print('CODES', codes, 'os_optics' in sys.modules and sys.modules['os_optics'] is not None)\n")
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-c", script, tmp], capture_output=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertIn(b"CODES [0, 0, 0, 0] False", done.stdout)
        with patch.object(C, "find_spec", return_value=None), patch.object(C.shutil, "which", return_value=None):
            code, _out, err = call(["verify", CATALOG_FREE])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "ENGINE_MISSING"))


@unittest.skipUnless(HAS_ENGINE, "verify runs the engine: os_optics is not importable")
class VerifyTests(unittest.TestCase):
    def test_catalog_free_case_matches_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = call(["verify", CATALOG_FREE, "--out", tmp])
            self.assertEqual(code, 0, err + out)
            document = json.loads(out)
            self.assertTrue((Path(tmp) / CATALOG_FREE / "run" / "manifest.json").is_file())
        self.assertEqual((document["status"], document["counts"]), ("MATCH", {"MATCH": 1, "MISMATCH": 0, "ERROR": 0}))
        entry = document["cases"][0]
        expected = load(CASES / CATALOG_FREE / "expected.json")
        self.assertEqual(entry["actual"], {"status": expected["status"], "targets": expected["targets"]})
        self.assertEqual(entry["differences"], [])

    def test_mismatch_is_reported_and_update_expected_records_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tree(["interop-diffraction"], Path(tmp) / "kit")
            case = root / "interop-diffraction"
            expected = load(case / "expected.json")
            wrong = dict(expected, status="PASS", targets={"spot": "UNMET"})
            (case / "expected.json").write_text(C.normalized_json(wrong), encoding="utf-8", newline="\n")
            project = load(case / "project.json")
            (case / "project.json").write_text(json.dumps(project), encoding="utf-8")
            document = C.verify(["interop-diffraction"], out=Path(tmp) / "runs", root=root)
            self.assertEqual(document["status"], "MISMATCH")
            self.assertEqual(sorted(d["field"] for d in document["cases"][0]["differences"]),
                             ["status", "targets.spot"])
            document = C.verify(["interop-diffraction"], out=Path(tmp) / "runs2", root=root, update_expected=True)
            self.assertEqual(document["cases"][0]["updated"], ["project.json", "expected.json", "CASE.md"])
            self.assertEqual(load(case / "expected.json"), expected)
            self.assertEqual((case / "project.json").read_bytes(),
                             (CASES / "interop-diffraction" / "project.json").read_bytes())
            self.assertEqual(C.check_case(case), [])

    def test_refusals(self):
        code, _out, err = call(["verify"])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "BAD_ARGUMENT"))
        code, _out, err = call(["verify", "no-such-case"])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "UNKNOWN_CASE"))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / CATALOG_FREE).mkdir()
            (Path(tmp) / CATALOG_FREE / "keep.txt").write_text("x", encoding="utf-8")
            code, _out, err = call(["verify", CATALOG_FREE, "--out", tmp])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "OUTPUT_EXISTS"))


def workflow_module(case):
    spec = spec_from_file_location(f"case_{case.replace('-', '_')}_workflow_check", CASES / case / "workflow.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_module(case, name):
    folder = str(CASES / case)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return import_module(name)


def engine_form(document):
    """The engine's normalized ProjectDocumentV2 content of a document (what a run directory's project.json holds)."""
    from os_optics.local import load_project

    return load_project(json.loads(json.dumps(document))).model_dump(mode="json", exclude_none=True)


@unittest.skipUnless(HAS_ENGINE, "the builders use os_optics: os_optics is not importable")
class ProjectConsistencyTests(unittest.TestCase):
    """Each case's project.json is the design its scripts build (engine-normalized, same content)."""

    def test_workflow_cases_hold_their_nominal_design(self):
        for case in ("expansion-focus", "polarization-control", "mirror-alignment"):
            module = workflow_module(case)
            config = module.config()
            defaults = getattr(module, "DEFAULTS", {})
            inputs = {name: defaults.get(name, info["default"]) for name, info in config["info"]["parameters"].items()}
            with self.subTest(case=case):
                self.assertEqual(load(CASES / case / "project.json"), engine_form(config["builder"](**inputs)))

    def test_interop_cases_hold_the_design_they_hand_off(self):
        oe = case_module("interop-optimization", "optimize_expander")
        self.assertEqual(load(CASES / "interop-optimization" / "project.json"), engine_form(oe.base_project()))
        handoff = case_module("interop-diffraction", "opensci_handoff")
        self.assertEqual(load(CASES / "interop-diffraction" / "project.json"),
                         engine_form(handoff.project_document(1.2)))
        layouts = case_module("interop-quantum", "build_layouts")
        quantum = load(CASES / "interop-quantum" / "project.json")
        self.assertEqual([(t["observation_id"], t["metric"]) for t in quantum["targets"]],
                         [("D1", "power_W"), ("D2", "power_W")])
        self.assertEqual(quantum, engine_form({**layouts.hom().document, "targets": quantum["targets"]}))
        step4 = case_module("interop-metasurface", "step4_engine_check")
        metasurface = load(CASES / "interop-metasurface" / "project.json")
        model = metasurface["optical_models"][0]
        self.assertEqual(model["model"]["model_kind"], "TABULATED_THIN_LENS")
        self.assertEqual(metasurface,
                         engine_form(step4.project(model, "tabulated", wavelengths_nm=[532.0], waist_um=30.0)))

    def test_ideal_lens_case_matches_the_request_api_script(self):
        script = case_module("ideal-lens-focus", "ideal_lens_focus")
        snapshot = script.build_project().model_dump(mode="json")
        project = load(CASES / CATALOG_FREE / "project.json")
        by_role = {c["optical_role"]: c for c in project["components"]}
        laser = next(c for c in snapshot["components"] if c["role"] == "source")
        lens = next(c for c in snapshot["components"] if c["role"] == "lens")
        source = by_role["source"]["source_model"]
        self.assertEqual((source["wavelength_nm"], source["power_w"], source["waist_radius_mm"]),
                         (laser["source"]["wavelength_nm"], laser["source"]["power_W"],
                          laser["source"]["waist_radius_x_mm"]))
        self.assertEqual(by_role["source"]["nominal_pose"]["position_mm"], laser["pose"]["position_mm"])
        self.assertEqual(by_role["lens"]["nominal_pose"]["position_mm"], lens["pose"]["position_mm"])
        model = project["optical_models"][0]["model"]
        self.assertEqual((model["model_kind"], float(model["focal_length_mm"]), float(model["clear_radius_mm"])),
                         ("IDEAL_THIN_LENS", 100.0, lens["surfaces"][0]["aperture"]["radius_mm"]))
        self.assertEqual(project["observations"][0]["pose"]["position_mm"], [110.0, 0.0, 20.0])
        self.assertEqual((project["targets"][0]["metric"], project["targets"][0]["maximum"]),
                         (snapshot["targets"][0]["metric"], snapshot["targets"][0]["maximum"]))


if __name__ == "__main__":
    unittest.main()
