# Material data and prescription

[简体中文](README.zh-CN.md)

The numerical Sellmeier coefficients are from SCHOTT's public optical-glass datasheets:
[N-BK7](https://www.schott.com/shop/medias/schott-datasheet-n-bk7-eng.pdf?context=bWFzdGVyfHJvb3R8NjkxODAwfGFwcGxpY2F0aW9uL3BkZnxoZTUvaDM4Lzg4MTAzMTYxMDM3MTAucGRmfGJjNmI4ZjFmY2Q1NjMxMTE0MjkzMTUwOGRmMTUzOTg2NWJjZTgzMjA0OTc2NTNiMThjN2RhMjI4NGZmMWM4MWU)
and [N-F2](https://media.schott.com/api/public/content/d9b4f8b7b230479fb8902ce568b5fd3b?v=a7e44e4e),
accessed 2026-11-11. Only numerical material facts are transcribed; no supplier drawing or lens prescription is used.

The model evaluates `n²=1+Σ Bᵢ λ²/(λ²−Cᵢ)` with λ in µm, dimensionless B and C in µm².

| Glass | B1, B2, B3 | C1, C2, C3 [µm²] |
| --- | --- | --- |
| N-BK7 | 1.03961212, 0.231792344, 1.01046945 | 0.00600069867, 0.0200179144, 103.560653 |
| N-F2 | 1.39757037, 0.159201403, 1.2686543 | 0.00995906143, 0.0546931752, 119.248346 |

The declared model band is 450–700 nm. At the d line the indices are 1.51680003 and 1.62005314.
The Engine's native `lens.achromat` builder uses these dispersions, 75 mm EFL, 12.7 mm outer diameter,
6 mm clear diameter, 3/2 mm centre thicknesses and crown shape factor zero. It solves centre-wavelength EFL
and equality of short/long paraxial BFL, then rounds radii to 1 nm. `prescription.json` is its design record;
`project.json` in the parent directory holds the complete material and surface models. Cement thickness,
surface figure error and bulk absorption are zero in this declared model. The case code, prescription and
documentation are original MIT contributions.

`snapshot.json` is a compact numerical reference from the released Engine, with the source Project digest.
Full runs and analysis arrays are generated into the user's output directory.
