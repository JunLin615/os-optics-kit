"""Render the engine's Optical Simulation View at a reproducible camera pose.

AZ is the angle from the optical axis to the target-to-eye direction (0 axial, 90 side-on).
EL is elevation towards world up, projected perpendicular to the optical axis. The positive
side is up cross axis. Use 0 <= AZ <= 180, |EL| <= min(AZ, 180-AZ), and |EL| < 90.
ROLL rotates screen up about the target-to-eye direction by the right-hand rule.
World up defaults to +Z; use --up-axis X,Y,Z if the optical axis is parallel to +Z.

PNG capture needs Chrome/Chromium and: python -m pip install "os-optics-kit[render]"
--html-only needs neither. Existing outputs require --overwrite.

Usage:
  os-optics-kit render RUN_DIR --out DIR --camera 86,8 --camera 55,22 --size 1920x1080
  os-optics-kit render RUN_DIR --out DIR --shots shots.json --focus element:M2 --background transparent
  os-optics-kit render RUN_DIR --out DIR --camera 86,8 --html-only

Exit codes: 0 written, 1 capture failed, 2 unresolved camera axis or focus, 4 refused input
or missing dependency. Results are compact JSON on stdout; refusals are JSON on stderr.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import sys

from .result_summary import json_cli_stdout

PROG = "os-optics-kit render"
ERROR_EXIT = 4
UNRESOLVED_ERRORS = {"CAMERA_UNDEFINED", "FOCUS_UNKNOWN", "FRAMING_EMPTY"}

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
    if len(vector) != 3 or not all(math.isfinite(component) for component in vector):
        raise RenderError("CAMERA_UNDEFINED", "camera axes must contain three finite numbers (X,Y,Z)")
    length = math.hypot(*vector)
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
        except (ValueError, RenderError):
            raise RenderError("CAMERA_UNDEFINED", f"--axis expects three finite numbers and a nonzero vector, not {override!r}") from None
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

    ``azimuth_deg`` is the actual angle to the optical axis, from 0 to 180 degrees. Projecting
    ``up_axis`` perpendicular to that axis defines the elevation direction. ``elevation_deg``
    is the angle above the plane perpendicular to that effective up: its magnitude cannot exceed
    min(azimuth, 180 - azimuth). At +/-90 elevation screen up is undefined, so those poles are
    excluded. Returns target-to-eye direction and screen up, both unit length.
    """
    if not all(math.isfinite(value) for value in (azimuth_deg, elevation_deg)):
        raise RenderError("CAMERA_INVALID", "camera angles must be finite numbers in degrees")
    if not 0 <= azimuth_deg <= 180 or not -90 < elevation_deg < 90:
        raise RenderError("CAMERA_INVALID", "use AZ from 0 to 180 degrees and EL strictly between -90 and 90")
    if abs(elevation_deg) > min(azimuth_deg, 180 - azimuth_deg):
        raise RenderError("CAMERA_INVALID", "this angle to the axis cannot reach that elevation; use |EL| <= min(AZ, 180-AZ)")
    axis = unit(axis)
    world_up = unit(list(up_axis))
    projected_up = [u - dot(world_up, axis) * a for u, a in zip(world_up, axis)]
    if math.hypot(*projected_up) < 1e-12:
        raise RenderError("CAMERA_UNDEFINED", "the optical axis is parallel to world up; choose a nonparallel --up-axis X,Y,Z")
    world_up = unit(projected_up)
    side = cross(world_up, axis)
    azimuth, elevation = math.radians(azimuth_deg), math.radians(elevation_deg)
    side_component = math.sqrt(max(0.0, math.sin(azimuth) ** 2 - math.sin(elevation) ** 2))
    direction = unit([math.cos(azimuth) * a + side_component * s + math.sin(elevation) * u
                      for a, s, u in zip(axis, side, world_up)])
    up = unit([u - dot(world_up, direction) * d for u, d in zip(world_up, direction)])
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
    if not all(math.isfinite(value) for value in values):
        raise RenderError("CAMERA_INVALID", "camera angles must be finite numbers in degrees")
    roll = values[2] if len(values) == 3 else 0.0
    return values[0], values[1], roll


