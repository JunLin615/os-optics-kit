"""``os-optics-kit contrib case`` with the engine: normalization, refusals, the full dry run (check, run, targets,
rendered images, case files, patch) and ``--refresh``. A catalog-free Project (declared ideal thin lenses) keeps the
tests offline. Skipped when os-optics is not installed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_contrib import HAS_GIT, call, fresh_clone, git, make_kit_repo

from os_optics_kit.contrib import _common as C
from os_optics_kit.contrib import case as case_mod

HAS_ENGINE = importlib.util.find_spec("os_optics") is not None


def _lens(instance_id: str, x_mm: float) -> dict:
    return {"instance_id": instance_id, "optical_role": "lens", "optical_model_id": f"{instance_id}-model",
            "custom_part": {"custom_part_key": instance_id.lower(), "description": "Ideal thin lens."},
            "nominal_pose": {"position_mm": [x_mm, 0.0, 0.0], "quaternion_wxyz": [0.5, 0.5, 0.5, 0.5]},
            "selected_parameters": [], "retained_dofs": []}


def _model(model_id: str, focal_mm: str) -> dict:
    return {"model_id": model_id, "model": {
        "model_kind": "IDEAL_THIN_LENS", "focal_length_mm": focal_mm, "clear_radius_mm": "6",
        "edge_radius_mm": "6.35", "status": "DECLARED",
        "valid_band_nm": {"min_nm": "400", "max_nm": "1100", "status": "DECLARED"}}}


PROJECT = {
    "schema": "opensci.project",
    "project_schema_version": "2.0",
    "components": [
        {"instance_id": "SRC", "optical_role": "source",
         "custom_part": {"custom_part_key": "user-laser", "description": "User-supplied laser source."},
         "nominal_pose": {"position_mm": [-200.0, 0.0, 0.0], "quaternion_wxyz": [0.5, 0.5, 0.5, 0.5]},
         "selected_parameters": [], "retained_dofs": [],
         "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": 780.0, "power_w": 0.001,
                          "waist_radius_mm": 1.0, "waist_position_mm": 0.0, "m2": 1.0,
                          "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}},
        _lens("L1", 0.0),
        _lens("L2", 150.0),
    ],
    "optical_models": [_model("L1-model", "50"), _model("L2-model", "100")],
    "observations": [{"observation_id": "output",
                      "pose": {"position_mm": [250.0, 0.0, 0.0], "quaternion_wxyz": [0.5, 0.5, 0.5, 0.5]},
                      "aperture": {"shape": "circle", "radius_mm": 12.5, "center_offset_mm": [0.0, 0.0],
                                   "obstruction_radius_mm": 0.0},
                      "absorbing": False, "medium": "air"}],
    "targets": [{"target_id": "expanded_radius", "metric": "beam_radius_mm", "observation_id": "output",
                 "minimum": 0.5, "maximum": 5.0, "unit": "mm", "parameters": []}],
    "materials": [],
}


@unittest.skipUnless(HAS_ENGINE, "os-optics is not installed")
class NormalizationTests(unittest.TestCase):
    def test_normalized_text_is_sorted_and_stable(self):
        project, text = case_mod.load_and_normalize(json.dumps(PROJECT).encode())
        self.assertEqual(list(json.loads(text)), sorted(json.loads(text)))
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(case_mod.load_and_normalize(text.encode())[1], text)
        self.assertEqual(case_mod.catalog_pins(project), [])

    def test_refusals(self):
        with self.assertRaises(C.ContribError) as ctx:
            case_mod.load_and_normalize(b'{"schema": "x"}')
        self.assertEqual(ctx.exception.code, "PROJECT_INVALID")
        self.assertEqual(ctx.exception.problems[0]["code"], "PROJECT_INVALID")
        without_targets = copy.deepcopy(PROJECT)
        without_targets["targets"] = []
        with self.assertRaises(C.ContribError) as ctx:
            case_mod.load_and_normalize(json.dumps(without_targets).encode())
        self.assertEqual(ctx.exception.code, "PROJECT_HAS_NO_TARGETS")

    def test_cli_refusal_is_json_exit_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text("{not json", encoding="utf-8")
            code, doc = call(["case", str(path), "--name", "bad-project", "--out", str(Path(tmp) / "o")])
            self.assertEqual((code, doc["error"]["code"]), (4, "PROJECT_INVALID"))


@unittest.skipUnless(HAS_ENGINE and HAS_GIT, "os-optics or git is not installed")
class CaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="contrib-engine-"))
        env = {"OPENSCI_CATALOG_DIR": str(self.tmp / "catalog"), "OPENSCI_CATALOG_CHECK_INTERVAL": "0"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = make_kit_repo(self.tmp)
        self.project = self.tmp / "design.json"
        self.project.write_text(json.dumps(PROJECT), encoding="utf-8")

    def test_dry_run_bundle_patch_and_refresh(self):
        out = self.tmp / "bundle"
        code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--title", "Ideal-lens expander",
                          "--tags", "beam-expander,catalog-free", "--tools", "summary", "--repo", str(self.repo),
                          "--out", str(out), "--dry-run", "--open-pr"])
        self.assertEqual(code, 0, doc)
        self.assertEqual(doc["status"], "READY")
        self.assertEqual(doc["baseline"]["catalog"], None)
        self.assertEqual(doc["verification"]["check"], "ACCEPTED")
        self.assertEqual(doc["verification"]["execution"], "COMPLETED")
        self.assertEqual(doc["verification"]["targets"], {"expanded_radius": "MET"})
        self.assertEqual(doc["pr"]["reason"], "DRY_RUN")
        case_dir = out / "case"
        self.assertEqual(sorted(C.relative_listing(case_dir))[:4], ["CASE.md", "README.md", "expected.json",
                                                                    "images/iso.png"])
        for image in (case_dir / "images").iterdir():
            self.assertLessEqual(image.stat().st_size, case_mod.IMAGE_LIMIT_BYTES)
        expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(expected["schema"], "os-optics-kit.case-expected")
        self.assertEqual((expected["catalog"], expected["status"]), (None, "PASS"))
        front, _ = C.parse_front_matter((case_dir / "CASE.md").read_text(encoding="utf-8"))
        self.assertEqual((front["catalog"], front["tools"], front["license"]), ("none", "summary", "MIT"))
        self.assertLessEqual(C.token_estimate((case_dir / "CASE.md").read_text(encoding="utf-8")), 700)
        listing = C.relative_listing(out)
        self.assertFalse([p for p in listing if p.endswith(".glb") or p.endswith("manifest.json")])

        applied = fresh_clone(self.repo, self.tmp)
        manifest = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
        git("am", str(out / manifest["patch"]), cwd=applied)
        index = json.loads((applied / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual(index["cases"][0]["name"], "ideal-expander")
        self.assertEqual(index["cases"][0]["project_sha256"],
                         C.sha256_file(applied / "cases" / "ideal-expander" / "project.json"))
        self.assertIn("Signed-off-by: Kit Contributor <contributor@example.com>", git("log", "-1", cwd=applied))

        # the agent edits the prose; --refresh re-verifies and rebuilds the patch with it
        case_md = case_dir / "CASE.md"
        edited = case_md.read_text(encoding="utf-8").replace(f"summary: {front['summary']}",
                                                             "summary: Two ideal thin lenses expand the beam.")
        case_md.write_bytes(edited.replace("\n", "\r\n").encode("utf-8"))
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual(code, 0, doc)
        patch = (out / json.loads((out / "bundle.json").read_text(encoding="utf-8"))["patch"]).read_text("utf-8")
        self.assertIn("summary: Two ideal thin lenses expand the beam.", patch)
        self.assertNotIn("\r", (out / "case" / "CASE.md").read_text(encoding="utf-8", newline=""))

        # the same case cannot be added twice; a broken edit is refused with the problems listed
        code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--repo", str(applied),
                          "--out", str(self.tmp / "again"), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_EXISTS"))
        case_md.write_text(edited.replace("## Targets", "## Results"), encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_FILES_INVALID"))
        self.assertIn("CASE.md: section '## Targets' missing", doc["error"]["problems"])

    def test_bundle_without_checkout_and_catalog_file_refusal(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        cwd = os.getcwd()
        os.chdir(outside)
        try:
            code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--out", str(self.tmp / "b"),
                              "--dry-run", "--catalog", str(self.tmp / "missing.json")])
            self.assertEqual((code, doc["error"]["code"]), (4, "CATALOG_FILE_MISSING"))
            code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--out", str(self.tmp / "b"),
                              "--dry-run"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, doc)
        self.assertIsNone(doc["patch"])
        self.assertTrue(any(w.startswith("NO_KIT_CHECKOUT") for w in doc["warnings"]))
        manifest = json.loads((self.tmp / "b" / "bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cases_index_entry"]["name"], "ideal-expander")


if __name__ == "__main__":
    unittest.main()
