"""os-optics-kit sfr: the slanted-edge e-SFR against an analytic truth, its diagnostics, its refusals, its files and
the comparison with a saved design MTF (no engine run needed)."""

import csv
import hashlib
import json
import math
import unittest

from tests.workflow_fixtures import HAS_NUMPY, NUMPY_MISSING, ROOT, TempDir, invoke, requires_matplotlib

if not HAS_NUMPY:  # the unit-test job without the engine has no numpy
    raise unittest.SkipTest(NUMPY_MISSING)

import numpy as np  # noqa: E402

from os_optics_kit import sfr as S  # noqa: E402

DESIGN = ROOT / "tests" / "data" / "sfr-design-imaging.json"  # trimmed 'os-optics results imaging' of a real run

# Declared tolerances, all against true_sfr (Gaussian PSF x square pixel, exact):
SFR_TOLERANCE = 0.025        # max |measured - true| over 0..0.5 cy/px, noise-free
ANGLE_TOLERANCE_DEG = 0.01   # noise-free edge angle
NOISY_ANGLE_TOLERANCE_DEG = 0.1
SFR50_TOLERANCE = 0.012      # cy/px, noise-free
# noise SD (contrast 0.6) -> max |error| over 0..0.5 cy/px for each of five seeds
NOISE_BOUNDS = {0.005: 0.035, 0.01: 0.06, 0.02: 0.12}


def errors(result, sigma, angle, f_max=S.NYQUIST_CY_PER_PX):
    frequency = result["frequency"]
    inside = frequency <= f_max + 1e-12
    return np.abs(result["sfr"][inside] - S.true_sfr(frequency[inside], sigma, angle))


def true_sfr50(sigma, angle):
    grid = np.linspace(0, 1, 200001)
    return grid[np.argmax(S.true_sfr(grid, sigma, angle) < 0.5)]


def run(argv):
    return invoke(S.main, [str(arg) for arg in argv])