def collect_shots(args, default_axis=None):
    if args.name and (len(args.camera or []) != 1 or args.shots):
        raise RenderError("SHOTS_INVALID", "--name needs exactly one --camera and no --shots file")
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
            try:
                angles = [float(shot.get(key, 0.0)) for key in ("azimuth_deg", "elevation_deg", "roll_deg")]
            except (TypeError, ValueError):
                raise RenderError("SHOTS_INVALID", f"--shots entry {index} needs numeric angles") from None
            shots.append({"name": shot.get("name", f"shot{len(shots) + 1}"),
                          "azimuth_deg": angles[0], "elevation_deg": angles[1], "roll_deg": angles[2]})
    if not shots:
        raise RenderError("SHOTS_MISSING", "give at least one --camera AZ,EL or a --shots file")
    seen = set()
    for shot in shots:
        name = shot["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise RenderError("SHOTS_INVALID", "shot names must start with a letter or digit and contain only "
                              "letters, digits, underscores or hyphens (1-64 characters)")
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name, re.IGNORECASE):
            raise RenderError("SHOTS_INVALID", f"{name!r} is a reserved filename; choose another shot name")
        if name.lower() in seen:
            raise RenderError("SHOTS_INVALID", f"duplicate shot name {shot['name']!r}")
        seen.add(name.lower())
        if not all(math.isfinite(shot[key]) for key in ("azimuth_deg", "elevation_deg", "roll_deg")):
            raise RenderError("CAMERA_INVALID", "camera angles must be finite numbers")
    return shots


def check_outputs(out, shots, html_only, overwrite, input_path=None):
    """Check the complete batch before writing any output."""
    paths = [out / "shots.json"]
    for shot in shots:
        paths.append(out / f"{shot['name']}.html")
        if not html_only:
            paths.append(out / f"{shot['name']}.png")
    for path in paths:
        if input_path and path.resolve() == input_path.resolve():
            raise RenderError("OUTPUT_INVALID", "an output would replace --shots; choose a different --out directory")
        if path.resolve().parent != out:
            raise RenderError("OUTPUT_INVALID", f"{path.name} points outside --out; choose another output directory")
        if path.exists() and not path.is_file():
            raise RenderError("OUTPUT_INVALID", f"{path} is not a file; choose another output directory")
        if (path.exists() or path.is_symlink()) and not overwrite:
            raise RenderError("OUTPUT_EXISTS", f"{path} already exists; choose a new --out or use --overwrite")


# ----------------------------------------------------------------------------------- the viewer page
STYLE = """
<style id="os-optics-kit-render">
  header, aside, #footer, #banner, #tooltip, #triad { display: none !important; }
  __LABEL_STYLE__
  #main { display: block !important; }
  #stage { width: 100vw !important; height: 100vh !important; }
  html, body { background: __BACKGROUND__ !important; }
</style>
"""

SCRIPT = """
<script id="os-optics-kit-render-camera">
(function () {
  var SHOT = __CONFIG__;
  var deadline = Date.now() + (SHOT.timeout_ms || 20000);
  window.__osOpticsShotState = {status: "waiting"};
  function fail(code, message) {
    window.__osOpticsShotState = {status: "error", error: {code: code, message: message}};
  }
  function apply(viewer) {
    var V = viewer.camera.position.constructor;
    var B = viewer.focusBox.constructor;
    (SHOT.hide_classes || []).forEach(function (name) { viewer.hiddenClasses[name] = true; });
    viewer.showEdges = !!SHOT.edges;
    viewer.labelMode = SHOT.labels ? "key" : "off";
    viewer.applyVisibility();
    if (!SHOT.beams) { viewer.beamObjects.forEach(function (group) { group.visible = false; }); }
    if (SHOT.background !== "studio") {
      viewer.scene.background = null;
      viewer.renderer.setClearColor(SHOT.background === "transparent" ? "#000000" : SHOT.background,
                                    SHOT.background === "transparent" ? 0 : 1);
    }
    var box = new B(new V().fromArray(SHOT.box_min), new V().fromArray(SHOT.box_max));
    var direction = new V().fromArray(SHOT.direction);
    var up = new V().fromArray(SHOT.up).applyAxisAngle(direction, SHOT.roll_deg * Math.PI / 180);
    // frame rebuilds the viewer's controls for this up vector, including roll.
    viewer.resize();
    viewer.frame(direction, up, box);
    viewer.renderNow();
    var matrix = viewer.camera.matrixWorld.elements.slice();
    var actualDirection = viewer.camera.position.clone().sub(viewer.controls.target).normalize();
    var actualUp = new V(matrix[4], matrix[5], matrix[6]);
    var actualBack = new V(matrix[8], matrix[9], matrix[10]);
    if (actualDirection.distanceTo(direction) > 1e-6 || actualUp.distanceTo(up) > 1e-6 ||
        actualBack.distanceTo(direction) > 1e-6 || viewer.controls.target.distanceTo(box.getCenter(new V())) > 1e-6) {
      throw new Error("the viewer did not apply the requested camera");
    }
    if (viewer.renderer.getContext().isContextLost() || viewer.renderer.info.render.calls === 0) {
      throw new Error("the viewer did not render the scene");
    }
    window.__osOpticsShotState = {status: "ready", camera: {
      direction: actualDirection.toArray(), up: actualUp.toArray(), matrix_world: matrix,
      position: viewer.camera.position.toArray(), target: viewer.controls.target.toArray()
    }};
  }
  (function waitForViewer() {
    var viewer = window.__opensciViewer;
    if (!viewer) {
      if (Date.now() < deadline) { setTimeout(waitForViewer, 25); }
      else { fail("PAGE_UNEXPECTED", "the engine viewer hook is unavailable; use os-optics 0.12.x"); }
      return;
    }
    if (!["frame", "resize", "renderNow", "applyVisibility"].every(function (key) {
      return typeof viewer[key] === "function";
    })) {
      fail("PAGE_UNEXPECTED", "the engine viewer hook is incomplete; use os-optics 0.12.x");
      return;
    }
    try { apply(viewer); } catch (error) { fail("CAMERA_APPLY_FAILED", error.message); }
  })();
})();
</script>
"""


