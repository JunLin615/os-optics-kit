"""Step 2: metalens focusing from the unit-cell library (numpy and scipy; no OpenSci import).

A hyperbolic metalens (focal length 200 um at 532 nm, diameter 100 um) is laid out
from the step-1 library: each 325 nm cell takes the pillar whose 532 nm response is
closest to the required phase, among pillars transmitting at least 80 % at 532 nm.
Under the local periodic approximation (LPA) every cell transmits its library
amplitude tau(r, lambda) at every wavelength. A Gaussian input (1/e^2 radius 30 um,
waist at the metasurface; optionally tilted) is multiplied by the cell responses and
propagated in air with the scalar angular-spectrum method. For each case the code
finds the axial position of the intensity maximum and reports there:

* ``z_focus_um`` and ``focus_xy_um``: position of the maximum;
* ``w_focus_um``: second-moment 1/e^2 radius inside a disk of 3 ideal-focus radii;
* ``focusing_efficiency``: power in that disk / incident Gaussian power;
* ``transmitted_fraction``: power leaving the metasurface / incident power.

LPA neglects coupling between neighbouring cells of different radius; scalar
propagation neglects the vector field. Tilted inputs reuse the normal-incidence cell
responses (only the incident phase ramp changes), an approximation that worsens with
angle. These are stated limits of this reference.

Usage: python examples/interop/metasurface/step2_focusing.py run-interop-metasurface [--samples 1024] [--nG 101]
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time

import numpy as np

if __package__:
    from ..common import ArgumentParser, emit, environment, write_json
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import ArgumentParser, emit, environment, write_json

DESIGN = {"focal_length_um": 200.0, "design_wavelength_nm": 532, "diameter_um": 100.0, "input_waist_radius_um": 30.0,
          "minimum_atom_transmittance": 0.8}
# Calibration cases set the field-angle and beam-size domain; test cases only check the finished model.
EXTRA_CASES = ({"wavelength_nm": 532, "tilt_deg": 2.0, "set": "calibration"},
               {"wavelength_nm": 532, "tilt_deg": 5.0, "set": "calibration"},
               {"wavelength_nm": 532, "tilt_deg": 8.0, "set": "calibration"},
               {"wavelength_nm": 532, "tilt_deg": 20.0, "set": "calibration"},
               {"wavelength_nm": 532, "input_waist_radius_um": 20.0, "set": "calibration"},
               {"wavelength_nm": 555, "tilt_deg": 1.5, "set": "test"},
               {"wavelength_nm": 545, "input_waist_radius_um": 25.0, "set": "test"})


class Metalens:
    def __init__(self, library, samples):
        self.library = library
        self.period = library["assumptions"]["period_um"]
        self.samples = samples
        coords = (np.arange(samples) - samples // 2) * self.period
        self.x = coords
        self.xx, self.yy = np.meshgrid(coords, coords, indexing="ij")
        radius = np.hypot(self.xx, self.yy)
        self.inside = radius <= DESIGN["diameter_um"] / 2
        lam0 = DESIGN["design_wavelength_nm"] * 1e-3
        f0 = DESIGN["focal_length_um"]
        target = np.mod(-2 * np.pi / lam0 * (np.sqrt(radius ** 2 + f0 ** 2) - f0), 2 * np.pi)
        tau0 = self.tau(DESIGN["design_wavelength_nm"])
        usable = np.where(np.abs(tau0) ** 2 >= DESIGN["minimum_atom_transmittance"])[0]
        unit = tau0[usable] / np.abs(tau0[usable])
        choice = usable[np.argmin(np.abs(unit[None, :] - np.exp(1j * target[self.inside])[:, None]), axis=1)]
        self.index = np.full(self.xx.shape, -1)
        self.index[self.inside] = choice
        self.atoms_used = int(usable.size)
        frequencies = np.fft.fftfreq(samples, d=self.period)
        self.fx, self.fy = np.meshgrid(frequencies, frequencies, indexing="ij")

    def tau(self, wavelength_nm):
        """Flux-normalized cell amplitude: |tau|^2 = transmitted power fraction T."""
        key = str(wavelength_nm)
        t = np.array([complex(a, b) for a, b in self.library["t"][key]])
        power = np.array(self.library["T"][key])
        return t * np.sqrt(power / np.maximum(np.abs(t) ** 2, 1e-30))

    def focus(self, wavelength_nm, tilt_deg=0.0, waist_um=None):
        waist_um = waist_um or DESIGN["input_waist_radius_um"]
        lam = wavelength_nm * 1e-3
        k = 2 * np.pi / lam
        gaussian = np.exp(-(self.xx ** 2 + self.yy ** 2) / waist_um ** 2) * np.exp(
            1j * k * math.sin(math.radians(tilt_deg)) * self.xx)
        field = np.zeros(self.xx.shape, complex)
        field[self.inside] = gaussian[self.inside] * self.tau(wavelength_nm)[self.index[self.inside]]
        cell_area = self.period ** 2
        incident = float(np.sum(np.abs(gaussian) ** 2) * cell_area)
        spectrum = np.fft.fft2(field)
        kz2 = k ** 2 - (2 * np.pi * self.fx) ** 2 - (2 * np.pi * self.fy) ** 2
        propagating = kz2 > 0
        kz = np.sqrt(np.abs(kz2))

        def intensity(z):
            return np.abs(np.fft.ifft2(np.where(propagating, spectrum * np.exp(1j * kz * z), 0))) ** 2

        from scipy.optimize import minimize_scalar

        coarse = np.linspace(100.0, 320.0, 45)
        start = coarse[int(np.argmax([intensity(z).max() for z in coarse]))]
        best = minimize_scalar(lambda z: -float(intensity(z).max()), bounds=(start - 6.0, start + 6.0), method="bounded",
                               options={"xatol": 1e-4})
        z_focus = float(best.x)
        image = intensity(z_focus)
        peak = np.unravel_index(int(np.argmax(image)), image.shape)
        xc, yc = self.x[peak[0]], self.x[peak[1]]
        rr = np.hypot(self.xx - xc, self.yy - yc)
        disk = rr <= 3 * lam * z_focus / (math.pi * waist_um)
        in_disk = float(image[disk].sum() * cell_area)
        w = math.sqrt(2 * float((image[disk] * rr[disk] ** 2).sum() / image[disk].sum()))
        return {"wavelength_nm": wavelength_nm, "tilt_deg": tilt_deg, "input_waist_radius_um": waist_um,
                "z_focus_um": z_focus, "focus_xy_um": [float(xc), float(yc)], "w_focus_um": w,
                "focusing_efficiency": in_disk / incident,
                "transmitted_fraction": float(np.sum(np.abs(field) ** 2) * cell_area) / incident}


def run_example(directory, samples, n_g, wavelengths_nm, extra):
    directory = Path(directory)
    library_path = directory / f"library_nG{n_g}.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    wavelengths = [w for w in library["wavelengths_nm"] if not wavelengths_nm or w in wavelengths_nm]
    lens = Metalens(library, samples)
    started = time.perf_counter()
    rows = [lens.focus(lam) for lam in wavelengths]
    if extra:
        rows += [dict(lens.focus(case["wavelength_nm"], case.get("tilt_deg", 0.0), case.get("input_waist_radius_um")),
                      set=case["set"]) for case in EXTRA_CASES if str(case["wavelength_nm"]) in library["t"]]
    table = {
        "schema": "opensci-interop-example/metasurface-focus-table/1",
        "library": library_path.name, "design": dict(DESIGN, atoms_used=lens.atoms_used),
        "grid": {"samples": samples, "dx_um": lens.period, "window_um": samples * lens.period},
        "method": "local periodic approximation (zeroth-order RCWA response per cell) + scalar angular spectrum in air",
        "seconds": round(time.perf_counter() - started, 1), "environment": environment("numpy", "scipy"), "rows": rows,
    }
    path = directory / f"focus_nG{n_g}_N{samples}.json"
    write_json(path, table)
    return {"example": "interop/metasurface step 2", "outcome": "COMPLETED", "environment": environment("numpy", "scipy"),
            "focus_table": str(path), "cases": len(rows), "seconds": table["seconds"]}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("directory", type=Path, help="directory with the step-1 library")
        parser.add_argument("--samples", type=int, default=1024, help="grid samples per side (default 1024)")
        parser.add_argument("--nG", type=int, default=101, help="library to use (default nG 101)")
        parser.add_argument("--wavelengths-nm", type=int, nargs="*", default=None, help="subset of library wavelengths")
        parser.add_argument("--no-extra", action="store_true", help="skip the tilted and smaller-beam cases")
        args = parser.parse_args(argv)
        return run_example(args.directory, args.samples, args.nG, args.wavelengths_nm, not args.no_extra)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
