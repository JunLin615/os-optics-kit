"""Native imaging calculations and explicit derived checks for the declared objective."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from scipy.special import j0, j1
from os_optics.local.paraxial import paraxial_analysis
from os_optics.results import load_run
from os_optics_kit import workflow_common as wc

HERE = Path(__file__).resolve().parent
WAVELENGTHS = [486.1327, 587.5618, 656.2725]
FREQUENCIES = [0, 10, 20, 40]


def imaging(run, output, *, grid=48, wavelengths=None):
    request = dict(launch_grid=grid, psf_samples=128, psf_half_width_um=80,
                   frequencies_cyc_per_mm=FREQUENCIES, include_psf_map=True)
    if wavelengths is not None:
        request['wavelengths_nm'] = wavelengths
    start = time.perf_counter()
    result = load_run(run).imaging('image', request).model_dump(mode='json')
    wc.write_json(output, result)
    return result, time.perf_counter() - start


def metrics(analysis):
    quantities = {q['quantity_id']: q for q in analysis['quantities']}
    arrays = analysis['arrays']
    rows = []
    for index, wavelength in enumerate(WAVELENGTHS):
        prefix = f'SRC#{index}.'
        if prefix + 'image_space_na' not in quantities:
            continue
        def value(name):
            q = quantities[prefix + name]
            if q['state'] != 'KNOWN':
                raise ValueError(f'{prefix}{name}: {q["state"]}, {q.get("reason")}')
            return q['value'] if q['values_ref'] is None else arrays[q['values_ref']]
        na = value('image_space_na')
        mtf = {direction: value('mtf_' + direction) for direction in ('tangential', 'sagittal')}
        reference = {direction: value('diffraction_limited_mtf_' + direction)
                     for direction in ('tangential', 'sagittal')}
        cutoff = 2 * na / (wavelength * 1e-6)
        normalized = np.clip(np.asarray(FREQUENCIES) / cutoff, 0, 1)
        circular = 2 / np.pi * (np.arccos(normalized) - normalized * np.sqrt(1 - normalized ** 2))
        row = {'wavelength_nm': wavelength, 'na': na,
               'spot_rms_um': value('geometric_spot_rms_radius'),
               'ee50_um': value('encircled_radius_50'), 'ee80_um': value('encircled_radius_80'),
               'reference_ee80_um': value('diffraction_limited_encircled_radius_80'),
               'strehl': value('strehl_ratio'), 'mtf': mtf, 'diffraction_mtf': reference,
               'circular_pupil_mtf_reference': circular.tolist(),
               'circular_reference_max_difference': float(np.max(np.abs(circular - reference['tangential']))),
               'window_light_share': arrays[prefix + 'psf']['window_light_share'],
               'pupil': arrays[prefix + 'rays'],
               'convergence': {q['quantity_id'][len(prefix):]: q['convergence'] for q in quantities.values()
                               if q['quantity_id'].startswith(prefix) and q['convergence'] is not None}}
        pupil = row['pupil']
        # Uniform-grid cell area relative to the declared 3 mm-radius pupil.
        # This is a geometric sampling estimate, distinct from Fresnel throughput.
        pitch = 2 * pupil['launch_radius_beam_radii'] / pupil['launch_grid'][1]
        row['geometric_pupil_area_fraction'] = pupil['rays_arrived'][1] * pitch ** 2 / (np.pi * 3 ** 2)
        # Independent scalar circular-pupil reference: E(x)=1-J0(x)^2-J1(x)^2,
        # x=2*pi*NA*r/lambda. The native reference retains actual pupil apodization.
        row['circular_airy_first_zero_um'] = .61 * wavelength * .001 / na
        row['circular_ee_radii_um'] = {
            str(percent): float(brentq(lambda x: 1-j0(x)**2-j1(x)**2-percent/100, .001, 3.83171)
                                * wavelength * .001 / (2*np.pi*na)) for percent in (50, 80)}
        row['ee80_over_native_diffraction_reference'] = row['ee80_um'] / row['reference_ee80_um']
        row['derived_checks'] = {
            'full_pupil_spot_at_most_2_um': row['spot_rms_um'] <= 2,
            'ee50_at_most_5_um': row['ee50_um'] <= 5,
            'mtf_10_20_40_at_least_0.85_0.75_0.55': all(
                all(v >= bound for v, bound in zip(curve[1:], [.85, .75, .55])) for curve in mtf.values()),
        }
        rows.append(row)
    return rows


def evaluate(document, folder, *, grid=48):
    record = wc.run_sample(document, folder)
    wc.write_json(folder / 'sample.json', record)
    if record.get('execution', {}).get('complete') is False or record.get('error'):
        raise ValueError(record)
    analysis, seconds = imaging(folder / 'run', folder / 'imaging.json', grid=grid)
    monitor = load_run(folder / 'run').monitor('image')
    throughput = [{'wavelength_nm': r['beam']['wavelength_nm'], 'primary_power_w': r['beam']['power_W'],
                   'primary_fraction_of_source_line': r['beam']['power_W'] / (0.001 / 3)} for r in monitor['arrivals']]
    result = {'native': record, 'imaging_seconds': seconds, 'fields': metrics(analysis),
              'primary_gaussian_throughput': throughput,
              'power_accounting': {k: monitor[k] for k in ('power_complete', 'untraced_power_W', 'power_upper_bound_W', 'issue_codes')}}
    wc.write_json(folder / 'metrics.json', result)
    return result


def displaced_plane(document, delta):
    changed = copy.deepcopy(document)
    changed['observations'][0]['pose']['position_mm'][0] += delta
    return changed


def focus_study(document, out, nominal):
    """Native full-pupil spot RMS squared is quadratic during free-space propagation.

    Three planes locate its minimum; a fourth native run at each predicted minimum
    checks it. These diagnostic best foci never replace the common-plane results.
    """
    step = 0.1
    sides = [evaluate(displaced_plane(document, delta), out / name, grid=24)
             for name, delta in [('minus', -step), ('plus', step)]]
    centre = evaluate(document, out / 'centre', grid=24)
    rows = []
    for i, wavelength in enumerate(WAVELENGTHS):
        y = [sides[0]['fields'][i]['spot_rms_um'] ** 2, centre['fields'][i]['spot_rms_um'] ** 2,
             sides[1]['fields'][i]['spot_rms_um'] ** 2]
        a, b, c = np.polyfit([-step, 0, step], y, 2)
        delta = float(-b / (2 * a))
        check = evaluate(displaced_plane(document, delta), out / f'best-{i}', grid=24)
        actual = check['fields'][i]['spot_rms_um']
        predicted = float(np.sqrt(max(0, c - b * b / (4 * a))))
        assert abs(predicted - actual) < .02
        paraxial = paraxial_analysis(document, {'components': ['OBJ'], 'wavelength_nm': wavelength})
        wc.write_json(out / f'paraxial-{i}.json', paraxial)
        rows.append({'wavelength_nm': wavelength, 'best_focus_offset_mm': delta,
                     'best_focus_x_mm': document['observations'][0]['pose']['position_mm'][0] + delta,
                     'best_geometric_spot_um': actual, 'quadratic_prediction_error_um': abs(predicted - actual),
                     'efl_mm': paraxial['system']['efl_mm'], 'bfl_mm': paraxial['system']['bfl_mm'],
                     'common_plane_spot_um': nominal['fields'][i]['spot_rms_um']})
    span = max(r['best_focus_x_mm'] for r in rows) - min(r['best_focus_x_mm'] for r in rows)
    result = {'criterion': 'minimum full-pupil geometric RMS radius', 'fit_planes_offset_mm': [-step, 0, step],
              'launch_grid': 24, 'rows': rows, 'best_focus_span_mm': span,
              'derived_checks': {'focus_span_at_most_0.06_mm': span <= .06,
                                 'centre_efl_75_mm_within_1_percent': abs(rows[1]['efl_mm'] - 75) <= .75}}
    wc.write_json(out / 'focus.json', result)
    return result
