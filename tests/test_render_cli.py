"""Render command file handling and JSON protocol, without an engine or browser."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from os_optics_kit import render_views as render


@pytest.fixture
def viewer(monkeypatch):
    """A small scene at the engine boundary; capture behaviour has separate browser tests."""
    engine = ModuleType("os_optics")
    viewer = ModuleType("os_optics.viewer")
    page = ModuleType("os_optics.viewer.page")
    mesh = SimpleNamespace(positions=[(0, -2, -2), (1, 2, 2)])
    scene = SimpleNamespace(parts=[SimpleNamespace(part_id="LENS", part_class="LENS", meshes=[mesh])])
    viewer.load_view = lambda _path: scene
    viewer.view_document = lambda _scene: {"main_axis": [1, 0, 0]}
    page.standalone_html = lambda _document: b"<html><body><div id='stage'></div></body></html>"
    engine.viewer = viewer
    viewer.page = page
    for module in (engine, viewer, page):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(render, "playwright_api", lambda: None, raising=False)
    return scene


def invoke(out, *args, html_only=True):
    argv = ["RUN", "--out", str(out), "--camera", "55,22", *args]
    if html_only:
        argv.append("--html-only")
    return render.main(argv)


def refusal(capsys, code):
    streams = capsys.readouterr()
    assert streams.out == ""
    assert len(streams.err.splitlines()) == 1
    result = json.loads(streams.err)
    assert result["error"]["code"] == code
    return result


@pytest.mark.parametrize("name", ["../outside", r"..\outside", "bad/name", "CON"])
def test_invalid_name_cannot_escape_output(viewer, tmp_path, capsys, name):
    out = tmp_path / "shots"
    assert invoke(out, "--name", name) == 4
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] in {"NAME_INVALID", "SHOTS_INVALID"}
    assert not list(tmp_path.rglob("*.html"))
    assert not (out / "shots.json").exists()


@pytest.mark.parametrize("filename", ["shot1.html", "shot1.png", "shots.json"])
def test_any_collision_refuses_entire_request(viewer, monkeypatch, tmp_path, capsys, filename):
    out = tmp_path / "shots"
    out.mkdir()
    existing = out / filename
    existing.write_text("USER CONTENT", encoding="utf-8")
    monkeypatch.setattr(render, "find_browser", lambda _explicit: sys.executable)
    assert invoke(out, html_only=filename != "shot1.png") == 4
    refusal(capsys, "OUTPUT_EXISTS")
    assert existing.read_text(encoding="utf-8") == "USER CONTENT"
    assert list(out.iterdir()) == [existing]


def test_repeat_requires_overwrite_and_keeps_unrelated_files(viewer, tmp_path, capsys):
    out = tmp_path / "shots"
    assert invoke(out, "--name", "Lens-1_close") == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert len(streams.out.splitlines()) == 1
    manifest = json.loads(streams.out)
    assert manifest["status"] == "OK"
    shot = manifest["shots"][0]
    assert shot["status"] == "html_only"
    assert shot["png"] is None and shot["applied_camera"] is None
    assert sorted(path.name for path in out.iterdir()) == ["Lens-1_close.html", "shots.json"]
    assert json.loads((out / "shots.json").read_text(encoding="utf-8")) == manifest

    html = out / "Lens-1_close.html"
    html.write_text("USER CONTENT", encoding="utf-8")
    unrelated = out / "notes.txt"
    unrelated.write_text("KEEP", encoding="utf-8")
    assert invoke(out, "--name", "Lens-1_close") == 4
    refusal(capsys, "OUTPUT_EXISTS")
    assert html.read_text(encoding="utf-8") == "USER CONTENT"
    assert invoke(out, "--name", "Lens-1_close", "--overwrite") == 0
    assert "os-optics-kit-render-camera" in html.read_text(encoding="utf-8")
    assert unrelated.read_text(encoding="utf-8") == "KEEP"


def test_invalid_later_pose_does_not_overwrite_earlier_shot(viewer, tmp_path, capsys):
    out = tmp_path / "shots"
    out.mkdir()
    html = out / "shot1.html"
    html.write_text("USER CONTENT", encoding="utf-8")
    assert invoke(out, "--camera", "0,22", "--overwrite") == 4
    refusal(capsys, "CAMERA_INVALID")
    assert html.read_text(encoding="utf-8") == "USER CONTENT"
    assert list(out.iterdir()) == [html]


def test_overwrite_replaces_all_intended_outputs(viewer, monkeypatch, tmp_path, capsys):
    def capture(_browser, _html, png, *_args):
        png.write_bytes(b"new capture")
        return {"direction": [1, 0, 0], "up": [0, 0, 1]}

    monkeypatch.setattr(render, "find_browser", lambda _explicit: sys.executable)
    monkeypatch.setattr(render, "capture", capture)
    out = tmp_path / "shots"
    out.mkdir()
    for name in ("shot1.html", "shot1.png", "shots.json"):
        (out / name).write_text("USER CONTENT", encoding="utf-8")
    assert invoke(out, "--overwrite", html_only=False) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    manifest = json.loads(streams.out)
    assert manifest["shots"][0]["status"] == "captured"
    assert (out / "shot1.png").read_bytes() == b"new capture"
    assert "os-optics-kit-render-camera" in (out / "shot1.html").read_text(encoding="utf-8")
    assert json.loads((out / "shots.json").read_text(encoding="utf-8")) == manifest


def test_batch_names_are_validated_before_writing(viewer, tmp_path, capsys):
    specs = tmp_path / "poses.json"
    specs.write_text(json.dumps([
        {"name": "closeup", "azimuth_deg": 55},
        {"name": "CLOSEUP", "azimuth_deg": 86},
    ]), encoding="utf-8")
    out = tmp_path / "shots"
    assert render.main(["RUN", "--out", str(out), "--shots", str(specs), "--html-only"]) == 4
    refusal(capsys, "SHOTS_INVALID")
    assert not list(out.glob("*"))


def test_overwrite_cannot_replace_input_shot_spec(viewer, tmp_path, capsys):
    out = tmp_path / "shots"
    out.mkdir()
    specs = out / "shots.json"
    original = json.dumps([{"name": "closeup", "azimuth_deg": 55, "elevation_deg": 22}])
    specs.write_text(original, encoding="utf-8")
    assert render.main(["RUN", "--out", str(out), "--shots", str(specs),
                        "--html-only", "--overwrite"]) == 4
    refusal(capsys, "OUTPUT_INVALID")
    assert specs.read_text(encoding="utf-8") == original
    assert list(out.iterdir()) == [specs]


def test_output_symlink_cannot_write_outside_even_with_overwrite(viewer, tmp_path, capsys):
    outside = tmp_path / "outside.html"
    outside.write_text("USER CONTENT", encoding="utf-8")
    out = tmp_path / "shots"
    out.mkdir()
    try:
        (out / "shot1.html").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks requires permission on this machine")
    assert invoke(out, "--overwrite") == 4
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["error"]["code"] in {"OUTPUT_INVALID", "OUTPUT_OUTSIDE"}
    assert outside.read_text(encoding="utf-8") == "USER CONTENT"
    assert not (out / "shots.json").exists()


@pytest.mark.parametrize("args, exit_code, error_code", [
    (["--focus", "element:MISSING"], 2, "FOCUS_UNKNOWN"),
    (["--axis", "0,0,0"], 2, "CAMERA_UNDEFINED"),
    (["--size", "1x1"], 4, "SIZE_INVALID"),
    (["--background", "magenta"], 4, "BACKGROUND_INVALID"),
    (["--camera", "NaN,0"], 4, "CAMERA_INVALID"),
])
def test_error_codes_are_compact_json_on_stderr(viewer, tmp_path, capsys, args, exit_code, error_code):
    out = tmp_path / "shots"
    assert invoke(out, *args) == exit_code
    refusal(capsys, error_code)
    assert not list(out.glob("*"))


def test_empty_geometry_is_unresolved_framing(viewer, tmp_path, capsys):
    viewer.parts.clear()
    assert invoke(tmp_path / "shots") == 2
    refusal(capsys, "FRAMING_EMPTY")


def test_missing_shot_file_is_json_not_traceback(viewer, tmp_path, capsys):
    assert invoke(tmp_path / "shots", "--shots", str(tmp_path / "missing.json")) == 4
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["error"]["message"]


def test_explicit_missing_browser_does_not_fall_back(viewer, tmp_path, capsys):
    assert invoke(tmp_path / "shots", "--browser", str(tmp_path / "missing-chrome"), html_only=False) == 4
    refusal(capsys, "BROWSER_NOT_FOUND")
    assert not list((tmp_path / "shots").glob("*"))


def test_capture_failure_cannot_claim_applied_camera(viewer, monkeypatch, tmp_path, capsys):
    def failed_capture(*_args, **_kwargs):
        raise render.RenderError("PAGE_UNEXPECTED", "the viewer camera hook is unavailable")

    monkeypatch.setattr(render, "find_browser", lambda _explicit: sys.executable)
    monkeypatch.setattr(render, "capture", failed_capture)
    out = tmp_path / "shots"
    assert invoke(out, html_only=False) == 1
    streams = capsys.readouterr()
    assert streams.err == ""
    assert len(streams.out.splitlines()) == 1
    manifest = json.loads(streams.out)
    assert manifest["status"] == "FAIL" and manifest["failed"] == 1
    shot = manifest["shots"][0]
    assert shot["status"] == "failed"
    assert shot["png"] is None and shot["applied_camera"] is None
    assert shot["error"]["code"] == "PAGE_UNEXPECTED"
    assert not (out / "shot1.png").exists()