class SyntheticEdgeTests(unittest.TestCase):
    def test_generator_integrates_pixels_exactly(self):
        # a sharp, untilted edge through column 10.25: column 10 holds a quarter of the step
        image = S.synthetic_edge((4, 20), 0.0, 0.0, low=0.0, high=1.0, centre=(1.5, 10.25))
        np.testing.assert_allclose(image[0, 9:12], [0.0, 0.25, 1.0], atol=1e-12)
        # blur keeps the mean: the pixel integral of a Gaussian edge is symmetric about it
        blurred = S.synthetic_edge((4, 41), 0.0, 1.0, low=0.0, high=1.0)
        self.assertAlmostEqual(float(blurred[0].sum()), 20.5, places=9)
        self.assertAlmostEqual(float(S.true_sfr(0.5, 0.0)), 2 / math.pi, places=12)

    def test_noise_free_sfr_matches_the_analytic_truth(self):
        for orientation in ("vertical", "horizontal"):
            for angle in (3.0, 5.0, 8.0, -6.0):
                for sigma in (0.3, 0.5, 1.0):
                    with self.subTest(orientation=orientation, angle=angle, sigma=sigma):
                        image = S.synthetic_edge((100, 80), angle, sigma, orientation=orientation)
                        result = S.measure_sfr(image)
                        self.assertEqual(result["orientation"], orientation)
                        self.assertLess(errors(result, sigma, angle).max(), SFR_TOLERANCE)
                        self.assertLess(abs(result["angle_deg"] - angle), ANGLE_TOLERANCE_DEG)
                        self.assertLess(abs(S.crossing(result["frequency"], result["sfr"]) -
                                            true_sfr50(sigma, angle)), SFR50_TOLERANCE)
                        self.assertEqual(int(result["empty"].sum()), 0)

    def test_polarity_fit_order_and_offset_edges_agree(self):
        reference = S.measure_sfr(S.synthetic_edge((100, 80), 5.0, 0.5))
        variants = {
            "bright_to_dark": S.synthetic_edge((100, 80), 5.0, 0.5, low=0.8, high=0.2),
            "fit_order_2": S.synthetic_edge((100, 80), 5.0, 0.5),
            "edge_off_centre": S.synthetic_edge((100, 80), 5.0, 0.5, centre=(49.5, 33.3)),
        }
        for name, image in variants.items():
            with self.subTest(variant=name):
                result = S.measure_sfr(image, fit_order=2 if name == "fit_order_2" else 1)
                self.assertLess(errors(result, 0.5, 5.0).max(), SFR_TOLERANCE)
        self.assertEqual(S.measure_sfr(variants["bright_to_dark"])["polarity"], "bright_to_dark")
        self.assertEqual(reference["polarity"], "dark_to_bright")

    def test_noise_degrades_gracefully(self):
        previous = 0.0
        for noise, bound in NOISE_BOUNDS.items():
            worst = []
            for seed in range(5):
                result = S.measure_sfr(S.synthetic_edge((100, 80), 5.0, 0.5, noise_sd=noise, seed=seed))
                worst.append(errors(result, 0.5, 5.0).max())
                self.assertLess(abs(result["angle_deg"] - 5.0), NOISY_ANGLE_TOLERANCE_DEG)
                self.assertAlmostEqual(result["noise_sd"], noise, delta=0.3 * noise)
            with self.subTest(noise=noise):
                self.assertLess(max(worst), bound)
                self.assertGreater(float(np.mean(worst)), previous)  # more noise, larger error
            previous = float(np.mean(worst))

    def test_the_derivative_correction_is_applied_and_bounded(self):
        result = S.measure_sfr(S.synthetic_edge((100, 80), 5.0, 0.5))
        expected = 1 / np.sinc(2 * result["frequency"] * 0.25)
        np.testing.assert_allclose(result["correction"], expected, rtol=1e-12)
        self.assertAlmostEqual(float(result["frequency"][-1]), 1.0, delta=float(result["frequency"][1]))


