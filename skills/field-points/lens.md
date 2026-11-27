# field-points: from the report to an os-optics-lens record

Read this when the result becomes a fact of an os-optics-lens record (field verification, CRA, edge MTF).

| Lens member | From `field-points.json` |
|---|---|
| `native_project.sha256` | `input.sha256_lf` (SHA-256 of the Project bytes with CRLF -> LF); bind only to this |
| `configuration_id` | `input.configuration_id` (null: the Project as written) |
| image plane | `plane.observation_id`, fixed: pose unchanged; `plane.widened` changes only the active area |
| `field.kind` | `conditions.field_kind`; pass the record's kind as `--expect-field-kind` |
| `field_basis` | yours to name, e.g. `diagonal_image_height_fraction` = `--image-heights-mm` of fraction x radius along `--azimuth-deg` |
| `sampled_field_fractions` | `conditions.requested` divided by the radius, in the same order |
| `frequency_lp_mm` | `minima[].frequency_cyc_per_mm` (the same number; Lens reads the unit from the name) |
| directions | `minima[].directions` (`tangential`, `sagittal`) |
| `limiting_sample` | `minima[].all_lines.limiting_sample` (`point_index`, `direction`, `wavelength_nm`) |
| spectrum | `conditions.wavelengths_nm`, `conditions.line_powers_w` |
| CRA | `points[].chief_ray.angle_deg` (to the plane normal) |
| first-order check | `points[].first_order` (`FIRST_ORDER_COMPARISON`), with its EFL or magnification and wavelength |

- Per line, not weighted: `metric` is `per_line_minimum`. A record whose value is a spectrally weighted MTF (equal or
  flux weights, complex OTF sum) states another metric; do not file a per-line minimum under its conditions.
- Value basis: per-point numbers are `ENGINE_COMPUTED`; a minimum over sampled points is `PROJECT_DERIVED`.
- An `UNCONFIRMED` minimum stays unconfirmed: its `missing[]` gives each sample's reason.
- The evaluated Project differs from the record's (sources replaced, targets removed, maybe a wider plane):
  `evaluated.derivation` lists every change; keep it with the evidence, bind facts to `input.sha256_lf`.