def inject(page_html, config, background):
    """Put the camera script into the engine's self-contained page (before </body>)."""
    style = STYLE.replace("__BACKGROUND__", "#f2f4f7" if background == "studio" else background)
    style = style.replace("__LABEL_STYLE__", "" if config.get("labels") else "#labels { display: none !important; }")
    script = SCRIPT.replace("__CONFIG__", json.dumps({**config, "background": background}, sort_keys=True))
    marker = page_html.lower().rfind("</body>")
    if marker < 0:
        raise RenderError("PAGE_UNEXPECTED", "the engine's page has no </body> to insert into")
    return page_html[:marker] + style + script + page_html[marker:]


def find_browser(explicit):
    if explicit:
        return explicit if Path(explicit).is_file() else shutil.which(explicit)
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def playwright_api():
    try:
        from playwright import sync_api
    except ImportError:
        raise RenderError("CAPTURE_NOT_INSTALLED", 'install PNG capture with: python -m pip install "os-optics-kit[render]"; '
                          "or use --html-only") from None
    return sync_api


def capture(browser, page_path, png_path, width, height, timeout_s, transparent):
    api = playwright_api()
    try:
        with api.sync_playwright() as playwright:
            with playwright.chromium.launch(executable_path=browser, headless=True, timeout=timeout_s * 1000) as chrome:
                page = chrome.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                page.set_default_timeout(timeout_s * 1000)
                page.goto(Path(page_path).resolve().as_uri())
                page.wait_for_function("window.__osOpticsShotState && ['ready', 'error'].includes("
                                       "window.__osOpticsShotState.status)", timeout=timeout_s * 1000 + 500)
                state = page.evaluate("window.__osOpticsShotState")
                if state["status"] != "ready":
                    raise RenderError(state["error"]["code"], state["error"]["message"])
                png = page.screenshot(omit_background=transparent, animations="disabled")
                Path(png_path).write_bytes(png)
                return state["camera"]
    except api.TimeoutError:
        raise RenderError("CAPTURE_TIMEOUT", f"the viewer was not ready within {timeout_s}s; inspect the HTML page "
                          "or increase --timeout-s") from None
    except api.Error as error:
        raise RenderError("CAPTURE_FAILED", f"Chrome/Chromium could not capture the page: {error}") from None


# ------------------------------------------------------------------------------------------- command
def build_parser():
    parser = ArgumentParser(prog=PROG, description=__doc__.split("\n")[0],
                            formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files after checking the whole batch")
    parser.add_argument("--camera", action="append", metavar="AZ,EL[,ROLL]",
                        help="angle to optical axis, elevation, optional roll in degrees; repeat for several shots")
    parser.add_argument("--shots", help="JSON list of {name, azimuth_deg, elevation_deg, roll_deg}")
    parser.add_argument("--name", help="one-camera filename: 1-64 letters/digits/_/-, starting with a letter or digit")
    parser.add_argument("--focus", default="optics",
                        help="what the camera frames: optics (default), scene, or element:<id>")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--background", default="studio",
                        help="studio (the engine's backdrop), transparent, or #rrggbb")
    parser.add_argument("--up-axis", default="0,0,1", help="world up X,Y,Z, nonparallel to optical axis; default 0,0,1")
    parser.add_argument("--axis", help="the optical axis X,Y,Z; default: the view's main axis")
    parser.add_argument("--edges", action="store_true", help="draw part outlines")
    parser.add_argument("--labels", action="store_true", help="draw key labels")
    parser.add_argument("--beams", action="store_true", help="keep the beam paths")
    parser.add_argument("--browser", help="browser executable; default: a Chrome/Chromium on this machine")
    parser.add_argument("--html-only", action="store_true", help="write pages with a camera request; no browser verification or PNG")
    parser.add_argument("--timeout-s", type=int, default=20, help="seconds to wait for page readiness/capture (default: 20)")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        with json_cli_stdout():
            manifest = run(args)
    except RenderError as error:
        print(json.dumps({"status": "ERROR", "error": {"code": error.code, "message": str(error), **error.details}},
                         separators=(",", ":")), file=sys.stderr)
        return 2 if error.code in UNRESOLVED_ERRORS else ERROR_EXIT
    except ValueError as error:
        print(json.dumps({"status": "ERROR", "error": {"code": "USAGE", "message": str(error)}},
                         separators=(",", ":")), file=sys.stderr)
        return ERROR_EXIT
    except OSError as error:
        print(json.dumps({"status": "ERROR", "error": {"code": "FILE_ERROR", "message": str(error)}},
                         separators=(",", ":")), file=sys.stderr)
        return ERROR_EXIT
    print(json.dumps(manifest, separators=(",", ":")), flush=True)
    return 1 if manifest["failed"] else 0


