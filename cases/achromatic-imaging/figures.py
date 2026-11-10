"""Case-specific PSF/MTF panels using Kit themes, provenance, gallery and bundles."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from os_optics_kit import gallery, present, presentation as pr, workflow_common as wc

from analysis import FREQUENCIES

TEXT = {
    'psf': {'en': 'Native point spread functions', 'zh-CN': '原生点扩散函数'},
    'quality': {'en': 'Common-plane image quality', 'zh-CN': '共同像面的成像质量'},
    'focus': {'en': 'Chromatic focus and common-plane spot', 'zh-CN': '色焦移与共同像面光斑'},
    'studies': {'en': 'Perturbation scales and alignment samples', 'zh-CN': '扰动尺度与装调采样'},
}
CAPTIONS = {
    'psf': {'en': 'Common image plane, equal incident line powers. Each monochromatic PSF is normalized per transmitted energy (1/µm²); all panels share an absolute colour scale. Central ±15 µm crops of saved ±80 µm maps.',
            'zh-CN': '共同像面、等入射线功率。每个单色 PSF 按透射能量归一化（1/µm²），各面板共用绝对色标。显示已保存 ±80 µm 图中的中央 ±15 µm。'},
    'quality': {'en': 'On axis, uniform 6 mm pupil. Solid: native diffraction MTF; dashed: the same traced pupil without phase error. T is shown; S differs by less than 0.0001. Encircled energy is a fraction of total transmitted light, about the peak.',
                'zh-CN': '轴上、均匀照明的 6 mm 孔径。实线为原生衍射 MTF，虚线为相同追迹孔径去除相位误差的参考；图示 T，S 差异小于 0.0001。包围能量为以峰值为中心的透射总能量占比。'},
    'focus': {'en': 'Left: diagnostic minimum geometric RMS focus relative to the fixed image plane. Right: all wavelengths evaluated at that one common plane. Gaussian-weighted native run targets are separate from these uniform-pupil metrics.',
              'zh-CN': '左：最小几何 RMS 光斑的诊断焦点相对固定像面的偏移。右：全部波长在同一共同像面上的结果。高斯加权的原生运行目标与这些均匀孔径指标分别报告。'},
    'studies': {'en': 'Sensitivity uses each declared perturbation scale. Tolerance uses 16 independent draws (seed 20261111); percentages describe this finite sample. The detector remains fixed.',
                'zh-CN': '灵敏度采用各自声明的扰动尺度。公差为 16 次独立采样（种子 20261111）；比例描述此有限样本。探测面保持固定。'},
}


def redraw(out, dest, language='en', background='light'):
    out, dest = Path(out), Path(dest)
    present.init([out / 'nominal/run'], dest, title='Achromatic imaging', overwrite=True)
    spec = wc.read_json(dest / 'presentation.json')
    spec['title'] = {'en': 'Achromatic imaging', 'zh-CN': '消色差成像'}
    spec['theme'].update(language=language, background=background, size='1200x720')
    spec['assets'] = [a for a in spec['assets'] if a['kind'] == 'view']
    spec['assets'][0].update(title={'en': '75 mm cemented objective', 'zh-CN': '75 mm 胶合物镜'},
                            caption={'en': 'Three spherical surfaces; 3 mm crown and 2 mm flint. Light travels toward the fixed image plane.',
                                     'zh-CN': '三个球面；冠玻璃厚 3 mm、火石玻璃厚 2 mm。光线传播至固定像面。'},
                            camera=[65, 18], focus='scene')
    wc.write_json(dest / 'presentation.json', spec)
    built = present.build(dest / 'presentation.json', quiet=True)
    if built['status'] != 'COMPLETED':
        raise ValueError(built)
    theme = pr.resolve_theme(spec['theme'])
    data = {'analysis': wc.read_json(out / 'nominal/imaging.json'),
            'metrics': wc.read_json(out / 'nominal/metrics.json')['fields'],
            'focus': wc.read_json(out / 'focus/focus.json'),
            'sensitivity': wc.read_json(out / 'sensitivity/ranking.json'),
            'tolerance': wc.read_json(out / 'tolerance/imaging-summary.json'),
            'alignment_targets': wc.read_json(out / 'tolerance/summary.json')['rows'],
            'fraction': wc.read_json(out / 'tolerance/fraction.json')}
    manifest = pr.read_manifest(dest)
    source = pr.resolve_source(dest, 'nominal', spec['sources']['nominal'])
    def tr(en, zh):
        return zh if language == 'zh-CN' else en
    with plt.rc_context(theme.rc()):
        for name in TEXT:
            figsize, dpi = theme.canvas()
            fig, axes = plt.subplots(1, 3 if name == 'psf' else 2, figsize=figsize, dpi=dpi, layout='constrained')
            if name == 'psf':
                maps = [data['analysis']['arrays'][f'SRC#{i}.psf'] for i in range(3)]
                maximum = max(float(np.max(m['map_per_um2'])) for m in maps)
                for ax, psf, row in zip(axes, maps, data['metrics']):
                    extent = [psf['axis_um'][0], psf['axis_um'][-1]] * 2
                    im = ax.imshow(psf['map_per_um2'], extent=extent, origin='lower', vmin=0, vmax=maximum,
                                   cmap='magma', interpolation='nearest')
                    ax.set(xlim=(-15, 15), ylim=(-15, 15), title=f'{row["wavelength_nm"]:.1f} nm',
                           xlabel=tr('Tangential [µm]', '子午方向 [µm]'), ylabel=tr('Sagittal [µm]', '弧矢方向 [µm]'))
                    ax.grid(False)
                fig.colorbar(im, ax=axes, shrink=.65, label=tr('Normalized PSF [1/µm²]', '归一化 PSF [1/µm²]'))
            elif name == 'quality':
                for i, row in enumerate(data['metrics']):
                    color = theme.series_color(i)
                    label = f'{row["wavelength_nm"]:.1f} nm'
                    axes[0].plot(FREQUENCIES, row['mtf']['tangential'], 'o-', color=color, label=label)
                    axes[0].plot(FREQUENCIES, row['diffraction_mtf']['tangential'], '--', color=color)
                    ee = data['analysis']['arrays'][f'SRC#{i}.encircled_energy']
                    axes[1].plot(ee['radius_um'], ee['fraction'], color=color, label=label)
                    axes[1].plot(ee['radius_um'], ee['diffraction_limited_fraction'], '--', color=color)
                axes[0].set(xlabel=tr('Spatial frequency [cycles/mm]', '空间频率 [cycles/mm]'), ylabel='MTF', ylim=(0, 1.02))
                axes[1].set(xlabel=tr('Radius about peak [µm]', '距峰值半径 [µm]'),
                            ylabel=tr('Encircled energy fraction', '包围能量占比'), xlim=(0, 20), ylim=(0, 1))
                for ax in axes:
                    ax.legend()
            elif name == 'focus':
                rows = data['focus']['rows']
                wave = [r['wavelength_nm'] for r in rows]
                axes[0].plot(wave, [1000*r['best_focus_offset_mm'] for r in rows], 'o-')
                axes[0].axhline(0, color=theme.colors['muted'], linestyle='--')
                axes[0].set(xlabel=tr('Wavelength [nm]', '波长 [nm]'), ylabel=tr('Best-focus offset [µm]', '最佳焦点偏移 [µm]'))
                axes[1].plot(wave, [r['spot_rms_um'] for r in data['metrics']], 'o-', label=tr('Geometric RMS radius', '几何 RMS 半径'))
                axes[1].plot(wave, [r['ee50_um'] for r in data['metrics']], 's-', label=tr('50% energy radius', '50% 能量半径'))
                axes[1].set(xlabel=tr('Wavelength [nm]', '波长 [nm]'), ylabel=tr('Common-plane radius [µm]', '共同像面半径 [µm]'), ylim=(0, 5.2))
                axes[1].legend()
            else:
                names = {'radius1_fraction': tr('R1 ±0.1%', 'R1 ±0.1%'), 'radius2_fraction': tr('R2 ±0.1%', 'R2 ±0.1%'),
                         'crown_thickness': tr('Crown ±20 µm', '冠玻璃 ±20 µm'), 'image_plane': tr('Plane ±50 µm', '像面 ±50 µm')}
                rows = data['sensitivity']['ranking']
                axes[0].barh([names[r['parameter']] for r in rows], [r['max_spot_change_um'] for r in rows])
                axes[0].invert_yaxis()
                axes[0].set(xlabel=tr('Maximum |Δ RMS radius| [µm]', '最大 |Δ RMS 半径| [µm]'))
                samples = data['alignment_targets']
                for axis, marker in [('u', 'o'), ('v', 's')]:
                    axes[1].plot(range(1, len(samples)+1), [r[f'target:centroid_{axis}']*1000 for r in samples],
                                 marker, label=f'{axis} [µm]')
                axes[1].axhline(50, color=theme.colors['muted'], linestyle='--', label=tr('±50 µm native limit', '±50 µm 原生限值'))
                axes[1].axhline(-50, color=theme.colors['muted'], linestyle='--')
                axes[1].set(xlabel=tr('Seeded draw', '固定种子采样序号'), ylabel=tr('d-line centroid [µm]', 'd 线质心 [µm]'))
                axes[1].legend()
            fig.suptitle(TEXT[name][language])
            asset = {'id': name, 'kind': 'plot', 'type': 'case-imaging', 'sources': ['nominal'],
                     'title': TEXT[name], 'caption': CAPTIONS[name], 'redraw': 'workflow.py present'}
            folder = dest / 'assets' / name
            folder.mkdir(parents=True, exist_ok=True)
            paths = {}
            for ext in ('png', 'svg', 'pdf'):
                paths['image' if ext == 'png' else ext] = folder / f'{name}.{ext}'
                fig.savefig(paths['image' if ext == 'png' else ext])
            plt.close(fig)
            paths['data'] = folder / 'data.json'
            wc.write_json(paths['data'], data)
            captions = {}
            for lang, caption in CAPTIONS[name].items():
                p = folder / f'caption.{lang}.txt'
                p.write_text(caption + '\n', encoding='utf-8')
                captions[lang] = pr.portable_path(p, dest)
            provenance = folder / 'provenance.json'
            wc.write_json(provenance, pr.provenance_record(asset, theme, [source], [paths['data']], dest))
            preview = pr.write_preview(paths['image'], dest / 'previews' / f'{name}.png')
            manifest['assets'].append({'id': name, 'kind': 'plot', 'status': 'written', 'title': TEXT[name][language],
                                       'titles': TEXT[name], 'caption': CAPTIONS[name][language], 'captions': CAPTIONS[name],
                                       'files': {key: pr.file_record(path, dest) for key, path in paths.items()},
                                       'caption_files': captions, 'provenance': pr.portable_path(provenance, dest),
                                       'preview': pr.portable_path(preview, dest), 'size_px': list(theme.size),
                                       'sources': ['nominal'], 'depends_on': []})
    manifest['counts']['written'] += len(TEXT)
    manifest['spec_sha256'] = wc.sha256_file(dest / 'presentation.json')
    pr.write_manifest(dest, manifest)
    gallery.write_gallery(dest)
    gallery.write_bundle(dest, dest.with_suffix('.zip'), overwrite=True)
