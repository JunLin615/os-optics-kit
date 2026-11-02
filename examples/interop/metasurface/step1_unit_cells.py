"""Step 1: RCWA unit-cell library of TiO2 nanopillars (grcwa; no OpenSci import).

Square lattice (period 325 nm) of TiO2 cylinders (height 600 nm, radius 40-150 nm)
on a SiO2 substrate, light incident normally from the substrate, linear x
polarization. For every radius and wavelength grcwa returns the zeroth-order complex
transmission coefficient t into air and the total transmitted and reflected power
(all orders). Material indices are constants (TiO2 2.43, SiO2 1.46): a stated
assumption of this example; a real design uses measured n(lambda).

Conventions written into the library: field exp(-i w t); the phase is made to grow
with optical path (with pillar radius); ``t`` is grcwa's zeroth-order amplitude ratio,
``T`` the transmitted power fraction. The flux-normalized amplitude used later is
tau = t sqrt(T / |t|^2) (|tau|^2 = T), because |t|^2 alone is not a power fraction
across media of different index.

Usage: python examples/interop/metasurface/step1_unit_cells.py --out run-interop-metasurface [--nG 101]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

# Each RCWA solve works on small matrices: one BLAS thread per process is fastest, and many threads per process
# slow the run down sharply on a busy machine. Set the variables before starting Python to choose otherwise.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np  # noqa: E402

if __package__:
    from ..common import ArgumentParser, emit, environment, package_versions, write_json
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import ArgumentParser, emit, environment, package_versions, write_json

PERIOD_UM, HEIGHT_UM = 0.325, 0.60
N_TIO2, N_SIO2 = 2.43, 1.46
GRID = 128
RADII_UM = tuple(float(r) for r in np.round(np.linspace(0.040, 0.150, 45), 6))
# Model rows; calibration wavelengths (set the error bounds); test wavelengths (used only to check the finished model).
TABLE_NM = (490, 500, 510, 520, 532, 540, 550, 560, 570, 580)
CALIBRATION_NM = (495, 515, 525, 545, 565)
TEST_NM = (505, 535, 555, 575)


def cell(radius_um, wavelength_um, n_g):
    import grcwa

    obj = grcwa.obj(n_g, [PERIOD_UM, 0], [0, PERIOD_UM], 1.0 / wavelength_um, 0.0, 0.0, verbose=0)
    obj.Add_LayerUniform(0.0, N_SIO2 ** 2)
    obj.Add_LayerGrid(HEIGHT_UM, GRID, GRID)
    obj.Add_LayerUniform(0.0, 1.0)
    obj.Init_Setup()
    x = (np.arange(GRID) + 0.5) / GRID * PERIOD_UM - PERIOD_UM / 2
    xx, yy = np.meshgrid(x, x, indexing="ij")
    obj.GridLayer_geteps(np.where(xx ** 2 + yy ** 2 <= radius_um ** 2, N_TIO2 ** 2, 1.0).flatten())
    obj.MakeExcitationPlanewave(1.0, 0.0, 0.0, 0.0, order=0)  # one linear polarization at normal incidence
    reflected, transmitted = obj.RT_Solve(normalize=1)
    incident, _ = obj.GetAmplitudes(0, 0.0)
    outgoing, _ = obj.GetAmplitudes(2, 0.0)
    component = int(np.argmax(np.abs(incident)))
    return complex(outgoing[component] / incident[component]), float(reflected), float(transmitted), int(obj.nG)


def run_example(out_dir, n_g, wavelengths_nm):
    import grcwa  # noqa: F401 - fail early when the solver is missing

    out_dir = Path(out_dir)
    started = time.perf_counter()
    rows = {lam: [cell(r, lam * 1e-3, n_g) for r in RADII_UM] for lam in wavelengths_nm}
    seconds = time.perf_counter() - started
    reference = 532 if 532 in rows else wavelengths_nm[0]
    phase = np.unwrap(np.angle([row[0] for row in rows[reference]]))
    sign = 1.0 if phase[5] > phase[0] else -1.0  # exp(-i w t): longer optical path -> larger phase
    library = {
        "schema": "opensci-interop-example/metasurface-unit-cells/1",
        "assumptions": {"period_um": PERIOD_UM, "height_um": HEIGHT_UM, "n_TiO2": N_TIO2, "n_SiO2": N_SIO2,
                        "incidence": "normal, from the SiO2 substrate into air, linear x polarization",
                        "phase_convention": "exp(-i w t); phase relative to the incident zeroth order, sign chosen so "
                                            "that the phase grows with pillar radius", "sign_applied": sign},
        "solver": {"tool": "grcwa", "version": package_versions("grcwa")["grcwa"], "nG_requested": n_g, "nG_used": rows[wavelengths_nm[0]][0][3], "grid": GRID},
        "radii_um": list(RADII_UM), "wavelengths_nm": list(wavelengths_nm),
        "t": {str(lam): [[row[0].real, sign * row[0].imag] for row in rows[lam]] for lam in wavelengths_nm},
        "R": {str(lam): [row[1] for row in rows[lam]] for lam in wavelengths_nm},
        "T": {str(lam): [row[2] for row in rows[lam]] for lam in wavelengths_nm},
    }
    path = out_dir / f"library_nG{n_g}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(library, separators=(",", ":")), encoding="utf-8")
    energy = max(abs(r + t - 1.0) for lam in wavelengths_nm for _, r, t, _ in rows[lam])
    summary = {"example": "interop/metasurface step 1", "outcome": "COMPLETED" if energy < 1e-3 else "UNEXPECTED_RESULT",
               "environment": environment("grcwa", "numpy"), "library": str(path), "solves": len(RADII_UM) * len(rows),
               "seconds": round(seconds, 1), "max_abs_R_plus_T_minus_1": energy,
               "phase_span_waves": {str(lam): float(np.ptp(np.unwrap(np.angle([row[0] for row in rows[lam]]))) / (2 * np.pi))
                                    for lam in wavelengths_nm}}
    write_json(out_dir / f"library_nG{n_g}.summary.json", summary)
    return summary


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--out", type=Path, default=Path("run-interop-metasurface"), help="output directory")
        parser.add_argument("--nG", type=int, default=101, help="number of RCWA Fourier orders (default 101)")
        parser.add_argument("--wavelengths-nm", type=int, nargs="+", default=list(TABLE_NM + CALIBRATION_NM + TEST_NM),
                            help="wavelengths in nm (default: table rows, calibration and test wavelengths)")
        args = parser.parse_args(argv)
        return run_example(args.out, args.nG, sorted(set(args.wavelengths_nm)))

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
