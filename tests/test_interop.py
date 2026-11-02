"""Offline checks of the external-tool (interop) examples.

Run from the repository root: python -m unittest tests.test_interop -v

The reference formulas, input parsing and network handling need only numpy (and
scipy for the diffraction reference); third-party solvers are never imported here.
Checks that run the Engine need os-optics 0.10.0 or newer and are skipped
otherwise, as is any check whose optional dependency is missing.
"""

from importlib.util import find_spec
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HAS_NUMPY = find_spec("numpy") is not None
HAS_SCIPY = HAS_NUMPY and find_spec("scipy") is not None

from examples.interop import common  # noqa: E402  (stdlib only)


def engine_supported():
    installed = common.engine_version()
    return installed is not None and common.version_tuple(installed) >= common.MINIMUM_ENGINE


class CommonTests(unittest.TestCase):
    def test_version_tuple_reads_the_release_part(self):
        self.assertEqual(common.version_tuple("0.8.0"), (0, 8, 0))
        self.assertEqual(common.version_tuple("0.8.0rc1"), (0, 8, 0))
        self.assertEqual(common.version_tuple("0.10.2+abc"), (0, 10, 2))
        self.assertLess(common.version_tuple("0.6.1"), common.MINIMUM_ENGINE)

    def test_require_engine_refuses_older_or_missing_engines(self):
        for installed in ("0.6.1", "0.8.0", "0.9.0", None):
            with self.subTest(installed=installed), patch.object(common, "engine_version", return_value=installed):
                with self.assertRaisesRegex(RuntimeError, "requires os-optics 0.10.0"):
                    common.require_engine()
        with patch.object(common, "engine_version", return_value="0.10.0"):
            self.assertEqual(common.require_engine(), "0.10.0")


    def test_decimal_text_is_a_plain_canonical_decimal(self):
        self.assertEqual(common.decimal_text(3.0), "3")
        self.assertEqual(common.decimal_text(0.1999999999), "0.2")
        self.assertEqual(common.decimal_text(-0.0), "0")
        self.assertEqual(common.decimal_text(1e-7, 9), "0.0000001")
        with self.assertRaises(ValueError):
            common.decimal_text(float("nan"))

    def test_pose_is_a_unit_quaternion_with_the_requested_axes(self):
        placed = common.pose((1, 2, 3), (1, 0, 0))
        w, x, y, z = placed["quaternion_wxyz"]
        self.assertAlmostEqual(w * w + x * x + y * y + z * z, 1.0, places=12)

        def rotate(v):  # q v q*
            qv = (x, y, z)
            t = [2 * (qv[1] * v[2] - qv[2] * v[1]), 2 * (qv[2] * v[0] - qv[0] * v[2]), 2 * (qv[0] * v[1] - qv[1] * v[0])]
            c = [qv[1] * t[2] - qv[2] * t[1], qv[2] * t[0] - qv[0] * t[2], qv[0] * t[1] - qv[1] * t[0]]
            return [v[i] + w * t[i] + c[i] for i in range(3)]

        for local, expected in (((0, 0, 1), (1, 0, 0)), ((1, 0, 0), (0, 0, 1))):
            for got, want in zip(rotate(local), expected):
                self.assertAlmostEqual(got, want, places=12)


