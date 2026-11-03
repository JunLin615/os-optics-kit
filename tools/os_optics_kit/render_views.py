"""Render the Optical Simulation View from camera poses the CLI does not expose.

``os-optics view`` offers nine named presets (iso / top / front / side / beam axis / ...) and no
camera parameters. Reviewing a lens design -- and writing about it -- needs arbitrary angles: a
near side-on view to see the element thickness, a three-quarter view to read the stack, a
close-up on one element. Today that is done by clicking preset buttons and dragging the mouse in
a real browser, which is neither reproducible nor scriptable.

This command writes the engine's own self-contained viewer page and injects one small script that
places the camera before the first frame: the page already exposes ``window.__opensciViewer`` with
``frame(direction, up, box)``. Geometry, materials and shading stay the engine's; only the camera
is ours. A headless browser then captures the frame.

Usage:
  os-optics-kit render RUN_DIR --out DIR --camera 86,8 --camera 55,22 --size 1920x1080
  os-optics-kit render RUN_DIR --out DIR --shots shots.json --focus element:M2 --background transparent
  os-optics-kit render RUN_DIR --out DIR --camera 86,8 --html-only

Exit codes: 0 the frames were written (or --html-only wrote the pages), 1 a capture failed,
2 the camera or the framing could not be resolved, 4 usage error, missing engine or no browser.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROG = "os-optics-kit render"
ERROR_EXIT = 4

#: classes that are not optics: they would dominate a close-up, so the optics framing drops them
NOT_OPTICS = ("SOURCE", "DETECTOR", "OBSERVATION_PLANE", "TARGET_PLANE", "OTHER_OPTICAL")

DEFAULT_SIZE = "1920x1080"
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


class ArgumentParser(argparse.ArgumentParser):
    """Usage errors raise ValueError so they map to exit 4, not to argparse's exit 2."""

    def error(self, message):
        raise ValueError(message)


class RenderError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


# ----------------------------------------------------------------------------- camera and framing
def unit(vector):
    length = math.sqrt(sum(component * component for component in vector))
    if length < 1e-12:
        raise RenderError("CAMERA_UNDEFINED", "a zero-length direction cannot define a camera")
    return [component / length for component in vector]


def cross(left, right):
    return [left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0]]


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def optical_axis(document, override=None):
    """The design's optical axis as a unit vector: ``--axis``, else the view's ``main_axis``, else a marker."""
    if override:
        try:
            return unit([float(piece) for piece in override.replace(" ", "").split(",")])
        except Exception:
            raise RenderError("CAMERA_UNDEFINED", f"--axis expects X,Y,Z, not {override!r}") from None
    main = document.get("main_axis")
    if main and len(main) == 3 and any(abs(component) > 1e-12 for component in main):
        return unit([float(component) for component in main])
    for marker in document.get("axis_markers") or []:
        if marker.get("kind") == "OPTICAL_AXIS":
            start, end = marker.get("start_mm"), marker.get("end_mm")
            if start and end:
                axis = [e - s for s, e in zip(start, end)]
                if any(abs(component) > 1e-12 for component in axis):
                    return unit(axis)
    raise RenderError("CAMERA_UNDEFINED", "the view states no main axis; pass --axis X,Y,Z")


def camera_direction(axis, azimuth_deg, elevation_deg, up_axis=(0.0, 0.0, 1.0)):
    """Eye direction from the framing centre for an azimuth (angle to the axis) and an elevation.

    ``azimuth_deg`` 0 looks down the axis, 90 is exactly side-on. ``elevation_deg`` lifts the eye
    towards ``up_axis``. Returns ``(direction, up)``: the direction points from the target to the
    eye, and ``up`` is the screen-up vector, both unit length.
    """
    world_up = unit(list(up_axis))
    if abs(dot(world_up, axis)) > 0.999:
        world_up = [0.0, 1.0, 0.0]
    side = cross(world_up, axis)
    if abs(dot(side, side)) < 1e-12:
        side = [0.0, 1.0, 0.0]
    side = unit(side)
    azimuth, elevation = math.radians(azimuth_deg), math.radians(elevation_deg)
    direction = [math.cos(azimuth) * a + math.sin(azimuth) * s for a, s in zip(axis, side)]
    direction = unit([math.cos(elevation) * d + math.sin(elevation) * u for d, u in zip(direction, world_up)])
    right = cross(world_up, direction)
    if abs(dot(right, right)) < 1e-12:
        right = [1.0, 0.0, 0.0]
    up = unit(cross(right, direction))
    return direction, up


