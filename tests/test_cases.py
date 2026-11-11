"""Checks of the case collection and of ``os-optics-kit cases`` (list, vocabulary, show, copy, verify, index).

The format, index, list, vocabulary and copy checks need only the standard library. The verify checks and the project
consistency checks run the engine and are skipped when ``os_optics`` is not importable. The image rules and
``cases images`` are tested in test_case_images.py.
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
INDEX_KEYS = ("name", "title", "summary", "path", "class", "domain", "provenance", "publication", "source", "scale",
              "engine", "catalog", "tools", "tags", "images", "files", "project_sha256")
LENS_CASES = ("achromatic-imaging", "aspheric-lens", "sonnar-telephoto-lens", "midwave-infrared-imager",
              "cylindrical-light-sheet", "ideal-lens-focus", "interop-metasurface")
#: derived scale of every case: (band, elements, sources, branches), as the case library rules give it
EXPECTED_SCALE = {
    "achromatic-imaging": ("small", 1, 1, 0), "aspheric-lens": ("medium", 5, 9, 0),
    "beam-expander": ("small", 2, 1, 0), "bounded-aberration-correction": ("small", 2, 1, 0),
    "cylindrical-light-sheet": ("small", 1, 1, 0), "detector-measurements": ("medium", 1, 1, 1),
    "expansion-focus": ("small", 3, 1, 0), "focus-requirement": ("small", 1, 1, 0),
    "ideal-lens-focus": ("small", 1, 1, 0), "interop-diffraction": ("small", 1, 1, 0),
    "interop-metasurface": ("small", 1, 1, 0), "interop-optimization": ("small", 2, 1, 0),
    "interop-quantum": ("medium", 1, 2, 1), "midwave-infrared-imager": ("medium", 5, 9, 0),
    "mirror-alignment": ("small", 1, 1, 0), "phase-only-flat-top": ("small", 3, 1, 0),
    "polarization-control": ("small", 2, 1, 0), "sonnar-telephoto-lens": ("medium", 6, 3, 0),
}


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

#: pieces of machine-local paths for the path-pattern tests (joined at run time)
DRIVE_C, DRIVE_D, HOME = "C" + ":", "D" + ":", "ho" + "me"

class IndexTests(unittest.TestCase):
    def test_every_case_passes_the_format_checks(self):
        self.assertEqual(C.check_all(), [])

    def test_index_is_current_and_complete(self):
        self.assertEqual((ROOT / "cases.json").read_text(encoding="utf-8"), C.index_text(C.build_index()))
        index = load(ROOT / "cases.json")
        self.assertEqual((index["schema"], index["schema_version"]), ("os-optics-kit.cases", 2))
        names = [c["name"] for c in index["cases"]]
        self.assertEqual(names, sorted(p.name for p in CASES.iterdir() if p.is_dir() and p.name != "__pycache__"))
        self.assertGreaterEqual(len(names), 10)
        for entry in index["cases"]:
            self.assertEqual(entry["path"], f"cases/{entry['name']}")
            for required in C.REQUIRED_FILES:
                self.assertIn(required, entry["files"], entry["name"])
            self.assertEqual(len(entry["project_sha256"]), 64)
            self.assertEqual(list(entry), [k for k in INDEX_KEYS if k != "source" or "source" in entry])
            self.assertEqual(entry["scale"], C.T.scale_of(load(CASES / entry["name"] / "project.json")))
            self.assertEqual(entry["images"][0]["kind"], "system-view", entry["name"])
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
                         ["bounded-aberration-correction", "interop-diffraction", "interop-metasurface",
                          "interop-optimization", "interop-quantum", "phase-only-flat-top"])
        code, out, _err = call(["list", "--class", "lens", "--catalog", "none"])
        self.assertEqual(sorted(c["name"] for c in json.loads(out)["cases"]), sorted(LENS_CASES))
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
        # the images record the SHA-256 of the project.json bytes they were drawn from
        self.assertEqual(self.codes(), ["IMAGES_RECORD", "PROJECT_NOT_NORMALIZED"])
        project["targets"] = []
        (self.case / "project.json").write_text(C.normalized_json(project), encoding="utf-8", newline="\n")
        self.assertIn("PROJECT_TARGETS", self.codes())
        expected = load(self.case / "expected.json")
        expected["targets"]["spot_radius"] = "PASSED"
        (self.case / "expected.json").write_text(C.normalized_json(expected), encoding="utf-8", newline="\n")
        self.assertIn("EXPECTED_SCHEMA", self.codes())

    def test_baseline_and_catalog_pins_agree(self):
        self.edit("CASE.md", "catalog: none", "catalog: 2026-10-28.1")
        # images.json records a catalog-free run, too
        self.assertEqual(self.codes(), ["BASELINE_MISMATCH", "CATALOG_PIN", "IMAGES_RECORD"])

    def test_images_sizes_paths_and_generated_files(self):
        (self.case / "images" / "extra.png").write_bytes(C.PNG_SIGNATURE + b"\0" * (C.IMAGE_MAX_BYTES + 1))
        self.assertEqual(self.codes(), ["IMAGES"])
        (self.case / "images" / "extra.png").unlink()
        (self.case / "notes.txt").write_text(f"run dir: {DRIVE_C}\\Users\\someone\\run\n", encoding="utf-8")
        (self.case / "scene.glb").write_bytes(b"glTF")
        (self.case / "big.csv").write_text("x\n" * (C.FILE_MAX_BYTES // 2 + 1), encoding="utf-8")
        self.assertEqual(self.codes(), ["FILE_TOO_LARGE", "GENERATED_FILE", "LOCAL_PATH"])

    def test_local_path_pattern(self):
        # Machine paths are assembled at run time so the repository check does not flag this file.
        for text in (f"{DRIVE_C}\\Users\\x", f"{DRIVE_D}/cache/x", f"/{HOME}/user/x", "\\\\server\\share",
                     "see /tmp/run"):
            self.assertIsNotNone(C.LOCAL_PATH.search(text), text)
        for text in ("https://github.com/x/y", "cases/a/b.json", "~/.opensci/token", "1:2", "a/b/c", "N:1"):
            self.assertIsNone(C.LOCAL_PATH.search(text), text)

    def test_index_write_and_check_on_a_tree(self):
        index = C.build_index(self.root)
        self.assertEqual((index["schema"], index["schema_version"]), ("os-optics-kit.cases", 2))
        self.assertEqual([c["name"] for c in index["cases"]], [CATALOG_FREE])
        entry = index["cases"][0]
        self.assertEqual(entry["catalog"], None)
        self.assertNotIn("__pycache__", " ".join(entry["files"]))
        self.assertEqual(list(entry), [k for k in INDEX_KEYS if k != "source"])
        self.assertEqual({k: entry[k] for k in ("class", "domain", "provenance", "publication", "tags")},
                         {"class": "lens", "domain": ["gaussian-beam"], "provenance": "self-built",
                          "publication": "own-work", "tags": ["ideal-lens", "focusing", "python-api"]})
        self.assertEqual(entry["scale"], {"band": "small", "elements": 1, "sources": 1, "branches": 0})
        self.assertEqual(entry["images"], [{"file": "images/iso.png", "kind": "system-view", "view": "iso"},
                                           {"file": "images/section.png", "kind": "system-view",
                                            "view": "section"}])
        self.assertIn("images.json", entry["files"])

    def test_source_appears_in_the_index_after_publication(self):
        self.edit("CASE.md", "provenance: self-built\npublication: own-work",
                  "provenance: literature\npublication: public-domain\nsource: A. Author, Some Optics Book (1950)")
        self.assertEqual(self.codes(), [])
        entry = C.build_index(self.root)["cases"][0]
        self.assertEqual(list(entry), list(INDEX_KEYS))
        self.assertEqual(entry["source"], "A. Author, Some Optics Book (1950)")

    def test_readme_pair_is_required(self):
        (self.case / "README.zh-CN.md").unlink()
        self.assertEqual(self.codes(), ["FILE_MISSING"])

    def test_readme_pair_links_each_other(self):
        messages = lambda: " | ".join(p["message"] for p in C.check_case(self.case))  # noqa: E731
        self.edit("README.md", "English · [简体中文](README.zh-CN.md)", "English · 简体中文")
        self.assertEqual(self.codes(), ["README_PAIR"])
        self.assertIn("README.md must link README.zh-CN.md", messages())
        # a link inside an HTML comment or a fenced code block is not a link
        self.edit("README.md", "English · 简体中文", "<!-- [简体中文](README.zh-CN.md) -->\n\n```\n[简体中文](README.zh-CN.md)\n```")
        self.assertEqual(self.codes(), ["README_PAIR"])
        # an image of the file is not a link either; an HTML link and ./ or #fragment targets are
        self.edit("README.md", "<!-- [简体中文](README.zh-CN.md) -->", "![x](README.zh-CN.md)")
        self.assertEqual(self.codes(), ["README_PAIR"])
        self.edit("README.md", "![x](README.zh-CN.md)", '<a href="./README.zh-CN.md#top">简体中文</a>')
        self.assertEqual(self.codes(), [])
        self.edit("README.zh-CN.md", "[English](README.md) · 简体中文", "English · 简体中文")
        self.assertEqual(self.codes(), ["README_PAIR"])
        self.assertIn("README.zh-CN.md must link README.md", messages())
        self.edit("README.zh-CN.md", "English · 简体中文", "[English](./README.md)")
        self.assertEqual(self.codes(), [])

    def test_non_object_project_is_reported_without_a_traceback(self):
        index = C.build_index(self.root)
        (self.root.parent / "cases.json").write_text(C.index_text(index), encoding="utf-8", newline="\n")
        (self.case / "project.json").write_text("[]\n", encoding="utf-8", newline="\n")
        with self.assertRaises(ValueError):
            C.T.scale_of([])
        code, out, err = call(["index", "--check", "--root", str(self.root.parent)])
        self.assertEqual((code, err), (1, ""))
        document = json.loads(out)
        self.assertEqual(document["status"], "PROBLEMS")
        self.assertIn({"case": CATALOG_FREE, "code": "PROJECT_INVALID", "message": "project.json must hold a JSON "
                                                                                   "object"}, document["problems"])
        code, out, err = call(["index", "--write", "--root", str(self.root.parent)])
        self.assertEqual((code, json.loads(out)["status"]), (1, "PROBLEMS"))


class FrontMatterTests(unittest.TestCase):
    """The taxonomy fields of CASE.md: vocabularies, counts, order and cross-field rules."""

    def setUp(self):
        self.meta = C.parse_front_matter((CASES / CATALOG_FREE / "CASE.md").read_text(encoding="utf-8"))[0]

    def check(self, meta):
        return sorted({code for code, _message in C.check_front_matter(meta, CATALOG_FREE)})

    def codes(self, **changes):
        meta = dict(self.meta)
        for key, value in changes.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        return self.check(meta)

    def messages(self, **changes):
        return " | ".join(message for _code, message in C.check_front_matter({**self.meta, **changes}, CATALOG_FREE))

    def ordered(self, **changes):
        """The reference front matter with changes, keys in the documented order (source after publication)."""
        merged = {**self.meta, **changes}
        return {key: merged[key] for key in C.FRONT_MATTER_ORDER if key in merged}

    def test_the_reference_case_is_valid(self):
        self.assertEqual(self.codes(), [])
        self.assertEqual(list(self.meta), [k for k in C.FRONT_MATTER_ORDER if k != "source"])

    def test_required_keys_and_order(self):
        for key in ("class", "domain", "provenance", "publication"):
            self.assertEqual(self.codes(**{key: None}), ["FRONT_MATTER_KEY"], key)
        swapped = {k: self.meta[k] for k in ("name", "title", "summary", "domain", "class")}
        swapped.update({k: v for k, v in self.meta.items() if k not in swapped})
        self.assertEqual(self.check(swapped), ["FRONT_MATTER_ORDER"])
        self.assertEqual(self.codes(colour="blue"), ["FRONT_MATTER_KEY"])

    def test_class(self):
        self.assertEqual(self.codes(**{"class": "integrated-path"}), [])
        self.assertEqual(self.codes(**{"class": "free-space"}), [])
        self.assertEqual(self.codes(**{"class": "telescope"}), ["FRONT_MATTER_CLASS"])
        self.assertIn("lens, free-space, integrated-path", self.messages(**{"class": "telescope"}))
        self.assertEqual(self.codes(**{"class": "lens, free-space"}), ["FRONT_MATTER_CLASS"])

    def test_domain(self):
        self.assertEqual(self.codes(domain="gaussian-beam, diffraction, dispersion"), [])
        self.assertEqual(self.codes(domain="optics"), ["FRONT_MATTER_DOMAIN"])
        self.assertEqual(self.codes(domain="imaging, imaging"), ["FRONT_MATTER_DOMAIN"])
        self.assertEqual(self.codes(domain="imaging, diffraction, dispersion, polarization"), ["FRONT_MATTER_DOMAIN"])
        self.assertIn("1 to 3", self.messages(domain="imaging, diffraction, dispersion, polarization"))

    def test_provenance_publication_and_source(self):
        self.assertEqual(self.codes(provenance="lab-notes"), ["FRONT_MATTER_PROVENANCE"])
        self.assertEqual(self.codes(publication="mine"), ["FRONT_MATTER_PUBLICATION"])
        self.assertEqual(self.codes(publication="open-license"), ["FRONT_MATTER_PUBLICATION"])
        self.assertEqual(self.check(self.ordered(source="A book")), ["FRONT_MATTER_SOURCE"])
        self.assertEqual(self.codes(provenance="patent"), ["FRONT_MATTER_PUBLICATION", "FRONT_MATTER_SOURCE"])
        self.assertEqual(self.codes(provenance="patent", publication="permission"), ["FRONT_MATTER_SOURCE"])
        cited = self.ordered(provenance="patent", publication="permission", source="US 1234567 A")
        self.assertEqual(self.check(cited), [])
        self.assertEqual(self.codes(provenance="patent", publication="permission", source="US 1234567 A"),
                         ["FRONT_MATTER_ORDER"])  # source appended after license
        self.assertEqual(self.check({**cited, "source": "x" * (C.T.SOURCE_MAX_CHARS + 1)}), ["FRONT_MATTER_SOURCE"])
        for provenance in C.T.values(C.T.PROVENANCE):
            for publication in C.T.values(C.T.PUBLICATION):
                source = None if provenance == "self-built" else "Ref"
                meta = self.ordered(provenance=provenance, publication=publication, **({"source": source}
                                                                                     if source else {}))
                valid = (provenance == "self-built") == (publication == "own-work")
                self.assertEqual(self.check(meta) == [], valid, (provenance, publication))

    def test_tags(self):
        self.assertEqual(self.codes(tags="interop, scipy, report, mtf, tolerance, sensitivity, alignment, rcwa"), [])
        for bad in ("none", "catalog-free, focusing", "gaussian-beam, focusing", "lens", "self-built",
                    "own-work", "catalog", "my-tag", "focusing, focusing",
                    "interop, scipy, report, mtf, tolerance, sensitivity, alignment, rcwa, optuna"):
            self.assertEqual(self.codes(tags=bad), ["FRONT_MATTER_TAGS"], bad)
        self.assertIn("repeats a front matter field", self.messages(tags="catalog-free"))
        self.assertIn("cases vocabulary", self.messages(tags="my-tag"))

    def test_the_per_case_tag_rows_are_in_the_vocabulary(self):
        rows = ["achromat, mtf, sensitivity, tolerance", "aspheric, wide-field, design-comparison, mtf",
                "full-aperture, mtf", "mid-infrared, manufacturability, mtf",
                "cylindrical-lens, light-sheet, independent-reference", "ideal-lens, focusing, python-api",
                "metasurface, tabulated-thin-lens, model-validation, rcwa, interop, grcwa",
                "beam-expander, lens-swap, parameter-sweep, edit-by-id",
                "beam-expander, focusing, parameter-sweep, sensitivity, tolerance, independent-reference",
                "focusing, requirement-check, placement-tolerance, report",
                "fold-mirror, alignment, parameter-sweep, measurements, independent-reference",
                "waveplate, polarizer, parameter-sweep, independent-reference",
                "beam-splitter, detector, measurements, calibration",
                "beam-expander, optimization, sensitivity, interop, scipy, optuna, salib",
                "aperture-stop, handoff, interop, lightpipes, prysm, poppy",
                "beam-splitter, hong-ou-mandel, linear-optical-network, interop, perceval, piquasso",
                "optimization, handoff, interop", "phase-mask, flat-top, handoff, interop"]
        for tags in rows:
            self.assertEqual(self.codes(tags=tags), [], tags)


class TaxonomyTests(unittest.TestCase):
    def test_scale_of_every_case(self):
        self.assertEqual(sorted(EXPECTED_SCALE), sorted(p.name for p in CASES.iterdir() if p.is_dir()
                                                        and p.name != "__pycache__"))
        for name, (band, elements, sources, branches) in EXPECTED_SCALE.items():
            with self.subTest(case=name):
                self.assertEqual(C.T.scale_of(load(CASES / name / "project.json")),
                                 {"band": band, "elements": elements, "sources": sources, "branches": branches})

    def test_scale_rules(self):
        def project(components, models=()):
            return {"components": components, "optical_models": list(models)}

        group = {"model_id": "G", "model": {"model_kind": "COMPOUND", "members": [
            {"model_id": "STOP"}, {"model_id": "L1"}, {"model_id": "SUB"}]}}
        sub = {"model_id": "SUB", "model": {"model_kind": "COMPOUND", "members": [{"model_id": "L2"},
                                                                                 {"model_id": "L3"}]}}
        stop = {"model_id": "STOP", "model": {"model_kind": "APERTURE_STOP"}}
        leaves = [{"model_id": n, "model": {"model_kind": "SURFACE_SEQUENCE"}} for n in ("L1", "L2", "L3")]
        lens = {"optical_role": "lens", "optical_model_id": "G"}
        self.assertEqual(C.T.scale_of(project([lens], [group, sub, stop] + leaves))["elements"], 3)
        self.assertEqual(C.T.scale_of(project([{"optical_role": "aperture", "optical_model_id": "STOP"}],
                                              [stop]))["elements"], 0)
        self.assertEqual(C.T.scale_of(project([{"optical_role": "lens", "optical_model_id": "missing"}]))["elements"], 1)
        loop = {"model_id": "A", "model": {"model_kind": "COMPOUND", "members": [{"model_id": "A"}]}}
        self.assertEqual(C.T.scale_of(project([{"optical_role": "lens", "optical_model_id": "A"}], [loop]))["elements"],
                         1)
        self.assertEqual(C.T.scale_of(project([{"optical_role": "lens", "product": {"sku": "x"}}] * 4))["band"],
                         "medium")
        self.assertEqual(C.T.scale_of(project([{"optical_role": "splitter", "product": {"sku": "x"}}] * 3))["band"],
                         "large")
        self.assertEqual(C.T.scale_of(project([{"optical_role": "lens"}] * 41))["band"], "very-large")
        self.assertEqual(C.T.scale_of(project([{"optical_role": "splitter"}] * 9))["band"], "very-large")
        self.assertEqual(C.T.scale_of(project([{"optical_role": "source"}] * 20)),
                         {"band": "small", "elements": 0, "sources": 20, "branches": 0})
        self.assertEqual(C.T.scale_of({}), {"band": "small", "elements": 0, "sources": 0, "branches": 0})
        self.assertEqual([C.T.band_of(e, 0) for e in (3, 4, 12, 13, 40, 41)],
                         ["small", "medium", "medium", "large", "large", "very-large"])

    def test_vocabulary_command(self):
        code, out, _err = call(["vocabulary"])
        self.assertEqual(code, 0)
        self.assertNotIn("\n", out.strip(), "compact JSON")
        document = json.loads(out)
        self.assertEqual(document, json.loads(json.dumps(C.T.vocabulary())))
        self.assertEqual([c["value"] for c in document["classes"]], ["lens", "free-space", "integrated-path"])
        self.assertEqual((document["classes"][0]["en"], document["classes"][0]["zh-CN"]), ("Lens", "镜头"))
        self.assertTrue(all(c["description"]["en"] and c["description"]["zh-CN"] for c in document["classes"]))
        self.assertEqual(len(document["domains"]), 12)
        self.assertEqual([p["value"] for p in document["provenance"]],
                         ["literature", "patent", "manufacturer-file", "customer-task", "self-built"])
        self.assertEqual([p["value"] for p in document["publication"]],
                         ["own-work", "open-license", "permission", "public-domain"])
        self.assertEqual([(b["value"], b["max_elements"], b["max_branches"]) for b in document["scale"]],
                         [("small", 3, 0), ("medium", 12, 2), ("large", 40, 8), ("very-large", None, None)])
        tags = {t["value"]: t for t in document["tags"]}
        self.assertEqual(len(tags), len(document["tags"]))
        self.assertEqual((tags["hong-ou-mandel"]["group"], tags["hong-ou-mandel"]["zh-CN"]),
                         ("application", "HOM 干涉"))
        self.assertEqual({t["group"] for t in document["tags"]}, set(document["tag_groups"]))
        self.assertNotIn("catalog-free", tags)
        self.assertFalse(set(tags) & C.T.field_values())
        self.assertEqual({k: document["images"][k] for k in ("max_images", "min_system_views", "max_system_views",
                                                              "max_figures", "max_image_bytes")},
                         {"max_images": 5, "min_system_views": 1, "max_system_views": 3, "max_figures": 3,
                          "max_image_bytes": 400 * 1024})
        for group in ("classes", "domains", "provenance", "publication", "scale", "tags"):
            for term in document[group]:
                self.assertTrue(term["en"] and term["zh-CN"], (group, term))


class ListFilterTests(unittest.TestCase):
    """``cases list`` filters on a checkout holding a classified index."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.checkout = Path(self._tmp.name) / "kit"
        copy_tree([CATALOG_FREE], self.checkout)
        index = C.build_index(self.checkout / "cases")
        other = {**index["cases"][0], "name": "other", "path": "cases/other", "class": "free-space",
                 "domain": ["diffraction", "gaussian-beam"], "catalog": "2026-10-28.1", "tags": ["interop"],
                 "scale": {"band": "medium", "elements": 4, "sources": 1, "branches": 0},
                 "provenance": "literature", "publication": "public-domain"}
        index["cases"].append(other)
        (self.checkout / "cases.json").write_text(C.index_text(index), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, *argv):
        with patch.dict("os.environ", {}, clear=False):
            code, out, err = call(["list", "--root", str(self.checkout), *argv])
        self.assertEqual(code, 0, err)
        return [c["name"] for c in json.loads(out)["cases"]]

    def refused(self, *argv):
        with patch.dict("os.environ", {}, clear=False):
            code, _out, err = call(["list", "--root", str(self.checkout), *argv])
        self.assertEqual(code, 4)
        error = json.loads(err)["error"]
        self.assertEqual(error["code"], "BAD_ARGUMENT")
        return error["message"]

    def test_filters(self):
        self.assertEqual(self.names(), [CATALOG_FREE, "other"])
        self.assertEqual(self.names("--class", "lens"), [CATALOG_FREE])
        self.assertEqual(self.names("--class", "integrated-path"), [])
        self.assertEqual(self.names("--domain", "gaussian-beam"), [CATALOG_FREE, "other"])
        self.assertEqual(self.names("--domain", "diffraction"), ["other"])
        self.assertEqual(self.names("--provenance", "self-built"), [CATALOG_FREE])
        self.assertEqual(self.names("--publication", "public-domain"), ["other"])
        self.assertEqual(self.names("--scale", "small"), [CATALOG_FREE])
        self.assertEqual(self.names("--scale", "medium"), ["other"])
        self.assertEqual(self.names("--tag", "focusing"), [CATALOG_FREE])
        self.assertEqual(self.names("--catalog", "none"), [CATALOG_FREE])
        self.assertEqual(self.names("--catalog", "2026-10-28.1"), ["other"])
        self.assertEqual(self.names("--catalog", "2026-01-01.1"), [])
        self.assertEqual(self.names("--domain", "gaussian-beam", "--class", "free-space", "--tag", "interop"),
                         ["other"])
        self.assertEqual(self.names("--domain", "gaussian-beam", "--tag", "focusing", "--catalog", "2026-10-28.1"), [])

    def test_unknown_values_are_refused_with_the_allowed_values(self):
        self.assertIn("lens, free-space, integrated-path", self.refused("--class", "telescope"))
        self.assertIn("imaging", self.refused("--domain", "optics"))
        self.assertIn("self-built", self.refused("--provenance", "mine"))
        self.assertIn("own-work", self.refused("--publication", "free"))
        self.assertIn("small, medium, large, very-large", self.refused("--scale", "huge"))
        self.assertIn("ideal-lens", self.refused("--tag", "catalog-free"))
        self.assertIn("none or a catalog release", self.refused("--catalog", "latest"))


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
        with patch.object(C._kit_engine, "engine_version", return_value=None):
            code, _out, err = call(["verify", CATALOG_FREE])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "ENGINE_NOT_INSTALLED"))
        self.assertIn('os-optics>=0.13,<0.14', json.loads(err)["error"]["message"])
        with patch.object(C._kit_engine, "engine_version", return_value="0.12.0"):
            code, _out, err = call(["verify", CATALOG_FREE])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "ENGINE_UNSUPPORTED"))


