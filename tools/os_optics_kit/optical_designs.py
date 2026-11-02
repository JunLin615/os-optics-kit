"""Parameterized optical designs as OpenSci Projects, with independent closed-form references.

Each builder returns a ``ProjectDocumentV2`` dictionary (``schema: opensci.project``, version 2.0) built from the
released APIs ``os_optics.native_library`` (``build_native_model`` + ``add_to_project``) and
``os_optics.composition.axis_angle_quaternion`` / ``os_optics.math.quat_multiply``. The native designs need
no catalog; the ``catalog_*`` variants start from a catalog example Project and need an installed catalog release.

Designs (``DESIGN_INFO`` lists every parameter with unit, default and meaning, the observation planes, the targets,
the frames and the model basis):

* ``expander_focus``        Keplerian expander (ideal thin lenses L1, L2) and focusing lens L3; planes
                            ``collimated`` and ``focus``.
* ``polarization_control``  linear source, waveplate (native retarder), linear polarizer; planes ``after_hwp`` and
                            ``detector``.
* ``alignment``             source, flat fold mirror, observation planes ``near`` and ``far`` along the reflected beam.
* ``catalog_beam_expander`` and ``catalog_polarization_control``: the catalog example kinds ``beam_expander`` and
                            ``polarization`` with the same kind of parameters.

Independent references (plain Python, no Engine data; used to check the Engine results):
``gaussian_expander_focus_reference`` (complex-q / ABCD propagation through thin lenses),
``jones_polarization_reference`` and ``malus_power_reference`` (Jones calculus / Malus law with transmittance and
extinction ratio), ``mirror_alignment_reference`` (exact vector reflection and plane intersection) with the
small-angle forms ``mirror_deviation_mrad`` (2θ in the plane of incidence, 2θ·cos(AOI) out of it) and
``two_plane_pointing`` (pointing from two plane positions).

Run helpers: ``check_design`` (``json_api.check_project``), ``run_design`` (one in-process run), ``design_outcome``
(status, model-scope issues, issues and target values of a run) and ``alignment_errors`` (position and pointing
errors from the two planes of a run).

Example::

    from examples.optical_designs import expander_focus, run_design, design_outcome
    outcome = design_outcome(run_design(expander_focus(f3_mm=150.0)))
    outcome["targets"]["spot_radius"]   # {"value": 0.0248..., "unit": "mm", "status": "FAIL", ...}
"""

from __future__ import annotations

import cmath
import copy
import math
from typing import Any, Mapping, Sequence

from os_optics.composition import axis_angle_quaternion
from os_optics.math import quat_multiply
from os_optics.native_library import add_to_project, build_native_model

# Element / plane orientations (unit quaternions w, x, y, z). Element local +Z is its optical axis (a mirror's local
# +Z is its face normal); local +Y is design +Z ("up") in every design here.
BEAM_ALONG_X = (0.5, 0.5, 0.5, 0.5)       # local +Z -> design +X, local +Y -> design +Z, local +X -> design +Y
BEAM_AGAINST_X = (0.5, 0.5, -0.5, -0.5)   # local +Z -> design -X, local +Y -> design +Z, local +X -> design -Y

BENCHMARK = {
    "name": "Free-Space Optical Experiment Benchmark",
    "authors": "JunLin615 and contributors",
    "url": "https://github.com/JunLin615/free-space-optical-experiment-benchmark",
    "commit": "e72ad3a9b6be12c7f0dbb00121fda23289c4e908",
    "license": "CC BY 4.0 (LICENSE-CONTENT)",
}
_ADAPTATION = ("task family only: the design, its numbers and its checks were written for OpenSci; no case text, "
               "gold answer or scoring material is copied")


def _case(case_id, case_revision, title, used_for, *, release=None, content_sha256=None, file_sha256=None):
    record = {"case_id": case_id, "case_revision": case_revision, "title": title, "used_for": used_for,
              "benchmark_release": release, "commit": BENCHMARK["commit"]}
    if content_sha256:
        record["case_content_sha256"] = content_sha256
    if file_sha256:
        record["file_sha256"] = file_sha256
    record["attribution"] = (f"Informed by {case_id} (case revision {case_revision}) of the {BENCHMARK['name']}, "
                             f"{BENCHMARK['authors']}, commit {BENCHMARK['commit'][:8]}, {BENCHMARK['url']}, "
                             f"{BENCHMARK['license']}. Changes: {_ADAPTATION}.")
    return record


# ------------------------------------------------------------------------------------------------- building blocks

def _num(value: float) -> str:
    """Native-library parameters are decimal strings."""
    return repr(float(value))


def turned(quaternion_wxyz: Sequence[float], axis_local: Sequence[float], angle_deg: float) -> list[float]:
    """``quaternion_wxyz`` turned by ``angle_deg`` about ``axis_local`` given in the element's own frame (right
    multiplication: the rotation axis passes through the element origin)."""
    return [float(v) for v in quat_multiply(tuple(quaternion_wxyz), axis_angle_quaternion(tuple(axis_local), angle_deg))]


def _rotated_in_design(quaternion_wxyz: Sequence[float], axis_design: Sequence[float], angle_deg: float) -> list[float]:
    """``quaternion_wxyz`` turned by ``angle_deg`` about ``axis_design`` given in the design frame (left multiplication)."""
    return [float(v) for v in quat_multiply(axis_angle_quaternion(tuple(axis_design), angle_deg), tuple(quaternion_wxyz))]


def gaussian_source(*, wavelength_nm: float, waist_radius_mm: float, power_w: float = 1e-3,
                    polarization_angle_deg: float = 0.0, position_mm: Sequence[float] = (0.0, 0.0, 0.0),
                    quaternion_wxyz: Sequence[float] = BEAM_ALONG_X) -> dict[str, Any]:
    """A TEM00 Gaussian source ``SRC`` with its waist at the source position, emitting along its local +Z; linear
    polarization at ``polarization_angle_deg`` from its local +X toward local +Y."""
    return {"instance_id": "SRC", "optical_role": "source",
            "custom_part": {"custom_part_key": "user-laser", "description": "Gaussian laser source"},
            "nominal_pose": {"position_mm": [float(c) for c in position_mm],
                             "quaternion_wxyz": [float(c) for c in quaternion_wxyz]},
            "selected_parameters": [], "retained_dofs": [],
            "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": float(wavelength_nm),
                             "power_w": float(power_w), "waist_radius_mm": float(waist_radius_mm),
                             "waist_position_mm": 0.0, "m2": 1.0,
                             "polarization": {"kind": "LINEAR", "angle_deg": float(polarization_angle_deg)}}}


def observation_plane(observation_id: str, position_mm: Sequence[float], *,
                      quaternion_wxyz: Sequence[float] = BEAM_ALONG_X, radius_mm: float = 12.5) -> dict[str, Any]:
    """A transparent circular observation plane; its u / v axes are its local +X / +Y, its normal local +Z."""
    return {"observation_id": observation_id,
            "pose": {"position_mm": [float(c) for c in position_mm],
                     "quaternion_wxyz": [float(c) for c in quaternion_wxyz]},
            "aperture": {"shape": "circle", "radius_mm": float(radius_mm), "center_offset_mm": [0.0, 0.0],
                         "obstruction_radius_mm": 0.0},
            "absorbing": False, "medium": "air"}


