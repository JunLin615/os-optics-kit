"""Step 3: fit the external focusing response to an OpenSci TABULATED_THIN_LENS model.

From the step-2 focus table (normal incidence, 30 um input beam) each table
wavelength becomes one model row:

* focal length: the equivalent thin lens that puts a Gaussian waist at the external
  focus, from inverting z' = f / (1 + (f / z_R)^2) for an input waist at the lens;
* transmittance: focusing efficiency / Gaussian power inside the clear aperture, so the
  aperture loss the Engine applies itself is not counted twice.

Between rows the model interpolates optical power (1/f) and transmittance linearly in
wavelength. The error bound of each supported metric is set on a calibration set only:
the table rows, five calibration wavelengths between them and a 20 um input beam,
plus the external reference's own numerical spread. The spread is read from further
step-2 focus tables in the same directory, one computed with other RCWA orders and one
with other propagation samples (calibration cases only); without both, step 3 stops
with ``CONVERGENCE_DATA_MISSING`` and prints the commands that produce them.
The field-angle domain is calibrated by running the model in OpenSci at the
calibration angles (2, 5, 8, 20 deg) and comparing the Engine's focus with the external
focus at each angle: the largest angle reached without leaving the bounds becomes
``max_field_angle_deg``. The largest validated input beam radius (30 um) becomes
``max_beam_radius_mm``. Test
wavelengths and test settings are never used here; step 4 checks the finished model on
them.

A single external focus does not define a general component: the model holds only
inside the stated wavelength span, field angle, beam size and polarization, for the
listed metrics. The field-angle calibration requires os-optics 0.10.0.

Usage: python examples/interop/metasurface/step3_fit_model.py run-interop-metasurface
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys

if __package__:
    from ..common import ArgumentParser, decimal_text, emit, require_engine, sha256_file, write_json
    from .step1_unit_cells import CALIBRATION_NM, TABLE_NM
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, decimal_text, emit, require_engine, sha256_file, write_json
    from step1_unit_cells import CALIBRATION_NM, TABLE_NM

MODEL_ID = "metalens_tio2_f200um"


def rayleigh_um(waist_um, wavelength_nm):
    return math.pi * waist_um ** 2 / (wavelength_nm * 1e-3)


def equivalent_focal_length_um(z_focus_um, waist_um, wavelength_nm):
    """Invert z' = f / (1 + (f / z_R)^2) (input waist at the lens) for the converging root f."""
    z_r = rayleigh_um(waist_um, wavelength_nm)
    a = z_focus_um / z_r ** 2
    return (1.0 - math.sqrt(1.0 - 4.0 * a * z_focus_um)) / (2.0 * a)


def thin_lens_focus(f_um, waist_um, wavelength_nm):
    """Waist position after the lens and waist radius (um) of a Gaussian with its waist at an ideal thin lens."""
    z_r = rayleigh_um(waist_um, wavelength_nm)
    ratio = f_um / z_r
    return f_um / (1.0 + ratio ** 2), waist_um * ratio / math.sqrt(1.0 + ratio ** 2)


def aperture_fraction(waist_um, clear_radius_um):
    return 1.0 - math.exp(-2.0 * clear_radius_um ** 2 / waist_um ** 2)


def interpolate(rows, wavelength_nm):
    """LINEAR_IN_OPTICAL_POWER focal length and LINEAR transmittance between rows (no extrapolation)."""
    lams = [r["wavelength_nm"] for r in rows]
    if not lams[0] <= wavelength_nm <= lams[-1]:
        raise ValueError(f"{wavelength_nm} nm lies outside the table {lams[0]}-{lams[-1]} nm")
    for lo, hi in zip(rows, rows[1:]):
        if lo["wavelength_nm"] <= wavelength_nm <= hi["wavelength_nm"]:
            u = (wavelength_nm - lo["wavelength_nm"]) / (hi["wavelength_nm"] - lo["wavelength_nm"])
            power = (1 - u) / lo["focal_length_um"] + u / hi["focal_length_um"]
            return 1.0 / power, (1 - u) * lo["transmittance"] + u * hi["transmittance"]
    return rows[0]["focal_length_um"], rows[0]["transmittance"]


