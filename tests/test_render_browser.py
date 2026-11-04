"""Real viewer regressions, using the catalog-free ideal-lens-focus case.

Install ``os-optics==0.12.0``, ``os-optics-kit[render]`` and pytest, with Chrome/Chromium
installed, then run ``python -m pytest tests/test_render_browser.py``. Missing optional
dependencies skip these checks; when available, all output stays in pytest's temporary directory.
"""

import contextlib
import io
import json
import math
from pathlib import Path

import pytest

from os_optics_kit import render_views as render

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def viewer_run(tmp_path_factory):
    pytest.importorskip("os_optics")
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("PIL.Image")
    browser = render.find_browser(None)
    if not browser:
        pytest.skip("Chrome/Chromium is not installed")
    from os_optics.local import load_project, run_optical_project

    run = tmp_path_factory.mktemp("render-run") / "run"
    run_optical_project(load_project(ROOT / "cases" / "ideal-lens-focus" / "project.json"), output_dir=run)
    return run, browser


def invoke(viewer_run, out, *options):
    run, browser = viewer_run
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = render.main([str(run), "--out", str(out), "--browser", browser,
                            "--size", "640x480", *options])
    assert stderr.getvalue() == "", stderr.getvalue()
    manifest = json.loads(stdout.getvalue())
    assert manifest == json.loads((out / "shots.json").read_text(encoding="utf-8"))
    return code, manifest


def observe(browser_path, page_paths):
    """Read the actual camera matrix and visible DOM after the viewer has rendered."""
    from playwright.sync_api import sync_playwright

    observations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
        for path in page_paths:
            page = browser.new_page(viewport={"width": 640, "height": 480})
            page.goto(path.resolve().as_uri())
            page.wait_for_function("window.__osOpticsShotState?.status === 'ready'", timeout=20000)
            observations.append(page.evaluate("""() => {
                const v = window.__opensciViewer;
                const visibleLabels = [...document.querySelectorAll('#labels .label')].filter(el => {
                    const rect = el.getBoundingClientRect();
                    return getComputedStyle(el).display !== 'none' && rect.width > 0 && rect.height > 0
                        && rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
                }).map(el => el.textContent);
                return {
                    matrix: v.camera.matrixWorld.elements.slice(),
                    direction: v.camera.position.clone().sub(v.controls.target).normalize().toArray(),
                    position: v.camera.position.toArray(), target: v.controls.target.toArray(),
                    labels: visibleLabels, labelMode: v.labelMode,
                    backgroundTexture: !!v.scene.background?.isTexture,
                    state: window.__osOpticsShotState
                };
            }"""))
            page.close()
        browser.close()
    return observations


@pytest.mark.parametrize("background", ["transparent", "#ff00ff", "studio"])
def test_background_pixels_and_default_hidden_labels(viewer_run, tmp_path, background):
    from PIL import Image

    code, manifest = invoke(viewer_run, tmp_path, "--camera", "55,22", "--background", background)
    assert (code, manifest["status"], manifest["failed"]) == (0, "OK", 0), manifest
    shot = manifest["shots"][0]
    assert shot["status"] == "captured"
    with Image.open(tmp_path / shot["png"]) as image:
        assert image.size == (640, 480)
        rgba = image.convert("RGBA")
        corner = rgba.getpixel((0, 0))
        if background == "transparent":
            assert image.mode == "RGBA"
            assert corner[3] == 0
            low, high = rgba.getchannel("A").getextrema()
            assert low == 0 and high > 0  # the lens itself is translucent
        elif background == "#ff00ff":
            assert corner == (255, 0, 255, 255)
            assert rgba.getpixel((639, 479)) == corner
        else:
            assert corner[3] == 255
            assert corner[:3] != (255, 0, 255)
            assert rgba.getpixel((0, 479)) != corner  # the studio gradient survives
    actual = observe(viewer_run[1], [tmp_path / shot["html"]])[0]
    assert actual["backgroundTexture"] == (background == "studio")
    assert actual["labelMode"] == "off"
    assert actual["labels"] == []


def test_applied_camera_angles_roll_and_visible_labels(viewer_run, tmp_path):
    from PIL import Image, ImageChops

    code, manifest = invoke(viewer_run, tmp_path, "--camera", "0,0", "--camera", "55,22",
                            "--camera", "55,22,45", "--labels")
    assert (code, manifest["status"]) == (0, "OK"), manifest
    shots = manifest["shots"]
    actual = observe(viewer_run[1], [tmp_path / shot["html"] for shot in shots])
    for shot, state in zip(shots, actual):
        camera = shot["applied_camera"]
        assert camera is not None
        assert camera["matrix_world"] == pytest.approx(state["matrix"], abs=1e-8)
        assert camera["direction"] == pytest.approx(state["direction"], abs=1e-8)
        assert camera["up"] == pytest.approx(state["matrix"][4:7], abs=1e-8)
        assert camera["position"] == pytest.approx(state["position"], abs=1e-8)
        assert camera["target"] == pytest.approx(state["target"], abs=1e-8)
        measured_angle = math.degrees(math.acos(max(-1, min(1, state["direction"][0]))))
        assert measured_angle == pytest.approx(shot["azimuth_deg"], abs=1e-6)
        assert state["labelMode"] == "key"
        assert "LENS" in state["labels"]
    assert actual[0]["matrix"][4:7] == pytest.approx([0, 0, 1], abs=1e-8)
    before, after = actual[1]["matrix"][4:7], actual[2]["matrix"][4:7]
    direction = actual[1]["direction"]
    roll = math.degrees(math.atan2(render.dot(direction, render.cross(before, after)), render.dot(before, after)))
    assert roll == pytest.approx(45, abs=1e-6)
    assert actual[2]["direction"] == pytest.approx(direction, abs=1e-8)
    with Image.open(tmp_path / shots[1]["png"]) as first, Image.open(tmp_path / shots[2]["png"]) as second:
        assert ImageChops.difference(first.convert("RGB"), second.convert("RGB")).getbbox() is not None


@pytest.mark.parametrize("broken", ["missing", "throws", "timeout"])
def test_failed_camera_never_reports_a_captured_pose(viewer_run, tmp_path, monkeypatch, broken):
    from os_optics.viewer import page as viewer_page

    original = viewer_page.standalone_html

    def broken_page(document):
        html = original(document)
        if broken == "missing":
            return html.replace(b"__opensciViewer", b"__hiddenViewer")
        return html.replace(b"window.__opensciViewer = viewer;",
                            b"viewer.frame = function () { throw new Error('camera application failed'); };"
                            b"window.__opensciViewer = viewer;")

    if broken == "timeout":
        # The engine page opens, but no camera application result arrives.
        monkeypatch.setattr(render, "inject", lambda html, config, background: html)
    else:
        monkeypatch.setattr(viewer_page, "standalone_html", broken_page)
    code, manifest = invoke(viewer_run, tmp_path, "--camera", "55,22", "--timeout-s", "2")
    assert (code, manifest["status"], manifest["failed"]) == (1, "FAIL", 1), manifest
    shot = manifest["shots"][0]
    assert shot["status"] == "failed"
    assert shot["png"] is None
    assert shot["applied_camera"] is None
    assert shot["error"]["code"] == {"missing": "PAGE_UNEXPECTED", "throws": "CAMERA_APPLY_FAILED",
                                      "timeout": "CAPTURE_TIMEOUT"}[broken]
    if broken == "timeout":
        assert "camera readiness" in shot["error"]["message"]
    assert not list(tmp_path.glob("*.png"))