def _target(target_id, metric, observation_id, unit, *, minimum=None, maximum=None, target_value=None,
            tolerance_rel=None):
    t = {"target_id": target_id, "metric": metric, "observation_id": observation_id, "unit": unit, "parameters": []}
    for key, value in (("minimum", minimum), ("maximum", maximum), ("target_value", target_value),
                       ("tolerance_rel", tolerance_rel)):
        if value is not None:
            t[key] = float(value)
    return t


def _project(source: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "opensci.project", "project_schema_version": "2.0", "components": [source],
            "observations": [], "targets": [], "materials": []}


def _add(document, class_id, parameters, instance_id, position_mm, quaternion_wxyz):
    build = build_native_model(class_id, parameters)
    return add_to_project(document, build, instance_id=instance_id, position_mm=tuple(float(c) for c in position_mm),
                          quaternion_wxyz=tuple(float(c) for c in quaternion_wxyz))


def by_id(rows: Sequence[Mapping[str, Any]], field: str, identifier: str) -> Any:
    """The one row of ``rows`` whose ``field`` equals ``identifier``."""
    matches = [row for row in rows if row.get(field) == identifier]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {field}={identifier!r}, found {len(matches)}")
    return matches[0]


# ------------------------------------------------------------------------------------------------- 1. expansion + focus

def expander_focus(*, f1_mm: float = 50.0, f2_mm: float = 150.0, f3_mm: float = 100.0, gap_error_mm: float = 0.0,
                   waist_radius_mm: float = 0.5, wavelength_nm: float = 780.0, focus_plane_shift_mm: float = 0.0,
                   max_spot_radius_mm: float = 0.02, expanded_radius_target_mm: float | None = None,
                   expanded_radius_tolerance_rel: float = 0.02, waist_window_mm: float = 0.5,
                   source_to_l1_mm: float = 100.0, l2_to_l3_mm: float = 100.0, collimated_plane_after_l2_mm: float = 50.0,
                   clear_aperture_mm: float = 25.0, lens_band_nm: Sequence[float] = (400.0, 1100.0),
                   power_w: float = 1e-3) -> dict[str, Any]:
    """Keplerian expander L1 (f1) + L2 (f2) and focusing lens L3 (f3), native ideal thin lenses, beam along design +X.

    Layout (design x, mm): source waist at ``-source_to_l1_mm``; L1 at 0; L2 at ``f1 + f2 + gap_error``; plane
    ``collimated`` at L2 + ``collimated_plane_after_l2_mm``; L3 at L2 + ``l2_to_l3_mm``; plane ``focus`` at
    L3 + f3 + ``focus_plane_shift_mm``. Targets: ``expanded_radius`` (``beam_radius_mm`` on ``collimated`` within
    ``expanded_radius_tolerance_rel`` of ``expanded_radius_target_mm``, default ``waist_radius_mm * f2 / f1``),
    ``spot_radius`` (``beam_radius_mm`` on ``focus`` at most ``max_spot_radius_mm``) and ``waist_location``
    (``waist_distance_x_mm`` on ``focus`` within ±``waist_window_mm``)."""
    document = _project(gaussian_source(wavelength_nm=wavelength_nm, waist_radius_mm=waist_radius_mm, power_w=power_w,
                                        position_mm=(-float(source_to_l1_mm), 0.0, 0.0)))
    x2 = float(f1_mm) + float(f2_mm) + float(gap_error_mm)
    x3 = x2 + float(l2_to_l3_mm)
    for instance_id, focal_mm, x_mm in (("L1", f1_mm, 0.0), ("L2", f2_mm, x2), ("L3", f3_mm, x3)):
        document = _add(document, "lens.ideal_thin",
                        {"focal_length_mm": _num(focal_mm), "clear_aperture_diameter_mm": _num(clear_aperture_mm),
                         "valid_band_nm": [_num(lens_band_nm[0]), _num(lens_band_nm[1])],
                         "model_id": f"thin-lens-{instance_id.lower()}"},
                        instance_id, (x_mm, 0.0, 0.0), BEAM_ALONG_X)
    document["observations"] = [
        observation_plane("collimated", (x2 + float(collimated_plane_after_l2_mm), 0.0, 0.0)),
        observation_plane("focus", (x3 + float(f3_mm) + float(focus_plane_shift_mm), 0.0, 0.0)),
    ]
    expected = (float(waist_radius_mm) * float(f2_mm) / float(f1_mm) if expanded_radius_target_mm is None
                else float(expanded_radius_target_mm))
    document["targets"] = [
        _target("expanded_radius", "beam_radius_mm", "collimated", "mm", target_value=expected,
                tolerance_rel=expanded_radius_tolerance_rel),
        _target("spot_radius", "beam_radius_mm", "focus", "mm", maximum=max_spot_radius_mm),
        _target("waist_location", "waist_distance_x_mm", "focus", "mm", minimum=-float(waist_window_mm),
                maximum=float(waist_window_mm)),
    ]
    return document


def _q_radius(q: complex, lam_mm: float) -> float:
    inverse = 1.0 / q
    return math.sqrt(-lam_mm / (math.pi * inverse.imag))


def gaussian_expander_focus_reference(*, f1_mm: float = 50.0, f2_mm: float = 150.0, f3_mm: float = 100.0,
                                      gap_error_mm: float = 0.0, waist_radius_mm: float = 0.5,
                                      wavelength_nm: float = 780.0, focus_plane_shift_mm: float = 0.0,
                                      source_to_l1_mm: float = 100.0, l2_to_l3_mm: float = 100.0,
                                      collimated_plane_after_l2_mm: float = 50.0, **_criteria: Any) -> dict[str, float]:
    """Independent check (not Engine physics): complex-q propagation of the ``expander_focus`` layout.

    ``q = z + i·z_R`` (z: distance after the waist; z_R = π w0² / λ), free space ``q -> q + d``, thin lens
    ``1/q -> 1/q - 1/f``, radius ``w = sqrt(-λ / (π·Im(1/q)))``. Returns ``expanded_radius_mm`` (radius on
    ``collimated``), ``spot_radius_mm`` (radius on ``focus``), ``waist_distance_mm`` (Re q on ``focus``: positive =
    waist before the plane), ``focus_waist_radius_mm`` and the far-field estimate ``spot_estimate_mm = λ f3 / (π w)``
    with w the radius at L3. Criterion keywords of ``expander_focus`` are accepted and ignored."""
    lam = float(wavelength_nm) * 1e-6
    q = complex(float(source_to_l1_mm), math.pi * float(waist_radius_mm) ** 2 / lam)

    def lens(q_in, f):
        return 1.0 / (1.0 / q_in - 1.0 / float(f))

    q = lens(q, f1_mm)
    q = lens(q + (float(f1_mm) + float(f2_mm) + float(gap_error_mm)), f2_mm)
    q_collimated = q + float(collimated_plane_after_l2_mm)
    q_l3 = q + float(l2_to_l3_mm)
    w_l3 = _q_radius(q_l3, lam)
    q_focus = lens(q_l3, f3_mm) + float(f3_mm) + float(focus_plane_shift_mm)
    return {"expanded_radius_mm": _q_radius(q_collimated, lam), "spot_radius_mm": _q_radius(q_focus, lam),
            "waist_distance_mm": q_focus.real, "focus_waist_radius_mm": math.sqrt(lam * q_focus.imag / math.pi),
            "spot_estimate_mm": lam * float(f3_mm) / (math.pi * w_l3)}


# ------------------------------------------------------------------------------------------------- 2. polarization