def ceil_sig(value, digits=2):
    """Round a positive bound up to ``digits`` significant digits."""
    if value <= 0:
        return 0.0
    exponent = math.floor(math.log10(value)) - digits + 1
    scale = 10.0 ** exponent
    return round(math.ceil(value / scale - 1e-9) * scale, max(0, -exponent))


def canonical_sha256(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


CONVERGENCE_WAVELENGTHS_NM = (490, 515, 532, 545, 580)  # table rows and calibration wavelengths across the band


class ConvergenceDataMissing(ValueError):
    """The external numerical spread cannot be computed from the files in the run directory."""

    code = "CONVERGENCE_DATA_MISSING"


def case_key(row):
    return row["wavelength_nm"], row["tilt_deg"], row["input_waist_radius_um"]


def calibration_keys(table, waist):
    """Cases of the calibration set: table rows and calibration wavelengths (30 um beam) and the calibration extras."""
    keys = {(lam, 0.0, waist) for lam in TABLE_NM + CALIBRATION_NM}
    keys |= {case_key(r) for r in table["rows"] if r.get("set") == "calibration"}
    return keys


def convergence_commands(directory):
    lams = " ".join(str(w) for w in CONVERGENCE_WAVELENGTHS_NM)
    base = "python examples/interop/metasurface"
    return [f"{base}/step1_unit_cells.py --out {directory} --nG 201 --wavelengths-nm {lams}",
            f"{base}/step2_focusing.py {directory} --nG 201",
            f"{base}/step2_focusing.py {directory} --samples 2048 --wavelengths-nm {lams}"]


def spreads(directory, main, keys):
    """External numerical spread per calibration case across the convergence tables next to the model's table.

    A convergence table is another step-2 table of the same design and method that differs in the RCWA orders
    (library) or in the propagation samples. Only calibration-set cases contribute; test cases never do. Both kinds
    of variation are required, otherwise ``ConvergenceDataMissing`` names the commands that produce them.
    """
    directory = Path(directory)
    main_table = json.loads(main.read_text(encoding="utf-8"))
    base = {case_key(r): r for r in main_table["rows"] if case_key(r) in keys}
    reference = {k: v for k, v in main_table["design"].items() if k != "atoms_used"}
    used, result = [], {}
    varied = {"orders": False, "samples": False}
    for path in sorted(directory.glob("focus_nG*_N*.json")):
        if path.name == main.name:
            continue
        table = json.loads(path.read_text(encoding="utf-8"))
        design = {k: v for k, v in table["design"].items() if k != "atoms_used"}
        if design != reference or table["method"] != main_table["method"]:
            continue
        rows = [row for row in table["rows"] if case_key(row) in base]
        if not rows:
            continue
        orders = table["library"] != main_table["library"]
        samples = table["grid"]["samples"] != main_table["grid"]["samples"]
        varied["orders"] |= orders
        varied["samples"] |= samples
        setting = f"{table['library']} N{table['grid']['samples']}"
        used.append({"table": path.name, "library": table["library"], "samples": table["grid"]["samples"],
                     "calibration_cases": len(rows)})
        for row in rows:
            ref = base[case_key(row)]
            entry = result.setdefault(case_key(row), {"z_rel": 0.0, "w_rel": 0.0, "eff_abs": 0.0, "settings": []})
            entry["z_rel"] = max(entry["z_rel"], abs(row["z_focus_um"] / ref["z_focus_um"] - 1.0))
            entry["w_rel"] = max(entry["w_rel"], abs(row["w_focus_um"] / ref["w_focus_um"] - 1.0))
            entry["eff_abs"] = max(entry["eff_abs"], abs(row["focusing_efficiency"] - ref["focusing_efficiency"]))
            entry["settings"].append(setting)
    missing = [name for name, found in (("other RCWA orders", varied["orders"]),
                                        ("other propagation samples", varied["samples"])) if not found]
    if missing:
        raise ConvergenceDataMissing(
            f"the error bounds include the external numerical spread, but {directory} has no focus table with "
            f"{' or '.join(missing)} for the calibration cases of {main.name}; produce them with: "
            + "; ".join(convergence_commands(directory)))
    return used, result


def orders_of(library_name):
    match = re.fullmatch(r"library_nG(\d+)\.json", library_name)
    return int(match.group(1)) if match else 0


def settings_text(main_table, used):
    """Settings the spread was taken over, e.g. 'RCWA orders 101/201 and propagation grids of 1024/2048 samples'."""
    orders = sorted({orders_of(main_table["library"])} | {orders_of(t["library"]) for t in used})
    samples = sorted({main_table["grid"]["samples"]} | {t["samples"] for t in used})
    return (f"RCWA orders {'/'.join(str(o) for o in orders)} and propagation grids of "
            f"{'/'.join(str(n) for n in samples)} samples")


def largest_spread(spread):
    """Largest spread per metric and the case where it occurs."""
    summary = {}
    for metric in ("z_rel", "w_rel", "eff_abs"):
        case, entry = max(spread.items(), key=lambda item: item[1][metric])
        summary[metric] = entry[metric]
        summary[f"{metric}_case"] = {"wavelength_nm": case[0], "tilt_deg": case[1], "input_waist_radius_um": case[2]}
    return summary


def transmittance_of(efficiency, fraction):
    value = efficiency / fraction
    if value > 1.0 + 1e-9:
        raise ValueError(f"focusing efficiency {efficiency} exceeds the Gaussian power {fraction} inside the aperture")
    return min(value, 1.0)


def deviation(ext, f_um, transmittance, waist_um, clear_um, label):
    z, w = thin_lens_focus(f_um, waist_um, ext["wavelength_nm"])
    efficiency = transmittance * aperture_fraction(waist_um, clear_um)
    return {"case": label, "wavelength_nm": ext["wavelength_nm"],
            "external": {"z_focus_um": ext["z_focus_um"], "w_focus_um": ext["w_focus_um"],
                         "focusing_efficiency": ext["focusing_efficiency"]},
            "model": {"z_focus_um": z, "w_focus_um": w, "focusing_efficiency": efficiency},
            "z_abs_err_um": z - ext["z_focus_um"], "z_rel_err": z / ext["z_focus_um"] - 1.0,
            "w_rel_err": w / ext["w_focus_um"] - 1.0, "efficiency_abs_err": efficiency - ext["focusing_efficiency"],
            "power_rel_err": efficiency / ext["focusing_efficiency"] - 1.0}


def build_model(rows, bounds, max_angle, waist, clear_um, transmittance_interpolation, tools, library_path, configuration,
                spread_settings):
    lam_lo, lam_hi = rows[0]["wavelength_nm"], rows[-1]["wavelength_nm"]
    model = {
        "model_kind": "TABULATED_THIN_LENS", "model_version": "1",
        "rows": [{"wavelength_nm": decimal_text(r["wavelength_nm"]),
                  "focal_length_mm": decimal_text(r["focal_length_um"] / 1000, 7),
                  "transmittance": decimal_text(r["transmittance"], 4)} for r in rows],
        "focal_interpolation": "LINEAR_IN_OPTICAL_POWER", "transmittance_interpolation": transmittance_interpolation,
        "clear_radius_mm": decimal_text(clear_um / 1000), "edge_radius_mm": decimal_text(clear_um / 1000 + 0.01),
        "max_field_angle_deg": decimal_text(max_angle),
        "max_beam_radius_mm": decimal_text(waist / 1000),
        "validated_polarizations": ["LINEAR_X"],
        "supported_metrics": [
            *({"metric": f"waist_distance_{axis}_mm", "abs_error": decimal_text(bounds["z_abs_um"] / 1000, 6),
               "basis": "largest deviation from the external focus over the calibration set (table rows, calibration "
                        "wavelengths, 20 um beam, accepted field angles) plus the external numerical spread"}
              for axis in ("x", "y")),
            *({"metric": f"waist_radius_{axis}_mm", "rel_error": decimal_text(bounds["w_rel"], 4),
               "basis": "largest relative deviation from the external second-moment focal radius over the calibration "
                        "set, plus the external numerical spread"} for axis in ("x", "y")),
            {"metric": "power_W", "rel_error": decimal_text(bounds["power_rel"], 4),
             "basis": "largest relative deviation of the delivered power from the external focusing efficiency over the "
                      "calibration set, plus the external numerical spread"},
        ],
        "evidence": {
            "method": "RCWA unit-cell library (TiO2 pillars on SiO2, constant indices, normal incidence, x polarization), "
                      "local periodic approximation and scalar angular-spectrum focusing of a 30 um Gaussian beam",
            "tools": tools, "source_data_sha256": sha256_file(library_path),
            "configuration_sha256": canonical_sha256(configuration),
            "fit_model": "rows: equivalent thin-lens focal length from the Gaussian focusing law at each row's external "
                         "focus; transmittance = focusing efficiency / Gaussian power inside the clear aperture; "
                         f"interpolation linear in optical power and {transmittance_interpolation.lower()} in transmittance",
            "validation_summary": f"rows {lam_lo}-{lam_hi} nm; error bounds from calibration wavelengths "
                                  f"{', '.join(str(w) for w in CALIBRATION_NM)} nm, a 20 um beam and field angles up to "
                                  f"{decimal_text(max_angle)} deg (Engine vs external), plus the external spread over "
                                  f"{spread_settings}; checked on separate test "
                                  "wavelengths and settings",
            "date": date.today().isoformat(),
        },
        "plane_z_mm": "0", "status": "DECLARED",
    }
    return {"model_id": MODEL_ID, "display_name": "TiO2 metalens f 200 um (tabulated response)", "model": model}


def engine_field_angles(provisional, table, waist, bounds):
    """Run the provisional model in OpenSci at each calibration angle; compare the Engine's focus with the external one."""
    require_engine()
    if __package__:
        from .step4_engine_check import project, run
    else:
        from step4_engine_check import project, run

    entries = []
    for row in sorted((r for r in table["rows"] if r["tilt_deg"] > 0 and r.get("set") == "calibration"),
                      key=lambda r: r["tilt_deg"]):
        outcome = run(project(provisional, "tabulated", wavelengths_nm=[row["wavelength_nm"]], waist_um=waist,
                              tilt_deg=row["tilt_deg"]))
        beam = outcome["beams"][0]
        entry = {"tilt_deg": row["tilt_deg"], "wavelength_nm": row["wavelength_nm"], "engine_status": outcome["status"],
                 "engine": {k: beam.get(k) for k in ("waist_axial_um", "waist_lateral_um", "waist_radius_um", "power_W")},
                 "external": {"z_focus_um": row["z_focus_um"], "lateral_um": row["focus_xy_um"][0],
                              "w_focus_um": row["w_focus_um"], "efficiency": row["focusing_efficiency"]},
                 "z_abs_err_um": beam["waist_axial_um"] - row["z_focus_um"],
                 "lateral_abs_err_um": beam["waist_lateral_um"] - row["focus_xy_um"][0],
                 "w_rel_err": beam["waist_radius_um"] / row["w_focus_um"] - 1.0,
                 "power_rel_err": beam["power_W"] / (0.001 * row["focusing_efficiency"]) - 1.0}
        entry["within_bounds"] = (abs(entry["z_abs_err_um"]) <= bounds["z_abs_um"]
                                  and abs(entry["lateral_abs_err_um"]) <= bounds["z_abs_um"]
                                  and abs(entry["w_rel_err"]) <= bounds["w_rel"]
                                  and abs(entry["power_rel_err"]) <= bounds["power_rel"])
        entries.append(entry)
    return entries


def run_example(directory, n_g, samples, transmittance_interpolation):
    directory = Path(directory)
    table_path = directory / f"focus_nG{n_g}_N{samples}.json"
    table = json.loads(table_path.read_text(encoding="utf-8"))
    library_path = directory / table["library"]
    library = json.loads(library_path.read_text(encoding="utf-8"))
    design = table["design"]
    waist, clear_um = design["input_waist_radius_um"], design["diameter_um"] / 2
    normal = {r["wavelength_nm"]: r for r in table["rows"]
              if r["tilt_deg"] == 0 and r["input_waist_radius_um"] == waist}
    fraction = aperture_fraction(waist, clear_um)
    rows = [{"wavelength_nm": lam, "focal_length_um": equivalent_focal_length_um(normal[lam]["z_focus_um"], waist, lam),
             "transmittance": transmittance_of(normal[lam]["focusing_efficiency"], fraction)} for lam in TABLE_NM]

    convergence_tables, spread = spreads(directory, table_path, calibration_keys(table, waist))
    max_spread = largest_spread(spread)
    spread_settings = settings_text(table, convergence_tables)

    calibration = []
    for lam in TABLE_NM + CALIBRATION_NM:
        f_um, transmittance = interpolate(rows, lam)
        calibration.append(dict(deviation(normal[lam], f_um, transmittance, waist, clear_um, f"{lam} nm"),
                                role="table row" if lam in TABLE_NM else "calibration wavelength"))
    for row in table["rows"]:
        if row["tilt_deg"] == 0 and row["input_waist_radius_um"] != waist and row.get("set") == "calibration":
            f_um, transmittance = interpolate(rows, row["wavelength_nm"])
            label = f"{row['wavelength_nm']} nm, input radius {row['input_waist_radius_um']:g} um"
            calibration.append(dict(deviation(row, f_um, transmittance, row["input_waist_radius_um"], clear_um, label),
                                    role="calibration beam size"))
    z_max = max(v["external"]["z_focus_um"] for v in calibration)
    eff_min = min(v["external"]["focusing_efficiency"] for v in calibration)
    bounds = {
        "z_abs_um": ceil_sig(max(abs(v["z_abs_err_um"]) for v in calibration) + max_spread["z_rel"] * z_max),
        "w_rel": ceil_sig(max(abs(v["w_rel_err"]) for v in calibration) + max_spread["w_rel"]),
        "power_rel": ceil_sig(max(abs(v["power_rel_err"]) for v in calibration) + max_spread["eff_abs"] / eff_min),
    }

    tools = [{"name": "grcwa", "version": library["solver"].get("version") or "unknown"}]
    tools += [{"name": name, "version": version}
              for name, version in (table.get("environment", {}).get("packages") or {}).items()
              if version and name in ("numpy", "scipy")]
    configuration = {"unit_cells": {k: library[k] for k in ("assumptions", "solver", "radii_um")},
                     "focusing": {k: table[k] for k in ("design", "grid", "method")}}
    provisional = build_model(rows, bounds, 89, waist, clear_um, transmittance_interpolation, tools, library_path,
                              configuration, spread_settings)
    field = engine_field_angles(provisional, table, waist, bounds)
    max_angle = None
    for entry in field:  # accepted from the smallest angle upward until the first one outside the bounds
        if not entry["within_bounds"]:
            break
        max_angle = entry["tilt_deg"]
    if max_angle is None:
        raise ValueError("no field angle stays inside the bounds: the model would have no field-angle domain")
    optical_model = build_model(rows, bounds, max_angle, waist, clear_um, transmittance_interpolation, tools,
                                library_path, configuration, spread_settings)
    write_json(directory / "metalens_model.json", optical_model)
    report = {"example": "interop/metasurface step 3", "outcome": "COMPLETED",
              "model": str(directory / "metalens_model.json"), "rows": rows, "aperture_fraction": fraction,
              "error_bounds": bounds, "external_spread": max_spread, "convergence_tables": convergence_tables,
              "calibration": calibration, "field_angles": field, "max_field_angle_deg": max_angle,
              "focus_table": str(table_path), "library": str(library_path)}
    write_json(directory / "step3_report.json", report)
    wavelengths = [v for v in calibration if v["role"] == "calibration wavelength"]
    return {"example": report["example"], "outcome": "COMPLETED", "model": report["model"], "error_bounds": bounds,
            "max_field_angle_deg": max_angle, "external_spread": max_spread,
            "calibration_wavelength_max": {"z_rel_err": max(abs(v["z_rel_err"]) for v in wavelengths),
                                           "w_rel_err": max(abs(v["w_rel_err"]) for v in wavelengths),
                                           "efficiency_abs_err": max(abs(v["efficiency_abs_err"]) for v in wavelengths)},
            "field_angles": [{k: e[k] for k in ("tilt_deg", "z_abs_err_um", "lateral_abs_err_um", "w_rel_err",
                                                "within_bounds")} for e in field],
            "report": str(directory / "step3_report.json")}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("directory", type=Path, help="directory with the step-1 library and step-2 focus tables")
        parser.add_argument("--nG", type=int, default=101, help="library of the model (default 101)")
        parser.add_argument("--samples", type=int, default=1024, help="focus table of the model (default 1024 samples)")
        parser.add_argument("--transmittance-interpolation", choices=("LINEAR", "POINTS_ONLY"), default="LINEAR")
        args = parser.parse_args(argv)
        return run_example(args.directory, args.nG, args.samples, args.transmittance_interpolation)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
