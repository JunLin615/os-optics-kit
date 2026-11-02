# Hong-Ou-Mandel interference from the network export

Two 780 nm sources meet on a 50:50 plate beam splitter. The engine describes the layout as a linear optical network:
input and output modes, the transfer matrix, every path's group delay and the conventions. Quantum-optics simulators
compute photon statistics from that export; the Project's lasers only define modes, wavelength and polarization.

![Top view: sources A and B, splitter BS and detector planes D1 and D2](images/top.png)

## Run it

```bash
os-optics-kit cases copy interop-quantum --dest .
cd interop-quantum
python -m pip install -r requirements-quantum.txt
python build_layouts.py --out q
python run_quantum.py q --inputs quantum_inputs.json
```

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
| `d1_power` | 1 mW | 1 mW ± 1 % | MET |
| `d2_power` | 1 mW | 1 mW ± 1 % | MET |

| Quantity (verified run) | Result |
| --- | --- |
| Perceval vs closed-form reference, all cases | 4e-8 |
| Piquasso vs closed-form reference, all cases | 2e-9 |
| HOM dip visibility over the ±0.3 mm delay scan | 0.9491 (reference 0.9491) |
| 3 mm window compensated by its phase delay only | indistinguishability 0.95 → 0.854 |
| 0.25 ps photons in the 1 mm Mach-Zehnder | refused: `SINGLE_FREQUENCY_MODEL_INVALID` |

The export's per-path group delay makes the window's 0.163 ps group-delay mismatch visible. Verified with
os-optics 0.12.0 on Python 3.14.7 (perceval-quandela 1.3.1, piquasso 8.0.1).
