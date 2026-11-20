"""Native-spec rebuilding and case gallery/report contracts, without study reruns."""
import shutil

import pytest

pytest.importorskip('os_optics')
pytest.importorskip('matplotlib')

from os_optics import local  # noqa: E402
from os_optics.results.model import RunResults  # noqa: E402
from os_optics_kit import present  # noqa: E402
from tests.test_imaging_requirement_cases import ROOT, module, wc  # noqa: E402


def forbidden(*args, **kwargs):
    raise AssertionError('Saved-result presentation must not simulate')


def test_native_spec_rebuild_and_complete_gallery(tmp_path, monkeypatch):
    figures = module('achromatic-imaging', 'figures.py', monkeypatch)
    analysis = module('achromatic-imaging', 'analysis.py', monkeypatch)
    saved = tmp_path / 'saved'
    analysis.evaluate(wc.read_json(ROOT / 'achromatic-imaging/project.json'), saved / 'nominal')
    snapshot = wc.read_json(ROOT / 'achromatic-imaging/data/snapshot.json')
    # Saved study metadata is sufficient to exercise the presentation contract.
    wc.write_json(saved / 'focus/focus.json', snapshot['focus'])
    wc.write_json(saved / 'sensitivity/ranking.json', snapshot['sensitivity'])
    wc.write_json(saved / 'tolerance/fraction.json', snapshot['tolerance'])
    wc.write_json(saved / 'tolerance/imaging-summary.json', {'samples': []})
    wc.write_json(saved / 'tolerance/summary.json', {'rows': []})
    before = {p: wc.sha256_file(p) for p in saved.rglob('*') if p.is_file()}
    monkeypatch.setattr(local, 'run_optical_project', forbidden)
    monkeypatch.setattr(RunResults, 'imaging', forbidden)

    # Isolate browser capture; keep native source resolution, spec validation,
    # handler dispatch, manifest writing and all four case plot renderers real.
    def saved_view(asset, ctx):
        image = ctx.asset_dir(asset['id']) / 'view.png'
        shutil.copyfile(ROOT / 'achromatic-imaging/images/iso.png', image)
        return {'files': {'image': image}}
    monkeypatch.setattr(present, 'build_view', saved_view)
    gallery = tmp_path / 'gallery'
    figures.redraw(saved, gallery)
    expected = {'view-nominal', 'psf', 'quality', 'focus', 'studies'}
    def complete_cards():
        assets = wc.read_json(gallery / 'assets.json')['assets']
        assert {a['id'] for a in assets} == expected
        assert all(a['status'] in ('written', 'reused') for a in assets)
        assert all((gallery / a['files']['image']['path']).is_file() for a in assets)
    complete_cards()
    result = present.build(gallery / 'presentation.json', out_dir=tmp_path / 'native-view', quiet=True)
    assert result['status'] == 'COMPLETED', result
    assert {a['id'] for a in result['assets']} == {'view-nominal'}
    complete_cards()
    # An in-place native rebuild followed by the full case entry point restores
    # the complete gallery, including when it previously held custom cards.
    assert present.build(gallery / 'presentation.json', quiet=True)['status'] == 'COMPLETED'
    figures.redraw(saved, gallery)
    complete_cards()
    assert {p: wc.sha256_file(p) for p in saved.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('case', ['achromatic-imaging', 'focus-requirement'])
def test_report_sections_have_localized_tables_and_json_links(case, tmp_path, monkeypatch):
    workflow = module(case, 'workflow.py', monkeypatch)
    snapshot = wc.read_json(ROOT / case / 'data/snapshot.json')
    if case == 'achromatic-imaging':
        for path, value in [('nominal/metrics.json', snapshot), ('focus/focus.json', snapshot['focus']),
                            ('sensitivity/ranking.json', snapshot['sensitivity']),
                            ('tolerance/fraction.json', snapshot['tolerance'])]:
            wc.write_json(tmp_path / path, value)
    else:
        wc.write_json(tmp_path / 'verification.json', snapshot['placement'])
    calls = []
    monkeypatch.setattr(workflow, 'build_report', lambda *a, **k: calls.append(k))
    monkeypatch.setattr(wc, 'run_sample', forbidden)
    workflow.report(tmp_path)
    assert len(calls) == 2
    english, chinese = ['\n'.join(call['sections'][0]['lines']) for call in calls]
    assert '{' not in english + chinese
    assert '| --- |' in english and '| --- |' in chinese
    assert all(call['artifacts'] for call in calls)
    if case == 'achromatic-imaging':
        assert '±0.05, ±0.1 %' in english
        assert '±25, ±50 µm' in chinese
        assert '| 20261111 | 16 | 16 | 16 |' in chinese
        assert '有限样本' in chinese and 'fixed common image plane' in english
    else:
        assert '| -0.78 | 未通过 | -0.77 | 通过 | 0.01 |' in chinese
        assert '| 0.79 | Pass | 0.8 | Unmet | 0.01 |' in english
        assert 'x=50 mm' in english and 'x=−10 mm' in chinese
