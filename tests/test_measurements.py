"""Independent analytical checks and artifact/CLI regressions for measurements."""

from dataclasses import replace
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from examples.analyze_measurements import MeasurementConfig, analyze_measurements, fit_linear, preprocess_csv


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data" / "measurements"
SCRIPT = ROOT / "examples" / "analyze_measurements.py"
DETECTOR = MeasurementConfig("input_power_mW", "signal_V", "dark_V", "mW", "V")
DRIFT = MeasurementConfig("time_s", "signal_V", "dark_V", "s", "V", "reference_V", "reference_dark_V", "V")


class MeasurementMathTests(unittest.TestCase):
    def test_detector_recovers_known_slope_offset_and_noise(self):
        _, records = preprocess_csv((DATA / "detector_response.csv").read_bytes(), DETECTOR)
        used = [row for row in records if row["used"]]
        self.assertEqual((len(records), len(used)), (10, 7))
        self.assertEqual([row["reasons"] for row in records[7:]],
                         [["missing:signal_V"], ["invalid:input_power_mW"], ["missing:dark_V"]])
        fit, predicted, residuals = fit_linear([row["x"] for row in used], [row["response"] for row in used])
        self.assertAlmostEqual(fit["slope"], 2.0, delta=1e-12)
        self.assertAlmostEqual(fit["intercept"], 0.005, delta=1e-12)
        self.assertAlmostEqual(fit["rmse"], 0.002, delta=1e-12)
        self.assertAlmostEqual(fit["slope_standard_error"], math.sqrt(0.000028 / 5 / 0.28), delta=1e-12)
        self.assertEqual(fit["degrees_of_freedom"], 5)
        for actual, expected in zip(residuals, [0.002, -0.003, 0.001, 0, 0.001, -0.003, 0.002]):
            self.assertAlmostEqual(actual, expected, delta=1e-12)
        for row, prediction, residual in zip(used, predicted, residuals):
            self.assertAlmostEqual(row["response"], prediction + residual, delta=1e-12)

    def test_normalization_removes_known_seven_percent_source_drift(self):
        _, records = preprocess_csv((DATA / "reference_drift.csv").read_bytes(), DRIFT)
        used = [row for row in records if row["used"]]
        self.assertEqual((len(records), len(used)), (13, 8))
        self.assertAlmostEqual(used[-1]["corrected_signal"] / used[0]["corrected_signal"], 1.07, delta=1e-12)
        self.assertTrue(all(abs(row["response"] - 0.4) < 1e-12 for row in used))
        fit, _, residuals = fit_linear([row["x"] for row in used], [row["response"] for row in used])
        self.assertAlmostEqual(fit["slope"], 0, delta=1e-12)
        self.assertAlmostEqual(fit["intercept"], 0.4, delta=1e-12)
        self.assertLess(max(map(abs, residuals)), 1e-12)
        self.assertIsNone(fit["r_squared"])
        self.assertEqual(records[8]["reasons"], ["reference_at_or_below_minimum"])
        self.assertEqual(records[9]["reasons"], ["missing:reference_V"])
        self.assertEqual(records[11]["reasons"], ["reference_at_or_below_minimum"])

    def test_missing_nonfinite_ragged_and_blank_records_are_never_imputed(self):
        data = b"x,s,b\n0,1,0\n1,nan,0\n2,inf,0\n3,-inf,0\n,invalid,\n\n4,2\n5,3,0,extra\n6,-1,0\n"
        _, records = preprocess_csv(data, MeasurementConfig("x", "s", "b", "s", "V"))
        self.assertEqual(len(records), 9)
        self.assertEqual(sum(row["used"] for row in records), 2)
        self.assertEqual(records[4]["reasons"], ["missing:x", "invalid:s", "missing:b"])
        self.assertIn("invalid_row_width", records[5]["reasons"])
        self.assertIn("missing:b", records[6]["reasons"])
        self.assertEqual(records[7]["raw"][-1], "extra")
        self.assertEqual(records[-1]["response"], -1)  # Negative corrected signal is valid.
        self.assertEqual([row["line_end"] for row in records], list(range(2, 11)))

    def test_csv_bom_quoted_multiline_and_overflow_accounting(self):
        config = MeasurementConfig("x", "s", "b", "s", "V")
        _, records = preprocess_csv('\ufeffx,s,b,note\n0,1,0,"two\nlines"\n1,1e308,-1e308,overflow\n'.encode(), config)
        self.assertEqual(records[0]["line_end"], 3)
        self.assertEqual(records[1]["reasons"], ["nonfinite_correction"])
        self.assertIsNone(records[1]["response"])
        json.dumps(records, allow_nan=False)

    def test_explicit_reference_floor_and_normalization_overflow(self):
        config = MeasurementConfig("x", "s", "b", "s", "V", "r", "rb", "mW")
        data = b"x,s,b,r,rb\n0,1,0,0.1,0\n1,2,0,0.2,0\n2,1e308,0,1e-308,0\n"
        _, records = preprocess_csv(data, replace(config, minimum_reference=0.1))
        self.assertFalse(records[0]["used"])
        self.assertTrue(records[1]["used"])
        self.assertEqual(records[1]["response"], 10)
        _, records = preprocess_csv(data, config)
        self.assertEqual(records[2]["reasons"], ["nonfinite_response"])
        self.assertEqual(config.response_unit, "(V)/(mW)")

    def test_independent_variant_with_large_offset_and_negative_slope(self):
        x = [1e9 + value for value in range(5)]
        y = [7 - 3 * value for value in range(5)]
        fit, _, residuals = fit_linear(x, y)
        self.assertEqual(fit["slope"], -3)
        self.assertEqual(fit["intercept"], 3e9 + 7)
        self.assertEqual(fit["r_squared"], 1)
        self.assertEqual(residuals, [0] * 5)
        for bad_x, bad_y in (([0, 1], [1, 2]), ([0, 0, 0], [1, 2, 3]), ([0, 1, 2], [1, math.nan, 3])):
            with self.assertRaises(ValueError):
                fit_linear(bad_x, bad_y)

    def test_fit_diagnostics_survive_extreme_response_units(self):
        baseline, _, _ = fit_linear([0, 1, 2, 3], [0, 1, 1.5, 3])
        for scale in (1e-200, 1e200):
            fit, _, _ = fit_linear([0, 1, 2, 3], [0, scale, 1.5 * scale, 3 * scale])
            for name in ("slope", "intercept", "rmse", "slope_standard_error", "intercept_standard_error"):
                self.assertAlmostEqual(fit[name] / scale, baseline[name], delta=1e-12)
            self.assertAlmostEqual(fit["r_squared"], baseline["r_squared"], delta=1e-12)

    def test_large_absolute_coordinates_keep_small_representable_variations(self):
        for x_shift, y_shift in ((1e16, 0), (0, 1e16), (1e16, 1e16)):
            with self.subTest(x_shift=x_shift, y_shift=y_shift):
                x = [x_shift + offset for offset in (0, 2, 4, 6)]
                y = [y_shift + offset for offset in (0, 2, 4, 6)]
                fit, predicted, residuals = fit_linear(x, y)
                self.assertEqual(fit["slope"], 1)
                self.assertEqual(fit["intercept"], y_shift - x_shift)
                self.assertEqual(fit["r_squared"], 1)
                self.assertEqual(fit["rmse"], 0)
                self.assertEqual(predicted, y)
                self.assertEqual(residuals, [0] * 4)

    def test_invalid_mapping_headers_and_csv_syntax_fail_before_analysis(self):
        for config in (replace(DETECTOR, signal=DETECTOR.x), replace(DETECTOR, reference="r"),
                       replace(DETECTOR, minimum_reference=-1), replace(DETECTOR, maximum_rmse=math.nan),
                       replace(DETECTOR, x_unit="")):
            with self.assertRaises(ValueError):
                config.validate()
        for data in (b"", b"x,s,s\n", b"x,s,b\n", b"input_power_mW,signal_V,dark_V,_analysis_x\n"):
            with self.assertRaises(ValueError):
                preprocess_csv(data, DETECTOR)
        with self.assertRaises(csv.Error):
            preprocess_csv(b'input_power_mW,signal_V,dark_V\n0,"unterminated,0\n', DETECTOR)


