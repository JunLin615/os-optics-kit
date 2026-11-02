"""External side of the quantum example: Perceval (with a Piquasso cross-check) on OpenSci network exports.

Reads only the files written by ``build_layouts.py`` (network exports and the
Projects they came from) and the user's ``quantum_inputs.json``; it never imports
os_optics. For every case:

1. load the transfer matrix T (lossy when the layout loses light) and take the
   group delay of each path (exported ``group_delay_ps`` when present, otherwise
   computed from the Project's declared glass, see ``network_io.py``);
2. check that a single-wavelength T describes the photons (path group-delay spread
   small against the wavepacket duration) and derive the photons' mutual
   indistinguishability p from their relative group delay;
3. add the detector efficiency as a loss in front of the detectors and embed the
   contraction in a unitary (unitary dilation, loss modes traced out);
4. Perceval (SLOS) computes the output distribution of the two partially
   distinguishable photons; Piquasso (PureFock) recomputes it as the p-weighted
   mixture of indistinguishable and distinguishable photons;
5. threshold detectors with dark counts give the coincidence probability P_cc,
   compared with the closed-form reference of ``reference.py``.

Requires network exports made by os-optics 0.10.0. Perceval is MIT licensed;
its required dependency exqalibur declares CC BY-NC-ND 4.0 in its package
metadata. Piquasso is Apache-2.0.

Usage: python examples/interop/quantum/run_quantum.py run-interop-quantum [--inputs quantum_inputs.json]
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

import numpy as np

if __package__:
    from ..common import ArgumentParser, comparison_status, emit, environment, write_json
    from . import network_io as nio
    from . import reference as ref
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, comparison_status, emit, environment, write_json
    import network_io as nio
    import reference as ref

DEFAULT_INPUTS = Path(__file__).resolve().parent / "quantum_inputs.json"
TOLERANCES = {"perceval_vs_reference": 1e-6, "piquasso_vs_reference": 1e-6, "relative_delay_vs_reference_ps": 1e-4,
              "exported_vs_computed_group_delay_ps": 1e-4}


def click(n, dark):
    """Threshold detector after the efficiency loss (already in T): photons -> click; none -> dark count."""
    return 1.0 if n > 0 else dark


def coincidence(distribution, d1, d2, dark):
    total = 0.0
    for occupation, probability in distribution.items():
        n1, n2 = sum(occupation[i] for i in d1), sum(occupation[i] for i in d2)
        total += probability * click(n1, dark) * click(n2, dark)
    return total


def perceval_distribution(u, ia, ib, p):
    import perceval as pcvl

    m = u.shape[0]

    def state(tag_b):
        occupation = ["0"] * m
        occupation[ia] = "{0}"
        occupation[ib] = "{%d}" % tag_b
        return pcvl.BasicState("|" + ",".join(occupation) + ">")

    weights = {pcvl.StateVector(state(0)): p}
    if p < 1.0:
        weights[pcvl.StateVector(state(1))] = 1.0 - p
    simulator = pcvl.Simulator(pcvl.BackendFactory.get_backend("SLOS"))
    simulator.set_circuit(pcvl.Unitary(pcvl.Matrix(u)))
    results = simulator.probs_svd(pcvl.SVDistribution({k: v for k, v in weights.items() if v > 0}))["results"]
    return {tuple(int(n) for n in occupation): float(prob) for occupation, prob in results.items()}


def piquasso_distribution(u, ia, ib, p):
    import piquasso as pq

    m = u.shape[0]

    def fock(photons):
        occupation = [0] * m
        for index in photons:
            occupation[index] += 1
        with pq.Program() as program:
            pq.Q() | pq.NumberState(occupation)
            pq.Q(*range(m)) | pq.Interferometer(u)
        simulator = pq.PureFockSimulator(d=m, config=pq.Config(cutoff=3))  # two photons at most
        return {tuple(int(n) for n in k): float(v) for k, v in
                simulator.execute(program).state.fock_probabilities_map.items() if v > 1e-15}

    together = fock([ia, ib])
    alone_a, alone_b = fock([ia]), fock([ib])
    apart = {}
    for occ_a, prob_a in alone_a.items():
        for occ_b, prob_b in alone_b.items():
            key = tuple(x + y for x, y in zip(occ_a, occ_b))
            apart[key] = apart.get(key, 0.0) + prob_a * prob_b
    keys = set(together) | set(apart)
    return {k: p * together.get(k, 0.0) + (1.0 - p) * apart.get(k, 0.0) for k in keys}


def reference_case(meta, wavepacket, detectors, dark):
    """Closed form from the layout parameters only (no exported data)."""
    duration, p0 = wavepacket["rms_intensity_duration_ps"], wavepacket["zero_delay_indistinguishability"]
    kind = meta["kind"]
    if kind in ("hom", "hom_delay_scan"):
        delay = -meta.get("source_B_shift_mm", 0.0) / ref.C_MM_PER_PS
        matrix = ref.hom_matrix()
    elif kind == "hom_window":
        delay = (ref.window_group_delay_ps(meta["window_mm"], 780.0)
                 - meta["source_B_shift_mm"] / ref.C_MM_PER_PS)
        matrix = ref.hom_matrix(window_mm=meta["window_mm"])
    elif kind == "mzi":
        delay = 0.0
        matrix = ref.mzi_matrix(meta["window_mm"])
    else:
        raise ValueError(f"no reference for case kind {kind}")
    p = ref.overlap(delay, duration, p0)
    pcc = ref.coincidence_probability(ref.two_photon_distribution(matrix, p), detectors["efficiency"], dark)
    return {"relative_delay_ps": delay, "indistinguishability": p, "pcc": pcc}


def analyze(name, meta, inputs, wavepacket_name, dark):
    wavepacket = inputs["wavepackets"][wavepacket_name]
    detectors = inputs["detectors"]
    network, t, rows, columns = nio.load_network(meta["network"])
    project = json.loads(Path(meta["project"]).read_text(encoding="utf-8"))
    sources = list(inputs["photons"]["per_source"])
    polarization = inputs["photons"]["polarization_mode"]
    delays = nio.path_group_delays(network, project)
    cross = [abs(p["group_delay_ps"] - nio.computed_group_delay(p, project, network["wavelength_nm"]))
             for p in network["paths"] if p.get("group_delay_ps") is not None]
    timing = nio.photon_delays(network, delays, sources, polarization,
                               inputs["validity"]["max_delay_spread_over_duration"],
                               wavepacket["rms_intensity_duration_ps"])
    p = ref.overlap(timing["relative_delay_ps"], wavepacket["rms_intensity_duration_ps"],
                    wavepacket["zero_delay_indistinguishability"])
    plane_pair = nio.lookup(detectors["planes"], name, "detectors.planes")
    d1, d2 = (nio.plane_modes(rows, plane) for plane in plane_pair)
    ia, ib = (nio.input_mode(columns, s, polarization) for s in sources)
    u = nio.dilate(math.sqrt(detectors["efficiency"]) * t)

    started = time.perf_counter()
    perceval_pcc = coincidence(perceval_distribution(u, ia, ib, p), d1, d2, dark)
    perceval_seconds = time.perf_counter() - started
    started = time.perf_counter()
    piquasso_pcc = coincidence(piquasso_distribution(u, ia, ib, p), d1, d2, dark)
    piquasso_seconds = time.perf_counter() - started
    reference = reference_case(meta, wavepacket, detectors, dark)
    result = {
        "network_case": Path(meta["network"]).name, "kind": meta["kind"], "wavepacket": wavepacket_name,
        "dark_count_probability": dark, "lossless": network["transfer_matrix"]["lossless"],
        "column_transmission": network["transfer_matrix"]["column_transmission"],
        "group_delay_method": sorted({method for _, method in delays.values()}),
        "exported_phase_delay_ps": sorted({round(pair["delay_ps"], 9) for pair in network["hints"]["path_pairs"]
                                           if not pair["same_source"]}),
        "relative_group_delay_ps": timing["relative_delay_ps"], "max_path_group_delay_spread_ps": timing["max_path_spread_ps"],
        "indistinguishability": p, "perceval_pcc": perceval_pcc, "piquasso_pcc": piquasso_pcc,
        "reference": reference,
        "deviation": {"perceval_vs_reference": abs(perceval_pcc - reference["pcc"]),
                      "piquasso_vs_reference": abs(piquasso_pcc - reference["pcc"]),
                      "relative_delay_vs_reference_ps": abs(timing["relative_delay_ps"] - reference["relative_delay_ps"])},
        "seconds": {"perceval": round(perceval_seconds, 4), "piquasso": round(piquasso_seconds, 4)},
        "provenance": {"contract_hash": network["contract_hash"], **{k: network["provenance"].get(k) for k in (
            "engine_version", "engine_build_id", "engine_input_hash", "project_hash")}},
    }
    if cross:
        result["deviation"]["exported_vs_computed_group_delay_ps"] = max(cross)
    return result


def plot_scan(scan, path):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    scan = sorted(scan, key=lambda row: row["relative_group_delay_ps"])
    delays = [row["relative_group_delay_ps"] for row in scan]
    figure, axis = plt.subplots(figsize=(5.2, 3.6))
    axis.plot(delays, [row["reference"]["pcc"] for row in scan], "k-", label="closed-form reference")
    axis.plot(delays, [row["perceval_pcc"] for row in scan], "o", color="C0", label="Perceval on OpenSci export")
    axis.plot(delays, [row["piquasso_pcc"] for row in scan], "x", color="C3", label="Piquasso cross-check")
    axis.set_xlabel("relative delay of photon A vs B (ps)")
    axis.set_ylabel("coincidence probability")
    axis.set_title("HOM delay scan, 780 nm, eta = 0.8")
    axis.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return str(path)


def run_example(directory, inputs_path):
    os.environ.setdefault("NUMBA_CACHE_DIR", tempfile.mkdtemp(prefix="numba-cache-"))
    directory = Path(directory)
    inputs = nio.parse_inputs(json.loads(Path(inputs_path).read_text(encoding="utf-8")))
    manifest = json.loads((directory / "networks.json").read_text(encoding="utf-8"))
    cases = {}
    for name, meta in manifest["cases"].items():
        if meta["kind"] == "refusal":
            cases[name] = {"kind": "refusal", "exit_code": meta["exit_code"], "refusal": meta.get("refusal")}
            continue
        wavepacket = nio.lookup(inputs["case_wavepackets"], name, "case_wavepackets")
        cases[name] = analyze(name, meta, inputs, wavepacket, inputs["detectors"]["dark_count_probability"])
    for name, extra in inputs["extra_cases"].items():
        meta = manifest["cases"][extra["network_case"]]
        wavepacket = extra.get("wavepacket") or nio.lookup(inputs["case_wavepackets"], extra["network_case"], "case_wavepackets")
        dark = extra.get("dark_count_probability", inputs["detectors"]["dark_count_probability"])
        try:
            cases[name] = analyze(extra["network_case"], meta, inputs, wavepacket, dark)
        except nio.ModelScopeError as exc:
            cases[name] = {"refused": exc.code, "message": str(exc)}
        cases[name]["expected_refusal"] = extra.get("expect_refusal")

    worst = {}
    unexpected = []
    for name, case in cases.items():
        expected = case.get("expected_refusal")
        if "refused" in case or case.get("kind") == "refusal":
            if case.get("refused") != expected and case.get("kind") != "refusal":
                unexpected.append(name)
            continue
        if expected:
            unexpected.append(name)
        for key, value in case["deviation"].items():
            worst[key] = max(worst.get(key, 0.0), value)
    scan = [c for c in cases.values() if c.get("kind") == "hom_delay_scan"]
    pcc = [row["perceval_pcc"] for row in scan]
    visibility = None
    if scan:
        far = max(pcc)
        visibility = {"perceval": 1.0 - min(pcc) / far,
                      "reference": 1.0 - min(r["reference"]["pcc"] for r in scan) / max(r["reference"]["pcc"] for r in scan),
                      "analytic_zero_delay": inputs["wavepackets"]["broadband"]["zero_delay_indistinguishability"]
                      if "broadband" in inputs["wavepackets"] else None,
                      "note": "1 - min/max of P_cc over the scanned delays; the scan ends at |delay| = 1 ps, where the "
                              "wavepacket overlap is exp(-4) of its zero-delay value"}
    checks = {key: {"value": worst[key], "tolerance": TOLERANCES[key]} for key in TOLERANCES if key in worst}
    status = comparison_status(not unexpected and all(c["value"] <= c["tolerance"] for c in checks.values()))
    figure = plot_scan(scan, directory / "hom_delay_scan.png") if scan else None
    report = {"example": "interop/quantum", "outcome": status, "requires": "network exports from os-optics>=0.10.0",
              "environment": environment("perceval-quandela", "exqalibur", "piquasso", "numba", "numpy", "scipy"),
              "licenses_as_reported_upstream": {
                  "perceval-quandela": "MIT", "exqalibur": "CC BY-NC-ND 4.0 (package metadata; required by Perceval)",
                  "piquasso": "Apache-2.0"},
              "quantum_inputs": {"file": str(inputs_path), **inputs}, "checks": checks, "unexpected_outcomes": unexpected,
              "hom_visibility": visibility, "cases": cases, "figure": figure}
    write_json(directory / "quantum_report.json", report)
    table = {name: ({"refused": case.get("refused") or case.get("refusal")} if "perceval_pcc" not in case else
                    {"p": round(case["indistinguishability"], 9), "perceval": round(case["perceval_pcc"], 10),
                     "reference": round(case["reference"]["pcc"], 10)}) for name, case in cases.items()}
    return {"example": "interop/quantum", "outcome": status, "checks": checks, "unexpected_outcomes": unexpected,
            "hom_visibility": visibility, "cases": table, "report": str(directory / "quantum_report.json"),
            "figure": figure}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("directory", type=Path, help="directory written by build_layouts.py")
        parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS, help="quantum inputs JSON file")
        args = parser.parse_args(argv)
        return run_example(args.directory, args.inputs)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