def run(args):
    width, height = parse_size(args.size)
    if args.timeout_s <= 0:
        raise RenderError("USAGE", "--timeout-s must be a positive number of seconds")
    if args.background not in ("studio", "transparent") and not re.fullmatch(r"#[0-9a-fA-F]{6}", args.background):
        raise RenderError("BACKGROUND_INVALID", "--background expects studio, transparent or a colour such as '#ff00ff'")
    up_axis = unit([float(piece) for piece in args.up_axis.split(",")])
    shots = collect_shots(args)
    out = Path(args.out).resolve()
    check_outputs(out, shots, args.html_only, args.overwrite, Path(args.shots) if args.shots else None)

    try:
        from os_optics.viewer import load_view, view_document
        from os_optics.viewer.page import standalone_html
    except ImportError:
        raise RenderError("ENGINE_NOT_INSTALLED",
                          "this command needs the os-optics engine's viewer: python -m pip install \"os-optics>=0.12,<0.13\"") from None

    try:
        scene = load_view(args.run_dir)
    except Exception as error:  # noqa: BLE001 - the engine's refusal is the message
        raise RenderError("RUN_UNREADABLE", str(error)) from None
    document = view_document(scene)
    axis = optical_axis(document, args.axis)
    parts = part_points(scene)
    box_min, box_max = framing_box(parts, args.focus)
    browser = None if args.html_only else find_browser(args.browser)
    if not args.html_only and not browser:
        raise RenderError("BROWSER_NOT_FOUND",
                          "no Chrome/Chromium found; pass --browser PATH, or --html-only to write the pages")
    if not args.html_only:
        playwright_api()

    background = args.background
    transparent = background == "transparent"
    page_html = standalone_html(document).decode("utf-8")
    prepared = []
    for shot in shots:
        direction, up = camera_direction(axis, shot["azimuth_deg"], shot["elevation_deg"], up_axis)
        config = {"direction": direction, "up": up, "box_min": box_min, "box_max": box_max,
                  "roll_deg": shot["roll_deg"], "hide_classes": list(NOT_OPTICS) if args.focus == "optics" else [],
                  "edges": bool(args.edges), "labels": bool(args.labels), "beams": bool(args.beams),
                  "timeout_ms": args.timeout_s * 1000}
        page = inject(page_html, config, background)
        prepared.append((shot, direction, up, page))

    # All input, geometry and output checks have passed for the entire batch.
    out.mkdir(parents=True, exist_ok=True)
    written = []
    failures = 0
    for shot, direction, up, page in prepared:
        page_path = out / f"{shot['name']}.html"
        page_path.write_text(page, encoding="utf-8")
        record = {"name": shot["name"], "html": page_path.name,
                  "azimuth_deg": shot["azimuth_deg"], "elevation_deg": shot["elevation_deg"],
                  "roll_deg": shot["roll_deg"], "direction": None, "up": None, "focus": args.focus,
                  "requested_camera": {"direction": direction, "up": up, "roll_deg": shot["roll_deg"]},
                  "applied_camera": None, "status": "html_only",
                  "box_min": [round(v, 6) for v in box_min], "box_max": [round(v, 6) for v in box_max],
                  "image_px": [width, height], "png": None}
        if not args.html_only:
            try:
                applied = capture(browser, page_path, out / f"{shot['name']}.png", width, height,
                                  args.timeout_s, transparent)
                record["png"] = f"{shot['name']}.png"
                record["status"] = "captured"
                record["applied_camera"] = applied
                record["direction"], record["up"] = applied["direction"], applied["up"]
            except RenderError as error:
                failures += 1
                record["status"] = "failed"
                record["error"] = {"code": error.code, "message": str(error)}
        written.append(record)

    manifest = {"status": "FAIL" if failures else "OK", "run_dir": str(Path(args.run_dir).resolve()),
                "out": str(out.resolve()), "focus": args.focus, "background": args.background,
                "shots": written, "failed": failures}
    (out / "shots.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    sys.exit(main())
