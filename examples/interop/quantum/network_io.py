"""Read an ``opensci-linear-optical-network/v1`` export and the quantum inputs (numpy only, no OpenSci import).

Conventions used from the export (stated in every document's ``conventions``):
``b_out = T a_in`` with ``T[row = output mode, column = input mode]``, flux-normalized
amplitudes, field ``exp(-i w t)``, one spatial mode per source and per distinct
output beam, each with polarizations H and V. Perceval ``Unitary(Matrix(U))`` and
Piquasso ``Interferometer(U)`` read ``U[output, input]`` the same way.

Group delay: when a path carries ``group_delay_ps`` it is used directly. Otherwise
it is computed from the path's optical path length, its geometric length and the
Project's declared glass: L_glass = (OPL - L) / (n - 1) and
tau_g = (L + (n_g - 1) L_glass) / c, with n and n_g from the declared Sellmeier
material of the elements on the path (one dispersive material per path, air = 1).
"""

from __future__ import annotations

import fnmatch
import json
import math
from pathlib import Path

import numpy as np

NETWORK_SCHEMA = "opensci-linear-optical-network/v1"
INPUTS_SCHEMA = "opensci-interop-example/quantum-inputs/1"
C_MM_PER_PS = 0.299792458


class InputError(ValueError):
    code = "QUANTUM_INPUT_INVALID"


class ModelScopeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------------------------------- quantum inputs
def _number(value, name, *, minimum=None, maximum=None, exclusive_minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise InputError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise InputError(f"{name} must be <= {maximum}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise InputError(f"{name} must be > {exclusive_minimum}")
    return float(value)


def _wavepacket(value, name):
    if not isinstance(value, dict) or value.get("shape") != "GAUSSIAN":
        raise InputError(f"{name}: only shape GAUSSIAN is supported")
    return {"shape": "GAUSSIAN",
            "rms_intensity_duration_ps": _number(value.get("rms_intensity_duration_ps"),
                                                 f"{name}.rms_intensity_duration_ps", exclusive_minimum=0),
            "zero_delay_indistinguishability": _number(value.get("zero_delay_indistinguishability"),
                                                       f"{name}.zero_delay_indistinguishability", minimum=0, maximum=1)}


def parse_inputs(document):
    """Validate the user's quantum inputs; every quantity is explicit (nothing is taken from the classical beams)."""
    if not isinstance(document, dict) or document.get("schema") != INPUTS_SCHEMA:
        raise InputError(f"quantum inputs must declare schema {INPUTS_SCHEMA!r}")
    photons = document.get("photons")
    if not isinstance(photons, dict) or not isinstance(photons.get("per_source"), dict) or not photons["per_source"]:
        raise InputError("photons.per_source must map source ids to photon numbers")
    per_source = {}
    for source, number in photons["per_source"].items():
        if isinstance(number, bool) or not isinstance(number, int) or number != 1:
            raise InputError(f"photons.per_source.{source}: this example supports exactly one photon per source")
        per_source[str(source)] = number
    if len(per_source) != 2:
        raise InputError("photons.per_source: this example takes exactly two single-photon sources")
    mode = photons.get("polarization_mode")
    if mode not in ("H", "V"):
        raise InputError("photons.polarization_mode must be H or V (the exported lab basis)")
    wavepackets = document.get("wavepackets")
    if not isinstance(wavepackets, dict) or not wavepackets:
        raise InputError("wavepackets must map profile names to wavepacket descriptions")
    wavepackets = {name: _wavepacket(value, f"wavepackets.{name}") for name, value in wavepackets.items()}
    detectors = document.get("detectors")
    if not isinstance(detectors, dict) or detectors.get("type") != "THRESHOLD":
        raise InputError("detectors.type must be THRESHOLD")
    parsed_detectors = {"type": "THRESHOLD",
                        "efficiency": _number(detectors.get("efficiency"), "detectors.efficiency", minimum=0, maximum=1),
                        "dark_count_probability": _number(detectors.get("dark_count_probability", 0.0),
                                                          "detectors.dark_count_probability", minimum=0, maximum=1)}
    planes = detectors.get("planes")
    if not isinstance(planes, dict) or not planes:
        raise InputError("detectors.planes must map a case pattern to two observation ids")
    for pattern, pair in planes.items():
        if not isinstance(pair, list) or len(pair) != 2 or len(set(pair)) != 2:
            raise InputError(f"detectors.planes.{pattern} must list two different observation ids")
    parsed_detectors["planes"] = {str(k): [str(v) for v in pair] for k, pair in planes.items()}
    case_wavepackets = document.get("case_wavepackets")
    if not isinstance(case_wavepackets, dict) or not case_wavepackets:
        raise InputError("case_wavepackets must map case patterns to wavepacket profile names")
    for pattern, profile in case_wavepackets.items():
        if profile not in wavepackets:
            raise InputError(f"case_wavepackets.{pattern}: unknown wavepacket profile {profile!r}")
    extra = {}
    for name, case in (document.get("extra_cases") or {}).items():
        if not isinstance(case, dict) or not isinstance(case.get("network_case"), str):
            raise InputError(f"extra_cases.{name}.network_case is required")
        entry = {"network_case": case["network_case"]}
        if "wavepacket" in case:
            if case["wavepacket"] not in wavepackets:
                raise InputError(f"extra_cases.{name}: unknown wavepacket profile {case['wavepacket']!r}")
            entry["wavepacket"] = case["wavepacket"]
        if "dark_count_probability" in case:
            entry["dark_count_probability"] = _number(case["dark_count_probability"],
                                                      f"extra_cases.{name}.dark_count_probability", minimum=0, maximum=1)
        if "expect_refusal" in case:
            entry["expect_refusal"] = str(case["expect_refusal"])
        extra[str(name)] = entry
    validity = document.get("validity") or {}
    return {"photons": {"per_source": per_source, "polarization_mode": mode}, "wavepackets": wavepackets,
            "detectors": parsed_detectors, "case_wavepackets": dict(case_wavepackets), "extra_cases": extra,
            "validity": {"max_delay_spread_over_duration": _number(
                validity.get("max_delay_spread_over_duration", 0.05), "validity.max_delay_spread_over_duration",
                exclusive_minimum=0)}}


def lookup(patterns, case_name, what):
    """The value of the first pattern (fnmatch, in file order) that matches ``case_name``."""
    for pattern, value in patterns.items():
        if fnmatch.fnmatchcase(case_name, pattern):
            return value
    raise InputError(f"no {what} entry matches case {case_name!r}")


# ----------------------------------------------------------------------------------------------- network
def load_network(path):
    network = json.loads(Path(path).read_text(encoding="utf-8"))
    if network.get("schema") != NETWORK_SCHEMA:
        raise ValueError(f"{path}: not an {NETWORK_SCHEMA} document")
    matrix = network["transfer_matrix"]
    t = np.array([[complex(re, im) for re, im in row] for row in matrix["entries"]])
    return network, t, list(matrix["rows"]), list(matrix["columns"])


def input_mode(columns, source_id, polarization):
    name = f"in:{source_id}#0:{polarization}"
    if name not in columns:
        raise InputError(f"the network has no input mode {name}; exported inputs: {columns}")
    return columns.index(name)


def plane_modes(rows, observation_id):
    indices = [i for i, row in enumerate(rows) if row.split(":")[1] == observation_id]
    if not indices:
        raise InputError(f"the network has no output modes on plane {observation_id!r}")
    return indices


def dilate(t):
    """Unitary dilation of a contraction T (padded square with vacuum modes): [[T, (I-TT^H)^1/2], [(I-T^HT)^1/2, -T^H]]."""
    n = max(t.shape)
    square = np.zeros((n, n), complex)
    square[:t.shape[0], :t.shape[1]] = t
    singular = np.linalg.svd(square, compute_uv=False)
    if singular.max() > 1.0 + 1e-9:
        raise ValueError(f"T is not passive (largest singular value {singular.max():.12f})")

    def psd_sqrt(matrix):
        values, vectors = np.linalg.eigh((matrix + matrix.conj().T) / 2.0)
        return vectors @ np.diag(np.sqrt(np.clip(values, 0.0, None))) @ vectors.conj().T

    identity = np.eye(n)
    unitary = np.block([[square, psd_sqrt(identity - square @ square.conj().T)],
                        [psd_sqrt(identity - square.conj().T @ square), -square.conj().T]])
    if not np.allclose(unitary.conj().T @ unitary, np.eye(2 * n), atol=1e-9):
        raise ValueError("unitary dilation failed")
    return unitary


# ----------------------------------------------------------------------------------------------- group delay
def _sellmeier(material, wavelength_nm):
    dispersion = material.get("dispersion") or {}
    if dispersion.get("kind") != "SELLMEIER":
        raise ModelScopeError("GROUP_DELAY_UNAVAILABLE",
                              f"material {material.get('material_id')} has no Sellmeier dispersion for a group index")
    b = [float(v) for v in dispersion["b"]]
    c = [float(v) for v in dispersion["c_um2"]]
    lam = wavelength_nm * 1e-3
    l2 = lam * lam
    n = math.sqrt(1.0 + sum(bi * l2 / (l2 - ci) for bi, ci in zip(b, c)))
    dn2 = sum(-2.0 * bi * ci * lam / (l2 - ci) ** 2 for bi, ci in zip(b, c))
    return n, n - lam * dn2 / (2.0 * n)


def path_materials(path, project):
    """Declared materials of the elements whose surfaces refract on this path."""
    components = {c["instance_id"]: c for c in project.get("components", [])}
    models = {m["model_id"]: m["model"] for m in project.get("optical_models", [])}
    materials = {m["material_id"]: m for m in project.get("model_materials", []) or []}
    found = {}
    for step in path["lineage"]:
        owner_surface, event = step.rsplit(":", 1)
        if event != "refract":
            continue
        owner = owner_surface.split("/")[0]
        model = models.get((components.get(owner) or {}).get("optical_model_id"))
        if model is None:
            raise ModelScopeError("GROUP_DELAY_UNAVAILABLE", f"path {path['path_id']}: no declared model for {owner}")
        for material_id in model.get("media") or []:
            if material_id not in materials:
                raise ModelScopeError("GROUP_DELAY_UNAVAILABLE",
                                      f"path {path['path_id']}: material {material_id} is not declared in the Project")
            found[material_id] = materials[material_id]
    return found


def path_group_delays(network, project):
    """{path_id: (group delay ps, method)} for every exported path."""
    wavelength = network["wavelength_nm"]
    delays = {}
    for path in network["paths"]:
        if path.get("group_delay_ps") is not None:
            delays[path["path_id"]] = (float(path["group_delay_ps"]), "exported")
            continue
        delays[path["path_id"]] = (computed_group_delay(path, project, wavelength), "computed from Project materials")
    return delays


def computed_group_delay(path, project, wavelength_nm):
    materials = path_materials(path, project)
    excess = path["opl_mm"] - path["length_mm"]
    if not materials:
        if abs(excess) > 1e-6:
            raise ModelScopeError("GROUP_DELAY_UNAVAILABLE", f"path {path['path_id']}: optical path differs from air")
        return path["length_mm"] / C_MM_PER_PS
    if len(materials) > 1:
        raise ModelScopeError("GROUP_DELAY_UNAVAILABLE",
                              f"path {path['path_id']} crosses several glasses; use an export with group_delay_ps")
    n, n_g = _sellmeier(next(iter(materials.values())), wavelength_nm)
    glass_mm = excess / (n - 1.0)
    return (path["length_mm"] + (n_g - 1.0) * glass_mm) / C_MM_PER_PS


def photon_delays(network, delays, sources, polarization, max_spread_over_duration, duration_ps):
    """Power-weighted mean group delay of each source's photon at each output beam, with validity checks.

    A single-frequency transfer matrix describes a photon only when the paths it takes to one output differ in group
    delay by much less than its duration; the relative delay of the two photons must be the same at every output.
    """
    per_beam = {}
    for path in network["paths"]:
        if path["source_id"] not in sources:
            continue
        column = 0 if polarization == "H" else 1  # Jones amplitudes [output H/V][input H/V]
        weight = float(sum(abs(complex(*row[column])) ** 2 for row in path["amplitudes"]))
        per_beam.setdefault(path["output_beam"], {}).setdefault(path["source_id"], []).append(
            (delays[path["path_id"]][0], weight))
    spread, relative = 0.0, []
    for beam, by_source in per_beam.items():
        means = {}
        for source, entries in by_source.items():
            values = [d for d, _ in entries]
            spread = max(spread, max(values) - min(values))
            total = sum(w for _, w in entries)
            means[source] = sum(d * w for d, w in entries) / total if total > 0 else sum(values) / len(values)
        if all(s in means for s in sources):
            relative.append(means[sources[0]] - means[sources[1]])
    if spread > max_spread_over_duration * duration_ps:
        raise ModelScopeError("SINGLE_FREQUENCY_MODEL_INVALID",
                              f"paths of one photon to one output differ by {spread:.4f} ps in group delay, more than "
                              f"{max_spread_over_duration} x the {duration_ps} ps wavepacket duration: the single-"
                              "wavelength transfer matrix does not describe this photon")
    if not relative:
        raise ModelScopeError("NO_COMMON_OUTPUT", "the two photons reach no common output beam")
    if max(relative) - min(relative) > max_spread_over_duration * duration_ps:
        raise ModelScopeError("MULTIPLE_RELATIVE_DELAYS", "the photons' relative delay differs between outputs")
    return {"relative_delay_ps": sum(relative) / len(relative), "max_path_spread_ps": spread,
            "relative_delay_spread_ps": max(relative) - min(relative)}