class RootTests(unittest.TestCase):
    def test_root_selects_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "kit"
            (checkout / "cases").mkdir(parents=True)
            shutil.copytree(CASES / CATALOG_FREE, checkout / "cases" / CATALOG_FREE)
            shutil.copyfile(ROOT / "cases.json", checkout / "cases.json")
            with patch.dict("os.environ", {}, clear=False):
                code, out, _ = call(["index", "--check", "--root", str(checkout)])
                document = json.loads(out)
                self.assertEqual(Path(document["root"]).resolve(), checkout.resolve())
                self.assertEqual(document["cases"], 1)
                # the copied index still lists every case: the checkout's own index is stale
                self.assertEqual(code, 1)
                self.assertIn("INDEX_STALE", {problem["code"] for problem in document["problems"]})
                self.assertNotIn("OS_OPTICS_KIT_ROOT", __import__("os").environ)

    def test_root_without_cases_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=False):
            code, _, err = call(["list", "--root", tmp])
        self.assertEqual(code, 4)
        self.assertIn("BAD_ARGUMENT", err)


class VerifyOfflineTests(unittest.TestCase):
    """The parts of verify that need no engine: run identity comparison and --jobs scheduling."""

    MANIFEST = {"catalog_version": None,
                "engine_run": {"engine_version": "0.13.0", "input_hash": {"digest": "e" * 64}},
                "optical_view": {"scene_content_hash_osc1": "s" * 64},
                "project": {"project_hash": {"digest": "p" * 64}}}

    def test_run_identity_and_comparison(self):
        identity = C.run_identity(self.MANIFEST)
        self.assertEqual(identity, {"catalog": None, "engine": "0.13.0", "engine_input_hash": "e" * 64,
                                    "project_hash": "p" * 64, "scene_content_hash": "s" * 64})
        self.assertEqual(C.compare_images_run({**identity, "scene_content_hash": "x"}, self.MANIFEST), [])
        self.assertEqual(C.compare_images_run({**identity, "engine_input_hash": "x"}, self.MANIFEST),
                         [{"field": "images.run.engine_input_hash", "expected": "x", "actual": "e" * 64}])
        self.assertEqual([d["field"] for d in C.compare_images_run(identity, None)],
                         ["images.run.project_hash", "images.run.engine_input_hash"])

    def test_jobs_runs_concurrently_and_keeps_the_order(self):
        import threading
        import time

        active, peak, lock = [0], [0], threading.Lock()

        def fake(name, out_dir, env, *, root=None, update_expected=False):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05 if name == "a" else 0.01)
            with lock:
                active[0] -= 1
            return {"name": name, "result": "MATCH", "run": "x"}

        names = ["a", "b", "c", "d"]
        with patch.object(C, "verify_case", side_effect=fake), tempfile.TemporaryDirectory() as tmp:
            document = C.verify(names, out=tmp, jobs=3)
            self.assertEqual([c["name"] for c in document["cases"]], names)
            self.assertGreater(peak[0], 1)
            peak[0] = 0
            document = C.verify(names, out=tmp, jobs=1)
            self.assertEqual(([c["name"] for c in document["cases"]], peak[0]), (names, 1))
        for bad in (0, -1, "2"):
            with self.assertRaises(C.CaseError):
                C.verify(names, jobs=bad)
        code, _out, err = call(["verify", CATALOG_FREE, "--jobs", "0"])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "BAD_ARGUMENT"))

    def test_a_case_named_twice_is_refused_before_any_run(self):
        with patch.object(C, "verify_case", side_effect=AssertionError("no case may run")), \
                patch.object(C, "engine_command", side_effect=AssertionError("no engine call")), \
                tempfile.TemporaryDirectory() as tmp:
            for jobs in (1, 2):
                with self.assertRaises(C.CaseError) as raised:
                    C.verify(["x", "y", "x"], out=tmp, jobs=jobs)
                self.assertEqual(raised.exception.code, "BAD_ARGUMENT")
                self.assertIn("case named more than once: x", str(raised.exception))
            self.assertEqual(list(Path(tmp).iterdir()), [])
            code, out, err = call(["verify", CATALOG_FREE, CATALOG_FREE, "--jobs", "2"])
        self.assertEqual((code, out, json.loads(err)["error"]["code"]), (4, "", "BAD_ARGUMENT"))


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

    def test_images_run_identity_is_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_tree([CATALOG_FREE], Path(tmp) / "kit")
            images = load(root / CATALOG_FREE / "images.json")
            recorded = images["run"]
            images["run"] = {**recorded, "project_hash": "0" * 64, "scene_content_hash": "1" * 64}
            (root / CATALOG_FREE / "images.json").write_text(C.normalized_json(images), encoding="utf-8",
                                                              newline="\n")
            document = C.verify([CATALOG_FREE], out=Path(tmp) / "runs", root=root)
        entry = document["cases"][0]
        self.assertEqual((document["status"], entry["result"]), ("MISMATCH", "MISMATCH"))
        # the scene content hash is not compared; the project hash is
        self.assertEqual(entry["differences"], [{"field": "images.run.project_hash", "expected": "0" * 64,
                                                 "actual": recorded["project_hash"]}])
        self.assertEqual(entry["actual"], entry["expected"])

    def test_jobs_keeps_the_order(self):
        names = ["focus-requirement", CATALOG_FREE]
        code, out, err = call(["verify", *names, "--jobs", "2"])
        self.assertEqual(code, 0, err + out)
        document = json.loads(out)
        self.assertEqual([c["name"] for c in document["cases"]], names)
        self.assertEqual(document["counts"], {"MATCH": 2, "MISMATCH": 0, "ERROR": 0})

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
