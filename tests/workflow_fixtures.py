"""Catalog-free Projects and helpers shared by the tool tests (native ideal thin lenses, no network).

Tests that run the engine are marked ``@requires_engine``: they are skipped, with that reason, only when
``os_optics`` cannot be imported. Plot tests are marked ``@requires_matplotlib`` (the ``os-optics-kit[plot]``
extra) and the tolerance sampling tests ``@requires_numpy`` (numpy is installed with the engine).
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib.util import find_spec
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _importable(name):
    try:
        __import__(name)
    except ImportError:
        return False
    return True


HAS_ENGINE = _importable("os_optics")
HAS_MATPLOTLIB = find_spec("matplotlib") is not None
HAS_NUMPY = find_spec("numpy") is not None
ENGINE_MISSING = "os-optics (the engine) is not installed: python -m pip install \"os-optics>=0.12,<0.13\""
PLOT_EXTRA_MISSING = "matplotlib is not installed: python -m pip install \"os-optics-kit[plot]\""
NUMPY_MISSING = "numpy is not installed (it is installed with os-optics)"
requires_engine = unittest.skipUnless(HAS_ENGINE, ENGINE_MISSING)
requires_matplotlib = unittest.skipUnless(HAS_MATPLOTLIB, PLOT_EXTRA_MISSING)
requires_numpy = unittest.skipUnless(HAS_NUMPY, NUMPY_MISSING)
#: run a tool as ``python -m os_optics_kit <command>`` in a subprocess
KIT = ("-m", "os_optics_kit")
BEAM_ALONG_X = (0.5, 0.5, 0.5, 0.5)  # local +Z -> design +X, local +Y -> design +Z


def source(wavelength_nm=780.0, waist_radius_mm=0.5, power_w=0.001):
    return {"instance_id": "SRC", "optical_role": "source",
            "custom_part": {"custom_part_key": "user-laser", "description": "User laser"},
            "nominal_pose": {"position_mm": [-100.0, 0.0, 0.0], "quaternion_wxyz": list(BEAM_ALONG_X)},
            "selected_parameters": [], "retained_dofs": [],
            "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": wavelength_nm, "power_w": power_w,
                             "waist_radius_mm": waist_radius_mm, "waist_position_mm": 0.0, "m2": 1.0,
                             "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}}


def plane(observation_id, x_mm, radius_mm=12.5):
    return {"observation_id": observation_id,
            "pose": {"position_mm": [x_mm, 0.0, 0.0], "quaternion_wxyz": list(BEAM_ALONG_X)},
            "aperture": {"shape": "circle", "radius_mm": radius_mm, "center_offset_mm": [0.0, 0.0],
                         "obstruction_radius_mm": 0.0}, "absorbing": False, "medium": "air"}


def lens(document, instance_id, focal_length_mm, x_mm, valid_band_nm=None):
    from os_optics.native_library import add_to_project, build_native_model

    parameters = {"focal_length_mm": str(focal_length_mm), "clear_aperture_diameter_mm": "25",
                  "model_id": f"thin-{instance_id}"}
    if valid_band_nm is not None:
        parameters["valid_band_nm"] = [str(v) for v in valid_band_nm]
    build = build_native_model("lens.ideal_thin", parameters)
    return add_to_project(document, build, instance_id=instance_id, position_mm=(x_mm, 0.0, 0.0),
                          quaternion_wxyz=BEAM_ALONG_X)


def expander(f1=50.0, f2=150.0, f3=100.0, wavelength_nm=780.0, l3_band_nm=None, max_spot_mm=0.02):
    """SRC -> L1 (x=0) -> L2 (x=f1+f2) -> L3 (100 mm after L2) -> plane 'focus' at L3 + f3; 'collimated' plane
    50 mm after L2. Targets: expanded_radius (1.5 mm +- 2 %), spot_radius (<= max_spot_mm), waist_on_plane."""
    document = {"schema": "opensci.project", "project_schema_version": "2.0", "components": [source(wavelength_nm)],
                "observations": [], "targets": [], "materials": []}
    x2 = f1 + f2
    x3 = x2 + 100.0
    document = lens(document, "L1", f1, 0.0)
    document = lens(document, "L2", f2, x2)
    document = lens(document, "L3", f3, x3, valid_band_nm=l3_band_nm)
    document["observations"] = [plane("collimated", x2 + 50.0), plane("focus", x3 + f3)]
    document["targets"] = [
        {"target_id": "expanded_radius", "metric": "beam_radius_mm", "observation_id": "collimated",
         "target_value": 0.5 * f2 / f1, "tolerance_rel": 0.02, "unit": "mm", "parameters": []},
        {"target_id": "spot_radius", "metric": "beam_radius_mm", "observation_id": "focus",
         "maximum": max_spot_mm, "unit": "mm", "parameters": []},
        {"target_id": "waist_on_plane", "metric": "waist_distance_x_mm", "observation_id": "focus",
         "minimum": -0.5, "maximum": 0.5, "unit": "mm", "parameters": []},
    ]
    return document


def by_id(items, key, ident):
    return next(item for item in items if item.get(key) == ident)


class TempDir:
    """A temporary directory (removed afterwards) that is not inside the repository."""

    def __enter__(self):
        self.path = Path(tempfile.mkdtemp(prefix="opensci-wf-"))
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


def invoke(main, argv):
    """Run a tool's ``main(argv)``; return (exit code, parsed stdout or None, stderr text)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue()


def catalog_available():
    """True when a public catalog release is installed for this environment (catalog tests skip otherwise).

    With OS_OPTICS_KIT_REQUIRE_CATALOG=1 (set by the alignment runner's catalog-tests check) a missing catalog
    raises instead, so the catalog tests fail rather than skip."""
    try:
        from os_optics.local import json_api

        available = bool(json_api.catalog_status().get("available"))
    except Exception:  # noqa: BLE001
        available = False
    if not available and os.environ.get("OS_OPTICS_KIT_REQUIRE_CATALOG") == "1":
        raise RuntimeError("OS_OPTICS_KIT_REQUIRE_CATALOG=1 but no catalog release is installed "
                           f"(OPENSCI_CATALOG_DIR={os.environ.get('OPENSCI_CATALOG_DIR')!r})")
    return available


def quiet_environment():
    os.environ.setdefault("OPENSCI_CATALOG_CHECK_INTERVAL", "0")
