"""The image rules of a case (images.json, budget, README pair) and ``os-optics-kit cases images``.

The format checks and the recording logic need only the standard library: the engine calls of ``cases images`` are
replaced by a fake that writes PNG files. One test renders the reference case with the real engine and is skipped
when ``os_optics`` is not importable.
Run from the repository root: python -m pytest tests/test_case_images.py
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from importlib.util import find_spec
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from os_optics_kit import cases as C

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
HAS_ENGINE = find_spec("os_optics") is not None
NAME = "ideal-lens-focus"
PNG = C.PNG_SIGNATURE + b"\0" * 64


def call(argv):
    out, err = io.BytesIO(), io.StringIO()
    stdout = io.TextIOWrapper(out, encoding="utf-8")
    with redirect_stdout(stdout), redirect_stderr(err):
        code = C.main(argv)
        stdout.flush()
    return code, out.getvalue().decode("utf-8"), err.getvalue()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class CaseTree:
    """A kit-checkout-like copy of the reference case in a temporary directory."""

    def __init__(self, tmp):
        self.root = Path(tmp) / "kit"
        (self.root / "cases").mkdir(parents=True)
        (self.root / "pyproject.toml").write_text("", encoding="utf-8")
        shutil.copytree(CASES / NAME, self.root / "cases" / NAME, ignore=shutil.ignore_patterns("__pycache__"))
        self.case = self.root / "cases" / NAME

    def images(self):
        return load(self.case / "images.json")

    def save(self, document):
        (self.case / "images.json").write_text(C.normalized_json(document), encoding="utf-8", newline="\n")

    def add_png(self, name, data=PNG):
        (self.case / name).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def readmes(self, files, captions=("x", "x")):
        """Show exactly these images, in this order, in both READMEs (replacing the image lines)."""
        for readme, caption in zip(C.README_PAIR, captions):
            path = self.case / readme
            lines = [line for line in path.read_text(encoding="utf-8").split("\n") if not line.startswith("![")]
            at = next(i for i, line in enumerate(lines) if line.startswith("## "))
            lines[at:at] = [f"![{caption}]({file})" for file in files] + [""]
            path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    def codes(self):
        return sorted({p["code"] for p in C.check_case(self.case)})

    def messages(self):
        return " | ".join(p["message"] for p in C.check_case(self.case))


class ImageCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = CaseTree(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assertProblem(self, code, text):
        self.assertEqual(self.tree.codes(), [code], self.tree.messages())
        self.assertIn(text, self.tree.messages())

    def edit(self, change):
        document = self.tree.images()
        change(document)
        self.tree.save(document)

    def test_the_reference_case_is_valid(self):
        self.assertEqual(C.check_case(self.tree.case), [])
        self.assertEqual(C.check_case(CASES / NAME), [])

    # ------------------------------------------------------------------ IMAGES_RECORD

    def test_missing_or_unreadable_record(self):
        (self.tree.case / "images.json").write_text("{", encoding="utf-8")
        self.assertProblem("IMAGES_RECORD", "images.json is not JSON")
        (self.tree.case / "images.json").unlink()
        self.assertProblem("IMAGES_RECORD", "images.json is missing")
        self.assertIn("cases images ideal-lens-focus", self.tree.messages())

    def test_crlf_and_non_utf8_records_are_reported(self):
        text = C.normalized_json(self.tree.images())
        (self.tree.case / "images.json").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertProblem("IMAGES_RECORD", "images.json is not in the normalized form (indent 2, sorted keys, LF")
        (self.tree.case / "images.json").write_bytes(text.encode("utf-8") + b"\xff")
        self.assertIn("IMAGES_RECORD", self.tree.codes())  # no UnicodeDecodeError escapes the check
        self.assertIn("images.json is not JSON in UTF-8", self.tree.messages())
        root = self.tree.root
        (root / "cases.json").write_text(C.index_text(C.build_index(root / "cases")), encoding="utf-8")
        code, out, err = call(["index", "--check", "--root", str(root)])
        self.assertEqual((code, err), (1, ""))
        self.assertIn("IMAGES_RECORD", {p["code"] for p in json.loads(out)["problems"]})

    def test_keys_schema_and_normalized_form(self):
        document = self.tree.images()
        (self.tree.case / "images.json").write_text(json.dumps(document), encoding="utf-8")
        self.assertProblem("IMAGES_RECORD", "normalized form")
        self.edit(lambda d: d.update(schema_version=2))
        self.assertProblem("IMAGES_RECORD", "schema must be os-optics-kit.case-images")
        self.edit(lambda d: d.update(extra=1, schema_version=1))
        self.assertProblem("IMAGES_RECORD", "exactly the keys")

    def test_project_sha256(self):
        self.edit(lambda d: d.update(project_sha256="0" * 64))
        self.assertProblem("IMAGES_RECORD", "project_sha256 is not the SHA-256 of project.json")

    def test_run_block(self):
        self.edit(lambda d: d["run"].update(engine="0.12.0"))
        self.assertProblem("IMAGES_RECORD", "run.engine '0.12.0' differs from the CASE.md engine")
        self.edit(lambda d: d["run"].update(engine="0.13.0", catalog="2026-10-28.1"))
        self.assertProblem("IMAGES_RECORD", "run.catalog '2026-10-28.1' differs from the CASE.md catalog")
        self.edit(lambda d: d["run"].pop("catalog"))
        self.assertProblem("IMAGES_RECORD", "run must have exactly the keys")

    def test_record_shape(self):
        self.edit(lambda d: d["images"][1].pop("options"))
        self.assertIn("must be a system-view", self.tree.messages())
        self.assertIn("IMAGES_RECORD", self.tree.codes())
        self.tree.save(CaseTree(self._tmp.name + "/x").images())
        self.edit(lambda d: d["images"][1].update(kind="plot"))
        self.assertIn("must be a system-view", self.tree.messages())

    def test_record_file_path_duplicates_and_existence(self):
        def bad_path(d):
            d["images"][1]["file"] = "section.png"
        self.edit(bad_path)
        self.assertIn("must be a PNG file directly under images/", self.tree.messages())
        self.assertEqual(self.tree.codes(), ["IMAGES", "IMAGES_RECORD"])  # images/section.png is now unrecorded
        document = CaseTree(self._tmp.name + "/y").images()
        document["images"].append(dict(document["images"][0]))
        self.tree.save(document)
        self.assertIn("images/iso.png is recorded twice", self.tree.messages())
        document["images"].pop()
        document["images"].append({"file": "images/top.png", "kind": "system-view", "options": ["--views", "top"],
                                   "sha256": "0" * 64, "view": "top"})
        self.tree.save(document)
        self.assertIn("images/top.png does not exist", self.tree.messages())

    def test_sha256(self):
        self.edit(lambda d: d["images"][0].update(sha256="0" * 64))
        self.assertProblem("IMAGES_RECORD", "the sha256 of images/iso.png differs from the file")
        self.edit(lambda d: d["images"][0].update(sha256=C.sha256_file(self.tree.case / "images/iso.png")))
        self.tree.add_png("images/iso.png", PNG + b"changed")
        self.assertProblem("IMAGES_RECORD", "the sha256 of images/iso.png differs from the file")

    def test_view_and_options(self):
        self.edit(lambda d: d["images"][0].update(view="perspective"))
        self.assertIn("view 'perspective' is not one of", self.tree.messages())
        self.edit(lambda d: d["images"][0].update(view="top"))
        self.assertIn("options must hold '--views top'", self.tree.messages())
        self.edit(lambda d: d["images"][0].update(view="camera"))
        self.assertIn("options of a camera view must hold '--camera A,E'", self.tree.messages())
        self.edit(lambda d: d["images"][0].update(view="iso", options="--views iso"))
        self.assertIn("options must be a list of strings", self.tree.messages())
        self.edit(lambda d: d["images"][0].update(options=["--views", "iso"]))
        self.edit(lambda d: d["images"][1].update(options=["--size", "1200x420"]))
        self.assertProblem("IMAGES_RECORD", "options of a section view must hold '--section ELEMENT|ALL'")

    def test_options_are_recorded_in_the_replayable_form(self):
        def section(options, file="images/section.png"):
            self.edit(lambda d: d["images"][1].update(options=options, file=file))

        section(["--section", "ALL", "--frame=scene", "--size", "1200x420"])
        self.assertEqual(self.tree.codes(), [])
        for options, text in ((["--section", "ALL", "--frame", "scene", "--size", "1200x420"], "one token '--frame="),
                              (["--section", "ALL", "--frame", "-6,-6,-4,6,6,168"], "one token '--frame="),
                              (["--section", "ALL", "--frame="], "--frame needs a value"),
                              (["--section", "ALL", "--size"], "--size needs a value"),
                              (["--section", "ALL", "--scale", "2"], "option '--scale' is not one of"),
                              (["--section", "ALL", "--size", "1200x420", "--size", "800x400"], "given twice"),
                              (["--section", "ALL", "--views", "iso"], "take only --frame, --section, --size"),
                              (["--section", "ALL", "--size", "big"], "--size 'big' is not WIDTHxHEIGHT"),
                              (["--section", "-L1"], "as one token '--section=-L1'")):
            with self.subTest(options=options):
                section(options)
                self.assertProblem("IMAGES_RECORD", text)
        section(["--section", "ALL", "--frame=-6,-6,-4,6,6,168", "--size", "1360x440"])
        self.assertEqual(self.tree.codes(), [])
        # the file is the one the view writes, so the record can be rendered again exactly
        section(["--section", "L1", "--frame=scene", "--size", "1200x420"])
        self.assertProblem("IMAGES_RECORD", "file 'images/section.png' must be images/section-L1.png")
        self.edit(lambda d: d["images"][0].update(view="camera", options=["--camera", "sixty", "--size", "960x540"]))
        self.assertIn("--camera 'sixty' is not ANGLE,ELEVATION", self.tree.messages())

    def test_every_recorded_option_parses_with_python_311_argparse_semantics(self):
        """CPython 3.11-3.13 argparse reads a separate argument starting with '-' (such as a negative box frame) as
        an option and refuses it; 3.14 accepts it. The recorded options keep every value starting with '-' joined to
        its option, so they replay on every supported Python."""
        def parses_on_311(options):
            return not any(option.startswith("--") and "=" not in option and number + 1 < len(options)
                           and options[number + 1].startswith("-") for number, option in enumerate(options))

        frame = "-6,-6,-4,6,6,168"
        options = C._view_options("section", "ALL", "1360x440", frame)
        self.assertEqual(options, ["--section", "ALL", f"--frame={frame}", "--size", "1360x440"])
        self.assertFalse(parses_on_311(["--section", "ALL", "--frame", frame]))
        self.assertTrue(parses_on_311(options))
        self.assertEqual(C._view_options("camera", "-30,20", "960x540", None), ["--camera=-30,20", "--size", "960x540"])
        self.assertEqual(C._view_options("section", "ALL", "1200x420", "scene"),
                         ["--section", "ALL", "--frame=scene", "--size", "1200x420"])
        parser = argparse.ArgumentParser()
        for option in C.VIEW_OPTIONS:
            parser.add_argument(option)
        self.assertEqual(parser.parse_args(options).frame, frame)
        for case in sorted(p for p in CASES.iterdir() if (p / "images.json").is_file()):
            for record in load(case / "images.json")["images"]:
                if record["kind"] == "system-view":
                    with self.subTest(case=case.name, file=record["file"]):
                        self.assertTrue(parses_on_311(record["options"]), record["options"])
                        self.assertNotIn("--frame", record["options"])
                        C.parse_view_options(record["options"])
                        parsed = vars(parser.parse_args(record["options"]))
                        self.assertEqual({f"--{k}": v for k, v in parsed.items() if v is not None},
                                         C.parse_view_options(record["options"]))

    def test_figure_script(self):
        sha = self.tree.add_png("images/plot.png")
        figure = {"file": "images/plot.png", "kind": "figure", "script": "plot.py", "sha256": sha}
        self.edit(lambda d: d["images"].append(figure))
        self.tree.readmes(["images/iso.png", "images/section.png", "images/plot.png"])
        self.assertProblem("IMAGES_RECORD", "script 'plot.py' must be a .py file of the case")
        self.edit(lambda d: d["images"][2].update(script="ideal_lens_focus.py"))
        self.assertEqual(self.tree.codes(), [])
        self.edit(lambda d: d["images"][2].update(script="README.md"))
        self.assertProblem("IMAGES_RECORD", "must be a .py file of the case")

    # ------------------------------------------------------------------ IMAGES

    def test_png_and_size(self):
        sha = self.tree.add_png("images/plot.png", b"GIF89a")
        self.edit(lambda d: d["images"].append({"file": "images/plot.png", "kind": "figure",
                                                "script": "ideal_lens_focus.py", "sha256": sha}))
        self.tree.readmes(["images/iso.png", "images/section.png", "images/plot.png"])
        self.assertProblem("IMAGES", "images/plot.png must be a PNG file")
        big = PNG + b"\0" * C.IMAGE_MAX_BYTES
        sha = self.tree.add_png("images/plot.png", big)
        self.edit(lambda d: d["images"][2].update(sha256=sha))
        self.assertProblem("IMAGES", f"images/plot.png is {len(big)} bytes")

    def test_unrecorded_files(self):
        self.tree.add_png("images/old.png")
        self.assertProblem("IMAGES", "images/old.png is not recorded in images.json")

    def figures(self, count, views=("iso", "section")):
        records = [r for r in self.tree.images()["images"] if r.get("view") in views]
        for number in range(count):
            sha = self.tree.add_png(f"images/fig{number}.png", PNG + bytes([number]))
            records.append({"file": f"images/fig{number}.png", "kind": "figure", "script": "ideal_lens_focus.py",
                            "sha256": sha})
        self.edit(lambda d: d.update(images=records))
        self.tree.readmes([r["file"] for r in records])
        for name in ("iso", "section"):
            if name not in views:
                (self.tree.case / f"images/{name}.png").unlink(missing_ok=True)

    def test_budget(self):
        self.figures(3)
        self.assertEqual(self.tree.codes(), [])  # 2 system views + 3 figures = 5
        self.figures(4)
        self.assertIn("4 figures; at most 3", self.tree.messages())
        self.assertIn("6 images; at most 5", self.tree.messages())

    def test_system_view_count(self):
        records = self.tree.images()["images"]
        for view in ("top", "side"):
            sha = self.tree.add_png(f"images/{view}.png", PNG + view.encode())
            records.insert(1, {"file": f"images/{view}.png", "kind": "system-view",
                               "options": ["--views", view, "--size", "960x540"], "sha256": sha, "view": view})
        self.edit(lambda d: d.update(images=records))
        self.tree.readmes([r["file"] for r in records])
        self.assertProblem("IMAGES", "4 system views; 1 to 3 per case")

    def test_first_image_is_a_snapshot_and_a_lens_has_a_section(self):
        records = self.tree.images()["images"]
        self.edit(lambda d: d.update(images=records[::-1]))
        self.tree.readmes([r["file"] for r in records[::-1]])
        self.assertProblem("IMAGES", "the first image must be a snapshot view")
        self.edit(lambda d: d.update(images=records[:1]))
        self.tree.readmes(["images/iso.png"])
        (self.tree.case / "images/section.png").unlink()
        self.assertProblem("IMAGES", "a lens case has a section view")
        text = (self.tree.case / "CASE.md").read_text(encoding="utf-8").replace("class: lens", "class: free-space")
        (self.tree.case / "CASE.md").write_text(text, encoding="utf-8", newline="\n")
        self.assertEqual(self.tree.codes(), [])
        self.figures(1, views=())
        self.assertIn("0 system views; 1 to 3", self.tree.messages())
        self.assertIn("the first image must be a snapshot view", self.tree.messages())

    def test_readme_pair_shows_the_record_in_order(self):
        self.tree.readmes(["images/section.png", "images/iso.png"])
        self.assertProblem("IMAGES", "README.md shows ['images/section.png', 'images/iso.png']")
        self.tree.readmes(["images/iso.png"])
        self.assertProblem("IMAGES", "it must show exactly ['images/iso.png', 'images/section.png']")
        self.tree.readmes(["images/iso.png", "images/section.png", "images/iso.png"])
        self.assertProblem("IMAGES", "each once")
        self.tree.readmes(["images/iso.png", "images/section.png"])
        zh = self.tree.case / "README.zh-CN.md"
        zh.write_text(zh.read_text(encoding="utf-8").replace("](images/iso.png)", "](images/top.png)"),
                      encoding="utf-8", newline="\n")
        self.assertIn("README.zh-CN.md shows images/top.png, which does not exist", self.tree.messages())
        self.assertIn("README.zh-CN.md shows ['images/top.png', 'images/section.png']", self.tree.messages())

    def test_html_images_count_and_other_readmes_may_show_existing_images(self):
        readme = self.tree.case / "README.md"
        text = readme.read_text(encoding="utf-8").replace("![3D view", '<img src="images/iso.png" alt="3D"> ![3D view')
        readme.write_text(text, encoding="utf-8", newline="\n")
        self.assertProblem("IMAGES", "each once")
        shutil.copyfile(CASES / NAME / "README.md", readme)
        (self.tree.case / "README.ja.md").write_text("![x](images/section.png)\n", encoding="utf-8")
        self.assertEqual(self.tree.codes(), [])
        (self.tree.case / "README.ja.md").write_text("![x](images/gone.png)\n", encoding="utf-8")
        self.assertProblem("IMAGES", "README.ja.md shows images/gone.png, which does not exist")

    def test_only_displayed_images_count_as_shown(self):
        shown = C.shown_images
        self.assertEqual(shown("[iso](images/iso.png)"), [])
        self.assertEqual(shown("<!-- ![a](images/iso.png) -->"), [])
        self.assertEqual(shown("<!--\n![a](images/iso.png)\n"), [])  # an unclosed comment runs to the end
        self.assertEqual(shown("```\n![a](images/iso.png)\n```\n"), [])
        self.assertEqual(shown("~~~~ md\n![a](images/iso.png)\n~~~\n![b](images/top.png)\n"), [])
        self.assertEqual(shown("```\n![a](images/iso.png)\n"), [])  # an unclosed fence runs to the end
        self.assertEqual(shown("```\nx\n```\n![b](images/top.png)"), ["images/top.png"])
        self.assertEqual(shown("[![a](images/iso.png)](images/iso.png)"), ["images/iso.png"])
        self.assertEqual(shown('![a](images/iso.png "the 3D view")'), ["images/iso.png"])
        self.assertEqual(shown("![a [1]](images/iso.png)"), ["images/iso.png"])
        self.assertEqual(shown('<IMG alt="x" src="images/iso.png">'), ["images/iso.png"])
        self.assertEqual(shown("![a](images/iso.png) and `![b](images/top.png)`"),
                         ["images/iso.png", "images/top.png"])  # inline code is not removed

    def test_links_comments_and_fences_do_not_satisfy_the_readme_pair(self):
        readme = self.tree.case / "README.md"
        original = readme.read_text(encoding="utf-8")
        for change in (lambda t: t.replace("![", "["),
                       lambda t: t.replace("![3D view", "<!-- ![3D view").replace("(images/iso.png)",
                                                                                  "(images/iso.png) -->"),
                       lambda t: t.replace("![3D view", "```\n![3D view").replace("(images/iso.png)",
                                                                                 "(images/iso.png)\n```")):
            readme.write_text(change(original), encoding="utf-8", newline="\n")
            with self.subTest(text=change(original)[:400]):
                self.assertIn("IMAGES", self.tree.codes())
                self.assertIn("README.md shows", self.tree.messages())
        readme.write_text(original.replace("![3D view", "[![3D view").replace("(images/iso.png)",
                                                                              "(images/iso.png)](images/iso.png)"),
                          encoding="utf-8", newline="\n")
        self.assertEqual(self.tree.codes(), [])


class FakeEngine:
    """Stands in for ``cases._engine``: ``project run`` writes project.json and manifest.json, ``view`` writes PNG
    files whose size depends on --size (``large`` sizes give PNGs over the budget)."""

    MANIFEST = {"catalog_version": None,
                "engine_run": {"engine_version": "0.13.0", "input_hash": {"digest": "e" * 64}},
                "optical_view": {"scene_content_hash_osc1": "s" * 64},
                "project": {"project_hash": {"digest": "p" * 64}}}

    def __init__(self, large=()):
        self.large = set(large)
        self.calls = []

    def __call__(self, argv, env, *, json_out=True):
        self.calls.append(list(argv))
        if argv[:2] == ["project", "run"]:
            out = Path(argv[argv.index("--out-dir") + 1])
            out.mkdir(parents=True)
            shutil.copyfile(argv[2], out / "project.json")
            (out / "manifest.json").write_text(json.dumps(self.MANIFEST), encoding="utf-8")
            return 0, None, ""
        if argv[0] == "catalog":
            return 0, {"active": {"catalog_version": "2026-10-28.1", "sha256": "c" * 64}}, ""
        assert argv[0] == "view" and "--no-browser" in argv, argv
        snap = Path(argv[argv.index("--snapshot") + 1])
        snap.mkdir(parents=True, exist_ok=True)
        size = argv[argv.index("--size") + 1]
        data = PNG + size.encode() + (b"\0" * C.IMAGE_MAX_BYTES if size in self.large else b"")
        if "--section" in argv:
            element = argv[argv.index("--section") + 1]
            png = snap / f"section-{element}-XZ.png"
            png.write_bytes(data)
            return 0, {"sections": {element: {"png": str(png)}}}, ""
        camera = "--camera" in argv or any(a.startswith("--camera=") for a in argv)
        name = "camera" if camera else argv[argv.index("--views") + 1]
        (snap / f"{name}.png").write_bytes(data)
        return 0, {"files": {name: str(snap / f"{name}.png")}}, ""

    def views(self):
        return [call[call.index("--snapshot") + 2:] for call in self.calls if call[0] == "view"]


class ImagesCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = CaseTree(self._tmp.name)
        self.engine = FakeEngine()
        patches = [patch.object(C, "_engine", self.engine), patch.object(C, "engine_command", return_value=["e"]),
                   patch.dict("os.environ", {}, clear=False)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def images(self, *argv):
        code, out, err = call(["images", NAME, "--root", str(self.tree.root), *argv])
        return code, (json.loads(out) if out.strip() else None), (json.loads(err)["error"] if err.strip() else None)

    def test_default_views_of_a_lens_case(self):
        (self.tree.case / "images.json").unlink()  # nothing recorded: the defaults
        code, document, error = self.images()
        self.assertEqual((code, error), (0, None))
        self.assertEqual(self.engine.views(), [["--views", "iso", "--size", "960x540"],
                                               ["--section", "ALL", "--frame=scene", "--size", "1200x420"]])
        self.assertEqual([(r["file"], r["view"]) for r in document["images"]],
                         [("images/iso.png", "iso"), ("images/section.png", "section")])
        record = self.tree.images()
        self.assertEqual(record["run"], {"catalog": None, "engine": "0.13.0", "engine_input_hash": "e" * 64,
                                         "project_hash": "p" * 64, "scene_content_hash": "s" * 64})
        self.assertEqual(record["project_sha256"], C.sha256_file(self.tree.case / "project.json"))
        self.assertEqual(record["images"], document["images"])
        self.assertEqual((self.tree.case / "images.json").read_text(encoding="utf-8"), C.normalized_json(record))
        for entry in record["images"]:
            self.assertEqual(entry["sha256"], C.sha256_file(self.tree.case / entry["file"]))
        self.assertEqual(self.tree.codes(), [])
        self.assertEqual((document["removed"], document["unrecorded"], document["warnings"]), ([], [], []))

    def test_views_in_the_order_given_section_last(self):
        code, document, _ = self.images("--section", "LENS", "--view", "top", "--camera", "60,20", "--frame", "scene",
                                        "--size", "800x600", "--section-size", "1000x400")
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.views(), [["--views", "top", "--size", "800x600"],
                                               ["--camera", "60,20", "--size", "800x600"],
                                               ["--section", "LENS", "--frame=scene", "--size", "1000x400"]])
        self.assertEqual([r["file"] for r in document["images"]],
                         ["images/top.png", "images/camera.png", "images/section-LENS.png"])
        # the previous views are replaced and their files removed
        self.assertEqual(document["removed"], ["images/iso.png", "images/section.png"])
        self.assertFalse((self.tree.case / "images/iso.png").exists())
        self.assertIn("IMAGES", self.tree.codes())  # the READMEs still show the old images

    def test_free_space_default_and_lens_warning(self):
        text = (self.tree.case / "CASE.md").read_text(encoding="utf-8").replace("class: lens", "class: free-space")
        (self.tree.case / "CASE.md").write_text(text, encoding="utf-8", newline="\n")
        (self.tree.case / "images.json").unlink()  # nothing recorded: the defaults
        code, document, _ = self.images()
        self.assertEqual([r["view"] for r in document["images"]], ["iso"])
        # explicit options keep the option defaults: no frame unless --frame is given
        code, document, _ = self.images("--size", "800x450")
        self.assertEqual([r["options"] for r in document["images"]], [["--views", "iso", "--size", "800x450"]])
        text = text.replace("class: free-space", "class: lens")
        (self.tree.case / "CASE.md").write_text(text, encoding="utf-8", newline="\n")
        code, document, _ = self.images("--view", "iso")
        self.assertEqual(code, 0)
        self.assertIn("class lens needs a section view", " ".join(document["warnings"]))

    def record_views(self, views, figures=()):
        """Record these system views (and figures) with PNG files, as a hand-made images.json."""
        document = self.tree.images()
        records = []
        for file, view, options in views:
            records.append({"file": file, "kind": "system-view", "options": options,
                            "sha256": self.tree.add_png(file, PNG + file.encode()), "view": view})
        for file in figures:
            records.append({"file": file, "kind": "figure", "script": "ideal_lens_focus.py",
                            "sha256": self.tree.add_png(file, PNG + b"figure" + file.encode())})
        document["images"] = records
        self.tree.save(document)
        return document

    def test_the_bare_command_renders_the_recorded_views_again(self):
        box = "-6,-6,-4,6,6,168"
        self.record_views([("images/top.png", "top", ["--views", "top", "--size", "800x600"]),
                           ("images/camera.png", "camera", ["--camera=-30,20", "--size", "640x480"]),
                           ("images/section-L1.png", "section", ["--section", "L1", "--frame", box,
                                                                 "--size", "1000x400"])],
                          figures=["images/plot.png"])
        (self.tree.case / "images" / "iso.png").unlink()
        (self.tree.case / "images" / "section.png").unlink()
        self.tree.readmes(["images/top.png", "images/camera.png", "images/section-L1.png", "images/plot.png"])
        code, document, error = self.images()
        self.assertEqual((code, error), (0, None))
        expected = [["--views", "top", "--size", "800x600"], ["--camera=-30,20", "--size", "640x480"],
                    ["--section", "L1", f"--frame={box}", "--size", "1000x400"]]
        self.assertEqual(self.engine.views(), expected)
        self.assertEqual([r["file"] for r in document["images"]],
                         ["images/top.png", "images/camera.png", "images/section-L1.png", "images/plot.png"])
        self.assertEqual([r["options"] for r in document["images"][:3]], expected)
        self.assertEqual((document["removed"], document["skipped"], document["warnings"]), ([], [], []))
        self.assertEqual(self.tree.codes(), [])
        # rendering the record again gives the same record; an oversized PNG is redrawn smaller from the recorded size
        self.engine.calls.clear()
        self.engine.large = {"800x600"}
        code, document, _ = self.images()
        self.assertEqual((code, document["images"][0]["options"]), (0, ["--views", "top", "--size", "600x450"]))
        self.assertEqual(self.engine.views()[1:], [["--views", "top", "--size", "600x450"]] + expected[1:])
        # an explicit view option plans from the options instead, as given
        self.engine.calls.clear()
        self.engine.large = set()
        code, document, _ = self.images("--size", "800x450")
        self.assertEqual(self.engine.views(), [["--views", "iso", "--size", "800x450"],
                                               ["--section", "ALL", "--size", "1200x420"]])
        self.assertEqual(document["removed"], ["images/top.png", "images/camera.png", "images/section-L1.png"])

    def test_a_record_that_cannot_be_rendered_again_is_refused(self):
        for views in ([("images/iso.png", "iso", ["--views", "iso", "--scale", "2"])],
                      [("images/iso.png", "iso", ["--views", "iso"]), ("images/top.png", "iso", ["--views", "iso"])],
                      [("images/top.png", "iso", ["--views", "iso"])]):
            with self.subTest(views=views):
                self.record_views(views)
                before = (self.tree.case / "images.json").read_bytes()
                code, _document, error = self.images()
                self.assertEqual((code, error["code"]), (4, "IMAGES_RECORD"))
                self.assertIn("give the views explicitly", error["message"])
                self.assertEqual((self.tree.case / "images.json").read_bytes(), before)
        self.assertEqual(self.engine.calls, [])

    def victims(self):
        """Two files outside the case's images/: one next to the case, one in another directory (absolute)."""
        near = self.tree.root / "cases" / "victim.png"
        far = Path(self._tmp.name) / "elsewhere" / "victim.png"
        far.parent.mkdir()
        for path in (near, far):
            path.write_bytes(PNG + b"victim")
        return near, far

    def test_recorded_paths_outside_images_are_never_deleted_or_hashed(self):
        near, far = self.victims()
        document = self.tree.images()
        for file in ("../victim.png", far.as_posix(), str(far), "images/../../victim.png"):
            document["images"].append({"file": file, "kind": "system-view", "options": ["--views", "top"],
                                       "sha256": "0" * 64, "view": "top"})
            document["images"].append({"file": file, "kind": "figure", "script": "ideal_lens_focus.py",
                                       "sha256": "0" * 64})
        self.tree.save(document)
        # the bare command refuses the record as a whole
        code, _document, error = self.images()
        self.assertEqual((code, error["code"]), (4, "IMAGES_RECORD"))
        # explicit views render; the outside files are reported, left in place and dropped from the record
        code, result, error = self.images("--view", "iso", "--section", "ALL")
        self.assertEqual((code, error), (0, None))
        self.assertEqual(sorted(set(result["skipped"])),
                         sorted({"../victim.png", far.as_posix(), str(far), "images/../../victim.png"}))
        self.assertIn("left untouched", " ".join(result["warnings"]))
        for path in (near, far):
            self.assertEqual(path.read_bytes(), PNG + b"victim")
        self.assertEqual([r["file"] for r in self.tree.images()["images"]], ["images/iso.png", "images/section.png"])
        self.assertEqual(self.tree.codes(), [])

    def test_a_symlinked_image_outside_the_case_is_not_touched(self):
        near, _far = self.victims()
        link = self.tree.case / "images" / "top.png"
        try:
            link.symlink_to(near)
        except OSError:
            self.skipTest("symbolic links are not available")
        self.assertFalse(C._case_image(self.tree.case, "images/top.png"))
        self.assertTrue(C._case_image(self.tree.case, "images/iso.png"))

    def test_a_view_is_never_written_through_a_symbolic_link(self):
        near, _far = self.victims()
        link = self.tree.case / "images" / "iso.png"
        link.unlink()
        try:
            link.symlink_to(near)
        except OSError:
            self.skipTest("symbolic links are not available")
        before = (self.tree.case / "images.json").read_bytes()
        code, _document, error = self.images("--view", "iso")
        self.assertEqual((code, error["code"]), (4, "BAD_ARGUMENT"))
        self.assertIn("images/iso.png is a symbolic link", error["message"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(near.read_bytes(), PNG + b"victim")
        self.assertEqual((self.tree.case / "images.json").read_bytes(), before)

    def test_a_symlinked_images_directory_is_refused_and_never_touched(self):
        outside = Path(self._tmp.name) / "outside"
        shutil.copytree(self.tree.case / "images", outside)
        shutil.rmtree(self.tree.case / "images")
        try:
            (self.tree.case / "images").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symbolic links are not available")
        kept = {p.name: p.read_bytes() for p in outside.iterdir()}
        self.assertFalse(C._case_image(self.tree.case, "images/iso.png"))
        for argv in ((), ("--view", "top")):
            code, _document, error = self.images(*argv)
            self.assertEqual((code, error["code"]), (4, "BAD_ARGUMENT"), argv)
            self.assertIn("images is a symbolic link", error["message"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual({p.name: p.read_bytes() for p in outside.iterdir()}, kept)

    def test_an_unreadable_record_is_refused_before_anything_is_rendered(self):
        for data in (b"{not json", b"\xff\xfe{}", b"[]"):
            (self.tree.case / "images.json").write_bytes(data)
            for argv in ((), ("--view", "iso")):
                code, _document, error = self.images(*argv)
                self.assertEqual((code, error["code"]), (4, "IMAGES_RECORD"), (data, argv))
                self.assertEqual((self.tree.case / "images.json").read_bytes(), data)
        self.assertEqual(self.engine.calls, [])

    def test_names_differing_only_in_case_are_the_same_file(self):
        # Windows and macOS file systems ignore case: Top.png is the file a top view writes
        self.record_views([("images/iso.png", "iso", ["--views", "iso", "--size", "960x540"])])
        self.tree.add_png("images/Top.png")
        code, _document, error = self.images("--figure", "images/Top.png", "--script", "ideal_lens_focus.py")
        self.assertEqual((code, error["code"]), (4, "BAD_ARGUMENT"))
        self.assertIn("a system view writes", error["message"])
        document = self.tree.images()
        document["images"].append({**document["images"][0], "file": "images/ISO.png"})
        self.tree.save(document)
        self.assertIn("IMAGES_RECORD", self.tree.codes())

    def test_a_view_is_never_rendered_over_a_recorded_figure(self):
        self.record_views([("images/iso.png", "iso", ["--views", "iso", "--size", "960x540"])],
                          figures=["images/top.png"])
        figure = (self.tree.case / "images" / "top.png").read_bytes()
        before = (self.tree.case / "images.json").read_bytes()
        code, _document, error = self.images("--view", "iso", "--view", "top")
        self.assertEqual((code, error["code"]), (4, "BAD_ARGUMENT"))
        self.assertIn("images/top.png is recorded as a figure", error["message"])
        self.assertEqual(self.engine.calls, [])
        self.assertEqual((self.tree.case / "images" / "top.png").read_bytes(), figure)
        self.assertEqual((self.tree.case / "images.json").read_bytes(), before)
        # and a figure cannot take a file name a view writes
        self.record_views([("images/iso.png", "iso", ["--views", "iso", "--size", "960x540"])])
        for name in ("images/top.png", "images/camera.png", "images/section.png", "images/section-L1.png"):
            self.tree.add_png(name)
            code, _document, error = self.images("--figure", name, "--script", "ideal_lens_focus.py")
            self.assertEqual((code, error["code"]), (4, "BAD_ARGUMENT"), name)
            self.assertIn("a system view writes", error["message"])

    def test_oversized_png_is_redrawn_smaller_or_refused(self):
        self.engine.large = {"960x540"}
        code, document, _ = self.images("--view", "iso")
        self.assertEqual(code, 0)
        self.assertEqual(document["images"][0]["options"], ["--views", "iso", "--size", "720x405"])
        self.engine.large = {"960x540", "720x405", "480x270"}
        before = (self.tree.case / "images.json").read_bytes()
        code, _document, error = self.images("--view", "iso")
        self.assertEqual((code, error["code"]), (4, "IMAGE_TOO_LARGE"))
        self.assertEqual((self.tree.case / "images.json").read_bytes(), before, "nothing written on a refusal")

    def test_given_run(self):
        run = Path(self._tmp.name) / "run"
        run.mkdir()
        shutil.copyfile(self.tree.case / "project.json", run / "project.json")
        (run / "manifest.json").write_text(json.dumps(FakeEngine.MANIFEST), encoding="utf-8")
        code, _document, _ = self.images("--run", str(run))
        self.assertEqual(code, 0)
        self.assertFalse([c for c in self.engine.calls if c[0] == "project"], "no fresh run")
        (run / "project.json").write_text("{}\n", encoding="utf-8")
        code, _document, error = self.images("--run", str(run))
        self.assertEqual((code, error["code"]), (4, "RUN_NOT_FROM_CASE"))
        code, _document, error = self.images("--run", str(Path(self._tmp.name) / "missing"))
        self.assertEqual((code, error["code"]), (4, "RUN_NOT_FROM_CASE"))

    def test_catalog_is_imported_into_a_private_store(self):
        catalog = Path(self._tmp.name) / "catalog.json"
        catalog.write_text("{}", encoding="utf-8")
        code, _document, _ = self.images("--catalog", str(catalog))
        self.assertEqual(code, 0)
        self.assertEqual(self.engine.calls[0][:2], ["catalog", "import"])

    def test_refusals(self):
        for argv, code in ((["--view", "perspective"], "BAD_ARGUMENT"),
                           (["--view", "iso", "--view", "iso"], "BAD_ARGUMENT"),
                           (["--camera", "60,20", "--camera", "10,0"], "BAD_ARGUMENT"),
                           (["--camera", "sixty"], "BAD_ARGUMENT"),
                           (["--section", "ALL"], "BAD_ARGUMENT"),
                           (["--view", "iso", "--view", "top", "--view", "side", "--section", "ALL"], "IMAGE_BUDGET"),
                           (["--size", "big"], "BAD_ARGUMENT"),
                           (["--figure", "images/iso.png"], "BAD_ARGUMENT"),
                           (["--figure", "images/x.png", "--script", "a.py", "--view", "iso"], "BAD_ARGUMENT")):
            with self.subTest(argv=argv):
                result, _document, error = self.images(*argv)
                self.assertEqual((result, error["code"]), (4, code))
        self.assertEqual(self.engine.calls, [])
        (self.tree.root / "pyproject.toml").unlink()
        result, _document, error = self.images()
        self.assertEqual((result, error["code"]), (4, "NOT_A_CHECKOUT"))

    def test_figures_are_recorded_kept_and_budgeted(self):
        sha = self.tree.add_png("images/plot.png")
        code, document, _ = self.images("--figure", "images/plot.png", "--script", "ideal_lens_focus.py")
        self.assertEqual(code, 0)
        self.assertEqual(document["images"][-1], {"file": "images/plot.png", "kind": "figure",
                                                  "script": "ideal_lens_focus.py", "sha256": sha})
        sha = self.tree.add_png("images/plot.png", PNG + b"redrawn")
        code, document, _ = self.images("--figure", "./images/plot.png", "--script", "ideal_lens_focus.py")
        self.assertEqual((len(document["images"]), document["images"][-1]["sha256"]), (3, sha))
        self.tree.readmes(["images/iso.png", "images/section.png", "images/plot.png"])
        self.assertEqual(self.tree.codes(), [])
        # rendering again keeps the figure after the new system views
        code, document, _ = self.images("--view", "iso", "--section", "ALL")
        self.assertEqual([r["file"] for r in document["images"]],
                         ["images/iso.png", "images/section.png", "images/plot.png"])
        self.assertEqual(self.tree.codes(), [])
        for number in range(2):
            self.tree.add_png(f"images/f{number}.png", PNG + bytes([number]))
            self.assertEqual(self.images("--figure", f"images/f{number}.png", "--script", "ideal_lens_focus.py")[0], 0)
        self.tree.add_png("images/f2.png")
        code, _document, error = self.images("--figure", "images/f2.png", "--script", "ideal_lens_focus.py")
        self.assertEqual((code, error["code"]), (4, "IMAGE_BUDGET"))
        code, _document, error = self.images("--view", "iso", "--view", "top", "--section", "ALL")
        self.assertEqual((code, error["code"]), (4, "IMAGE_BUDGET"))  # 3 views + 3 figures

    def test_figure_refusals(self):
        for figure, script, code in (("images/none.png", "ideal_lens_focus.py", "BAD_ARGUMENT"),
                                     ("images/iso.png", "ideal_lens_focus.py", "BAD_ARGUMENT"),
                                     ("images/plot.png", "missing.py", "BAD_ARGUMENT"),
                                     ("images/plot.png", "README.md", "BAD_ARGUMENT")):
            self.tree.add_png("images/plot.png")
            with self.subTest(figure=figure, script=script):
                result, _document, error = self.images("--figure", figure, "--script", script)
                self.assertEqual((result, error["code"]), (4, code))
        self.tree.add_png("images/plot.png", PNG + b"\0" * C.IMAGE_MAX_BYTES)
        result, _document, error = self.images("--figure", "images/plot.png", "--script", "ideal_lens_focus.py")
        self.assertEqual((result, error["code"]), (4, "IMAGE_TOO_LARGE"))
        (self.tree.case / "images.json").unlink()
        self.tree.add_png("images/plot.png")
        result, _document, error = self.images("--figure", "images/plot.png", "--script", "ideal_lens_focus.py")
        self.assertEqual((result, error["code"]), (4, "IMAGES_RECORD_MISSING"))

    def test_a_figure_before_any_system_view_is_refused(self):
        document = self.tree.images()
        self.tree.add_png("images/plot.png")
        for images in ([], [{"file": "images/old.png", "kind": "figure", "script": "ideal_lens_focus.py",
                             "sha256": self.tree.add_png("images/old.png")}]):
            with self.subTest(images=images):
                self.tree.save({**document, "images": images})
                before = (self.tree.case / "images.json").read_bytes()
                result, _document, error = self.images("--figure", "images/plot.png", "--script",
                                                       "ideal_lens_focus.py")
                self.assertEqual((result, error["code"]), (4, "IMAGES_RECORD_MISSING"))
                self.assertIn("records no system view", error["message"])
                self.assertEqual((self.tree.case / "images.json").read_bytes(), before)


@unittest.skipUnless(HAS_ENGINE, "cases images runs the engine: os_optics is not importable")
class ImagesEngineTests(unittest.TestCase):
    def test_render_the_reference_case(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=False):
            tree = CaseTree(tmp)
            committed = tree.images()
            code, out, err = call(["images", NAME, "--root", str(tree.root), "--view", "iso", "--section", "ALL",
                                   "--frame", "scene"])
            self.assertEqual(code, 0, err)
            document = json.loads(out)
            record = tree.images()
            self.assertEqual([r["options"] for r in record["images"]], [r["options"] for r in committed["images"]])
            for key in ("project_hash", "engine_input_hash", "engine", "catalog"):
                self.assertEqual(record["run"][key], committed["run"][key], key)
            for entry in record["images"]:
                data = (tree.case / entry["file"]).read_bytes()
                self.assertTrue(data.startswith(C.PNG_SIGNATURE))
                self.assertLessEqual(len(data), C.IMAGE_MAX_BYTES)
            self.assertEqual(document["warnings"], [])
            self.assertEqual(C.check_case(tree.case), [])

    def test_the_bare_command_renders_the_recorded_views_again(self):
        """``cases images NAME`` with no view option renders the committed record again: the same files, views and
        options, valid PNGs within the budget, a run identity equal to a fresh run of the case, and a sha256 per
        image that matches the file written. PNG bytes are not compared with the committed ones: engine snapshots
        are not byte-identical across operating systems and font stacks; the sha256 is a file-integrity record."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=False):
            tree = CaseTree(tmp)
            committed = tree.images()
            code, out, err = call(["images", NAME, "--root", str(tree.root)])
            self.assertEqual(code, 0, err)
            record = tree.images()
            self.assertEqual([(r["file"], r["kind"], r["view"], r["options"]) for r in record["images"]],
                             [(r["file"], r["kind"], r["view"], r["options"]) for r in committed["images"]])
            for entry in record["images"]:
                data = (tree.case / entry["file"]).read_bytes()
                self.assertTrue(data.startswith(C.PNG_SIGNATURE), entry["file"])
                self.assertLessEqual(len(data), C.IMAGE_MAX_BYTES, entry["file"])
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest(), entry["file"])
            # the run identity of the committed record and of the new one equal a fresh run of project.json
            run = Path(tmp) / "fresh-run"
            done = subprocess.run(C.engine_command() + ["project", "run", str(tree.case / "project.json"),
                                                        "--out-dir", str(run)], capture_output=True, text=True)
            self.assertIn(done.returncode, (0, 1, 2, 3), done.stderr)
            fresh = C.run_identity(json.loads((run / "manifest.json").read_text(encoding="utf-8")))
            for key in ("project_hash", "engine_input_hash", "engine", "catalog"):
                self.assertEqual(committed["run"][key], fresh[key], key)
                self.assertEqual(record["run"][key], fresh[key], key)
            self.assertEqual(record["project_sha256"], committed["project_sha256"])
            self.assertEqual((json.loads(out)["removed"], C.check_case(tree.case)), ([], []))


if __name__ == "__main__":
    unittest.main()
