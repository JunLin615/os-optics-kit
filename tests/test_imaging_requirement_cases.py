"""Scientific checks for the two independently copyable requirement cases."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip('os_optics')

from os_optics.local.paraxial import paraxial_analysis  # noqa: E402
from os_optics_kit import workflow_common as wc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / 'cases'


def module(case, filename, monkeypatch):
    path = ROOT / case / filename
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location(case.replace('-', '_') + filename[:-3], path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_placement_preserves_requirement_geometry_and_checks_both_boundaries(tmp_path, monkeypatch):
    workflow = module('focus-requirement', 'workflow.py', monkeypatch)
    doc = wc.read_json(ROOT / 'focus-requirement/project.json')
    for index, (shift, expected) in enumerate([(-.78, 'FAIL'), (-.77, 'PASS'), (0, 'PASS'), (.79, 'PASS'), (.8, 'FAIL')]):
        changed = workflow.shifted(doc, shift)
        assert changed['observations'] == doc['observations']
        assert changed['components'][0] == doc['components'][0]
        assert changed['targets'] == doc['targets']
        record = wc.run_sample(changed, tmp_path / str(index))
        assert record['execution']['complete']
        assert record['outcome'] == expected
        actual = next(t['value'] for t in record['targets'] if t['id'] == 'spot_radius')
        assert actual == pytest.approx(workflow.reference_radius(shift, doc), abs=1e-10)


def test_achromat_dispersion_common_plane_and_native_diffraction(tmp_path, monkeypatch):
    analysis = module('achromatic-imaging', 'analysis.py', monkeypatch)
    doc = wc.read_json(ROOT / 'achromatic-imaging/project.json')
    assert all(m['dispersion']['kind'] == 'SELLMEIER' for m in doc['model_materials'])
    focal = [paraxial_analysis(doc, {'components': ['OBJ'], 'wavelength_nm': wavelength})['system']
             for wavelength in analysis.WAVELENGTHS]
    assert focal[0]['bfl_mm'] == pytest.approx(focal[2]['bfl_mm'], abs=1e-6)
    assert abs(focal[0]['bfl_mm'] - focal[1]['bfl_mm']) > .03
    assert focal[1]['efl_mm'] == pytest.approx(75, abs=1e-4)
    result = analysis.evaluate(doc, tmp_path / 'objective')
    assert result['native']['outcome'] == 'PASS'
    for field in result['fields']:
        assert all(field['derived_checks'].values())
        assert field['circular_reference_max_difference'] < .03
        assert field['reference_ee80_um'] == pytest.approx(field['circular_ee_radii_um']['80'], rel=.02)
        # Linear polarization gives weak s/p Fresnel apodization even on axis.
        assert field['mtf']['tangential'] == pytest.approx(field['mtf']['sagittal'], abs=1e-4)
        assert .98 < field['geometric_pupil_area_fraction'] < 1.02
    # Full-aperture image quality uses a different illumination than the native Gaussian rings.
    native_d = next(t['value'] for t in result['native']['targets'] if t['id'] == 'pupil_spot_d') * 1000
    assert result['fields'][1]['spot_rms_um'] > native_d
    saved = wc.read_json(tmp_path / 'objective/run/project.json')
    assert saved['observations'] == doc['observations']


def test_prescription_perturbation_changes_surface_without_resolving_focus(monkeypatch):
    workflow = module('achromatic-imaging', 'workflow.py', monkeypatch)
    doc = wc.read_json(ROOT / 'achromatic-imaging/project.json')
    changed = workflow.perturbed(doc, {'radius1_fraction': .001, 'crown_thickness': .02})
    assert changed['observations'] == doc['observations']
    assert changed['targets'] == doc['targets']
    before = doc['optical_models'][0]['model']['surfaces']
    after = changed['optical_models'][0]['model']['surfaces']
    assert float(after[0]['radius_mm']) == pytest.approx(float(before[0]['radius_mm']) * 1.001)
    assert after[1]['radius_mm'] == before[1]['radius_mm']
    assert float(after[2]['vertex_z_mm']) == pytest.approx(float(before[2]['vertex_z_mm']) + .02)
    assert wc.check(changed)['ok']