def part_points(scene):
    """``part_id -> (class, [(x, y, z), ...])`` for every drawn part, in world millimetres."""
    parts = {}
    for part in scene.parts:
        points = []
        for mesh in part.meshes:
            points.extend([tuple(float(v) for v in row) for row in mesh.positions])
        parts[part.part_id] = (part.part_class, points)
    return parts


def framing_box(parts, focus):
    """Axis-aligned ``(min, max)`` over the parts the focus selects; ``focus`` is optics / scene / element:ID."""
    if focus.startswith("element:"):
        wanted = focus.split(":", 1)[1]
        chosen = [points for part_id, (part_class, points) in parts.items() if part_id == wanted]
        if not chosen:
            raise RenderError("FOCUS_UNKNOWN", f"no part {wanted!r} in the view",
                              available=sorted(parts)[:40])
    elif focus == "optics":
        chosen = [points for _part_id, (part_class, points) in parts.items() if part_class not in NOT_OPTICS]
        if not chosen:
            chosen = [points for _part_id, (_part_class, points) in parts.items()]
    elif focus == "scene":
        chosen = [points for _part_id, (_part_class, points) in parts.items()]
    else:
        raise RenderError("FOCUS_INVALID", f"--focus expects optics, scene or element:<id>, not {focus!r}")
    points = [point for group in chosen for point in group]
    if not points:
        raise RenderError("FRAMING_EMPTY", "the selected framing has no geometry")
    low = [min(point[index] for point in points) for index in range(3)]
    high = [max(point[index] for point in points) for index in range(3)]
    return low, high


def parse_size(text):
    try:
        width_text, height_text = text.lower().split("x")
        width, height = int(width_text), int(height_text)
    except Exception:
        raise RenderError("SIZE_INVALID", f"--size expects WIDTHxHEIGHT, not {text!r}") from None
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        raise RenderError("SIZE_INVALID", f"--size out of range: {text!r}")
    return width, height


def parse_camera(text):
    """'86,8' or '86,8,-4' -> (azimuth, elevation, roll)."""
    try:
        values = [float(piece) for piece in text.replace(" ", "").split(",")]
    except ValueError:
        raise RenderError("CAMERA_INVALID", f"--camera expects AZ,EL[,ROLL] in degrees, not {text!r}") from None
    if len(values) not in (2, 3):
        raise RenderError("CAMERA_INVALID", f"--camera expects AZ,EL[,ROLL], not {text!r}")
    roll = values[2] if len(values) == 3 else 0.0
    return values[0], values[1], roll


def collect_shots(args, default_axis):
    shots = []
    for index, text in enumerate(args.camera or []):
        azimuth, elevation, roll = parse_camera(text)
        shots.append({"name": args.name if (args.name and len(args.camera) == 1) else f"shot{index + 1}",
                      "azimuth_deg": azimuth, "elevation_deg": elevation, "roll_deg": roll})
    if args.shots:
        with open(args.shots, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, list) or not loaded:
            raise RenderError("SHOTS_INVALID", "--shots must hold a non-empty JSON list")
        for index, shot in enumerate(loaded):
            if not isinstance(shot, dict) or "azimuth_deg" not in shot:
                raise RenderError("SHOTS_INVALID", f"--shots entry {index} needs at least azimuth_deg")
            shots.append({"name": str(shot.get("name") or f"shot{len(shots) + 1}"),
                          "azimuth_deg": float(shot["azimuth_deg"]),
                          "elevation_deg": float(shot.get("elevation_deg", 0.0)),
                          "roll_deg": float(shot.get("roll_deg", 0.0))})
    if not shots:
        raise RenderError("SHOTS_MISSING", "give at least one --camera AZ,EL or a --shots file")
    seen = {}
    for shot in shots:
        seen[shot["name"]] = seen.get(shot["name"], 0) + 1
        if seen[shot["name"]] > 1:
            raise RenderError("SHOTS_INVALID", f"duplicate shot name {shot['name']!r}")
    return shots