@unittest.skipUnless(engine_supported(), "os-optics 0.10.0 or newer is required")
class OptimizationOutcomeTests(unittest.TestCase):
    def test_refused_sample_is_logged_and_counts_toward_budget(self):
        from examples.interop.optimization.optimize_expander import EngineObjective, INFEASIBLE_PENALTY_RAD
        with tempfile.TemporaryDirectory() as tmp:
            objective = EngineObjective(Path(tmp) / "eval.jsonl", max_evaluations=1)
            with patch.object(objective, "_run", side_effect=ValueError("invalid design")):
                self.assertEqual(objective(200), INFEASIBLE_PENALTY_RAD)
            row = json.loads(objective.log_path.read_text())
            with self.assertRaisesRegex(ValueError, "evaluation budget"):
                objective(200)
        self.assertFalse(row["objective_valid"])
        self.assertEqual(row["run_status"], "ERROR")
        self.assertEqual(row["error"]["message"], "invalid design")
        self.assertIsNone(row["identity"])

    def test_existing_evaluations_are_preserved(self):
        from examples.interop.optimization.optimize_expander import EngineObjective
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval.jsonl"
            path.write_text("existing evidence", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                EngineObjective(path)
            self.assertEqual(path.read_text(), "existing evidence")

    def test_unmet_objective_stays_available_for_sensitivity(self):
        from examples.interop.optimization.optimize_expander import EngineObjective, INFEASIBLE_PENALTY_RAD
        with tempfile.TemporaryDirectory() as tmp:
            objective = EngineObjective(Path(tmp) / "eval.jsonl", max_divergence_rad=0)
            self.assertEqual(objective(200), INFEASIBLE_PENALTY_RAD)
            self.assertLess(objective(200, stage="tolerance"), 0.001)
            rows = [json.loads(line) for line in objective.log_path.read_text().splitlines()]
        self.assertTrue(all(r["objective_valid"] for r in rows))
        self.assertTrue(all(r["assessment"] == "UNMET" and not r["requirement_feasible"] for r in rows))
        self.assertTrue(all(r["identity"]["result_hash"] for r in rows))

    def test_budget_limited_sample_keeps_failure_evidence(self):
        from examples.interop.optimization.optimize_expander import EngineObjective, INFEASIBLE_PENALTY_RAD
        with tempfile.TemporaryDirectory() as tmp:
            objective = EngineObjective(Path(tmp) / "eval.jsonl", compute_budget={"max_trace_steps": 1})
            self.assertEqual(objective(200), INFEASIBLE_PENALTY_RAD)
            row = json.loads(objective.log_path.read_text())
        self.assertFalse(row["objective_valid"])
        self.assertEqual(row["assessment"], "INDETERMINATE")
        self.assertEqual(row["execution"]["outcome"], "PARTIAL")
        self.assertIn("max_trace_steps", row["execution"]["budgets"]["exhausted"])

    def test_unrelated_scope_issue_does_not_invalidate_selected_objective(self):
        from examples.interop.optimization import optimize_expander as oe
        from os_optics.local import load_project, run_optical_project
        from os_optics.results import load_run
        results = load_run(run_optical_project(load_project(oe.base_project())))
        original = results.describe()
        original["status"]["validation_status"] = "OUT_OF_MODEL_SCOPE"
        with tempfile.TemporaryDirectory() as tmp:
            objective = oe.EngineObjective(Path(tmp) / "eval.jsonl")
            with patch.object(type(results), "describe", return_value=original), patch.object(objective, "_load_run", return_value=results):
                self.assertLess(objective(200), 0.001)
            row = json.loads(objective.log_path.read_text())
        self.assertTrue(row["objective_valid"])
        self.assertTrue(row["requirement_feasible"])

@unittest.skipUnless(HAS_SCIPY, "numpy and scipy are needed for the diffraction reference")
class DiffractionReferenceTests(unittest.TestCase):
    def setUp(self):
        import numpy as np

        from examples.interop.diffraction import hankel_reference, tem00_field

        self.np, self.hankel, self.field = np, hankel_reference, tem00_field

    def test_sampled_field_carries_the_beam_power(self):
        np = self.np
        coords = (np.arange(512) - 256) * 0.02
        xx, yy = np.meshgrid(coords, coords)
        u = self.field.sampled_field(xx, yy, w_x_mm=1.0, w_y_mm=0.8, r_x_mm=5e4, r_y_mm=math.inf, power_w=0.002,
                                     wavelength_nm=532.0)
        self.assertAlmostEqual(float(np.sum(np.abs(u) ** 2) * 0.02 ** 2), 0.002, places=12)

    def test_propagation_matches_the_gaussian_beam_law(self):
        w, radius, z_r = self.field.beam_at_distance(1.0, 100.0, 532.0)
        w2, radius2, z = self.field.propagate_state(0.0, z_r, 100.0, 532.0)
        self.assertAlmostEqual(w, w2, places=12)
        self.assertAlmostEqual(radius, radius2, places=6)
        self.assertEqual(z, 100.0)
        self.assertGreater(radius, 0.0)  # diverging past the waist

    def test_untruncated_focus_is_the_gaussian_focus(self):
        w, f, lam = 1.0, 200.0, 532.0
        focus = self.hankel.HankelFocus(stop_radius_mm=4.0, w_mm=w, r_mm=math.inf, focal_length_mm=f, wavelength_nm=lam)
        w_f = self.hankel.gaussian_focus_radius_mm(w, f, lam)
        self.assertAlmostEqual(focus.irradiance_per_watt(0.0) / (2 / (math.pi * w_f ** 2)), 1.0, places=6)
        self.assertAlmostEqual(focus.encircled_fraction(w_f), 1 - math.exp(-2), places=6)
        self.assertAlmostEqual(self.field.power_inside_circle(1.2, 1.0), 1 - math.exp(-2 * 1.44), places=14)

    def test_hard_stop_lowers_the_focal_peak_below_the_gaussian_value(self):
        focus = self.hankel.HankelFocus(stop_radius_mm=1.2, w_mm=1.0, r_mm=math.inf, focal_length_mm=200.0,
                                        wavelength_nm=532.0)
        gaussian = 2 / (math.pi * self.hankel.gaussian_focus_radius_mm(1.0, 200.0, 532.0) ** 2)
        self.assertLess(focus.irradiance_per_watt(0.0), 0.7 * gaussian)


@unittest.skipUnless(HAS_NUMPY, "numpy is needed for the quantum reference")
class QuantumReferenceTests(unittest.TestCase):
    def setUp(self):
        import numpy as np

        from examples.interop.quantum import network_io, reference

        self.np, self.ref, self.nio = np, reference, network_io

    def test_hom_dip_is_the_textbook_formula(self):
        for p in (0.0, 0.5, 0.95, 1.0):
            distribution = self.ref.two_photon_distribution(self.ref.hom_matrix(), p)
            self.assertAlmostEqual(sum(distribution.values()), 1.0, places=12)
            self.assertAlmostEqual(self.ref.coincidence_probability(distribution, 0.8), 0.64 * (1 - p) / 2, places=12)

    def test_lossy_distribution_is_normalized_and_dark_counts_add_coincidences(self):
        distribution = self.ref.two_photon_distribution(self.ref.hom_matrix(window_mm=3.0), 0.9)
        self.assertAlmostEqual(sum(distribution.values()), 1.0, places=12)
        self.assertGreater(distribution[(0, 1)], 0.0)
        clean = self.ref.coincidence_probability(distribution, 0.8)
        dark = self.ref.coincidence_probability(distribution, 0.8, 0.01)
        self.assertGreater(dark, clean)

    def test_lossy_two_photon_distribution_matches_an_explicit_unitary_completion(self):
        np = self.np

        def brute_force(m, p):
            u = self.nio.dilate(m)  # 4 x 4: outputs 0, 1 and two loss modes
            groups = [0, 1, 2, 2]  # loss modes merged into one "lost" group
            together, apart = {}, {}
            for i in range(4):
                for j in range(i, 4):
                    sub = u[np.ix_([i, j], [0, 1])]
                    perm = sub[0, 0] * sub[1, 1] + sub[0, 1] * sub[1, 0]
                    prob = abs(perm) ** 2 / (2.0 if i == j else 1.0)
                    key = (sum(g == 0 for g in (groups[i], groups[j])), sum(g == 1 for g in (groups[i], groups[j])))
                    together[key] = together.get(key, 0.0) + prob
            for i in range(4):
                for j in range(4):
                    prob = abs(u[i, 0]) ** 2 * abs(u[j, 1]) ** 2
                    key = (sum(g == 0 for g in (groups[i], groups[j])), sum(g == 1 for g in (groups[i], groups[j])))
                    apart[key] = apart.get(key, 0.0) + prob
            return {k: p * together.get(k, 0.0) + (1 - p) * apart.get(k, 0.0) for k in set(together) | set(apart)}

        for matrix in (self.ref.mzi_matrix(1.0), self.ref.mzi_matrix(1.0003), self.ref.hom_matrix(window_mm=3.0)):
            for p in (0.0, 0.6, 0.95):
                closed = self.ref.two_photon_distribution(matrix, p)
                explicit = brute_force(matrix, p)
                self.assertAlmostEqual(sum(closed.values()), 1.0, places=12)
                for key, value in closed.items():
                    self.assertAlmostEqual(value, explicit.get(key, 0.0), places=12, msg=(key, p))

    def test_balanced_mzi_swaps_the_photons(self):
        distribution = self.ref.two_photon_distribution(self.ref.mzi_matrix(0.0), 0.3)
        self.assertAlmostEqual(distribution[(1, 1)], 1.0, places=12)

    def test_glass_indices_match_catalogue_values_and_finite_differences(self):
        nbk7 = self.ref.N_BK7_SELLMEIER
        self.assertAlmostEqual(self.ref.sellmeier_index(nbk7, 587.5618), 1.5168, places=4)
        h = 1e-3
        dn = (self.ref.sellmeier_index(nbk7, 780 + h) - self.ref.sellmeier_index(nbk7, 780 - h)) / (2 * h)
        self.assertAlmostEqual(self.ref.group_index(nbk7, 780.0), self.ref.sellmeier_index(nbk7, 780.0) - 780.0 * dn,
                               places=8)
        self.assertAlmostEqual(self.ref.window_group_delay_ps(3.0, 780.0) - self.ref.window_phase_delay_ps(3.0, 780.0),
                               0.163, places=3)

    def test_unitary_dilation_embeds_a_contraction(self):
        np = self.np
        rng = np.random.default_rng(3)
        z = rng.normal(size=(3, 3)) + 1j * rng.normal(size=(3, 3))
        t = 0.9 * z / np.linalg.norm(z, 2)
        u = self.nio.dilate(t[:, :2])  # non-square: padded with a vacuum input
        self.assertTrue(np.allclose(u.conj().T @ u, np.eye(6), atol=1e-10))
        self.assertTrue(np.allclose(u[:3, :2], t[:, :2]))
        with self.assertRaises(ValueError):
            self.nio.dilate(2.0 * np.eye(2))

    def test_group_delay_from_project_glass_matches_the_reference(self):
        project = {"components": [{"instance_id": "W", "optical_model_id": "w"}],
                   "optical_models": [{"model_id": "w", "model": {"model_kind": "SURFACE_SEQUENCE", "media": ["G"]}}],
                   "model_materials": [{"material_id": "G", "dispersion": {
                       "kind": "SELLMEIER", "b": [str(v) for v in self.ref.N_BK7_SELLMEIER["b"]],
                       "c_um2": [str(v) for v in self.ref.N_BK7_SELLMEIER["c_um2"]]}}]}
        n = self.ref.sellmeier_index(self.ref.N_BK7_SELLMEIER, 780.0)
        path = {"path_id": "A#0", "length_mm": 110.0, "opl_mm": 110.0 + (n - 1) * 3.0,
                "lineage": ["W/S1:refract", "W/S2:refract", "BS/S1:split_transmit"]}
        delay = self.nio.computed_group_delay(path, project, 780.0)
        expected = 110.0 / self.ref.C_MM_PER_PS + self.ref.window_group_delay_ps(3.0, 780.0)
        self.assertAlmostEqual(delay, expected, places=9)
        air = {"path_id": "B#0", "length_mm": 110.0, "opl_mm": 110.0, "lineage": ["BS/S1:split_reflect"]}
        self.assertAlmostEqual(self.nio.computed_group_delay(air, project, 780.0), 110.0 / self.ref.C_MM_PER_PS, places=9)
        exported = dict(path, group_delay_ps=1.25, source_id="A", output_beam="out:D1:0", amplitudes=[])
        delays = self.nio.path_group_delays({"wavelength_nm": 780.0, "paths": [exported]}, project)
        self.assertEqual(delays["A#0"], (1.25, "exported"))

    def test_single_frequency_model_is_refused_for_short_photons_in_an_unbalanced_interferometer(self):
        def path(pid, source, delay):
            return {"path_id": pid, "source_id": source, "output_beam": "out:P:0",
                    "amplitudes": [[[0.5, 0.0], [0.0, 0.0]], [[0.0, 0.0], [0.5, 0.0]]], "group_delay_ps": delay}

        network = {"paths": [path("A1", "A", 0.0), path("A2", "A", 1.8), path("B1", "B", 0.0), path("B2", "B", 1.8)]}
        delays = {p["path_id"]: (p["group_delay_ps"], "exported") for p in network["paths"]}
        result = self.nio.photon_delays(network, delays, ["A", "B"], "V", 0.05, 1000.0)
        self.assertAlmostEqual(result["relative_delay_ps"], 0.0)
        with self.assertRaises(self.nio.ModelScopeError) as caught:
            self.nio.photon_delays(network, delays, ["A", "B"], "V", 0.05, 0.25)
        self.assertEqual(caught.exception.code, "SINGLE_FREQUENCY_MODEL_INVALID")


@unittest.skipUnless(HAS_NUMPY, "numpy is needed for the quantum input parser")
class QuantumInputTests(unittest.TestCase):
    def setUp(self):
        from examples.interop.quantum import network_io

        self.nio = network_io
        self.document = json.loads((ROOT / "examples/interop/quantum/quantum_inputs.json").read_text(encoding="utf-8"))

    def test_the_example_inputs_parse(self):
        parsed = self.nio.parse_inputs(self.document)
        self.assertEqual(parsed["photons"]["per_source"], {"A": 1, "B": 1})
        self.assertEqual(self.nio.lookup(parsed["case_wavepackets"], "mzi_t1", "case_wavepackets"), "narrowband")
        self.assertEqual(self.nio.lookup(parsed["detectors"]["planes"], "hom_delay_0p1", "planes"), ["D1", "D2"])
        self.assertEqual(parsed["extra_cases"]["mzi_t1_broadband"]["expect_refusal"], "SINGLE_FREQUENCY_MODEL_INVALID")

    def test_invalid_inputs_are_refused_with_the_field_named(self):
        def broken(edit):
            document = copy.deepcopy(self.document)
            edit(document)
            return document

        cases = {
            "schema": lambda d: d.pop("schema"),
            "per_source.A": lambda d: d["photons"]["per_source"].update(A=2),
            "polarization_mode": lambda d: d["photons"].update(polarization_mode="D"),
            "zero_delay_indistinguishability": lambda d: d["wavepackets"]["broadband"].update(
                zero_delay_indistinguishability=1.2),
            "rms_intensity_duration_ps": lambda d: d["wavepackets"]["narrowband"].update(rms_intensity_duration_ps=0),
            "detectors.efficiency": lambda d: d["detectors"].update(efficiency=True),
            "dark_count_probability": lambda d: d["detectors"].update(dark_count_probability=-0.1),
            "unknown wavepacket": lambda d: d["case_wavepackets"].update({"hom*": "missing"}),
            "two different": lambda d: d["detectors"]["planes"].update({"hom*": ["D1", "D1"]}),
        }
        for message, edit in cases.items():
            with self.subTest(field=message), self.assertRaisesRegex(self.nio.InputError, message.split(".")[-1]):
                self.nio.parse_inputs(broken(edit))
        with self.assertRaises(self.nio.InputError):
            self.nio.lookup({"mzi*": "x"}, "hom", "case_wavepackets")

    def test_network_loader_checks_the_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "net.json"
            path.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.nio.load_network(path)


@unittest.skipUnless(HAS_NUMPY, "numpy is needed by the metasurface steps")
class MetasurfaceFitTests(unittest.TestCase):
    def setUp(self):
        from examples.interop.metasurface import step3_fit_model

        self.fit = step3_fit_model

    def test_equivalent_focal_length_inverts_the_gaussian_focusing_law(self):
        for f_um in (150.0, 200.0, 260.0):
            z, w = self.fit.thin_lens_focus(f_um, 30.0, 532.0)
            self.assertAlmostEqual(self.fit.equivalent_focal_length_um(z, 30.0, 532.0), f_um, places=7)
            self.assertLess(z, f_um)
            self.assertLess(w, 30.0)

    def test_interpolation_is_linear_in_optical_power_and_never_extrapolates(self):
        rows = [{"wavelength_nm": 500, "focal_length_um": 200.0, "transmittance": 0.8},
                {"wavelength_nm": 600, "focal_length_um": 100.0, "transmittance": 0.6}]
        self.assertEqual(self.fit.interpolate(rows, 500), (200.0, 0.8))
        f_um, transmittance = self.fit.interpolate(rows, 550)
        self.assertAlmostEqual(1 / f_um, 0.5 * (1 / 200 + 1 / 100), places=15)
        self.assertAlmostEqual(transmittance, 0.7, places=15)
        with self.assertRaises(ValueError):
            self.fit.interpolate(rows, 450)

    @staticmethod
    def focus_table(library, samples, rows):
        return {"library": library, "grid": {"samples": samples}, "method": "lpa",
                "design": {"focal_length_um": 200.0, "atoms_used": 30 if library.endswith("101.json") else 31},
                "rows": [{"wavelength_nm": lam, "tilt_deg": tilt, "input_waist_radius_um": waist, "z_focus_um": z,
                          "w_focus_um": 1.2, "focusing_efficiency": 0.9, **({"set": kind} if kind else {})}
                         for lam, tilt, waist, z, kind in rows]}

    def test_spread_needs_other_orders_and_other_samples(self):
        main = [(490, 0.0, 30.0, 200.0, None), (532, 2.0, 30.0, 199.0, "calibration")]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            common.write_json(folder / "focus_nG101_N1024.json", self.focus_table("library_nG101.json", 1024, main))
            keys = self.fit.calibration_keys(self.focus_table("library_nG101.json", 1024, main), 30.0)
            with self.assertRaises(self.fit.ConvergenceDataMissing) as raised:
                self.fit.spreads(folder, folder / "focus_nG101_N1024.json", keys)
            self.assertEqual(raised.exception.code, "CONVERGENCE_DATA_MISSING")
            self.assertIn("--nG 201", str(raised.exception))
            self.assertIn("--samples 2048", str(raised.exception))
            common.write_json(folder / "focus_nG201_N1024.json",
                              self.focus_table("library_nG201.json", 1024, [(490, 0.0, 30.0, 201.0, None)]))
            with self.assertRaises(self.fit.ConvergenceDataMissing) as raised:
                self.fit.spreads(folder, folder / "focus_nG101_N1024.json", keys)
            self.assertIn("other propagation samples", str(raised.exception))
            self.assertNotIn("other RCWA orders", str(raised.exception))
            common.write_json(folder / "focus_nG101_N2048.json",
                              self.focus_table("library_nG101.json", 2048, [(532, 2.0, 30.0, 199.5, "calibration")]))
            used, spread = self.fit.spreads(folder, folder / "focus_nG101_N1024.json", keys)
        self.assertEqual([t["table"] for t in used], ["focus_nG101_N2048.json", "focus_nG201_N1024.json"])
        largest = self.fit.largest_spread(spread)
        self.assertAlmostEqual(largest["z_rel"], 0.005, places=12)
        self.assertEqual(largest["z_rel_case"]["wavelength_nm"], 490)
        self.assertEqual(self.fit.settings_text(self.focus_table("library_nG101.json", 1024, main), used),
                         "RCWA orders 101/201 and propagation grids of 1024/2048 samples")

    def test_test_cases_never_enter_the_spread(self):
        main = [(490, 0.0, 30.0, 200.0, None), (555, 1.5, 30.0, 190.0, "test"), (545, 0.0, 25.0, 194.0, "test")]
        other = [(490, 0.0, 30.0, 200.2, None), (555, 1.5, 30.0, 199.0, "test"), (545, 0.0, 25.0, 204.0, "test")]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            common.write_json(folder / "focus_nG101_N1024.json", self.focus_table("library_nG101.json", 1024, main))
            common.write_json(folder / "focus_nG201_N1024.json", self.focus_table("library_nG201.json", 1024, other))
            common.write_json(folder / "focus_nG101_N2048.json", self.focus_table("library_nG101.json", 2048, other))
            keys = self.fit.calibration_keys(self.focus_table("library_nG101.json", 1024, main), 30.0)
            _, spread = self.fit.spreads(folder, folder / "focus_nG101_N1024.json", keys)
        self.assertEqual(set(spread), {(490, 0.0, 30.0)})
        self.assertAlmostEqual(self.fit.largest_spread(spread)["z_rel"], 0.001, places=12)

    def test_error_bounds_round_up(self):
        self.assertEqual(self.fit.ceil_sig(0.0123), 0.013)
        self.assertEqual(self.fit.ceil_sig(0.012), 0.012)
        self.assertEqual(self.fit.ceil_sig(0.0), 0.0)
        self.assertAlmostEqual(self.fit.aperture_fraction(30.0, 50.0), 1 - math.exp(-2 * 2500 / 900), places=15)


@unittest.skipUnless(HAS_NUMPY and engine_supported(), "needs os-optics 0.10.0 or newer")
class EngineInteropTests(unittest.TestCase):
    def test_hom_layout_exports_a_four_mode_lossless_network(self):
        from os_optics.local import json_api

        from examples.interop.quantum import build_layouts

        layout = build_layouts.hom()
        exported = json_api.linear_optical_network(layout.document)
        self.assertTrue(exported["ok"], exported)
        matrix = exported["network"]["transfer_matrix"]
        self.assertEqual(len(matrix["rows"]), 4)
        self.assertEqual(len(matrix["columns"]), 4)
        self.assertTrue(matrix["lossless"])
        self.assertTrue(all(p.get("group_delay_ps") is not None for p in exported["network"]["paths"]))

    def test_tabulated_lens_outside_its_rows_is_out_of_model_scope(self):
        from os_optics.local import load_project, run_optical_project
        from os_optics.results import load_run

        from examples.interop.metasurface import step4_engine_check

        model = {"model_id": "metalens", "model": {
            "model_kind": "TABULATED_THIN_LENS", "model_version": "1",
            "rows": [{"wavelength_nm": "500", "focal_length_mm": "0.2", "transmittance": "0.8"},
                     {"wavelength_nm": "560", "focal_length_mm": "0.18", "transmittance": "0.7"}],
            "focal_interpolation": "LINEAR_IN_OPTICAL_POWER", "transmittance_interpolation": "LINEAR",
            "clear_radius_mm": "0.05", "edge_radius_mm": "0.06", "max_field_angle_deg": "2",
            "max_beam_radius_mm": "0.03", "validated_polarizations": ["LINEAR_X"],
            "supported_metrics": [{"metric": "waist_radius_x_mm", "rel_error": "0.1", "basis": "test fixture"}],
            "evidence": {"method": "test fixture", "tools": [{"name": "none", "version": "0"}],
                         "source_data_sha256": "0" * 64, "fit_model": "test fixture",
                         "validation_summary": "test fixture", "date": "2026-10-15"},
            "status": "DECLARED"}}
        statuses = {}
        for wavelength in (530.0, 450.0):
            document = step4_engine_check.project(model, "tabulated", wavelengths_nm=[wavelength], waist_um=30.0)
            results = load_run(run_optical_project(load_project(document)))
            status = results.describe()["status"]
            statuses[wavelength] = status.get("validation_status") or status["engine_status"]
        self.assertNotEqual(statuses[530.0], "OUT_OF_MODEL_SCOPE")
        self.assertEqual(statuses[450.0], "OUT_OF_MODEL_SCOPE")


if __name__ == "__main__":
    unittest.main()
