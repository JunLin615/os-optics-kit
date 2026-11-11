"""``os-optics-kit contrib case`` with the engine: normalization, refusals, the full dry run (check, run, targets,
classified front matter, system views recorded in images.json, both READMEs, patch), ``--refresh``, the lens section
view, the INCOMPLETE bundle when some or all views cannot be rendered and the refresh that completes it, and the
README case tables of the patch. A catalog-free Project (declared ideal thin lenses) keeps the tests offline.
Skipped when os-optics is not installed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_contrib import HAS_GIT, call, fresh_clone, git, make_kit_repo

from os_optics_kit import _frontmatter as fm
from os_optics_kit import cases as kit_cases
from os_optics_kit.contrib import _common as C
from os_optics_kit.contrib import case as case_mod

HAS_ENGINE = importlib.util.find_spec("os_optics") is not None
FREE_SPACE = ["--class", "free-space", "--domain", "gaussian-beam", "--tags", "beam-expander"]
LENS = ["--class", "lens", "--domain", "gaussian-beam", "--tags", "ideal-lens,focusing"]
RENDER_VIEW = kit_cases._render_view


def failing_view(*views: str):
    """A ``cases._render_view`` that fails for ``views`` and renders every other view."""
    def render(run_dir, work, view, *args, **kwargs):
        if view in views:
            raise kit_cases.CaseError("ENGINE_FAILED", f"os-optics view {view} failed (exit 3)")
        return RENDER_VIEW(run_dir, work, view, *args, **kwargs)
    return render


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
            code, doc = call(["case", str(path), "--name", "bad-project", *FREE_SPACE, "--out", str(Path(tmp) / "o")])
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
                          "--class", "free-space", "--domain", "gaussian-beam,diffraction", "--tags",
                          "beam-expander,parameter-sweep", "--tools", "summary", "--repo", str(self.repo),
                          "--out", str(out), "--dry-run", "--open-pr"])
        self.assertEqual(code, 0, doc)
        self.assertEqual(doc["status"], "READY")
        self.assertEqual(doc["baseline"]["catalog"], None)
        self.assertEqual(doc["verification"]["check"], "ACCEPTED")
        self.assertEqual(doc["verification"]["execution"], "COMPLETED")
        self.assertEqual(doc["verification"]["targets"], {"expanded_radius": "MET"})
        self.assertEqual(doc["pr"]["reason"], "DRY_RUN")
        case_dir = out / "case"
        self.assertEqual(C.relative_listing(case_dir), ["CASE.md", "README.md", "README.zh-CN.md", "expected.json",
                                                        "images.json", "images/iso.png", "project.json"])
        self.assertEqual(doc["images"], ["images/iso.png"])
        self.assertLessEqual((case_dir / "images" / "iso.png").stat().st_size, kit_cases.IMAGE_MAX_BYTES)
        expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
        self.assertEqual(expected["schema"], "os-optics-kit.case-expected")
        self.assertEqual((expected["catalog"], expected["status"]), (None, "PASS"))
        front, _ = fm.split((case_dir / "CASE.md").read_text(encoding="utf-8"))
        self.assertEqual(list(front), list(kit_cases.FRONT_MATTER_KEYS))
        self.assertEqual((front["class"], front["domain"], front["provenance"], front["publication"], front["tags"]),
                         ("free-space", "gaussian-beam, diffraction", "self-built", "own-work",
                          "beam-expander, parameter-sweep"))
        self.assertEqual((front["catalog"], front["tools"], front["license"]), ("none", "summary", "MIT"))
        self.assertLessEqual(fm.token_estimate((case_dir / "CASE.md").read_text(encoding="utf-8")), 700)
        self.assertEqual(doc["warnings"], [])
        listing = C.relative_listing(out)
        self.assertFalse([p for p in listing if p.endswith(".glb") or p.endswith("manifest.json")])

        # images.json: the iso snapshot with the options used and the identity of the verified run
        images = json.loads((case_dir / "images.json").read_text(encoding="utf-8"))
        self.assertEqual((case_dir / "images.json").read_text(encoding="utf-8"), kit_cases.normalized_json(images))
        self.assertEqual([(r["file"], r["kind"], r["view"], r["options"][:2]) for r in images["images"]],
                         [("images/iso.png", "system-view", "iso", ["--views", "iso"])])
        self.assertEqual(images["images"][0]["sha256"], C.sha256_file(case_dir / "images" / "iso.png"))
        self.assertEqual(images["project_sha256"], C.sha256_file(case_dir / "project.json"))
        self.assertEqual((images["run"]["engine"], images["run"]["catalog"]), (doc["baseline"]["engine"], None))
        self.assertTrue(images["run"]["project_hash"] and images["run"]["engine_input_hash"])
        for readme, partner in (("README.md", "README.zh-CN.md"), ("README.zh-CN.md", "README.md")):
            text = (case_dir / readme).read_text(encoding="utf-8")
            self.assertEqual(kit_cases.shown_images(text), ["images/iso.png"])
            self.assertNotIn("{{", text)
            self.assertIn(f"]({partner})", text.split("\n")[2])  # the two READMEs link each other
        self.assertIn("## 结果", (case_dir / "README.zh-CN.md").read_text(encoding="utf-8"))
        self.assertEqual(manifest_of(out)["unrendered_views"], [])
        pr_body = (out / "PR_BODY.md").read_text(encoding="utf-8")
        for needle in ("| Class | `free-space` |", "| Domain | `gaussian-beam`, `diffraction` |",
                       "| Provenance | `self-built` |", "| Publication | `own-work` |", "system view `iso`"):
            self.assertIn(needle, pr_body)

        applied = fresh_clone(self.repo, self.tmp)
        manifest = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
        git("am", str(out / manifest["patch"]), cwd=applied)
        index = json.loads((applied / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual(index["schema_version"], 2)
        entry = index["cases"][0]
        self.assertEqual(entry["name"], "ideal-expander")
        self.assertEqual((entry["class"], entry["domain"], entry["tags"]),
                         ("free-space", ["gaussian-beam", "diffraction"], ["beam-expander", "parameter-sweep"]))
        self.assertEqual(entry["images"], [{"file": "images/iso.png", "kind": "system-view", "view": "iso"}])
        self.assertEqual(entry["scale"]["band"], "small")
        self.assertEqual(entry["project_sha256"], C.sha256_file(applied / "cases" / "ideal-expander" / "project.json"))
        self.assertEqual(entry, manifest["cases_index_entry"])
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=applied).split()
        for name in ("images.json", "README.zh-CN.md", "images/iso.png"):
            self.assertIn(f"cases/ideal-expander/{name}", committed)
        self.assertEqual(kit_cases.check_case(applied / "cases" / "ideal-expander"), [])
        # the patch carries the README case tables, so the CI step 'case_tables.py --check' passes
        for name in ("cases.json", "README.md", "README.zh-CN.md"):
            self.assertIn(name, committed)
        tables = subprocess.run([sys.executable, "scripts/case_tables.py", "--check"], cwd=applied,
                                capture_output=True, text=True)
        self.assertEqual(tables.returncode, 0, tables.stdout + tables.stderr)
        self.assertIn("cases/ideal-expander", (applied / "README.md").read_text(encoding="utf-8"))
        self.assertIn("README case tables regenerated", pr_body)
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
        self.assertNotIn(b"\r", (out / "case" / "CASE.md").read_bytes())
        refreshed, _ = fm.split((out / "case" / "CASE.md").read_text(encoding="utf-8"))
        self.assertEqual({k: refreshed[k] for k in ("class", "domain", "tags")},
                         {k: front[k] for k in ("class", "domain", "tags")})  # the bundle's front matter is kept
        again = json.loads((out / "case" / "images.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in again["images"]], ["images/iso.png"])
        self.assertEqual(again["run"]["project_hash"], images["run"]["project_hash"])

        # the same case cannot be added twice; a broken edit is refused with the problems listed
        code, doc = call(["case", str(self.project), "--name", "ideal-expander", *FREE_SPACE, "--repo",
                          str(applied), "--out", str(self.tmp / "again"), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_EXISTS"))
        case_md.write_text(edited.replace("## Targets", "## Results"), encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_FILES_INVALID"))
        self.assertTrue(any(p.startswith("SECTIONS: ") for p in doc["error"]["problems"]), doc)
        case_md.write_text(edited.replace("class: free-space", "class: telescope"), encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_FILES_INVALID"))
        self.assertTrue(any(p.startswith("FRONT_MATTER_CLASS: ") for p in doc["error"]["problems"]), doc)

    def test_bundle_without_checkout_and_catalog_file_refusal(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        cwd = os.getcwd()
        os.chdir(outside)
        try:
            code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--out", str(self.tmp / "b"),
                              *FREE_SPACE, "--dry-run", "--catalog", str(self.tmp / "missing.json")])
            self.assertEqual((code, doc["error"]["code"]), (4, "CATALOG_FILE_MISSING"))
            code, doc = call(["case", str(self.project), "--name", "ideal-expander", "--out", str(self.tmp / "b"),
                              *FREE_SPACE, "--dry-run"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, doc)
        self.assertIsNone(doc["patch"])
        self.assertTrue(any(w.startswith("NO_KIT_CHECKOUT") for w in doc["warnings"]))
        manifest = json.loads((self.tmp / "b" / "bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["cases_index_entry"]["name"], "ideal-expander")
        self.assertEqual(kit_cases.check_case(self._staged(self.tmp / "b")), [])

    def _staged(self, bundle: Path) -> Path:
        """The bundle's case copied to cases/NAME, as the kit's index check sees it."""
        name = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))["name"]
        target = self.tmp / "staged" / bundle.name / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(bundle / "case", target)
        return target

    def test_lens_case_records_iso_and_section(self):
        out = self.tmp / "lens"
        code, doc = call(["case", str(self.project), "--name", "ideal-relay", "--class", "lens", "--domain",
                          "gaussian-beam", "--tags", "ideal-lens,focusing", "--repo", str(self.repo), "--out",
                          str(out), "--dry-run"])
        self.assertEqual(code, 0, doc)
        self.assertEqual(doc["images"], ["images/iso.png", "images/section.png"])
        images = json.loads((out / "case" / "images.json").read_text(encoding="utf-8"))
        section = images["images"][1]
        self.assertEqual((section["view"], section["options"]), ("section", ["--section", "ALL", "--frame=scene",
                                                                            "--size", "1200x420"]))
        for readme in ("README.md", "README.zh-CN.md"):
            text = (out / "case" / readme).read_text(encoding="utf-8")
            self.assertEqual(kit_cases.shown_images(text), ["images/iso.png", "images/section.png"])
        self.assertEqual(kit_cases.check_case(self._staged(out)), [])

    def test_unrendered_view_gives_an_incomplete_bundle_that_refresh_completes(self):
        def failing(*args, **kwargs):
            raise kit_cases.CaseError("ENGINE_FAILED", "os-optics view failed (exit 3)")

        out = self.tmp / "incomplete"
        with mock.patch.object(kit_cases, "_render_view", failing):
            code, doc = call(["case", str(self.project), "--name", "ideal-expander", *FREE_SPACE, "--repo",
                              str(self.repo), "--out", str(out), "--dry-run", "--open-pr"])
        self.assertEqual((code, doc["status"]), (1, "INCOMPLETE"), doc)
        self.assertIsNone(doc["patch"])
        self.assertEqual(doc["pr"], {"opened": False, "reason": "INCOMPLETE"})
        self.assertEqual(doc["images"], [])
        warning = next(w for w in doc["warnings"] if w.startswith("IMAGE_NOT_RENDERED"))
        for needle in ("images/iso.png", "ENGINE_FAILED", "os-optics view RUN --no-browser --snapshot DIR --views iso",
                       "os-optics-kit contrib case --refresh BUNDLE"):
            self.assertIn(needle, warning)
        for needle in (f"os-optics-kit contrib case --refresh {out}", "os-optics-kit cases images ideal-expander --view "
                       "iso", "README.md and README.zh-CN.md", "cases index --write", "scripts/case_tables.py --write"):
            self.assertIn(needle, doc["next_step"])
        self.assertEqual([v["view"] for v in manifest_of(out)["unrendered_views"]], ["iso"])
        self.assertFalse(list(out.glob("*.patch")))
        problems = kit_cases.check_case(self._staged(out))
        self.assertTrue(problems)
        self.assertEqual({p["code"] for p in problems}, {"IMAGES"})

        # once the engine draws the view, --refresh renders the default views, puts them in place of the generated
        # "no system view" line of both READMEs and completes the bundle
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (0, "READY", ["images/iso.png"]), doc)
        self.assertTrue(doc["patch"])
        for readme in ("README.md", "README.zh-CN.md"):
            text = (out / "case" / readme).read_text(encoding="utf-8")
            self.assertEqual(kit_cases.shown_images(text), ["images/iso.png"])
        self.assertEqual(kit_cases.check_case(self._staged(out)), [])
        self.assertEqual(manifest_of(out)["unrendered_views"], [])

    def _assert_complete(self, out: Path, images: list[str]) -> None:
        for readme in ("README.md", "README.zh-CN.md"):
            text = (out / "case" / readme).read_text(encoding="utf-8")
            self.assertEqual(kit_cases.shown_images(text), images, readme)
        recorded = json.loads((out / "case" / "images.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in recorded["images"]], images)
        self.assertEqual(manifest_of(out)["unrendered_views"], [])
        self.assertTrue(list(out.glob("*.patch")))
        self.assertEqual(kit_cases.check_case(self._staged(out)), [])

    def test_lens_section_not_rendered_then_refreshed(self):
        out = self.tmp / "lens-partial"
        with mock.patch.object(kit_cases, "_render_view", failing_view("section")):
            code, doc = call(["case", str(self.project), "--name", "ideal-relay", *LENS, "--repo", str(self.repo),
                              "--out", str(out), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (1, "INCOMPLETE", ["images/iso.png"]), doc)
        self.assertTrue(any(w.startswith("IMAGE_NOT_RENDERED: images/section.png") for w in doc["warnings"]))
        self.assertIn("os-optics-kit cases images ideal-relay --view iso --section ALL --frame=scene", doc["next_step"])
        self.assertEqual(manifest_of(out)["unrendered_views"], [{"index": 1, "view": "section", "argument": "ALL",
                                                                 "size": "1200x420", "frame": "scene"}])
        # the agent captions the image it has; the refresh adds the section after it and keeps the caption
        readme = out / "case" / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            f"![{case_mod.CAPTIONS['iso'][0]}]", "![Two ideal lenses relay the beam]"), encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (0, "READY", ["images/iso.png", "images/section.png"]),
                         doc)
        self._assert_complete(out, ["images/iso.png", "images/section.png"])
        self.assertIn("![Two ideal lenses relay the beam](images/iso.png)", readme.read_text(encoding="utf-8"))
        section = json.loads((out / "case" / "images.json").read_text(encoding="utf-8"))["images"][1]
        self.assertEqual(section["options"], ["--section", "ALL", "--frame=scene", "--size", "1200x420"])

    def test_lens_iso_not_rendered_then_refreshed(self):
        out = self.tmp / "lens-no-iso"
        with mock.patch.object(kit_cases, "_render_view", failing_view("iso")):
            code, doc = call(["case", str(self.project), "--name", "ideal-relay", *LENS, "--repo", str(self.repo),
                              "--out", str(out), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (1, "INCOMPLETE", ["images/section.png"]), doc)
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (0, "READY", ["images/iso.png", "images/section.png"]),
                         doc)
        self._assert_complete(out, ["images/iso.png", "images/section.png"])

    def test_class_edited_to_lens_gets_its_section_on_refresh(self):
        out = self.tmp / "to-lens"
        code, doc = call(["case", str(self.project), "--name", "ideal-relay", *FREE_SPACE, "--repo", str(self.repo),
                          "--out", str(out), "--dry-run"])
        self.assertEqual((code, doc["images"]), (0, ["images/iso.png"]), doc)
        case_md = out / "case" / "CASE.md"
        case_md.write_text(case_md.read_text(encoding="utf-8").replace("class: free-space", "class: lens"),
                           encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["status"], doc["images"]), (0, "READY", ["images/iso.png", "images/section.png"]),
                         doc)
        self._assert_complete(out, ["images/iso.png", "images/section.png"])

    def test_hand_edited_readme_images_are_reported(self):
        out = self.tmp / "hand-edited"
        with mock.patch.object(kit_cases, "_render_view", failing_view("section")):
            call(["case", str(self.project), "--name", "ideal-relay", *LENS, "--repo", str(self.repo), "--out",
                  str(out), "--dry-run"])
        readme = out / "case" / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("](images/iso.png)",
                                                                     "](images/iso.png) shows the relay"),
                          encoding="utf-8")
        code, doc = call(["case", "--refresh", str(out), "--repo", str(self.repo), "--dry-run"])
        self.assertEqual((code, doc["error"]["code"]), (4, "CASE_FILES_INVALID"), doc)
        self.assertTrue(any(w.startswith("README_IMAGES_NOT_UPDATED: case/README.md") for w in doc["error"]["warnings"]))
        self.assertTrue(any(p.startswith("IMAGES: README.md shows") for p in doc["error"]["problems"]), doc)
        # the bundle is left as it was, so that the README can be fixed and the refresh run again
        self.assertEqual([v["view"] for v in manifest_of(out)["unrendered_views"]], ["section"])


def manifest_of(bundle: Path) -> dict:
    return json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