# ----------------------------------------------------------------------------------- the viewer page
STYLE = """
<style id="os-optics-kit-render">
  header, aside, #footer, #banner, #tooltip, #triad, #labels { display: none !important; }
  #main { display: block !important; }
  #stage { width: 100vw !important; height: 100vh !important; }
  html, body { background: __BACKGROUND__ !important; }
</style>
"""

SCRIPT = """
<script id="os-optics-kit-render-camera">
(function () {
  var SHOT = __CONFIG__;
  function apply(viewer) {
    var V = viewer.camera.position.constructor;
    var B = viewer.focusBox.constructor;
    (SHOT.hide_classes || []).forEach(function (name) { viewer.hiddenClasses[name] = true; });
    viewer.showEdges = !!SHOT.edges;
    viewer.labelMode = SHOT.labels ? "key" : "off";
    viewer.applyVisibility();
    if (!SHOT.beams) { viewer.beamObjects.forEach(function (group) { group.visible = false; }); }
    var box = new B(new V().fromArray(SHOT.box_min), new V().fromArray(SHOT.box_max));
    viewer.frame(new V().fromArray(SHOT.direction), new V().fromArray(SHOT.up), box);
    viewer.autoFrame = null;
    if (SHOT.roll_deg) {
      var angle = SHOT.roll_deg * Math.PI / 180.0;
      var target = viewer.controls.target.clone();
      var offset = viewer.camera.position.clone().sub(target);
      var up = viewer.camera.up.clone().applyAxisAngle(offset.clone().normalize(), angle);
      viewer.camera.up.copy(up);
    }
    viewer.requestRender();
    window.__osOpticsShotState = "applied";
  }
  var tries = 0;
  (function waitForViewer() {
    var viewer = window.__opensciViewer;
    if (!viewer) {
      if (tries++ < 600) { setTimeout(waitForViewer, 25); }
      return;
    }
    try { apply(viewer); } catch (error) { window.__osOpticsShotState = "error: " + error.message; }
  })();
})();
</script>
"""


def inject(page_html, config, background):
    """Put the camera script into the engine's self-contained page (before </body>)."""
    style = STYLE.replace("__BACKGROUND__", background)
    script = SCRIPT.replace("__CONFIG__", json.dumps(config, sort_keys=True))
    marker = page_html.lower().rfind("</body>")
    if marker < 0:
        raise RenderError("PAGE_UNEXPECTED", "the engine's page has no </body> to insert into")
    return page_html[:marker] + style + script + page_html[marker:]


def find_browser(explicit):
    for candidate in ([explicit] if explicit else []) + list(CHROME_CANDIDATES):
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def capture(browser, page_path, png_path, width, height, timeout_s, transparent):
    args = [browser, "--headless=new", "--no-sandbox", "--disable-gpu-sandbox",
            "--allow-file-access-from-files", "--hide-scrollbars", "--force-device-scale-factor=1",
            f"--window-size={width},{height}", f"--virtual-time-budget={max(1000, timeout_s * 1000)}",
            f"--screenshot={Path(png_path)}"]
    if transparent:
        args.append("--default-background-color=00000000")
    args.append(Path(page_path).resolve().as_uri())
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        completed = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=log, timeout=timeout_s + 60)
        if completed.returncode != 0:
            log.seek(0)
            tail = log.read()[-1500:]
            raise RenderError("CAPTURE_FAILED", f"the browser exited {completed.returncode}", detail=tail)
    if not Path(png_path).exists() or Path(png_path).stat().st_size < 2000:
        raise RenderError("CAPTURE_FAILED", "the browser wrote no image", file=str(png_path))