def polarization_control(*, hwp_angle_deg: float = 22.5, polarizer_angle_deg: float = 45.0,
                         source_polarization_angle_deg: float = 0.0, wavelength_nm: float = 1030.0,
                         retardance_waves: float = 0.5, design_wavelength_nm: float = 1030.0,
                         retarder_model: str = "ZERO_ORDER", retarder_band_nm: Sequence[float] = (1000.0, 1060.0),
                         hwp_transmittance: float = 0.99, polarizer_transmittance: float = 0.9,
                         extinction_ratio: float = 1000.0, polarizer_band_nm: Sequence[float] = (600.0, 1200.0),
                         min_power_w: float = 0.8e-3, power_w: float = 1e-3, azimuth_target_deg: float | None = None,
                         azimuth_tolerance_deg: float = 0.5, waist_radius_mm: float = 1.0) -> dict[str, Any]:
    """Linear source -> waveplate ``HWP`` (native ``waveplate.retarder``) -> linear polarizer ``POL`` (native
    ``polarizer.linear``) -> plane ``detector``; plane ``after_hwp`` between the plates. Beam along design +X.

    Positions (design x, mm): source -100, HWP 0, ``after_hwp`` 25, POL 50, ``detector`` 100. Angles are measured
    from design +Y toward design +Z (right-handed about the beam direction +X); the HWP fast axis and the POL
    transmission axis are their local +X, each turned about its own optical axis (local +Z, through the element
    centre on axis). ``retarder_model``: ``ZERO_ORDER`` (retardance ``retardance_waves · design_wavelength_nm / λ``)
    or ``ACHROMATIC`` (constant over ``retarder_band_nm``). Targets: ``transmitted_power`` (``power_W`` on
    ``detector`` at least ``min_power_w``) and, when ``azimuth_target_deg`` is given, ``output_azimuth``
    (``polarization_azimuth_deg`` on ``after_hwp`` within ±``azimuth_tolerance_deg``; the metric lies in [0, 180))."""
    if retarder_model not in ("ZERO_ORDER", "ACHROMATIC"):
        raise ValueError("retarder_model must be 'ZERO_ORDER' or 'ACHROMATIC'")
    document = _project(gaussian_source(wavelength_nm=wavelength_nm, waist_radius_mm=waist_radius_mm, power_w=power_w,
                                        polarization_angle_deg=source_polarization_angle_deg,
                                        position_mm=(-100.0, 0.0, 0.0)))
    document = _add(document, "waveplate.retarder",
                    {"retardance_waves": _num(retardance_waves), "design_wavelength_nm": _num(design_wavelength_nm),
                     "retardance_dispersion": retarder_model, "transmittance": _num(hwp_transmittance),
                     "diameter_mm": "25.4", "valid_band_nm": [_num(retarder_band_nm[0]), _num(retarder_band_nm[1])],
                     "model_id": "retarder-hwp"},
                    "HWP", (0.0, 0.0, 0.0), turned(BEAM_ALONG_X, (0, 0, 1), hwp_angle_deg))
    document = _add(document, "polarizer.linear",
                    {"transmittance": _num(polarizer_transmittance), "extinction_ratio": _num(extinction_ratio),
                     "diameter_mm": "25.4", "valid_band_nm": [_num(polarizer_band_nm[0]), _num(polarizer_band_nm[1])],
                     "model_id": "linear-polarizer"},
                    "POL", (50.0, 0.0, 0.0), turned(BEAM_ALONG_X, (0, 0, 1), polarizer_angle_deg))
    document["observations"] = [observation_plane("after_hwp", (25.0, 0.0, 0.0)),
                                observation_plane("detector", (100.0, 0.0, 0.0))]
    document["targets"] = [_target("transmitted_power", "power_W", "detector", "W", minimum=min_power_w)]
    if azimuth_target_deg is not None:
        document["targets"].append(_target("output_azimuth", "polarization_azimuth_deg", "after_hwp", "deg",
                                           minimum=float(azimuth_target_deg) - float(azimuth_tolerance_deg),
                                           maximum=float(azimuth_target_deg) + float(azimuth_tolerance_deg)))
    return document


def jones_polarization_reference(*, hwp_angle_deg: float = 22.5, polarizer_angle_deg: float = 45.0,
                                 source_polarization_angle_deg: float = 0.0, retardance_waves: float = 0.5,
                                 power_w: float = 1e-3, hwp_transmittance: float = 0.99,
                                 polarizer_transmittance: float = 0.9, extinction_ratio: float = 1000.0,
                                 wavelength_nm: float = 1030.0, design_wavelength_nm: float = 1030.0,
                                 retarder_model: str = "ZERO_ORDER", **_unused: Any) -> dict[str, float | None]:
    """Independent check (not Engine physics): Jones calculus of linear input -> retarder -> linear polarizer.

    Basis (e1, e2) = (design +Y, design +Z), angles from e1 toward e2. Retarder at angle θ:
    ``R(θ)·diag(1, exp(+i·2π·Γ))·R(-θ)`` with Γ = ``retardance_waves`` (times ``design_wavelength_nm / wavelength_nm``
    for ``ZERO_ORDER``), field scaled by sqrt(T_hwp). Polarizer: power ``T_pol·|E_par|² + (T_pol/ER)·|E_perp|²``.
    The phase sign is the one for which ``stokes_s3 = 2·Im(conj(E1)·E2)/S0`` follows the Engine metric convention
    (-1 right circular, +1 left circular). Returns ``power_after_hwp_w``, ``power_w`` (after the polarizer),
    ``stokes_s1/s2/s3``, ``azimuth_deg`` in [0, 180) (``None`` for circular light), ``ellipticity_deg`` = asin(s3)/2."""
    gamma = float(retardance_waves)
    if retarder_model == "ZERO_ORDER":
        gamma *= float(design_wavelength_nm) / float(wavelength_nm)
    phi = math.radians(float(source_polarization_angle_deg))
    e1, e2 = complex(math.cos(phi)), complex(math.sin(phi))
    t = math.radians(float(hwp_angle_deg))
    c, s = math.cos(t), math.sin(t)
    fast, slow = c * e1 + s * e2, -s * e1 + c * e2           # components on the fast / slow axes
    slow *= cmath.exp(1j * 2.0 * math.pi * gamma)
    amplitude = math.sqrt(float(hwp_transmittance))
    e1, e2 = amplitude * (c * fast - s * slow), amplitude * (s * fast + c * slow)
    s0 = abs(e1) ** 2 + abs(e2) ** 2
    s1, s2, s3 = (abs(e1) ** 2 - abs(e2) ** 2) / s0, 2.0 * (e1.conjugate() * e2).real / s0, 2.0 * (e1.conjugate() * e2).imag / s0
    a = math.radians(float(polarizer_angle_deg))
    par = math.cos(a) * e1 + math.sin(a) * e2
    perp = -math.sin(a) * e1 + math.cos(a) * e2
    t_pol = float(polarizer_transmittance)
    transmitted = t_pol * abs(par) ** 2 + t_pol / float(extinction_ratio) * abs(perp) ** 2
    linear = math.hypot(s1, s2)
    azimuth = None if linear < 1e-12 else math.degrees(0.5 * math.atan2(s2, s1)) % 180.0
    return {"power_after_hwp_w": float(power_w) * s0, "power_w": float(power_w) * transmitted,
            "stokes_s1": s1, "stokes_s2": s2, "stokes_s3": s3, "azimuth_deg": azimuth,
            "ellipticity_deg": math.degrees(0.5 * math.asin(max(-1.0, min(1.0, s3))))}