class MeasurementArtifactTests(unittest.TestCase):
    def test_bundle_preserves_input_and_audits_every_record_and_artifact(self):
        source = DATA / "detector_response.csv"
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            summary = analyze_measurements(source, Path(directory) / "out", replace(DETECTOR, maximum_rmse=0.003))
            self.assertEqual(summary["status"], "WARNING")
            self.assertEqual(summary["targets"][0]["status"], "PASS")
            artifacts = {role: Path(path) for role, path in summary["artifacts"].items()}
            self.assertEqual(artifacts["original_input"].read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(json.loads(artifacts["summary"].read_text()), summary)
            provenance = json.loads(artifacts["provenance"].read_text())
            self.assertFalse(provenance["software"]["engine_required"])
            for role, checksum in provenance["artifact_sha256"].items():
                self.assertEqual(hashlib.sha256(artifacts[role].read_bytes()).hexdigest(), checksum)
            self.assertEqual(artifacts["plot"].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            with artifacts["processed_csv"].open(newline="", encoding="utf-8") as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(len(records), 10)
            self.assertEqual(records[8]["input_power_mW"], "invalid")
            self.assertEqual(json.loads(records[8]["_analysis_reasons"]), ["invalid:input_power_mW"])
            self.assertEqual(records[8]["_analysis_residual"], "")
            self.assertEqual(records[0]["_analysis_input_sha256"], provenance["input"]["sha256"])
            before = {role: path.read_bytes() for role, path in artifacts.items()}
            with self.assertRaisesRegex(ValueError, "already exists"):
                analyze_measurements(source, Path(directory) / "out", DETECTOR)
            self.assertEqual(before, {role: path.read_bytes() for role, path in artifacts.items()})

    def test_insufficient_or_degenerate_data_produce_unknown_and_keep_audit(self):
        for content in ("x,s,b\n", "x,s,b\n0,,0\n", "x,s,b\n1,2,0\n1,3,0\n1,4,0\n"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "input.csv"
                source.write_text(content)
                summary = analyze_measurements(source, Path(directory) / "out", MeasurementConfig("x", "s", "b", "s", "V", maximum_rmse=1))
                self.assertEqual(summary["status"], "UNKNOWN")
                self.assertIsNone(summary["fit"])
                self.assertEqual(summary["targets"][0]["status"], "UNKNOWN")
                self.assertTrue(Path(summary["artifacts"]["processed_csv"]).exists())

    def test_cli_success_failure_unknown_usage_and_independence_from_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            base = [sys.executable, str(SCRIPT), str(DATA / "detector_response.csv"),
                    "--x", "input_power_mW", "--signal", "signal_V", "--background", "dark_V",
                    "--x-unit", "mW", "--signal-unit", "V"]
            for index, bound, expected in ((0, "0.003", 0), (1, "0.001", 1), (2, "nan", 4)):
                output = Path(directory) / str(index)
                result = subprocess.run([*base, "--out", str(output), "--maximum-rmse", bound], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 4:
                    self.assertEqual(result.stdout, "")
                    self.assertIn("error", json.loads(result.stderr))
                    self.assertFalse(output.exists())
                else:
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(json.loads(result.stdout)["targets"][0]["status"], "PASS" if expected == 0 else "FAIL")
                    self.assertLess(len(result.stdout), 5000)
            result = subprocess.run([sys.executable, str(SCRIPT), "--unknown"], capture_output=True, text=True, timeout=60)
            self.assertEqual((result.returncode, result.stdout), (4, ""))
            self.assertIn("error", json.loads(result.stderr))
            source = Path(directory) / "custom.csv"
            for name, content, expected_code, expected_status in (
                ("unknown", "input_power_mW,signal_V,dark_V\n0,1,0\n1,2,0\n", 2, "UNKNOWN"),
                ("pass", "input_power_mW,signal_V,dark_V\n0,1,0\n1,4,0\n2,7,0\n3,10,0\n", 0, "PASS"),
            ):
                source.write_text(content)
                result = subprocess.run([*base[:2], str(source), *base[3:], "--out", str(Path(directory) / name)], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, expected_code, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], expected_status)
                self.assertEqual(result.stderr, "")
            # Block engine imports even when the published engine is installed.
            code = """import sys
class BlockEngine:
    def find_spec(self, fullname, *args):
        if fullname == 'os_optics' or fullname.startswith('os_optics.'):
            raise AssertionError('measurement analysis imported the engine')
sys.meta_path.insert(0, BlockEngine())
from examples.analyze_measurements import main
raise SystemExit(main(sys.argv[1:]))
"""
            result = subprocess.run([sys.executable, "-c", code, *base[2:], "--out", str(Path(directory) / "independent")], cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["rows"]["used"], 7)


if __name__ == "__main__":
    unittest.main()
