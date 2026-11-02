"""Polarization control: a rotatable waveplate before a rotatable linear polarizer; check transmitted power and the
polarization direction after the plate, and study one parameter at a time.

Usage:
  python examples/polarization_control.py --out DIR [design inputs] [--study NAME=VALUES ...]
  python examples/polarization_control.py --list-parameters

Design (examples/optical_designs.py ``polarization_control``, no catalog needed): linearly polarized Gaussian source
(x = -100 mm) -> waveplate HWP (native retarder, x = 0) -> plane ``after_hwp`` (x = 25) -> linear polarizer POL
(x = 50) -> plane ``detector`` (x = 100). Beam along design +X. Angles are measured from design +Y toward design +Z
(right-handed about the beam): the source polarization, the plate fast axis, the polarizer transmission axis and the
reported polarization azimuth. ``retarder_model`` ZERO_ORDER scales the retardance as design_wavelength / wavelength;
ACHROMATIC keeps it constant inside ``retarder_band_nm``.

Targets (checked by the Engine):
  transmitted_power  power_W on detector at least --min-power-w (default 0.8 mW; e.g. --min-power-w 0.85mW)
  output_azimuth     polarization_azimuth_deg on after_hwp within +-azimuth_tolerance_deg of --azimuth-target-deg
                     (default 45 deg: a half-wave plate at 22.5 deg turns 0 deg light to 45 deg); the azimuth of
                     circular light is undefined and gives UNKNOWN

Default studies (13 samples): hwp_angle_deg 0..67.5 (power below the 0.8 mW threshold at 0, 7.5, 45, 60 and 67.5
deg, where Malus' law gives 0.446, 0.668, 0.446, 0.061 and 0.0009 mW; the azimuth target is met only at 22.5 deg)
and wavelength_nm 1000..1100 (the retarder is characterized for 1000-1060 nm: 1064 and 1100 nm are OUT_OF_MODEL
with MODEL_WAVELENGTH_OUTSIDE_BAND although their targets read PASS).

Independent check: Jones calculus of linear input -> retarder R(theta) diag(1, exp(i 2 pi Gamma)) R(-theta) ->
polarizer with finite extinction ratio (optical_designs.jones_polarization_reference) for the detector power
(relative tolerance 1e-12), the azimuth (absolute 1e-9 deg) and the Stokes parameters s1, s2, s3 and power read from
the saved run on after_hwp (absolute 1e-12); Malus' law P0 T_hwp T_pol [cos^2(2 theta - phi - alpha) +
sin^2(...)/ER] whenever the effective retardance is exactly half a wave. Model basis: ideal thin Jones elements.

Output: DIR as described in examples/design_workflow.py; every sample's after_hwp Stokes parameters, azimuth and
ellipticity are in studies/<NAME>/closed_form.json ("analysis"). Stdout: one compact JSON document.
Exit code from the nominal design: 0 PASS (WARNING included), 1 FAIL, 2 UNKNOWN, 3 OUT_OF_MODEL, 4 ERROR (the
Engine refused the nominal design: the summary holds nominal.error) or refused input (JSON error on stderr,
nothing run). Unmet or out-of-model study samples do not change the exit code; they are counted and listed in the
output.
"""

from __future__ import annotations

if __package__:
    from . import design_workflow as dw
else:
    import design_workflow as dw

DEFAULT_STUDIES = [
    "hwp_angle_deg=0,7.5,15,22.5,30,45,60,67.5",
    "wavelength_nm=1000,1030,1060,1064,1100",
]
DEFAULTS = {"azimuth_target_deg": 45.0}
REFERENCE = ("independent Jones calculus (optical_designs.jones_polarization_reference) and Malus' law "
             "(optical_designs.malus_power_reference) for an exact half-wave retardance")


def _after_hwp(record):
    if not record.get("run_dir"):
        return None
    from os_optics.results import load_run

    rows = load_run(record["run_dir"], verify=False).arrivals("after_hwp")
    return rows[0] if rows else None


def checks(params, record):
    """Engine values (targets and the saved after_hwp arrival) vs Jones calculus / Malus' law for one sample."""
    if __package__:
        from . import optical_designs as D
    else:
        import optical_designs as D

    jones = D.jones_polarization_reference(**params)
    values = dw.target_values(record)
    arrival = _after_hwp(record) or {}
    method = "Jones calculus"
    rows = [dw.compare_value("transmitted_power", "W", values.get("transmitted_power"), jones["power_w"],
                             method=method, rel=1e-12)]
    if "output_azimuth" in values:
        rows.append(dw.compare_value("output_azimuth", "deg", values.get("output_azimuth"), jones["azimuth_deg"],
                                     method=method, abs_tol=1e-9, period=180.0))
    rows.append(dw.compare_value("after_hwp power_W", "W", arrival.get("power_W"), jones["power_after_hwp_w"],
                                 method=method, rel=1e-12))
    for name in ("stokes_s1", "stokes_s2", "stokes_s3"):
        rows.append(dw.compare_value(f"after_hwp {name}", "1", arrival.get(name), jones[name], method=method,
                                     abs_tol=1e-12))
    gamma = float(params["retardance_waves"])
    if params["retarder_model"] == "ZERO_ORDER":
        gamma *= float(params["design_wavelength_nm"]) / float(params["wavelength_nm"])
    if gamma == 0.5:
        rows.append(dw.compare_value("transmitted_power", "W", values.get("transmitted_power"),
                                     D.malus_power_reference(**params), method="Malus law", rel=1e-12))
    analysis = {"after_hwp": {key: arrival.get(key) for key in (
        "stokes_s1", "stokes_s2", "stokes_s3", "polarization_azimuth_deg", "polarization_ellipticity_deg",
        "polarization_handedness", "power_W")}, "effective_retardance_waves": gamma,
        "reference_azimuth_deg": jones["azimuth_deg"], "reference_ellipticity_deg": jones["ellipticity_deg"]}
    return {"rows": rows, "analysis": analysis}


def config():
    if __package__:
        from . import optical_designs as D
    else:
        import optical_designs as D

    return {"workflow": "polarization_control", "design": "polarization_control",
            "info": D.DESIGN_INFO["polarization_control"], "builder": D.polarization_control, "checks": checks,
            "reference_method": REFERENCE, "default_studies": DEFAULT_STUDIES, "max_evaluations": 40,
            "choices": {"retarder_model": ["ZERO_ORDER", "ACHROMATIC"]}}


def main(argv=None):
    return dw.main(config(), DEFAULTS, argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
