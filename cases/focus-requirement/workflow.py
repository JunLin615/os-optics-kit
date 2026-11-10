"""A fixed-plane focusing requirement. Compute, study, and redraw are separate commands."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from os_optics_kit import present, workflow_common as wc
from os_optics_kit.build_report import build_report
from os_optics_kit.compare_runs import compare, write_outputs
from os_optics_kit.edit_project import apply_edits
from os_optics_kit.gallery import write_bundle
from os_optics_kit.sweep_parameters import run_sweep

HERE = Path(__file__).resolve().parent
LABELS = {
    'spot_radius': {'en': 'Radius at fixed work plane', 'zh-CN': '固定工作面的光束半径'},
    'lens_shift': {'en': 'Lens placement error', 'zh-CN': '透镜位置误差'},
    'before': {'en': 'Lens displaced +1 mm', 'zh-CN': '透镜偏移 +1 mm'},
    'nominal': {'en': 'Nominal placement', 'zh-CN': '标称位置'},
    'placement': {'en': 'Placement sweep', 'zh-CN': '位置扫描'},
}


def placement_ops(shift):
    return [{'op': 'transform', 'selector': {'ids': ['LENS']}, 'frame': 'design',
             'translation_mm': [shift, 0, 0]}]


def shifted(document, shift):
    changed = apply_edits(document, placement_ops(shift))['project']
    assert changed['observations'] == document['observations']
    assert changed['components'][0] == document['components'][0]
    assert changed['targets'] == document['targets']
    return changed


def reference_radius(shift, document):
    """Independent complex-q check at the fixed physical detector, in mm."""
    source = document['components'][0]['source_model']
    wavelength = source['wavelength_nm'] * 1e-6
    q = complex(10 + shift, math.pi * source['waist_radius_mm'] ** 2 / wavelength)
    q = 1 / (1 / q - 1 / 50) + 50 - shift
    return math.sqrt(-wavelength / (math.pi * (1 / q).imag))


def compute(out):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose a new or empty output directory for compute')
    document = wc.read_json(HERE / 'project.json')
    out.mkdir(parents=True, exist_ok=True)
    wc.write_json(out / 'project.json', document)
    for name, shift in [('nominal', 0), ('before', 1)]:
        record = wc.run_sample(shifted(document, shift), out / name)
        wc.write_json(out / name / 'sample.json', record)
        if record['outcome'] != ('PASS' if shift == 0 else 'FAIL'):
            raise ValueError(f'{name}: unexpected native outcome {record["outcome"]}')
    return document


def sweep_spec(values):
    return {'max_evaluations': len(values), 'targets': ['spot_radius'],
            'parameters': [{'name': 'lens_shift', 'unit': 'mm', 'values': values,
                            'ops': placement_ops('$value')}],
            'objective': {'column': 'target:spot_radius', 'sense': 'minimize'}}


def boundaries(rows):
    rows = sorted(rows, key=lambda r: r['param:lens_shift'])
    transitions = []
    for left, right in zip(rows, rows[1:]):
        if (left['outcome'] == 'PASS') != (right['outcome'] == 'PASS'):
            transitions.append({'left_mm': left['param:lens_shift'], 'right_mm': right['param:lens_shift'],
                                'left_outcome': left['outcome'], 'right_outcome': right['outcome']})
    if len(transitions) != 2:
        raise ValueError('Expected a passing interval bracketed by two failing sides')
    return transitions


def study(out, resolution=0.01):
    if not 0.001 <= resolution <= 0.05:
        raise ValueError('resolution must be 0.001..0.05 mm')
    document = wc.read_json(out / 'project.json')
    coarse = run_sweep(sweep_spec([i / 5 for i in range(-5, 6)]), out / 'placement', base=document)
    values = []
    for bracket in boundaries(coarse['rows']):
        low, high = bracket['left_mm'], bracket['right_mm']
        count = math.ceil((high - low) / resolution - 1e-9)
        values.extend(low + (high - low) * i / count for i in range(count + 1))
    fine = run_sweep(sweep_spec(sorted(set(values))), out / 'refinement', base=document)
    rows = coarse['rows'] + fine['rows']
    errors = []
    for row in rows:
        if row['outcome'] not in ('PASS', 'FAIL'):
            raise ValueError(f'Unresolved sample {row["sample_id"]}')
        errors.append(abs(row['target:spot_radius'] - reference_radius(row['param:lens_shift'], document)))
    brackets = boundaries(rows)
    interval = [brackets[0]['right_mm'], brackets[1]['left_mm']]
    answer = {'schema': 'focus-requirement.verification', 'resolution_mm': resolution,
              'sampled_passing_interval_mm': interval, 'boundary_brackets': brackets,
              'max_reference_error_mm': max(errors), 'evaluations': len(rows),
              'geometry': {'source_x_mm': -10, 'nominal_lens_x_mm': 0, 'fixed_work_plane_x_mm': 50},
              'requirement': {'metric': 'beam_radius_mm', 'maximum_mm': 0.020,
                              'convention': 'Gaussian 1/e^2 intensity radius at the fixed work plane'},
              'identity': coarse['identity']}
    assert max(errors) < 1e-10
    assert all(b['right_mm'] - b['left_mm'] <= resolution + 1e-12 for b in brackets)
    wc.write_json(out / 'verification.json', answer)
    write_outputs(compare([out / 'before/run', out / 'nominal/run'], labels=['before', 'nominal'],
                          project=out / 'project.json'), out / 'comparison')
    for language in ('en', 'zh-CN'):
        title = 'Fixed-plane requirement' if language == 'en' else '固定工作面要求'
        lines = (['Gaussian 1/e² radius ≤20 µm at x=50 mm; the source remains at x=−10 mm.',
                  f'Sampled passing interval: {interval} mm; boundary resolution ≤{resolution} mm.',
                  f'Boundary brackets (left/right outcomes): {brackets}.',
                  'The interval describes axial placement alone; every sampled point is retained.',
                  f'Maximum complex-q reference error: {max(errors):.3g} mm.'] if language == 'en' else
                 ['在 x=50 mm 处，高斯光束的 1/e² 强度半径须≤20 µm；光源固定在 x=−10 mm。',
                  f'采样通过区间：{interval} mm；边界分辨率≤{resolution} mm。',
                  f'边界夹逼（左右端点的结果）：{brackets}。',
                  '此区间仅描述轴向位置误差；保留全部采样点。',
                  f'复 q 参数独立参考的最大误差：{max(errors):.3g} mm。'])
        build_report([out / 'placement'], out_dir=out / f'report-{language}', project=out / 'project.json',
                     sections=[{'title': title, 'lines': lines}],
                     artifacts=[(out / 'verification.json', 'fixed-plane verification'),
                                (out / 'comparison/compare.json', 'before and after'),
                                (out / 'refinement/summary.json', 'boundary refinement')])
    return answer


def redraw(out, dest, language='en', background='light'):
    """Only saved runs/studies are read. Native Kit creates the gallery and bundle."""
    paths = [out / 'before/run', out / 'nominal/run', out / 'placement']
    present.init(paths, dest, labels=['before', 'nominal', 'placement'],
                 title='Fixed-plane focusing requirement', overwrite=True)
    spec = wc.read_json(dest / 'presentation.json')
    spec['title'] = {'en': 'Fixed-plane focusing requirement', 'zh-CN': '固定工作面的聚焦要求'}
    spec['theme'].update(language=language, background=background, size='1200x720', labels=LABELS)
    for source_id, source in spec['sources'].items():
        source['label'] = LABELS[source_id]
    spec['assets'] = [a for a in spec['assets'] if a.get('type') in ('targets', 'sweep')]
    for asset in spec['assets']:
        if asset.get('type') == 'targets':
            asset['title'] = {'en': 'Before and after placement correction', 'zh-CN': '位置修正前后比较'}
        asset['caption'] = {'en': '780 nm, 1 mm source waist radius; work plane fixed at x=50 mm. Radius bound: 20 µm.',
                            'zh-CN': '780 nm，光源束腰半径 1 mm；工作面固定在 x=50 mm。半径上限：20 µm。'}
    wc.write_json(dest / 'presentation.json', spec)
    result = present.build(dest / 'presentation.json', quiet=True)
    if result['status'] != 'COMPLETED':
        raise ValueError(result)
    write_bundle(dest, dest.with_suffix('.zip'), overwrite=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['compute', 'study', 'present', 'all'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dest', type=Path)
    parser.add_argument('--resolution-mm', type=float, default=0.01)
    parser.add_argument('--language', choices=['en', 'zh-CN'], default='en')
    parser.add_argument('--background', choices=['light', 'dark'], default='light')
    args = parser.parse_args(argv)
    if args.command in ('compute', 'all'):
        compute(args.out)
    if args.command in ('study', 'all'):
        study(args.out, args.resolution_mm)
    if args.command in ('present', 'all'):
        redraw(args.out, args.dest or args.out / 'presentation', args.language, args.background)


if __name__ == '__main__':
    main()
