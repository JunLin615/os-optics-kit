"""Beam expansion and focusing: design a Keplerian expander with a focusing lens, check beam-size targets, and study
one parameter at a time.

Usage:
  python examples/beam_expansion_focus.py --out DIR [design inputs] [--study NAME=VALUES ...]
  python examples/beam_expansion_focus.py --list-parameters

Design (examples/optical_designs.py ``expander_focus``, no catalog needed): Gaussian source (waist at the source)
-> ideal thin lens L1 (f1) -> L2 (f2) at f1 + f2 + gap_error -> L3 (f3), 100 mm after L2; plane ``collimated`` 50 mm
after L2, plane ``focus`` at L3 + f3 + focus_plane_shift. Beam along design +X.

Targets (checked by the Engine):
  expanded_radius   beam_radius_mm on collimated within +-expanded_radius_tolerance_rel of waist_radius * f2 / f1
                    (or --expanded-radius-target-mm)
  spot_radius       beam_radius_mm on focus at most --max-spot-radius-mm
  waist_location    waist_distance_x_mm on focus within +-waist_window_mm (positive: the waist lies before the plane)

Default studies (21 samples): gap_error_mm -5..5 (expander defocus: spot and expanded radius miss beyond about
1 mm), f3_mm 50..200 (spot radius misses from 125 mm), wavelength_nm 532..1300 (spot radius misses from 1064 nm;
1300 nm lies outside the declared lens band 400-1100 nm and is OUT_OF_MODEL), waist_radius_mm 0.25..4 (0.25 mm
misses the spot target, 3 mm passes with WARNING GAUSSIAN_TRUNCATION on the 25 mm apertures, 4 mm is OUT_OF_MODEL
with GAUSSIAN_SEVERE_TRUNCATION and no target values).

Independent check: complex-q Gaussian propagation through thin lenses (q -> q + d, 1/q -> 1/q - 1/f,
w = sqrt(-lambda / (pi Im(1/q)))) for expanded_radius, spot_radius (relative tolerance 1e-9) and waist_location
(absolute 1e-9 mm). Model basis: paraxial Gaussian beam through ideal thin lenses (no aberration, no dispersion).

Output: DIR as described in examples/design_workflow.py (nominal run, one sweep study per parameter with table.csv,
comparison with the nominal run, plots, closed-form check and report; DIR/report/report.md links everything). Stdout:
one compact JSON document.
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
    "gap_error_mm=-5,-2,-1,0,1,2,5",
    "f3_mm=50,100,125,150,200",
    "wavelength_nm=532,780,1064,1300",
    "waist_radius_mm=0.25,0.5,1,3,4",
]
REFERENCE = ("independent complex-q Gaussian propagation through thin lenses "
             "(optical_designs.gaussian_expander_focus_reference)")


def checks(params, record):
    """Engine target values vs the complex-q reference for one sample."""
    if __package__:
        from . import optical_designs as D
    else:
        import optical_designs as D

    reference = D.gaussian_expander_focus_reference(**params)
    values = dw.target_values(record)
    method = "complex-q propagation"
    rows = [
        dw.compare_value("expanded_radius", "mm", values.get("expanded_radius"), reference["expanded_radius_mm"],
                         method=method, rel=1e-9),
        dw.compare_value("spot_radius", "mm", values.get("spot_radius"), reference["spot_radius_mm"],
                         method=method, rel=1e-9),
        dw.compare_value("waist_location", "mm", values.get("waist_location"), reference["waist_distance_mm"],
                         method=method, abs_tol=1e-9),
    ]
    analysis = {"reference_focus_waist_radius_mm": reference["focus_waist_radius_mm"],
                "magnification": float(params["f2_mm"]) / float(params["f1_mm"])}
    return {"rows": rows, "analysis": analysis}


def config():
    if __package__:
        from . import optical_designs as D
    else:
        import optical_designs as D

    return {"workflow": "beam_expansion_focus", "design": "expander_focus", "info": D.DESIGN_INFO["expander_focus"],
            "builder": D.expander_focus, "checks": checks, "reference_method": REFERENCE,
            "default_studies": DEFAULT_STUDIES, "max_evaluations": 40}


def main(argv=None):
    return dw.main(config(), {}, argv, description=__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