# ------------------------------------------------------------------------------------------- command
def build_parser():
    parser = ArgumentParser(prog=PROG, description=__doc__.split("\n")[0],
                            formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--camera", action="append", metavar="AZ,EL[,ROLL]",
                        help="one camera pose in degrees; repeat for several shots")
    parser.add_argument("--shots", help="JSON list of {name, azimuth_deg, elevation_deg, roll_deg}")
    parser.add_argument("--name", help="shot name when exactly one --camera is given")
    parser.add_argument("--focus", default="optics",
                        help="what the camera frames: optics (default), scene, or element:<id>")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--background", default="studio",
                        help="studio (the engine's backdrop), transparent, or #rrggbb")
    parser.add_argument("--up-axis", default="0,0,1", help="world up axis, default 0,0,1")
    parser.add_argument("--axis", help="the optical axis X,Y,Z; default: the view's main axis")
    parser.add_argument("--edges", action="store_true", help="draw part outlines")
    parser.add_argument("--labels", action="store_true", help="draw key labels")
    parser.add_argument("--beams", action="store_true", help="keep the beam paths")
    parser.add_argument("--browser", help="browser executable; default: a Chrome/Chromium on this machine")
    parser.add_argument("--html-only", action="store_true", help="write the camera-placed pages, capture nothing")
    parser.add_argument("--timeout-s", type=int, default=20)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv or sys.argv[1:])
        return run(args)
    except RenderError as error:
        print(json.dumps({"status": "ERROR", "error": {"code": error.code, "message": str(error), **error.details}},
                         indent=1), flush=True)
        return ERROR_EXIT
    except ValueError as error:
        print(json.dumps({"status": "ERROR", "error": {"code": "USAGE", "message": str(error)}}, indent=1), flush=True)
        return ERROR_EXIT


def run(args):
    try:
        from os_optics.viewer import load_view, view_document
        from os_optics.viewer.page import standalone_html
    except ImportError:
        raise RenderError("ENGINE_NOT_INSTALLED",
                          "this command needs the os-optics engine's viewer: python -m pip install \"os-optics>=0.12,<0.13\"") from None

    width, height = parse_size(args.size)
    up_axis = [float(piece) for piece in args.up_axis.split(",")]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        scene = load_view(args.run_dir)
    except Exception as error:  # noqa: BLE001 - the engine's refusal is the message
        raise RenderError("RUN_UNREADABLE", str(error)) from None
    document = view_document(scene)
    axis = optical_axis(document, args.axis)
    parts = part_points(scene)
    box_min, box_max = framing_box(parts, args.focus)
    shots = collect_shots(args, axis)

    browser = None if args.html_only else find_browser(args.browser)
    if not args.html_only and not browser:
        raise RenderError("BROWSER_NOT_FOUND",
                          "no Chrome/Chromium found; pass --browser PATH, or --html-only to write the pages")

    background = args.background
    transparent = background == "transparent"
    page_html = standalone_html(document).decode("utf-8")
    written = []
    failures = 0
    for shot in shots:
        direction, up = camera_direction(axis, shot["azimuth_deg"], shot["elevation_deg"], up_axis)
        config = {"direction": direction, "up": up, "box_min": box_min, "box_max": box_max,
                  "roll_deg": shot["roll_deg"], "hide_classes": list(NOT_OPTICS) if args.focus == "optics" else [],
                  "edges": bool(args.edges), "labels": bool(args.labels), "beams": bool(args.beams)}
        page = inject(page_html, config, "transparent" if transparent else (
            background if background.startswith("#") else "#f2f4f7"))
        page_path = out / f"{shot['name']}.html"
        page_path.write_text(page, encoding="utf-8")
        record = {"name": shot["name"], "html": page_path.name,
                  "azimuth_deg": shot["azimuth_deg"], "elevation_deg": shot["elevation_deg"],
                  "roll_deg": shot["roll_deg"], "direction": [round(v, 6) for v in direction],
                  "up": [round(v, 6) for v in up], "focus": args.focus,
                  "box_min": [round(v, 6) for v in box_min], "box_max": [round(v, 6) for v in box_max],
                  "image_px": [width, height], "png": None}
        if not args.html_only:
            try:
                capture(browser, page_path, out / f"{shot['name']}.png", width, height, args.timeout_s, transparent)
                record["png"] = f"{shot['name']}.png"
            except RenderError as error:
                failures += 1
                record["error"] = error.as_dict() if hasattr(error, "as_dict") else {"code": error.code,
                                                                                   "message": str(error)}
        written.append(record)

    manifest = {"status": "ERROR" if failures else "OK", "run_dir": str(Path(args.run_dir).resolve()),
                "out": str(out.resolve()), "focus": args.focus, "background": args.background,
                "shots": written, "failed": failures}
    (out / "shots.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=1), flush=True)
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
