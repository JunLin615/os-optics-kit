"""Compute, study or redraw a three-colour objective at one common image plane."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from os_optics_kit import workflow_common as wc
from os_optics_kit.build_report import build_report
from os_optics_kit.compare_runs import compare, write_outputs
from os_optics_kit.sensitivity_analysis import run_sensitivity
from os_optics_kit.tolerance_study import run_tolerance

from analysis import evaluate, displaced_plane, focus_study, imaging, metrics

HERE = Path(__file__).resolve().parent


def compute(out, image_shift_mm=0):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Choose a new or empty output directory for compute')
    document = displaced_plane(wc.read_json(HERE / 'project.json'), image_shift_mm)
    wc.write_json(out / 'project.json', document)
    nominal = evaluate(document, out / 'nominal')
    evaluate(displaced_plane(document, .15), out / 'defocused')
    coarse, seconds = imaging(out / 'nominal/run', out / 'convergence24.json', grid=24)
    coarse_fields = metrics(coarse)
    convergence = {'coarse_grid': 24, 'baseline_grid': 48, 'coarse_seconds': seconds,
                   'max_spot_difference_um': max(abs(a['spot_rms_um'] - b['spot_rms_um'])
                                                for a, b in zip(coarse_fields, nominal['fields'])),
                   'max_mtf_difference': max(abs(x-y) for a, b in zip(coarse_fields, nominal['fields'])
                                             for x, y in zip(a['mtf']['tangential'], b['mtf']['tangential']))}
    assert convergence['max_spot_difference_um'] < .02
    assert convergence['max_mtf_difference'] < .03
    wc.write_json(out / 'convergence.json', convergence)
    focus_study(document, out / 'focus', nominal)
    write_outputs(compare([out / 'defocused/run', out / 'nominal/run'], labels=['defocused', 'nominal'],
                          project=out / 'project.json'), out / 'comparison')
    return nominal


def perturbed(document, values):
    """Perturb the written prescription; no achromat re-solve or refocus."""
    doc = copy.deepcopy(document)
    def decimal(value):
        return f'{value:.9f}'.rstrip('0').rstrip('.')
    model = doc['optical_models'][0]['model']
    for index, name in enumerate(('radius1_fraction', 'radius2_fraction')):
        surface = model['surfaces'][index]
        surface['radius_mm'] = decimal(float(surface['radius_mm']) * (1 + values.get(name, 0)))
    thickness = values.get('crown_thickness', 0)
    for surface in model['surfaces'][1:]:
        surface['vertex_z_mm'] = decimal(float(surface['vertex_z_mm']) + thickness)
    geometry = doc['optical_models'][0]['design_geometry']
    geometry['z_max_mm'] = decimal(float(geometry['z_max_mm']) + thickness)
    return displaced_plane(doc, values.get('image_plane', 0))


def analyse_samples(folder, grid=24):
    output = []
    for sample in sorted((folder / 'samples').glob('*/sample.json')):
        record = wc.read_json(sample)
        if record.get('error'):
            output.append({'sample_id': sample.parent.name, 'native_outcome': record['outcome'],
                           'role': record['role'], 'parameters': record['parameters'],
                           'derived_checks_pass': False, 'fields': [], 'error': record['error']})
            continue
        analysis, seconds = imaging(sample.parent / 'run', sample.parent / 'imaging.json', grid=grid)
        fields = metrics(analysis)
        output.append({'sample_id': sample.parent.name, 'role': record['role'], 'parameters': record['parameters'],
                       'native_outcome': record['outcome'], 'fields': fields, 'imaging_seconds': seconds,
                       'derived_checks_pass': all(all(f['derived_checks'].values()) for f in fields)})
    wc.write_json(folder / 'imaging-summary.json', {'launch_grid': grid, 'samples': output})
    return output


def study(out):
    doc = wc.read_json(out / 'project.json')
    sensitivity = wc.read_json(HERE / 'studies/sensitivity_imaging.json')
    run_sensitivity(sensitivity, out / 'sensitivity', build=lambda values: perturbed(doc, values),
                    plots=False, report=False)
    rows = analyse_samples(out / 'sensitivity')
    nominal = next(row for row in rows if all(p['offset'] == 0 for p in row['parameters'].values()))
    rankings = []
    for parameter in sensitivity['parameters']:
        candidates = [r for r in rows if abs(r['parameters'][parameter['name']]['offset']) > 0]
        effect = max(abs(r['fields'][i]['spot_rms_um'] - nominal['fields'][i]['spot_rms_um'])
                     for r in candidates for i in range(3))
        rankings.append({'parameter': parameter['name'], 'steps': parameter['steps'], 'unit': parameter['unit'],
                         'max_spot_change_um': effect})
    rankings.sort(key=lambda row: row['max_spot_change_um'], reverse=True)
    wc.write_json(out / 'sensitivity/ranking.json', {'basis': 'largest absolute full-pupil spot RMS change across wavelengths and declared steps',
                                                  'ranking': rankings})
    tolerance = wc.read_json(HERE / 'studies/tolerance_imaging.json')
    native = run_tolerance(tolerance, out / 'tolerance', base=doc, plots=False, report=False)
    rows = analyse_samples(out / 'tolerance')
    # The nominal evaluation is not a random draw.
    draws = [row for row in rows if row['role'] == 'sample']
    fraction = {'seed': tolerance['seed'], 'draws': len(draws),
                'derived_pass_count': sum(row.get('derived_checks_pass', False) for row in draws),
                'native_counts': native['counts'],
                'meaning': 'finite simulated sample under the declared independent distributions'}
    assert fraction['draws'] == tolerance['samples']
    wc.write_json(out / 'tolerance/fraction.json', fraction)
    report(out)


def report(out):
    nominal = wc.read_json(out / 'nominal/metrics.json')
    focus = wc.read_json(out / 'focus/focus.json')
    fraction = wc.read_json(out / 'tolerance/fraction.json')
    ranking = wc.read_json(out / 'sensitivity/ranking.json')['ranking']
    for language in ('en', 'zh-CN'):
        table = [('| Wavelength [nm] | Common-plane RMS [µm] | 50% energy radius [µm] | MTF at 40 cycles/mm |'
                  if language == 'en' else '| 波长 [nm] | 共同像面 RMS [µm] | 50% 能量半径 [µm] | 40 cycles/mm 处 MTF |'),
                 '| --- | --- | --- | --- |']
        table += [f'| {f["wavelength_nm"]:.4f} | {f["spot_rms_um"]:.4f} | {f["ee50_um"]:.4f} | '
                  f'{f["mtf"]["tangential"][-1]:.4f} |' for f in nominal['fields']]
        names = {'radius1_fraction': ('First-surface radius R1', '第一面曲率半径 R1'),
                 'radius2_fraction': ('Second-surface radius R2', '第二面曲率半径 R2'),
                 'crown_thickness': ('Crown centre thickness', '冠玻璃中心厚度'),
                 'image_plane': ('Image-plane position', '像面位置')}
        sensitivity = [
            '| Parameter | Perturbation steps | Maximum absolute RMS-radius change [µm] |'
            if language == 'en' else '| 参数 | 扰动步长 | RMS 半径最大绝对变化 [µm] |',
            '| --- | --- | --- |']
        for row in ranking:
            radius = row['parameter'].endswith('_fraction')
            steps = ', '.join(f'±{step * (100 if radius else 1000):g}' for step in row['steps'])
            scale = steps + (' %' if radius else ' µm')
            sensitivity.append(f'| {names[row["parameter"]][language == "zh-CN"]} | {scale} | '
                               f'{row["max_spot_change_um"]:.4f} |')
        native_pass = fraction['native_counts']['by_outcome']['PASS']
        tolerance = [
            '| Random seed | Draws | Native targets passed | Derived imaging checks passed |'
            if language == 'en' else '| 随机种子 | 样本数 | 原生目标通过数 | 派生成像检查通过数 |',
            '| --- | --- | --- | --- |',
            f'| {fraction["seed"]} | {fraction["draws"]} | {native_pass} | {fraction["derived_pass_count"]} |']
        lines = ([
            'On-axis object at infinity; uniform illumination across the 6 mm physical clear pupil.',
            'All common-plane results use x=76.478991 mm, unless the command states an image-plane variation.',
            '486.1327 / 587.5618 / 656.2725 nm have equal incident powers; results are per wavelength.',
            '', *table, '',
            f'Geometric best-focus span: {focus["best_focus_span_mm"]:.6g} mm; these foci are diagnostic only.',
            '', 'Sensitivity: largest absolute RMS-radius change across the three wavelengths and stated steps.',
            '', *sensitivity, '',
            'Seeded alignment tolerance at the fixed common image plane:', '', *tolerance, '',
            'Counts describe the finite sample under the declared independent distributions; the nominal run is excluded.',
        ] if language == 'en' else [
            '物体位于无穷远轴上；均匀照明的物理通光口径为 6 mm。',
            '共同像面为 x=76.478991 mm；命令指定的像面变化另行记录。',
            '486.1327 / 587.5618 / 656.2725 nm 的入射功率相等；逐波长报告结果。',
            '', *table, '',
            f'几何最佳焦点跨度：{focus["best_focus_span_mm"]:.6g} mm；最佳焦点仅用于诊断。',
            '', '灵敏度：三个波长及给定扰动步长中，RMS 半径的最大绝对变化。',
            '', *sensitivity, '',
            '固定共同像面上的装调公差采样：', '', *tolerance, '',
            '通过数描述声明的独立分布下的有限样本；标称运行不计入采样。',
        ])
        build_report([out / 'nominal/run', out / 'defocused/run'], out_dir=out / f'report-{language}',
                     project=out / 'project.json',
                     sections=[{'title': 'Imaging verification' if language == 'en' else '成像验证', 'lines': lines}],
                     artifacts=[(out / name, name) for name in ['nominal/imaging.json', 'focus/focus.json',
                                'convergence.json', 'sensitivity/ranking.json', 'tolerance/fraction.json']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['compute', 'study', 'report', 'present', 'all'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dest', type=Path)
    parser.add_argument('--image-shift-mm', type=float, default=0)
    parser.add_argument('--language', choices=['en', 'zh-CN'], default='en')
    parser.add_argument('--background', choices=['light', 'dark'], default='light')
    args = parser.parse_args(argv)
    if args.command in ('compute', 'all'):
        compute(args.out, args.image_shift_mm)
    if args.command in ('study', 'all'):
        study(args.out)
    if args.command == 'report':
        report(args.out)
    if args.command in ('present', 'all'):
        from figures import redraw
        redraw(args.out, args.dest or args.out / 'presentation', args.language, args.background)


if __name__ == '__main__':
    main()