def malus_power_reference(*, hwp_angle_deg: float = 22.5, polarizer_angle_deg: float = 45.0,
                          source_polarization_angle_deg: float = 0.0, power_w: float = 1e-3,
                          hwp_transmittance: float = 0.99, polarizer_transmittance: float = 0.9,
                          extinction_ratio: float = 1000.0, **_unused: Any) -> float:
    """Independent check: an ideal half-wave plate turns linear polarization at φ to 2θ − φ; Malus law with finite
    extinction gives ``P = P0·T_hwp·T_pol·[cos²(2θ − φ − α) + sin²(2θ − φ − α)/ER]`` (W)."""
    x = math.radians(2.0 * float(hwp_angle_deg) - float(source_polarization_angle_deg) - float(polarizer_angle_deg))
    return (float(power_w) * float(hwp_transmittance) * float(polarizer_transmittance)
            * (math.cos(x) ** 2 + math.sin(x) ** 2 / float(extinction_ratio)))


# ------------------------------------------------------------------------------------------------- 3. alignment

def _fold_frames(mirror_aoi_deg: float) -> tuple[list[float], list[float]]:
    """Nominal mirror and plane quaternions for a +X beam folded in the design XY plane at ``mirror_aoi_deg``."""
    mirror = _rotated_in_design(BEAM_AGAINST_X, (0, 0, 1), -float(mirror_aoi_deg))
    plane = _rotated_in_design(BEAM_ALONG_X, (0, 0, 1), 180.0 - 2.0 * float(mirror_aoi_deg))
    return mirror, plane


def alignment(*, mirror_tilt_local_x_mrad: float = 0.0, mirror_tilt_local_y_mrad: float = 0.0,
              source_offset_y_mm: float = 0.0, source_offset_z_mm: float = 0.0,
              source_tilt_local_x_mrad: float = 0.0, source_tilt_local_y_mrad: float = 0.0,
              near_plane_mm: float = 100.0, far_plane_mm: float = 400.0, source_to_mirror_mm: float = 100.0,
              mirror_aoi_deg: float = 45.0, position_tolerance_mm: float = 0.1, pointing_tolerance_mrad: float = 0.5,
              plane_radius_mm: float = 12.5, wavelength_nm: float = 633.0, waist_radius_mm: float = 0.5,
              mirror_band_nm: Sequence[float] = (400.0, 1100.0), power_w: float = 1e-3) -> dict[str, Any]:
    """Source -> flat fold mirror ``M1`` (native ``mirror.flat``) -> planes ``near`` and ``far`` on the nominal
    reflected axis.

    The source sits at design x = ``-source_to_mirror_mm`` and emits along +X; ``M1`` (face centre at the origin)
    folds the beam in the design XY plane at ``mirror_aoi_deg`` (45: into +Y). The planes are centred on the nominal
    reflected axis at ``near_plane_mm`` and ``far_plane_mm`` after the mirror, normal along it; plane u = design
    +Z × (nominal reflected direction) (for 45°: design -X), v = design +Z.
    Mirror tilts turn ``M1`` about its local in-face axes through its face centre: local +Y = design +Z (beam moves in
    the plane of incidence, along u, by 2θ), local +X = the in-face axis in the plane of incidence (beam moves along
    v by 2θ·cos(AOI)); the local-Y tilt is applied first. Source offsets move the source along design Y / Z; source
    tilts turn it about its local +X (= design +Y; positive tips the beam toward design -Z) and local +Y (= design
    +Z; positive turns the beam toward design +Y), local X first.
    Targets: ``near_position_u/v`` and ``far_position_u/v`` (``position_u_mm`` / ``position_v_mm`` within
    ±``position_tolerance_mm``), ``far_pointing_u/v`` (``direction_cosine_u`` / ``_v`` on ``far`` within
    ±sin(``pointing_tolerance_mrad``))."""
    if not 0.0 < float(near_plane_mm) < float(far_plane_mm):
        raise ValueError("need 0 < near_plane_mm < far_plane_mm")
    source_q = turned(turned(BEAM_ALONG_X, (1, 0, 0), math.degrees(float(source_tilt_local_x_mrad) * 1e-3)),
                      (0, 1, 0), math.degrees(float(source_tilt_local_y_mrad) * 1e-3))
    document = _project(gaussian_source(wavelength_nm=wavelength_nm, waist_radius_mm=waist_radius_mm, power_w=power_w,
                                        position_mm=(-float(source_to_mirror_mm), float(source_offset_y_mm),
                                                     float(source_offset_z_mm)),
                                        quaternion_wxyz=source_q))
    mirror_q, plane_q = _fold_frames(mirror_aoi_deg)
    mirror_q = turned(turned(mirror_q, (0, 1, 0), math.degrees(float(mirror_tilt_local_y_mrad) * 1e-3)),
                      (1, 0, 0), math.degrees(float(mirror_tilt_local_x_mrad) * 1e-3))
    document = _add(document, "mirror.flat",
                    {"diameter_mm": "25.4", "reflectance": "0.99",
                     "valid_band_nm": [_num(mirror_band_nm[0]), _num(mirror_band_nm[1])], "model_id": "fold-mirror"},
                    "M1", (0.0, 0.0, 0.0), mirror_q)
    fold = math.radians(180.0 - 2.0 * float(mirror_aoi_deg))
    axis = (math.cos(fold), math.sin(fold), 0.0)
    document["observations"] = [
        observation_plane(oid, tuple(distance * c for c in axis), quaternion_wxyz=plane_q, radius_mm=plane_radius_mm)
        for oid, distance in (("near", float(near_plane_mm)), ("far", float(far_plane_mm)))]
    tol, cos_tol = float(position_tolerance_mm), math.sin(float(pointing_tolerance_mrad) * 1e-3)
    document["targets"] = [
        _target(f"{plane}_position_{axis_name}", f"position_{axis_name}_mm", plane, "mm", minimum=-tol, maximum=tol)
        for plane in ("near", "far") for axis_name in ("u", "v")]
    document["targets"] += [
        _target(f"far_pointing_{axis_name}", f"direction_cosine_{axis_name}", "far", "1", minimum=-cos_tol,
                maximum=cos_tol) for axis_name in ("u", "v")]
    return document