class DiagnosticsTests(unittest.TestCase):
    def analyse(self, image, *extra, suffix=".npy"):
        with TempDir() as tmp:
            path = tmp / f"edge{suffix}"
            if suffix == ".npy":
                np.save(path, image)
            else:
                path.write_text("\n".join(",".join(f"{v:.9g}" for v in row) for row in image), encoding="utf-8")
            code, summary, err = run([path, "--out", tmp / "out", "--no-plot", *extra])
        self.assertIn(code, (0, 2), err)
        return code, summary

    def codes(self, summary):
        return [issue["code"] for issue in summary["issues"]]

    def test_clean_edge_passes(self):
        code, summary = self.analyse(S.synthetic_edge((100, 80), 5.0, 0.5))
        self.assertEqual((code, summary["status"], summary["issues"]), (0, "PASS", []))
        self.assertEqual(summary["edge"]["orientation"], "vertical")
        self.assertEqual(summary["edge"]["sfr_direction"], "x (columns)")
        self.assertAlmostEqual(summary["sfr"]["sfr50_cy_per_px"], true_sfr50(0.5, 5.0), delta=SFR50_TOLERANCE)
        self.assertAlmostEqual(summary["sfr"]["sfr_at_nyquist"], float(S.true_sfr(0.5, 0.5, 5.0)),
                               delta=SFR_TOLERANCE)

    def test_horizontal_edge_from_csv(self):
        _code, summary = self.analyse(S.synthetic_edge((80, 100), 4.0, 0.7, orientation="horizontal"),
                                      suffix=".csv")
        self.assertEqual(summary["edge"]["orientation"], "horizontal")
        self.assertEqual(summary["edge"]["sfr_direction"], "y (rows)")
        self.assertAlmostEqual(summary["edge"]["angle_deg"], 4.0, delta=ANGLE_TOLERANCE_DEG)
        self.assertFalse(summary["clipping"]["checked"])  # float data without --full-scale

    def test_shallow_and_steep_angles_are_flagged(self):
        _code, summary = self.analyse(S.synthetic_edge((100, 80), 1.0, 0.5))
        self.assertIn("EDGE_ANGLE_SHALLOW", self.codes(summary))
        _code, summary = self.analyse(S.synthetic_edge((60, 160), 40.0, 0.5))
        self.assertIn("EDGE_ANGLE_STEEP", self.codes(summary))

    def test_untilted_edge_is_unknown(self):
        code, summary = self.analyse(S.synthetic_edge((100, 80), 0.0, 0.5))
        self.assertEqual((code, summary["status"]), (2, "UNKNOWN"))
        self.assertEqual(self.codes(summary)[0], "INSUFFICIENT_PHASE_COVERAGE")
        self.assertIn("EMPTY_BINS", self.codes(summary))
        self.assertGreater(summary["esf"]["empty_bins"], summary["esf"]["bins"] // 2)

    def test_clipping_few_rows_low_contrast_and_short_span(self):
        clipped = np.clip(np.round(S.synthetic_edge((100, 80), 5.0, 0.5, low=0.0, high=1.2) * 255), 0, 255)
        _code, summary = self.analyse(clipped.astype(np.uint8))
        self.assertIn("CLIPPED_PIXELS", self.codes(summary))
        self.assertGreater(summary["clipping"]["at_full_scale"], 0)
        self.assertEqual(summary["clipping"]["full_scale"], 255.0)
        _code, summary = self.analyse(S.synthetic_edge((14, 80), 5.0, 0.5))
        self.assertIn("FEW_ROWS", self.codes(summary))
        _code, summary = self.analyse(S.synthetic_edge((100, 80), 5.0, 0.5, low=0.45, high=0.55))
        self.assertIn("LOW_EDGE_CONTRAST", self.codes(summary))
        _code, summary = self.analyse(S.synthetic_edge((100, 20), 5.0, 0.5))
        self.assertIn("SHORT_ESF_SPAN", self.codes(summary))

    def test_user_frequencies_and_pixel_pitch(self):
        _code, summary = self.analyse(S.synthetic_edge((100, 80), 5.0, 0.5), "--pixel-pitch-um", "4",
                                      "--at", "0.1,0.25", "--at-mm", "50")
        sfr = summary["sfr"]
        self.assertAlmostEqual(sfr["nyquist_cy_per_mm"], 125.0)
        self.assertAlmostEqual(sfr["sfr50_cy_per_mm"], sfr["sfr50_cy_per_px"] / 0.004, delta=1e-3)
        at = sfr["at"]
        self.assertEqual([row["frequency_cy_per_px"] for row in at], [0.1, 0.25, 0.2])
        self.assertEqual([row["frequency_cy_per_mm"] for row in at], [25.0, 62.5, 50.0])
        for row in at:
            self.assertAlmostEqual(row["sfr"], float(S.true_sfr(row["frequency_cy_per_px"], 0.5, 5.0)),
                                   delta=SFR_TOLERANCE)


class ImageInputTests(unittest.TestCase):
    def test_png_and_tiff_with_oecf_and_channels(self):
        from PIL import Image

        truth = S.synthetic_edge((100, 80), 5.0, 0.5)
        encoded = truth ** (1 / 2.2)
        cases = {
            "grey16.png": (Image.fromarray(np.round(truth * 65535).astype(np.uint16)), []),
            "grey16.tif": (Image.fromarray(np.round(truth * 65535).astype(np.uint16)), []),
            "gamma8.png": (Image.fromarray(np.round(encoded * 255).astype(np.uint8)), ["--oecf", "gamma",
                                                                                       "--gamma", "2.2"]),
            "rgb8.png": (Image.fromarray(np.round(np.dstack([truth] * 3) * 255).astype(np.uint8)), []),
            "green8.png": (Image.fromarray(np.round(np.dstack([truth * 0, truth, truth * 0]) * 255).astype(
                np.uint8)), ["--channel", "G"]),
        }
        with TempDir() as tmp:
            for name, (image, extra) in cases.items():
                with self.subTest(input=name):
                    path = tmp / name
                    image.save(path)
                    code, summary, err = run([path, "--out", tmp / f"out-{name}", "--no-plot", *extra])
                    self.assertEqual(code, 0, err)
                    self.assertAlmostEqual(summary["edge"]["angle_deg"], 5.0, delta=0.02)
                    self.assertAlmostEqual(summary["sfr"]["sfr50_cy_per_px"], true_sfr50(0.5, 5.0), delta=0.01)
                    provenance = json.loads((tmp / f"out-{name}" / "summary.json").read_text(encoding="utf-8"))[
                        "provenance"]
                    self.assertEqual(provenance["input"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(provenance["input"]["bit_depth"], 8)
            self.assertIn("channel G", provenance["method"]["channel"])

    def test_linearize(self):
        values = np.array([0.0, 64.0, 255.0])
        np.testing.assert_allclose(S.linearize(values, "gamma", gamma=2.0, full_scale=255.0),
                                   (values / 255) ** 2)
        srgb = S.linearize(np.array([0.0, 0.04045 * 255, 255.0]), "srgb", full_scale=255.0)
        np.testing.assert_allclose(srgb, [0.0, 0.04045 / 12.92, 1.0])
        table = np.array([[0.0, 0.0], [100.0, 0.5], [255.0, 1.0]])
        np.testing.assert_allclose(S.linearize(np.array([50.0, 177.5]), "table", table=table), [0.25, 0.75])


class RefusalTests(unittest.TestCase):
    """Bad inputs exit 4 with a JSON error code on stderr, nothing on stdout and no output directory."""

    def refuse(self, code, argv, setup=None):
        with TempDir() as tmp:
            edge = tmp / "edge.npy"
            np.save(edge, S.synthetic_edge((100, 80), 5.0, 0.5))
            if setup:
                setup(tmp)
            argv = [str(a).replace("EDGE", str(edge)).replace("TMP", str(tmp)) for a in argv]
            exit_code, summary, err = run(argv + ["--out", str(tmp / "out")])
            out_exists = (tmp / "out").exists() and any((tmp / "out").iterdir())
        self.assertEqual((exit_code, summary), (4, None), err)
        self.assertEqual(json.loads(err)["error"]["code"], code, err)
        self.assertFalse(out_exists)
        return json.loads(err)["error"]["message"]

    def test_input_refusals(self):
        flat = lambda tmp: np.save(tmp / "flat.npy", np.full((50, 50), 0.5))  # noqa: E731
        nan = lambda tmp: np.save(tmp / "nan.npy", np.where(np.eye(50) > 0, np.nan, 0.5))  # noqa: E731
        cube = lambda tmp: np.save(tmp / "cube.npy", np.zeros((5, 5, 5)))  # noqa: E731
        text = lambda tmp: (tmp / "image.jpg").write_bytes(b"x")  # noqa: E731
        broken = lambda tmp: (tmp / "broken.png").write_bytes(b"not a png")  # noqa: E731

        def palette(tmp):
            from PIL import Image

            Image.fromarray(np.zeros((40, 40), dtype=np.uint8)).convert("P").save(tmp / "palette.png")

        cases = [
            ("INPUT_NOT_FOUND", ["TMP/missing.npy"], None),
            ("UNSUPPORTED_INPUT", ["TMP/image.jpg"], text),
            ("INPUT_UNREADABLE", ["TMP/broken.png"], broken),
            ("UNSUPPORTED_IMAGE_MODE", ["TMP/palette.png"], palette),
            ("INPUT_NOT_2D", ["TMP/cube.npy"], cube),
            ("EDGE_NOT_FOUND", ["TMP/flat.npy"], flat),
            ("NONFINITE_VALUES", ["TMP/nan.npy"], nan),
            ("ROI_INVALID", ["EDGE", "--roi", "0,0,100,100"], None),
            ("ROI_INVALID", ["EDGE", "--roi", "1,2,3"], None),
            ("ROI_TOO_SMALL", ["EDGE", "--roi", "30,0,10,100"], None),
            ("EDGE_OUTSIDE_ROI", ["EDGE", "--roi", "0,0,42,100"], None),
            ("OECF_NEEDS_FULL_SCALE", ["EDGE", "--oecf", "gamma", "--gamma", "2.2"], None),
            ("BAD_ARGUMENT", ["EDGE", "--oecf", "gamma"], None),
            ("BAD_ARGUMENT", ["EDGE", "--channel", "R"], None),
            ("BAD_ARGUMENT", ["EDGE", "--design-unit", "cy/mm"], None),
            ("BAD_ARGUMENT", ["EDGE", "--fit-order", "3"], None),
            ("PIXEL_PITCH_REQUIRED", ["EDGE", "--at-mm", "10"], None),
        ]
        for code, argv, setup in cases:
            with self.subTest(code=code, argv=argv):
                self.refuse(code, argv, setup)

    def test_design_refusals(self):
        def design_csv(tmp):
            (tmp / "design.csv").write_text("frequency,mtf\n0,1\n50,0.5\n", encoding="utf-8")

        cases = [
            ("PIXEL_PITCH_REQUIRED", ["EDGE", "--design", DESIGN, "--design-line", "SRC0#0"], None),
            ("DESIGN_SELECTION_REQUIRED", ["EDGE", "--design", DESIGN, "--pixel-pitch-um", "5"], None),
            ("DESIGN_SELECTION_UNKNOWN", ["EDGE", "--design", DESIGN, "--design-line", "SRC9",
                                          "--pixel-pitch-um", "5"], None),
            ("DESIGN_CURVE_UNAVAILABLE", ["EDGE", "--design", DESIGN, "--design-line", "SRC0",
                                          "--design-curve", "geometric_mtf", "--pixel-pitch-um", "5"], None),
            ("DESIGN_UNIT_REQUIRED", ["EDGE", "--design", "TMP/design.csv"], design_csv),
            ("DESIGN_INVALID", ["EDGE", "--design", ROOT / "baseline.json", "--pixel-pitch-um", "5"], None),
            ("NO_FREQUENCY_OVERLAP", ["EDGE", "--design", DESIGN, "--design-line", "SRC0",
                                      "--pixel-pitch-mm", "1e-6"], None),
        ]
        for code, argv, setup in cases:
            with self.subTest(code=code):
                self.refuse(code, argv, setup)
        self.assertIn("GEOMETRIC_REGIME_NOT_MET", self.refuse(
            "DESIGN_CURVE_UNAVAILABLE", ["EDGE", "--design", DESIGN, "--design-line", "SRC0",
                                         "--design-curve", "geometric_mtf", "--pixel-pitch-um", "5"]))
        self.assertIn("SRC0#0 (587.5618 nm)", self.refuse(
            "DESIGN_SELECTION_REQUIRED", ["EDGE", "--design", DESIGN, "--pixel-pitch-um", "5"]))

    def test_existing_output_needs_overwrite(self):
        with TempDir() as tmp:
            edge = tmp / "edge.npy"
            np.save(edge, S.synthetic_edge((100, 80), 5.0, 0.5))
            out = tmp / "out"
            out.mkdir()
            (out / "mine.txt").write_text("keep", encoding="utf-8")
            code, summary, err = run([edge, "--out", out, "--no-plot"])
            self.assertEqual((code, summary, json.loads(err)["error"]["code"]), (4, None, "OUTPUT_EXISTS"))
            (out / "comparison.csv").write_text("stale", encoding="utf-8")
            code, summary, err = run([edge, "--out", out, "--no-plot", "--overwrite"])
            self.assertEqual(code, 0, err)
            self.assertFalse((out / "comparison.csv").exists())  # a stale artifact of an earlier run is removed
            self.assertEqual((out / "mine.txt").read_text(encoding="utf-8"), "keep")


class FilesTests(unittest.TestCase):
    def test_bundle_without_plot(self):
        with TempDir() as tmp:
            edge = tmp / "edge.npy"
            image = np.pad(S.synthetic_edge((100, 80), 6.0, 0.5), ((5, 5), (20, 10)), mode="edge")
            np.save(edge, image)
            code, summary, err = run([edge, "--out", tmp / "out", "--roi", "20,5,80,100", "--no-plot",
                                      "--pixel-pitch-mm", "0.003"])
            self.assertEqual(code, 0, err)
            out = tmp / "out"
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ["edge.csv", "esf.csv", "lsf.csv", "sfr.csv", "summary.json"])
            self.assertEqual(set(summary["artifacts"]), {"sfr_csv", "esf_csv", "lsf_csv", "edge_csv", "summary"})
            self.assertAlmostEqual(summary["edge"]["position_at_roi_centre_px"], 20 + 39.5, delta=0.01)
            with (out / "sfr.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(list(rows[0]), ["frequency_cy_per_px", "frequency_cy_per_mm", "sfr",
                                             "derivative_correction"])
            self.assertEqual(float(rows[0]["sfr"]), 1.0)
            self.assertAlmostEqual(float(rows[5]["frequency_cy_per_mm"]), float(rows[5]["frequency_cy_per_px"])
                                   / 0.003, delta=1e-3)
            with (out / "esf.csv").open(encoding="utf-8") as stream:
                esf = list(csv.DictReader(stream))
            self.assertEqual(len(esf), summary["esf"]["bins"])
            self.assertEqual({row["filled"] for row in esf}, {"false"})
            full = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual({key: value for key, value in full.items() if key != "provenance"}, summary)
            provenance = full["provenance"]
            self.assertEqual(provenance["input"]["sha256"], hashlib.sha256(edge.read_bytes()).hexdigest())
            self.assertEqual(provenance["settings"]["roi"], [20, 5, 80, 100])
            self.assertFalse(provenance["software"]["engine_required"])
            self.assertIn("1/4 px bin average", provenance["method"]["not_corrected"])

    @requires_matplotlib
    def test_plots(self):
        with TempDir() as tmp:
            edge = tmp / "edge.npy"
            np.save(edge, S.synthetic_edge((100, 80), 5.0, 0.5))
            code, summary, err = run([edge, "--out", tmp / "out", "--pixel-pitch-um", "5", "--design", DESIGN,
                                      "--design-line", "SRC1#0"])
            self.assertEqual(code, 0, err)
            for name in ("sfr.png", "comparison.png"):
                self.assertEqual((tmp / "out" / name).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.edge = self.tmp / "edge.npy"
        np.save(self.edge, S.synthetic_edge((100, 80), 5.0, 0.5))

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def compare(self, *extra):
        out = self.tmp / f"out{len(list(self.tmp.iterdir()))}"
        code, summary, err = run([self.edge, "--out", out, "--no-plot", *extra])
        self.assertEqual(code, 0, err)
        with (out / "comparison.csv").open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        return summary, json.loads((out / "comparison.json").read_text(encoding="utf-8")), rows

    def test_engine_curve_on_the_measured_grid(self):
        summary, document, rows = self.compare("--design", DESIGN, "--design-line", "SRC0#0", "--pixel-pitch-um", "5")
        fixture = json.loads(DESIGN.read_text(encoding="utf-8"))["arrays"]
        native_f = np.array(fixture["SRC0#0.frequencies_cyc_per_mm"])
        tangential = np.array(fixture["SRC0#0.mtf_tangential"])
        self.assertEqual(document["design"]["frequency_unit"], "cy/mm")
        self.assertEqual((document["design"]["source_id"], document["design"]["wavelength_nm"]), ("SRC0", 587.5618))
        self.assertEqual(document["design"]["identity"]["engine_input_hash"][:8], "527c41c2")
        self.assertEqual(document["design"]["not_included"], ["pixel aperture", "sampling", "image processing"])
        aspects = {row["aspect"]: row for row in document["input_differences"]}
        self.assertEqual(aspects["pixel aperture"], {"aspect": "pixel aperture", "measured": "included",
                                                     "design": "not included"})
        self.assertIn("sign", aspects)
        # the grid: measured frequencies within the design range, cy/mm = cy/px / pitch
        grid_px = np.array([float(row["frequency_cy_per_px"]) for row in rows])
        grid_mm = np.array([float(row["frequency_cy_per_mm"]) for row in rows])
        np.testing.assert_allclose(grid_mm, grid_px / 0.005, rtol=1e-6)
        self.assertGreaterEqual(grid_mm.min(), native_f[0] - 1e-9)
        self.assertLessEqual(grid_mm.max(), native_f[-1] + 1e-9)
        # the design value is the engine curve, interpolated, with nothing multiplied in
        design = np.array([float(row["design_tangential"]) for row in rows])
        np.testing.assert_allclose(design, np.interp(grid_mm, native_f, tangential), rtol=1e-6, atol=1e-9)
        native = document["curves"]["tangential"]["at_design_frequencies"]
        self.assertEqual([row["design"] for row in native], tangential.tolist())
        nyquist = document["curves"]["tangential"]["design_abs_at_nyquist"]
        self.assertAlmostEqual(nyquist, float(tangential[native_f == 100.0][0]), places=6)
        measured = np.array([float(row["measured_sfr"]) for row in rows])
        difference = np.array([float(row["measured_minus_design_tangential_abs"]) for row in rows])
        np.testing.assert_allclose(difference, measured - np.abs(design), atol=1e-8)
        self.assertEqual(summary["comparison"]["scalars"]["tangential"]["points"], len(rows))
        self.assertIn("not included", summary["comparison"]["statement"])

    def test_utf16_redirected_engine_json_is_read(self):
        utf16 = self.tmp / "imaging-utf16.json"
        utf16.write_bytes(DESIGN.read_text(encoding="utf-8").encode("utf-16"))
        _summary, document, rows = self.compare("--design", utf16, "--design-line", "SRC1", "--pixel-pitch-um", "5",
                                                "--design-direction", "sagittal")
        self.assertEqual(document["design"]["line"], "SRC1#0")
        self.assertEqual(list(document["curves"]), ["sagittal"])
        self.assertIn("design_sagittal_abs", rows[0])

    def test_signed_csv_curve_in_cycles_per_pixel(self):
        design = self.tmp / "design.csv"
        design.write_text("f_cy_per_px,mtf\n0,1\n0.2,0.4\n0.3,-0.1\n0.4,-0.05\n0.6,0.02\n", encoding="utf-8")
        summary, document, rows = self.compare("--design", design, "--design-unit", "cy/px")
        self.assertEqual(document["curves"]["csv"]["negative_design_frequencies"], [0.3, 0.4])
        self.assertIn("DESIGN_NEGATIVE", [issue["code"] for issue in summary["comparison"]["issues"]])
        self.assertNotIn("frequency_cy_per_mm", rows[0])  # no pitch: no cycles/mm column
        by_f = {float(row["frequency_cy_per_px"]): row for row in rows}
        f = min(by_f, key=lambda value: abs(value - 0.3))
        signed = float(by_f[f]["design_csv"])
        self.assertLess(signed, 0)
        self.assertAlmostEqual(float(by_f[f]["design_csv_abs"]), -signed)
        self.assertLessEqual(max(by_f), 0.6 + 1e-9)


if __name__ == "__main__":
    unittest.main()