# small vector helpers for the independent geometry
def _rot(axis: Sequence[float], angle_rad: float) -> list[list[float]]:
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / n, y / n, z / n
    c, s, k = math.cos(angle_rad), math.sin(angle_rad), 1.0 - math.cos(angle_rad)
    return [[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
            [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
            [z * x * k - y * s, z * y * k + x * s, c + z * z * k]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    return [c / n for c in v]


def mirror_alignment_reference(*, mirror_tilt_local_x_mrad: float = 0.0, mirror_tilt_local_y_mrad: float = 0.0,
                               source_offset_y_mm: float = 0.0, source_offset_z_mm: float = 0.0,
                               source_tilt_local_x_mrad: float = 0.0, source_tilt_local_y_mrad: float = 0.0,
                               near_plane_mm: float = 100.0, far_plane_mm: float = 400.0,
                               source_to_mirror_mm: float = 100.0, mirror_aoi_deg: float = 45.0,
                               **_unused: Any) -> dict[str, float]:
    """Independent check (not Engine physics): the chief ray of the ``alignment`` layout by exact vector geometry.

    Frames are built from vectors (no quaternions): source axes (local X, Y, Z) = (design +Y, +Z, +X) turned by the
    source tilts; mirror normal n0 = unit(o - d) for the nominal input d = +X and output o, local Y = design +Z,
    local X = Y × n0, then the mirror tilts. Reflection ``r = d - 2(d·n)n``; intersection with each plane. Returns
    ``{near,far}_{u,v}_mm``, ``far_direction_cosine_{u,v}`` and ``pointing_{u,v}_mrad`` = atan2(Δu or Δv, plane
    separation)."""
    mrad = 1e-3
    source = [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]          # columns: local X, Y, Z in design
    source = _mul(_mul(source, _rot((1, 0, 0), float(source_tilt_local_x_mrad) * mrad)),
                  _rot((0, 1, 0), float(source_tilt_local_y_mrad) * mrad))
    d = _apply(source, [0.0, 0.0, 1.0])
    p = [-float(source_to_mirror_mm), float(source_offset_y_mm), float(source_offset_z_mm)]
    fold = math.radians(180.0 - 2.0 * float(mirror_aoi_deg))
    o = [math.cos(fold), math.sin(fold), 0.0]
    n0 = _unit([o[i] - (1.0, 0.0, 0.0)[i] for i in range(3)])
    y_l = [0.0, 0.0, 1.0]
    x_l = _cross(y_l, n0)
    frame = [[x_l[i], y_l[i], n0[i]] for i in range(3)]
    frame = _mul(_mul(frame, _rot((0, 1, 0), float(mirror_tilt_local_y_mrad) * mrad)),
                 _rot((1, 0, 0), float(mirror_tilt_local_x_mrad) * mrad))
    n = _apply(frame, [0.0, 0.0, 1.0])
    t = -_dot(p, n) / _dot(d, n)
    hit = [p[i] + t * d[i] for i in range(3)]
    r = [d[i] - 2.0 * _dot(d, n) * n[i] for i in range(3)]
    u_axis, v_axis = _cross([0.0, 0.0, 1.0], o), [0.0, 0.0, 1.0]
    out: dict[str, float] = {}
    for name, distance in (("near", float(near_plane_mm)), ("far", float(far_plane_mm))):
        centre = [distance * c for c in o]
        s = _dot([centre[i] - hit[i] for i in range(3)], o) / _dot(r, o)
        x = [hit[i] + s * r[i] - centre[i] for i in range(3)]
        out[f"{name}_u_mm"], out[f"{name}_v_mm"] = _dot(x, u_axis), _dot(x, v_axis)
    out["far_direction_cosine_u"], out["far_direction_cosine_v"] = _dot(r, u_axis), _dot(r, v_axis)
    out.update(two_plane_pointing(out["near_u_mm"], out["near_v_mm"], out["far_u_mm"], out["far_v_mm"],
                                  float(far_plane_mm) - float(near_plane_mm)))
    return out


def mirror_deviation_mrad(tilt_mrad: float, *, axis: str, mirror_aoi_deg: float = 45.0) -> float:
    """Small-angle beam deviation of a mirror tilt θ: ``axis="in_plane"`` (tilt about the in-face axis normal to the
    plane of incidence) 2θ; ``axis="out_of_plane"`` (tilt about the in-face axis in the plane of incidence)
    2θ·cos(AOI)."""
    if axis == "in_plane":
        return 2.0 * float(tilt_mrad)
    if axis == "out_of_plane":
        return 2.0 * float(tilt_mrad) * math.cos(math.radians(float(mirror_aoi_deg)))
    raise ValueError("axis must be 'in_plane' or 'out_of_plane'")


def two_plane_pointing(near_u_mm: float, near_v_mm: float, far_u_mm: float, far_v_mm: float,
                       separation_mm: float) -> dict[str, float]:
    """Pointing from two parallel planes with the same u / v axes ``separation_mm`` apart:
    ``pointing_u_mrad = 1000·atan2(far_u - near_u, separation)``, likewise v."""
    if not separation_mm > 0:
        raise ValueError("separation_mm must be positive")
    return {"pointing_u_mrad": 1e3 * math.atan2(far_u_mm - near_u_mm, separation_mm),
            "pointing_v_mrad": 1e3 * math.atan2(far_v_mm - near_v_mm, separation_mm)}


def alignment_errors(run: Any, *, near: str = "near", far: str = "far") -> dict[str, Any]:
    """Position and pointing errors from two observation planes of one run.

    ``run``: a ``RunResults``, a ``LocalOpticalRun`` or a run directory. Reads the chief-ray hit (``u_mm``, ``v_mm``)
    and direction (``direction_u``, ``direction_v``) of the first arrival on each plane (u / v: the plane's local
    +X / +Y; ``monitor(id)["plane"]`` holds them in design coordinates). The planes must be parallel with equal u / v
    axes; the separation is measured along the near plane's normal. Returns positions (mm), the two-plane pointing
    (mrad), the far-plane direction-cosine pointing ``asin(direction)`` (mrad) and ``reached`` per plane; values are
    ``None`` where a plane received no beam."""
    from os_optics.results import RunResults, load_run

    results = run if isinstance(run, RunResults) else load_run(run)
    planes = {name: results.monitor(name)["plane"] for name in (near, far)}
    for key in ("u_axis", "v_axis", "normal"):
        if _dot(planes[near][key], planes[far][key]) < 1.0 - 1e-12:
            raise ValueError(f"planes {near!r} and {far!r} are not parallel with equal axes ({key})")
    separation = _dot([b - a for a, b in zip(planes[near]["origin_mm"], planes[far]["origin_mm"])],
                      planes[near]["normal"])
    hits: dict[str, dict[str, Any] | None] = {}
    for name in (near, far):
        rows = results.arrivals(name)
        hits[name] = rows[0] if rows else None
    out: dict[str, Any] = {"units": {"position": "mm", "pointing": "mrad"}, "separation_mm": separation,
                           "reached": {name: hits[name] is not None for name in (near, far)}}
    for name, label in ((near, "near"), (far, "far")):
        row = hits[name]
        out[f"{label}_u_mm"] = row["u_mm"] if row else None
        out[f"{label}_v_mm"] = row["v_mm"] if row else None
    if hits[near] and hits[far]:
        out.update(two_plane_pointing(out["near_u_mm"], out["near_v_mm"], out["far_u_mm"], out["far_v_mm"], separation))
        out["far_direction_pointing_u_mrad"] = 1e3 * math.asin(hits[far]["direction_u"])
        out["far_direction_pointing_v_mrad"] = 1e3 * math.asin(hits[far]["direction_v"])
    else:
        out.update({"pointing_u_mrad": None, "pointing_v_mrad": None, "far_direction_pointing_u_mrad": None,
                    "far_direction_pointing_v_mrad": None})
    return out


# ------------------------------------------------------------------------------------------------- catalog variants

class CatalogDesignUnavailable(RuntimeError):
    """The catalog example Project could not be produced (``error``: the Engine's structured error)."""

    def __init__(self, error: Mapping[str, Any]):
        super().__init__(str(error))
        self.error = dict(error)


def _catalog_example(kind: str) -> dict[str, Any]:
    from os_optics.local import json_api

    reply = json_api.example_project_document(kind=kind)
    if not reply.get("ok"):
        raise CatalogDesignUnavailable(reply.get("error") or {"code": "UNKNOWN"})
    return reply["project"]


def catalog_beam_expander(*, l2_shift_mm: float = 0.0, wavelength_nm: float | None = None,
                          waist_radius_mm: float | None = None) -> dict[str, Any]:
    """The catalog example kind ``beam_expander`` (two catalog plano-convex lenses) with L2 moved along the beam by
    ``l2_shift_mm`` and optional source wavelength / waist.

    Keeps the example target ``expanded_waist`` (``waist_radius_x_mm`` on ``output``: the radius of the waist the
    output beam has or had, which measures collimation and changes strongly with L2 spacing) and adds
    ``output_beam_radius`` (``beam_radius_mm`` on ``output``: the radius at the plane, which changes little with
    spacing), same target value and relative tolerance. Raises ``CatalogDesignUnavailable`` without a catalog."""
    document = _catalog_example("beam_expander")
    l2 = by_id(document["components"], "instance_id", "L2")
    l2["nominal_pose"]["position_mm"][0] = float(l2["nominal_pose"]["position_mm"][0]) + float(l2_shift_mm)
    source = by_id(document["components"], "instance_id", "SRC")["source_model"]
    if wavelength_nm is not None:
        source["wavelength_nm"] = float(wavelength_nm)
    if waist_radius_mm is not None:
        source["waist_radius_mm"] = float(waist_radius_mm)
    waist = by_id(document["targets"], "target_id", "expanded_waist")
    document["targets"].append(_target("output_beam_radius", "beam_radius_mm", waist["observation_id"], "mm",
                                       target_value=waist["target_value"], tolerance_rel=waist["tolerance_rel"]))
    return document


def catalog_polarization_control(*, hwp_angle_deg: float = 22.5, polarizer_angle_deg: float = 45.0,
                                 source_polarization_angle_deg: float = 0.0, wavelength_nm: float | None = None,
                                 min_power_w: float = 0.6e-3) -> dict[str, Any]:
    """The catalog example kind ``polarization`` (zero-order half-wave plate ``HWP``, linear polarizer ``POL``) with
    both plates turned about their optical axes to the given angles (same angle convention as
    ``polarization_control``), an added plane ``after_hwp`` (x = 25 mm) and the target ``transmitted_power``
    (``power_W`` on ``detector`` at least ``min_power_w``). The example's zero-order plate is specified at one
    wavelength; another ``wavelength_nm`` reports ``MODEL_WAVELENGTH_OUTSIDE_BAND``. Raises
    ``CatalogDesignUnavailable`` without a catalog."""
    document = _catalog_example("polarization")
    by_id(document["components"], "instance_id", "HWP")["nominal_pose"]["quaternion_wxyz"] = turned(
        BEAM_ALONG_X, (0, 0, 1), hwp_angle_deg)
    by_id(document["components"], "instance_id", "POL")["nominal_pose"]["quaternion_wxyz"] = turned(
        BEAM_ALONG_X, (0, 0, 1), polarizer_angle_deg)
    source = by_id(document["components"], "instance_id", "SRC")["source_model"]
    source["polarization"]["angle_deg"] = float(source_polarization_angle_deg)
    if wavelength_nm is not None:
        source["wavelength_nm"] = float(wavelength_nm)
    document["observations"].append(observation_plane("after_hwp", (25.0, 0.0, 0.0)))
    document["targets"] = [_target("transmitted_power", "power_W", "detector", "W", minimum=min_power_w)]
    return document


def catalog_element_values(document: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Transmittance, extinction ratio, retardance and design wavelength of the catalog ``HWP`` / ``POL`` of a
    ``catalog_polarization_control`` Project, read from the installed catalog (floats; absent fields omitted)."""
    from os_optics.local import json_api

    out = {}
    for instance_id in ("HWP", "POL"):
        sku = by_id(document["components"], "instance_id", instance_id)["product"]["optical_sku"]
        reply = json_api.catalog_product(sku)
        if not reply.get("ok"):
            raise CatalogDesignUnavailable(reply.get("error") or {"code": "UNKNOWN"})
        specs = reply["product"]["specifications"]
        out[instance_id] = {name: float(specs[name]["value"]) for name in
                            ("transmittance", "extinction_ratio", "retardance_waves", "design_wavelength_nm")
                            if isinstance(specs.get(name), Mapping)}
    return out


# ------------------------------------------------------------------------------------------------- run helpers

def check_design(document: Mapping[str, Any]) -> dict[str, Any]:
    """``os_optics.local.json_api.check_project``: ``{"ok": True, "project_hash", "engine_input_hash",
    "catalog_version", ...}`` or ``{"ok": False, "error": {...}}``; nothing is run."""
    from os_optics.local import json_api

    return json_api.check_project(copy.deepcopy(dict(document)))


def run_design(document: Mapping[str, Any], output_dir: Any = None) -> Any:
    """Load and run one design in this process; returns the ``LocalOpticalRun`` (run directory written when
    ``output_dir`` is given). A refused Project raises ``os_optics.local.LocalRunError`` (``.problems``)."""
    from os_optics.local import load_project, run_optical_project

    return run_optical_project(load_project(copy.deepcopy(dict(document))), output_dir=output_dir)


def design_outcome(run: Any) -> dict[str, Any]:
    """Status, model-scope issues, Engine issue codes and every target of a ``LocalOpticalRun``.

    ``in_model_scope`` is ``False`` whenever ``model_scope_issues`` is non-empty or the run, the Engine, an Engine
    issue or a target reports ``OUT_OF_MODEL_SCOPE``, whatever ``status`` says (a FAIL target takes precedence over OUT_OF_MODEL_SCOPE
    in ``status``)."""
    from os_optics.results import load_run

    compact = load_run(run).targets()
    targets = {}
    for row in compact["targets"]:
        targets[row["target_id"]] = {**row, **row["bounds"]}
    return {"status": run.status.value, "engine_status": compact["engine_status"],
            "outcome": compact["outcome"], "in_model_scope": compact["in_model_scope"],
            "model_scope_issues": compact["model_scope_issues"],
            "out_of_model_issues": compact["out_of_model_issues"],
            "issues": sorted({i.code.value for i in run.result.issues}),
            "targets": targets, "catalog_version": run.catalog_version}


# ------------------------------------------------------------------------------------------------- metadata

def _p(unit, default, meaning, role="physical"):
    return {"unit": unit, "default": default, "meaning": meaning, "role": role}


DESIGN_INFO: dict[str, dict[str, Any]] = {
    "expander_focus": {
        "builder": "expander_focus",
        "reference": "gaussian_expander_focus_reference",
        "summary": "Keplerian beam expander (ideal thin lenses L1, L2) followed by a focusing ideal thin lens L3.",
        "catalog": False,
        "parameters": {
            "f1_mm": _p("mm", 50.0, "focal length of L1"),
            "f2_mm": _p("mm", 150.0, "focal length of L2; nominal magnification f2/f1"),
            "f3_mm": _p("mm", 100.0, "focal length of the focusing lens L3"),
            "gap_error_mm": _p("mm", 0.0, "L1-L2 separation minus f1 + f2 (defocus of the expander)"),
            "waist_radius_mm": _p("mm", 0.5, "source 1/e^2 waist radius (waist at the source)"),
            "wavelength_nm": _p("nm", 780.0, "source vacuum wavelength"),
            "focus_plane_shift_mm": _p("mm", 0.0, "plane 'focus' position minus (L3 + f3), along the beam"),
            "source_to_l1_mm": _p("mm", 100.0, "source waist to L1 distance"),
            "l2_to_l3_mm": _p("mm", 100.0, "L2 to L3 distance"),
            "collimated_plane_after_l2_mm": _p("mm", 50.0, "plane 'collimated' distance after L2"),
            "clear_aperture_mm": _p("mm", 25.0, "clear aperture diameter of each lens", role="applicability"),
            "power_w": _p("W", 1e-3, "source power", role="scale"),
            "max_spot_radius_mm": _p("mm", 0.02, "spot_radius maximum", role="criterion"),
            "expanded_radius_target_mm": _p("mm", None, "expanded_radius target value; None: waist_radius_mm*f2/f1",
                                            role="criterion"),
            "expanded_radius_tolerance_rel": _p("1", 0.02, "expanded_radius relative tolerance", role="criterion"),
            "waist_window_mm": _p("mm", 0.5, "waist_location bound: |waist_distance_x_mm| <= this", role="criterion"),
            "lens_band_nm": _p("nm", (400.0, 1100.0), "declared valid band of the lenses", role="applicability"),
        },
        "observations": {
            "collimated": "between L2 and L3, collimated_plane_after_l2_mm after L2; normal +X; u = +Y, v = +Z",
            "focus": "f3 + focus_plane_shift_mm after L3; normal +X; u = +Y, v = +Z",
        },
        "targets": {
            "expanded_radius": "beam_radius_mm on collimated: expanded 1/e^2 radius at the plane (mm)",
            "spot_radius": "beam_radius_mm on focus: focused 1/e^2 radius at the plane (mm)",
            "waist_location": ("waist_distance_x_mm on focus: Re(q), plane minus waist position along the beam (mm); "
                               "positive = waist before the plane (diverging), negative = waist after it"),
        },
        "frames": "beam along design +X; lenses at y = z = 0; element local +Z = design +X, local +Y = design +Z",
        "model_basis": ("native lens.ideal_thin (IDEAL_THIN_LENS: u' = u - y/f, no aberration or dispersion); "
                        "paraxial Gaussian q propagation for the beam metrics"),
        "outcomes": {
            "nominal": "PASS",
            "unmet": "gap_error_mm = 5 (spot 0.0365 mm > 0.02, expanded radius outside 2 %); f3_mm >= 125; 1064 nm",
            "diagnostic": ("waist_radius_mm = 3: WARNING GAUSSIAN_TRUNCATION; wavelength_nm outside lens_band_nm: "
                           "MODEL_WAVELENGTH_OUTSIDE_BAND; gap_error_mm <= -199: refused, OPTICAL_BODIES_OVERLAP"),
        },
        "provenance": [
            _case("SEED-2-2", "0.2.2", "Designing a fivefold afocal beam expander",
                  "two-lens Keplerian afocal expansion with magnification f2/f1", release="0.1.0-rc1",
                  content_sha256="dfe041664ee0fc501e683ad4ecadfedb5ab7294d9f4b0bb137fd76b40494cbc6"),
            _case("SEED-7-4", "0.1.0", "Independently verify a fivefold beam expander",
                  "verifying an expander by beam size at a plane and by collimation (waist location) checks",
                  file_sha256="36d8132bc1f860bb36b57a48f746b1ad9d3a0c937f4fa535e562484ba705db87"),
        ],
    },
    "polarization_control": {
        "builder": "polarization_control",
        "reference": "jones_polarization_reference",
        "summary": "Linear source, rotatable waveplate (native retarder) and rotatable linear polarizer.",
        "catalog": False,
        "parameters": {
            "hwp_angle_deg": _p("deg", 22.5, "waveplate fast-axis angle"),
            "polarizer_angle_deg": _p("deg", 45.0, "polarizer transmission-axis angle"),
            "source_polarization_angle_deg": _p("deg", 0.0, "source linear polarization angle"),
            "wavelength_nm": _p("nm", 1030.0, "source vacuum wavelength (changes the ZERO_ORDER retardance)"),
            "retardance_waves": _p("waves", 0.5, "retardance at design_wavelength_nm (0.5 half-wave, 0.25 quarter-wave)"),
            "design_wavelength_nm": _p("nm", 1030.0, "retarder design wavelength"),
            "retarder_model": _p(None, "ZERO_ORDER", "ZERO_ORDER (retardance ~ 1/λ) or ACHROMATIC (constant)"),
            "hwp_transmittance": _p("1", 0.99, "waveplate power transmittance"),
            "polarizer_transmittance": _p("1", 0.9, "polarizer transmittance along its axis"),
            "extinction_ratio": _p("1", 1000.0, "polarizer extinction ratio T_axis / T_cross"),
            "power_w": _p("W", 1e-3, "source power"),
            "waist_radius_mm": _p("mm", 1.0, "source waist radius", role="scale"),
            "min_power_w": _p("W", 0.8e-3, "transmitted_power minimum", role="criterion"),
            "azimuth_target_deg": _p("deg", None, "output_azimuth target value; None: no azimuth target",
                                     role="criterion"),
            "azimuth_tolerance_deg": _p("deg", 0.5, "output_azimuth half width", role="criterion"),
            "retarder_band_nm": _p("nm", (1000.0, 1060.0), "declared valid band of the waveplate",
                                   role="applicability"),
            "polarizer_band_nm": _p("nm", (600.0, 1200.0), "declared valid band of the polarizer",
                                    role="applicability"),
        },
        "observations": {
            "after_hwp": "x = 25 mm, between the plates; normal +X; u = +Y, v = +Z",
            "detector": "x = 100 mm, after the polarizer; normal +X; u = +Y, v = +Z",
        },
        "targets": {
            "transmitted_power": "power_W on detector (W)",
            "output_azimuth": "polarization_azimuth_deg on after_hwp, from u toward v in [0, 180) (deg); optional",
        },
        "frames": ("beam along design +X; angles from design +Y toward design +Z (right-handed about +X); Stokes "
                   "basis (e1, e2) = (+Y, +Z); stokes_s3 = 2 Im(conj(E1) E2)/S0: -1 right circular (clockwise "
                   "viewed facing the source), +1 left; polarization_ellipticity_deg = asin(s3)/2 (-45 right)"),
        "model_basis": ("native waveplate.retarder and polarizer.linear (POLARIZATION_ELEMENT, ideal thin Jones "
                        "elements); Jones calculus of the chief-ray field"),
        "outcomes": {
            "nominal": "PASS (P = P0 T_hwp T_pol = 0.891 mW)",
            "unmet": "hwp_angle_deg = 0 (P = 0.4459 mW < 0.8 mW); polarizer_angle_deg = 135 (extinction floor)",
            "diagnostic": ("wavelength_nm outside retarder_band_nm: MODEL_WAVELENGTH_OUTSIDE_BAND; "
                           "retardance_waves = 0.25 at hwp_angle_deg = 45 with azimuth_target_deg set: "
                           "output_azimuth UNKNOWN (azimuth undefined for circular light)"),
        },
        "provenance": [
            _case("SEED-1-7", "0.2.1", "Half-wave plate and linear polarization",
                  "half-wave plate rotation turns linear polarization by twice the plate angle", release="0.1.0-rc1",
                  content_sha256="cf0f6a8b3db99ad00d9ecce099cc92d0dacac129305a0a33ea2758dfd374c3db"),
            _case("SEED-1-8", "0.1.0", "Continuously adjustable free-space attenuation",
                  "rotatable half-wave plate before a fixed polarizer as an adjustable attenuator",
                  file_sha256="6154639f8afa52299907ef9779466c16da20b273665f2533ed73951dff728dec"),
        ],
    },
    "alignment": {
        "builder": "alignment",
        "reference": "mirror_alignment_reference",
        "summary": "Source and flat fold mirror; beam position and pointing on two observation planes.",
        "catalog": False,
        "parameters": {
            "mirror_tilt_local_x_mrad": _p("mrad", 0.0, "mirror tilt about its in-face axis in the plane of incidence"),
            "mirror_tilt_local_y_mrad": _p("mrad", 0.0, "mirror tilt about its in-face axis design +Z"),
            "source_offset_y_mm": _p("mm", 0.0, "source shift along design +Y"),
            "source_offset_z_mm": _p("mm", 0.0, "source shift along design +Z"),
            "source_tilt_local_x_mrad": _p("mrad", 0.0, "source turn about its local +X (= design +Y)"),
            "source_tilt_local_y_mrad": _p("mrad", 0.0, "source turn about its local +Y (= design +Z)"),
            "near_plane_mm": _p("mm", 100.0, "plane 'near' distance after the mirror"),
            "far_plane_mm": _p("mm", 400.0, "plane 'far' distance after the mirror"),
            "source_to_mirror_mm": _p("mm", 100.0, "source to mirror distance"),
            "mirror_aoi_deg": _p("deg", 45.0, "nominal angle of incidence on the mirror (fold = 180 - 2 AOI)"),
            "wavelength_nm": _p("nm", 633.0, "source vacuum wavelength", role="applicability"),
            "waist_radius_mm": _p("mm", 0.5, "source waist radius", role="scale"),
            "power_w": _p("W", 1e-3, "source power", role="scale"),
            "position_tolerance_mm": _p("mm", 0.1, "|position_u/v_mm| bound on both planes", role="criterion"),
            "pointing_tolerance_mrad": _p("mrad", 0.5, "|direction_cosine_u/v| <= sin(this) on far", role="criterion"),
            "plane_radius_mm": _p("mm", 12.5, "observation plane radius (a beam outside it does not reach the plane)",
                                  role="applicability"),
            "mirror_band_nm": _p("nm", (400.0, 1100.0), "declared valid band of the mirror", role="applicability"),
        },
        "observations": {
            "near": "near_plane_mm after the mirror on the nominal reflected axis; u = +Z x axis (45 deg: -X), v = +Z",
            "far": "far_plane_mm after the mirror, same axes as near",
        },
        "targets": {
            "near_position_u": "position_u_mm on near (mm)", "near_position_v": "position_v_mm on near (mm)",
            "far_position_u": "position_u_mm on far (mm)", "far_position_v": "position_v_mm on far (mm)",
            "far_pointing_u": "direction_cosine_u on far (1)", "far_pointing_v": "direction_cosine_v on far (1)",
        },
        "frames": ("incident beam along design +X, folded in the design XY plane; mirror face centre at the origin; "
                   "mirror local +Z = face normal, local +Y = design +Z; plane u / v = plane local +X / +Y"),
        "model_basis": "native mirror.flat (exact 3D chief-ray trace, declared reflectance)",
        "outcomes": {
            "nominal": "PASS (all positions and pointing 0)",
            "unmet": ("mirror_tilt_local_x_mrad = 0.5 (far v = -0.283 mm); source_tilt_local_x_mrad = 1 "
                      "(far v = -0.5 mm, pointing -1 mrad)"),
            "diagnostic": ("mirror_tilt_local_y_mrad = 60: the far plane is not reached (OBSERVATION_NOT_REACHED, "
                           "values None); wavelength_nm outside mirror_band_nm: MODEL_WAVELENGTH_OUTSIDE_BAND"),
        },
        "provenance": [
            _case("SEED-1-2", "0.1.0", "Establishing an axis with two steering mirrors and two apertures",
                  "beam position and direction checked at two separated planes",
                  file_sha256="e8b23207326f24f1961b020cca8fca81651151367ee69735ed2431c3e74fac29"),
            {"case_id": "EX-ALIGN-01", "case_revision": "1.0.0", "title": "Two-iris alignment diagnosis",
             "used_for": "separating position error from angle error with observations at two distances",
             "benchmark_release": "optics-examples-v1", "commit": BENCHMARK["commit"],
             "case_content_sha256": "5d92ff16059730f7c20a226cad6fe481b2fd1b7995daf942befb631ce0d74738",
             "attribution": (f"Informed by EX-ALIGN-01 (version 1.0.0, case library optics-examples-v1) of the "
                             f"{BENCHMARK['name']}, {BENCHMARK['authors']}, commit {BENCHMARK['commit'][:8]}, "
                             f"{BENCHMARK['url']}, {BENCHMARK['license']}. Changes: {_ADAPTATION}.")},
        ],
    },
    "catalog_beam_expander": {
        "builder": "catalog_beam_expander",
        "summary": "Catalog example kind beam_expander with L2 spacing, wavelength and waist parameters.",
        "catalog": True,
        "parameters": {
            "l2_shift_mm": _p("mm", 0.0, "L2 displacement along the beam (design +X)"),
            "wavelength_nm": _p("nm", None, "source wavelength; None: example value 780"),
            "waist_radius_mm": _p("mm", None, "source waist radius; None: example value 1.0"),
        },
        "observations": {"output": "example plane 100 mm after L2"},
        "targets": {
            "expanded_waist": "waist_radius_x_mm on output: radius of the output beam's (real or virtual) waist",
            "output_beam_radius": "beam_radius_mm on output: radius at the plane",
        },
        "model_basis": "catalog plano-convex lenses (sequential surfaces, catalog glass and coating)",
        "provenance": "as expander_focus",
    },
    "catalog_polarization_control": {
        "builder": "catalog_polarization_control",
        "reference": "malus_power_reference (with catalog_element_values)",
        "summary": "Catalog example kind polarization with plate angles, source angle and wavelength parameters.",
        "catalog": True,
        "parameters": {
            "hwp_angle_deg": _p("deg", 22.5, "half-wave plate fast-axis angle"),
            "polarizer_angle_deg": _p("deg", 45.0, "polarizer transmission-axis angle"),
            "source_polarization_angle_deg": _p("deg", 0.0, "source linear polarization angle"),
            "wavelength_nm": _p("nm", None, "source wavelength; None: example value 1030", role="applicability"),
            "min_power_w": _p("W", 0.6e-3, "transmitted_power minimum", role="criterion"),
        },
        "observations": {"after_hwp": "x = 25 mm (added)", "detector": "example plane x = 100 mm"},
        "targets": {"transmitted_power": "power_W on detector (W)"},
        "model_basis": "catalog zero-order half-wave plate (specified at 1030 nm) and linear polarizer",
        "provenance": "as polarization_control",
    },
}


__all__ = [
    "BEAM_AGAINST_X", "BEAM_ALONG_X", "BENCHMARK", "CatalogDesignUnavailable", "DESIGN_INFO", "alignment",
    "alignment_errors", "by_id", "catalog_beam_expander", "catalog_element_values", "catalog_polarization_control",
    "check_design", "design_outcome", "expander_focus", "gaussian_expander_focus_reference", "gaussian_source",
    "jones_polarization_reference", "malus_power_reference", "mirror_alignment_reference", "mirror_deviation_mrad",
    "observation_plane", "polarization_control", "run_design", "turned", "two_plane_pointing",
]
