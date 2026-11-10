"""Presentation animations from saved scenes and recorded study samples (``present`` asset kind ``animation``).

Nothing is simulated. Camera motions are captured from the engine's viewer of a saved run; a playback shows the
saved runs and recorded values of a study's samples one after another, each held for a fixed time. Scientific
values are never interpolated between samples.

Modes (the asset's ``mode``):

* ``orbit`` (``source``: a saved run): the camera turns about the vertical (``orbit.about: "up"``, at
  ``orbit.elevation_deg``) or about the optical axis (``"optical_axis"``, at ``orbit.angle_deg`` to the axis), from
  ``orbit.start_deg`` through ``orbit.sweep_deg``. One eye distance for every frame keeps the scale constant. A full
  turn (a multiple of 360 degrees) loops seamlessly at constant speed; a partial turn eases in and out.
* ``transition`` (a saved run): the camera moves from ``from`` (``camera`` [AZ, EL(, ROLL)], ``focus``) to ``to``,
  for example from the optical system to ``element:L2``. The view direction is interpolated on the sphere and the
  zoom is geometric with the framing box, eased; ``hold_s`` holds the start and the end pose.
* ``playback`` (a sweep study): one segment per sample, ordered by the swept parameter (``order_by``, default the
  first parameter; ties keep the recorded order), each held for ``hold_s``. ``panels``: ``view`` (the sample's
  saved run), ``plot`` (the study curve of ``y`` with the current sample marked) or ``view+plot``. All views share
  one camera and one framing box (the union over the samples), all plots one set of axis ranges. Every frame names
  the parameter value and the ``y`` value with units and the sample's outcome, and states that the frames are
  recorded samples. Samples without a saved run or without an exact value stay in the sequence, labelled so.

Common fields: ``source``, ``formats`` (``["mp4", "gif"]``; MP4 needs the ``media`` extra and an even width and
height), ``fps`` (24), ``gif_fps`` (at most 15), ``duration_s``, ``hold_s``, ``beams`` (true), ``up_axis``
([0, 0, 1]), ``easing`` (``auto``, ``linear``, ``ease``) and ``sequence``: a saved ``sequence.json`` (path relative
to the specification) to replay its camera poses, sample order and timing with the current theme and text.
The pixel size is the theme's ``size`` and is honoured exactly; text follows the theme language and background.

Outputs in the asset directory: ``<id>.mp4`` / ``<id>.gif``, the representative still ``<id>.png``,
``sequence.json`` (the replayable sequence: shots with camera vectors, framing boxes and eye distances, segments
with durations, frame counts and labels) and, for a playback, ``data.json`` (the recorded sample values shown).
Rendered views are cached (``ctx.cache_dir``) by run identity, camera, framing, size, background and beams, so a
rebuild that changes only text, labels or language re-encodes without opening a browser.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import os
from pathlib import Path
from typing import Any

from . import presentation as pr
from . import render_views as rv
from . import workflow_common as wc

SEQUENCE_SCHEMA = "os-optics-kit.animation-sequence"
DATA_SCHEMA = "os-optics-kit.animation-data"
SCHEMA_VERSION = 1
SEQUENCE_FILE = "sequence.json"
DATA_FILE = "data.json"
MODES = ("orbit", "transition", "playback")
MEDIA_FORMATS = ("mp4", "gif")
PANELS = ("view", "plot", "view+plot")
EASINGS = ("auto", "linear", "ease")
#: the engine viewer's fit margin and perspective field of view (os_optics viewer: FIT_MARGIN, camera_fov_deg)
FIT_MARGIN = 1.12
DEFAULT_FOV_DEG = 14.0
VIEW_CACHE = "animation-views"
MEDIA_RECOVERY = 'python -m pip install "os-optics-kit[media]", or set formats to ["gif"]'
RENDER_TIMEOUT_S = 60
SEPARATOR = " · "
#: outcome -> marker; colours come from the theme (presentation.STATUS_COLORS)
MARKERS = {"NOT_APPLICABLE": "o", "PASS": "o", "FAIL": "X", "UNKNOWN": "D", "OUT_OF_MODEL": "^", "ERROR": "s"}

COMMON_KEYS = ("id", "kind", "title", "caption", "theme", "enabled")
FIELDS: dict[str, dict[str, Any]] = {
    "mode": {"default": None, "modes": MODES, "text": "orbit | transition | playback"},
    "source": {"default": None, "modes": MODES,
               "text": "source id: a saved run (orbit, transition) or a study (playback)"},
    "formats": {"default": ["mp4", "gif"], "modes": MODES, "text": "any of mp4 (media extra, even size), gif"},
    "fps": {"default": 24, "modes": MODES, "text": "video frames per second, 1-60"},
    "gif_fps": {"default": "min(fps, 15)", "modes": MODES, "text": "GIF frames per second, 1-fps"},
    "duration_s": {"default": {"orbit": 6.0, "transition": 3.0}, "modes": ("orbit", "transition"),
                   "text": "seconds of camera motion"},
    "hold_s": {"default": {"transition": 0.5, "playback": 1.0}, "modes": ("transition", "playback"),
               "text": "transition: start and end pose held; playback: seconds per sample"},
    "easing": {"default": "auto", "modes": ("orbit", "transition"),
               "text": "auto (full turns linear, otherwise eased) | linear | ease"},
    "beams": {"default": True, "modes": MODES, "text": "draw the beam paths"},
    "up_axis": {"default": [0.0, 0.0, 1.0], "modes": MODES, "text": "world up X,Y,Z"},
    "focus": {"default": "optics", "modes": ("orbit", "playback"), "text": "optics | scene | element:<id>"},
    "camera": {"default": [55.0, 22.0], "modes": ("playback",),
               "text": "[AZ, EL(, ROLL)] degrees, as in render --camera"},
    "orbit": {"default": {"about": "up", "start_deg": 55.0, "sweep_deg": 360.0, "elevation_deg": 20.0,
                          "angle_deg": 70.0}, "modes": ("orbit",),
              "text": "about up (elevation_deg) | optical_axis (angle_deg to the axis); start_deg, sweep_deg"},
    "from": {"default": {"camera": [55.0, 22.0], "focus": "optics"}, "modes": ("transition",),
             "text": "start pose {camera, focus}"},
    "to": {"default": None, "modes": ("transition",), "text": "end pose {camera, focus}, e.g. focus element:L2"},
    "y": {"default": None, "modes": ("playback",), "text": "study column: target:<id>, derived:<name>"},
    "order_by": {"default": "first parameter", "modes": ("playback",), "text": "parameter that orders the samples"},
    "panels": {"default": "view+plot", "modes": ("playback",), "text": "view | plot | view+plot"},
    "sequence": {"default": None, "modes": MODES,
                 "text": "saved sequence.json to replay (camera poses, order, timing), relative to the spec"},
}

#: figure text of the animations, both languages (to move into presentation.TEXT)
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "title.orbit": "{source}: orbit view",
        "title.transition": "{source}: from {start} to {end}",
        "title.playback": "{y} vs {x}: recorded samples",
        "caption.orbit_up": "{source} as the camera turns {sweep} deg about the vertical at {elevation} deg "
                            "elevation; {duration} s at {fps} fps.",
        "caption.orbit_axis": "{source} as the camera turns {sweep} deg about the optical axis at {angle} deg to "
                              "the axis; {duration} s at {fps} fps.",
        "caption.transition": "{source}: camera move from {start} to {end}; {duration} s at {fps} fps.",
        "caption.playback": "{count} recorded samples of {x} from {low} to {high}, {hold} s each; {passed} PASS.",
        "caption.playback_missing": "{count} without a saved run.",
        "focus.optics": "the optical system",
        "focus.scene": "the full scene",
        "pose": "{focus} ({azimuth} deg, {elevation} deg elevation)",
        "label.value": "{name} = {value}",
        "label.no_value": "{name}: no exact value",
        "label.sample": "sample {index} of {count}",
        "note.no_run": "No saved run for this sample",
    },
    "zh-CN": {
        "title.orbit": "{source}：环绕视图",
        "title.transition": "{source}：从{start}到{end}",
        "title.playback": "{y} 随 {x}：记录的样本",
        "caption.orbit_up": "相机绕竖直轴旋转 {sweep}°、仰角 {elevation}° 观察{source}；"
                            "{duration} 秒，{fps} 帧/秒。",
        "caption.orbit_axis": "相机绕光轴旋转 {sweep}°、与光轴夹角 {angle}° 观察{source}；"
                              "{duration} 秒，{fps} 帧/秒。",
        "caption.transition": "{source}：相机从{start}移动到{end}；{duration} 秒，{fps} 帧/秒。",
        "caption.playback": "{x} 从 {low} 到 {high} 的 {count} 个记录样本，每个显示 {hold} 秒；"
                            "{passed} 个 PASS。",
        "caption.playback_missing": "{count} 个样本没有保存的运行结果。",
        "focus.optics": "光学系统",
        "focus.scene": "完整场景",
        "pose": "{focus}（{azimuth}°，仰角 {elevation}°）",
        "label.value": "{name} = {value}",
        "label.no_value": "{name}：无精确值",
        "label.sample": "第 {index} / {count} 个样本",
        "note.no_run": "该样本没有保存的运行结果",
    },
}


def settings() -> dict:
    """Compact description of the animation asset: modes, fields with defaults, outputs (``present settings``)."""
    return {
        "kind": "animation",
        "modes": {"orbit": "camera turn around a saved run, constant scale",
                  "transition": "camera move between two poses of a saved run (e.g. into element:<id>)",
                  "playback": "recorded study samples in parameter order, one segment per sample"},
        "fields": {name: {"default": spec["default"], "modes": list(spec["modes"]), "text": spec["text"]}
                   for name, spec in FIELDS.items()},
        "size": "theme.size, exact; MP4 needs an even width and height",
        "outputs": {"image": "<id>.png (representative still)", "mp4": "<id>.mp4", "gif": "<id>.gif",
                    "sequence": "sequence.json (replayable)", "data": "data.json (playback values)"},
        "media": MEDIA_RECOVERY,
    }


def text(key, language, **values):
    table = TEXT.get(language) or TEXT["en"]
    return table[key].format(**values)


# --------------------------------------------------------------------------------------------- settings

def _invalid(asset_id, key, message, recovery=None):
    return pr.PresentationError("SETTING_INVALID", f"asset {asset_id}: {key} {message}",
                                recovery=recovery or "run 'os-optics-kit present settings' for the animation fields",
                                key=f"assets.{asset_id}.{key}")


def _number(asset_id, key, value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise _invalid(asset_id, key, f"{value!r}: use a number from {low} to {high}")
    return float(value)


def _camera(asset_id, key, value):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise _invalid(asset_id, key, f"{value!r}: use [AZ, EL] or [AZ, EL, ROLL] in degrees")
    angles = [_number(asset_id, key, v, -360.0, 360.0) for v in value]
    return angles + [0.0] * (3 - len(angles))


def _focus(asset_id, key, value):
    if value in ("optics", "scene") or (isinstance(value, str) and value.startswith("element:") and len(value) > 8):
        return value
    raise _invalid(asset_id, key, f"{value!r}: use optics, scene or element:<id>")


def _pose(asset_id, key, value, default):
    value = {**default, **(value or {})} if isinstance(value or {}, dict) else value
    if not isinstance(value, dict) or set(value) - {"camera", "focus"}:
        raise _invalid(asset_id, key, "is an object {\"camera\": [AZ, EL], \"focus\": \"optics\"}")
    return {"camera": _camera(asset_id, f"{key}.camera", value["camera"]),
            "focus": _focus(asset_id, f"{key}.focus", value["focus"])}


def normalize(asset: dict) -> dict:
    """The asset's animation settings with defaults, checked; refuses unknown fields with the known ones."""
    asset_id = asset.get("id", "?")
    unknown = sorted(set(asset) - set(COMMON_KEYS) - set(FIELDS))
    if unknown:
        raise pr.PresentationError("SETTING_UNKNOWN", f"asset {asset_id}: unknown animation fields {unknown}; known: "
                                   f"{sorted(FIELDS)}", recovery="run 'os-optics-kit present settings'",
                                   unknown=unknown)
    mode = asset.get("mode")
    if mode not in MODES:
        raise _invalid(asset_id, "mode", f"{mode!r}: choose {list(MODES)}")
    if not isinstance(asset.get("source"), str):
        raise _invalid(asset_id, "source", "names a source of the specification",
                       recovery="set source to the id of a run (orbit, transition) or a study (playback)")
    stray = sorted(key for key in asset if key in FIELDS and mode not in FIELDS[key]["modes"])
    if stray:
        raise _invalid(asset_id, stray[0], f"does not apply to mode {mode}; fields of {mode}: "
                       f"{sorted(k for k, s in FIELDS.items() if mode in s['modes'])}")
    formats = asset.get("formats", ["mp4", "gif"])
    if not isinstance(formats, list) or not formats or any(f not in MEDIA_FORMATS for f in formats):
        raise _invalid(asset_id, "formats", f"{formats!r}: a non-empty list of {list(MEDIA_FORMATS)}")
    fps = _number(asset_id, "fps", asset.get("fps", 24), 1, 60)
    gif_fps = _number(asset_id, "gif_fps", asset.get("gif_fps", min(fps, 15.0)), 1, fps)
    up_axis = asset.get("up_axis", [0.0, 0.0, 1.0])
    if not isinstance(up_axis, list) or len(up_axis) != 3:
        raise _invalid(asset_id, "up_axis", f"{up_axis!r}: [X, Y, Z]")
    up_axis = [_number(asset_id, "up_axis", v, -1e6, 1e6) for v in up_axis]
    if math.hypot(*up_axis) < 1e-9:
        raise _invalid(asset_id, "up_axis", "is a zero vector")
    beams = asset.get("beams", True)
    if not isinstance(beams, bool):
        raise _invalid(asset_id, "beams", "true or false")
    easing = asset.get("easing", "auto")
    if easing not in EASINGS:
        raise _invalid(asset_id, "easing", f"{easing!r}: choose {list(EASINGS)}")
    sequence = asset.get("sequence")
    if sequence is not None and not (isinstance(sequence, str) and sequence):
        raise _invalid(asset_id, "sequence", "is the path of a saved sequence.json, relative to the specification")
    cfg: dict[str, Any] = {"mode": mode, "source": asset["source"], "formats": list(dict.fromkeys(formats)),
                           "fps": fps, "gif_fps": gif_fps, "up_axis": up_axis, "beams": beams, "easing": easing,
                           "sequence": sequence}
    if mode == "orbit":
        orbit = {**FIELDS["orbit"]["default"], **(asset.get("orbit") or {})}
        extra = sorted(set(orbit) - set(FIELDS["orbit"]["default"]))
        if extra:
            raise _invalid(asset_id, "orbit", f"has unknown keys {extra}; keys: {sorted(FIELDS['orbit']['default'])}")
        if orbit["about"] not in ("up", "optical_axis"):
            raise _invalid(asset_id, "orbit.about", f"{orbit['about']!r}: up or optical_axis")
        cfg["orbit"] = {"about": orbit["about"],
                        "start_deg": _number(asset_id, "orbit.start_deg", orbit["start_deg"], -360, 360),
                        "sweep_deg": _number(asset_id, "orbit.sweep_deg", orbit["sweep_deg"], -1080, 1080),
                        "elevation_deg": _number(asset_id, "orbit.elevation_deg", orbit["elevation_deg"], -89, 89),
                        "angle_deg": _number(asset_id, "orbit.angle_deg", orbit["angle_deg"], 1, 179)}
        if cfg["orbit"]["sweep_deg"] == 0:
            raise _invalid(asset_id, "orbit.sweep_deg", "is 0; use for example 360 or 90")
        cfg["duration_s"] = _number(asset_id, "duration_s", asset.get("duration_s", 6.0), 0.2, 120)
        cfg["focus"] = _focus(asset_id, "focus", asset.get("focus", "optics"))
    elif mode == "transition":
        if asset.get("to") is None and sequence is None:
            raise _invalid(asset_id, "to", "is missing: the end pose, e.g. {\"camera\": [35, 12], \"focus\": "
                           "\"element:L2\"}")
        cfg["from"] = _pose(asset_id, "from", asset.get("from"), FIELDS["from"]["default"])
        cfg["to"] = _pose(asset_id, "to", asset.get("to"), {"camera": cfg["from"]["camera"], "focus": "optics"})
        cfg["duration_s"] = _number(asset_id, "duration_s", asset.get("duration_s", 3.0), 0.2, 120)
        cfg["hold_s"] = _number(asset_id, "hold_s", asset.get("hold_s", 0.5), 0, 30)
    else:
        if not isinstance(asset.get("y"), str) or ":" not in asset["y"]:
            raise _invalid(asset_id, "y", f"{asset.get('y')!r}: a study column such as target:<id>")
        cfg["y"] = asset["y"]
        cfg["order_by"] = asset.get("order_by")
        cfg["panels"] = asset.get("panels", "view+plot")
        if cfg["panels"] not in PANELS:
            raise _invalid(asset_id, "panels", f"{cfg['panels']!r}: choose {list(PANELS)}")
        cfg["camera"] = _camera(asset_id, "camera", asset.get("camera", [55.0, 22.0]))
        cfg["focus"] = _focus(asset_id, "focus", asset.get("focus", "optics"))
        cfg["hold_s"] = _number(asset_id, "hold_s", asset.get("hold_s", 1.0), 0.1, 30)
    return {key: value for key, value in cfg.items() if key not in FIELDS or mode in FIELDS[key]["modes"]}


def check_size(size, formats):
    """Refuse an odd width or height for MP4 (H.264 4:2:0 needs even dimensions); the size is never altered."""
    width, height = size
    if "mp4" in formats and (width % 2 or height % 2):
        even = f"{width + width % 2}x{height + height % 2}"
        raise pr.PresentationError("SETTING_INVALID", f"MP4 needs an even width and height; the size is "
                                   f"{width}x{height}", recovery=f"set the theme size to {even}, or set formats to "
                                   "[\"gif\"]", key="size")
    return size


def media_modules(formats):
    """Import the encoders the formats need; MEDIA_EXTRA_MISSING with the install step otherwise."""
    try:
        from PIL import Image  # noqa: F401 - Pillow comes with matplotlib
        import matplotlib  # noqa: F401
    except ImportError:
        raise pr.PresentationError("MEDIA_EXTRA_MISSING", "animations need matplotlib and Pillow",
                                   recovery='python -m pip install "os-optics-kit[plot]"') from None
    if "mp4" in formats:
        try:
            import imageio_ffmpeg  # noqa: F401
        except ImportError:
            raise pr.PresentationError("MEDIA_EXTRA_MISSING", "MP4 export needs imageio-ffmpeg",
                                       recovery=MEDIA_RECOVERY) from None


# --------------------------------------------------------------------------------------------- camera geometry

def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _scale(a, k):
    return [x * k for x in a]


def _add(*vectors):
    return [sum(parts) for parts in zip(*vectors)]


def rotate(vector, axis, angle_rad):
    """``vector`` turned about the unit ``axis`` by ``angle_rad`` (right-hand rule, Rodrigues)."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return _add(_scale(vector, c), _scale(rv.cross(axis, vector), s), _scale(axis, rv.dot(axis, vector) * (1 - c)))


def slerp(a, b, t):
    """Spherical interpolation of unit vectors (constant angular speed); antiparallel ends turn about any normal."""
    a, b = rv.unit(a), rv.unit(b)
    cosine = max(-1.0, min(1.0, rv.dot(a, b)))
    angle = math.acos(cosine)
    if angle < 1e-9:
        return list(a)
    if math.pi - angle < 1e-6:
        helper = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
        return rotate(a, rv.unit(rv.cross(a, helper)), angle * t)
    sine = math.sin(angle)
    return rv.unit(_add(_scale(a, math.sin((1 - t) * angle) / sine), _scale(b, math.sin(t * angle) / sine)))


def ease(t):
    """Cosine ease in and out: 0 -> 0, 1 -> 1, zero speed at both ends."""
    t = min(1.0, max(0.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def screen_up(direction, up):
    """``up`` made perpendicular to the view direction (unit)."""
    projected = _sub(up, _scale(direction, rv.dot(up, direction)))
    if math.hypot(*projected) < 1e-9:
        raise pr.PresentationError("CAMERA_UNDEFINED", "the view direction is parallel to the up axis",
                                   recovery="lower the elevation or set up_axis to another direction")
    return rv.unit(projected)


def pose_vectors(axis, camera, up_axis):
    """``(direction, up)`` of a camera ``[AZ, EL, ROLL]`` as ``render --camera`` states it (roll folded into up)."""
    azimuth, elevation, roll = camera
    try:
        direction, up = rv.camera_direction(axis, azimuth, elevation, up_axis)
    except rv.RenderError as error:
        raise pr.PresentationError(error.code, str(error), recovery="use 0 <= AZ <= 180 and |EL| <= min(AZ, 180-AZ), "
                                   "as in 'os-optics-kit render --camera'") from None
    if roll:
        up = rotate(up, direction, math.radians(roll))
    return direction, up


def orbit_poses(axis, up_axis, orbit, fractions):
    """``[(direction, up, angle_deg)]`` along an orbit for path fractions in [0, 1].

    About ``up``: the eye circles the vertical at ``elevation_deg``; the angle is measured in the horizontal plane
    from the optical axis towards the side (``up x axis``), as ``render`` measures AZ. About ``optical_axis``: the
    eye keeps ``angle_deg`` to the axis and turns about it with its screen up (the view turns rigidly)."""
    axis, world_up = rv.unit(axis), rv.unit(up_axis)
    poses = []
    if orbit["about"] == "up":
        horizontal = _sub(axis, _scale(world_up, rv.dot(axis, world_up)))
        if math.hypot(*horizontal) < 1e-9:
            raise pr.PresentationError("CAMERA_UNDEFINED", "the optical axis is vertical, so an orbit about up has no "
                                       "reference direction", recovery="set orbit.about to optical_axis, or set "
                                       "up_axis to a direction across the optical axis")
        reference = rv.unit(horizontal)
        side = rv.cross(world_up, reference)
        elevation = math.radians(orbit["elevation_deg"])
        for fraction in fractions:
            angle = orbit["start_deg"] + orbit["sweep_deg"] * fraction
            theta = math.radians(angle)
            direction = rv.unit(_add(_scale(reference, math.cos(elevation) * math.cos(theta)),
                                     _scale(side, math.cos(elevation) * math.sin(theta)),
                                     _scale(world_up, math.sin(elevation))))
            poses.append((direction, screen_up(direction, world_up), angle))
        return poses
    lift = _sub(world_up, _scale(axis, rv.dot(world_up, axis)))
    if math.hypot(*lift) < 1e-9:
        raise pr.PresentationError("CAMERA_UNDEFINED", "the optical axis is parallel to up_axis",
                                   recovery="set up_axis to a direction across the optical axis")
    lift = rv.unit(lift)
    side = rv.cross(lift, axis)
    tilt = math.radians(orbit["angle_deg"])
    start = _add(_scale(axis, math.cos(tilt)), _scale(side, math.sin(tilt)))
    for fraction in fractions:
        angle = orbit["start_deg"] + orbit["sweep_deg"] * fraction
        turn = math.radians(angle)
        poses.append((rv.unit(rotate(start, axis, turn)), rv.unit(rotate(lift, axis, turn)), angle))
    return poses


def fit_distance(box, direction, up, fov_deg, aspect, margin=FIT_MARGIN):
    """Eye distance at which the viewer's perspective camera fits ``box`` (the viewer's own ``frame`` rule)."""
    low, high = box
    center = [(a + b) / 2 for a, b in zip(low, high)]
    d = rv.unit(direction)
    right = rv.cross(up, d)
    right = rv.unit(right) if math.hypot(*right) > 1e-9 else [1.0, 0.0, 0.0]
    true_up = rv.unit(rv.cross(d, right))
    size = [b - a for a, b in zip(low, high)]
    distance = max(math.hypot(*size) * 0.1, 1.0)
    tan_v = math.tan(math.radians(fov_deg) / 2) / margin
    tan_h = tan_v * aspect
    for corner in range(8):
        point = [high[i] if corner & (1 << i) else low[i] for i in range(3)]
        offset = _sub(point, center)
        along = rv.dot(offset, d)
        distance = max(distance, abs(rv.dot(offset, right)) / tan_h + along,
                       abs(rv.dot(offset, true_up)) / tan_v + along)
    return distance


def transition_poses(start, end, fractions):
    """``[(direction, up, box, distance)]`` from ``start`` to ``end`` (each ``(direction, up, box, distance)``).

    The direction and up turn on the sphere; the distance changes geometrically (an even zoom rate) and the framing
    box follows the zoom, so the end's subject stays in view while the camera closes in."""
    d0, u0, box0, dist0 = start
    d1, u1, box1, dist1 = end
    poses = []
    for s in fractions:
        direction = slerp(d0, d1, s)
        up = screen_up(direction, slerp(u0, u1, s))
        distance = dist0 * (dist1 / dist0) ** s
        weight = (dist0 - distance) / (dist0 - dist1) if abs(dist0 - dist1) > 1e-9 * max(dist0, 1.0) else s
        box = ([a + (b - a) * weight for a, b in zip(box0[0], box1[0])],
               [a + (b - a) * weight for a, b in zip(box0[1], box1[1])])
        poses.append((direction, up, box, distance))
    return poses


def path_fractions(count, loop, easing):
    """``count`` path fractions; a loop leaves out the end (it equals the start) and moves at constant speed."""
    if count < 2:
        return [0.0]
    raw = [i / count for i in range(count)] if loop else [i / (count - 1) for i in range(count)]
    if easing == "linear" or (easing == "auto" and loop):
        return raw
    return [ease(t) for t in raw]


# --------------------------------------------------------------------------------------------- timeline

def assign_timeline(segments, fps):
    """Set ``start_s`` and ``frames`` (video frames at ``fps``) of each segment from its ``duration_s``.

    Frame boundaries are rounded from the cumulative time, so the total equals round(duration * fps)."""
    start = 0.0
    for segment in segments:
        end = start + segment["duration_s"]
        segment["start_s"] = round(start, 6)
        segment["frames"] = int(round(end * fps)) - int(round(start * fps))
        start = end
    return round(start, 6)


def gif_timeline(segments, gif_fps, key=None):
    """``[(segment index, duration_ms)]`` of the GIF: the timeline sampled at ``gif_fps``; consecutive samples of the
    same image (``key(segment)``, default the segment) merge into one frame. Durations are multiples of 10 ms."""
    total = sum(s["duration_s"] for s in segments)
    count = max(1, int(round(total * gif_fps)))
    key = key or (lambda segment: id(segment))
    bounds = []
    acc = 0.0
    for segment in segments:
        bounds.append((acc, acc + segment["duration_s"]))
        acc += segment["duration_s"]
    frames: list[list] = []
    index = 0
    for j in range(count):
        t = j / gif_fps
        while index < len(segments) - 1 and t >= bounds[index][1] - 1e-9:
            index += 1
        begin = int(round(j / gif_fps * 100)) * 10
        finish = int(round(min(total, (j + 1) / gif_fps) * 100)) * 10 if j < count - 1 else int(round(total * 100)) * 10
        if frames and key(segments[frames[-1][0]]) == key(segments[index]):
            frames[-1][1] += finish - begin
        else:
            frames.append([index, finish - begin])
    return [(i, max(10, ms)) for i, ms in frames]


# --------------------------------------------------------------------------------------------- study samples

def _columns(summary):
    return [c for c in summary.get("columns", []) if ":" in c and c.count(":") == 1]


def study_samples(study_dir, y, order_by=None):
    """Recorded samples of a study for column ``y``: parameters, unit, bounds and the samples in recorded order."""
    study_dir = Path(study_dir)
    summary_path = study_dir / "summary.json"
    if not summary_path.is_file():
        raise pr.PresentationError("SOURCE_MISSING", f"{study_dir.name} has no summary.json",
                                   recovery="point the source at a study directory written by 'os-optics-kit sweep'")
    summary = wc.read_json(summary_path)
    rows = summary.get("rows") or []
    parameters = summary.get("parameters") or []
    if not parameters or not rows:
        raise pr.PresentationError("SOURCE_MISSING", f"{study_dir.name}: the study has no samples",
                                   recovery="run the study with 'os-optics-kit sweep' first")
    if y not in summary.get("columns", []) or not any(y in row for row in rows):
        raise pr.PresentationError("COLUMN_UNKNOWN", f"column {y!r} is not in the study table; columns: "
                                   f"{_columns(summary)}", recovery="set y to one of the listed columns",
                                   columns=_columns(summary))
    names = [p["name"] for p in parameters]
    order_by = order_by or names[0]
    if order_by not in names:
        raise pr.PresentationError("COLUMN_UNKNOWN", f"order_by {order_by!r} is not a parameter of the study; "
                                   f"parameters: {names}", recovery=f"set order_by to one of {names}")
    unit = next((row.get(f"{y}:unit") for row in rows if row.get(f"{y}:unit")), None)
    samples = []
    for index, row in enumerate(rows):
        value = row.get(y)
        exact = (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                 and row.get(f"{y}:exact") is not False)
        run_rel = row.get("run_dir")
        has_run = bool(run_rel) and pr.source_kind(study_dir / run_rel) == "run"
        samples.append({
            "sample_id": row["sample_id"], "recorded_index": index,
            "parameters": {p["name"]: {"value": row.get(f"param:{p['name']}"),
                                       "unit": row.get(f"param:{p['name']}:unit") or p.get("unit")}
                           for p in parameters},
            "y": {"value": value if exact else None, "unit": unit, "exact": bool(exact)},
            "outcome": row.get("outcome") or "ERROR", "has_run": has_run, "run_dir": run_rel if has_run else None,
            "identity": {k: row.get(k) for k in ("project_hash", "input_hash", "result_hash", "engine_build_id")},
        })
    bounds = None
    if y.startswith("target:"):
        target_id = y.split(":", 1)[1]
        for sample in samples:
            path = study_dir / "samples" / sample["sample_id"] / "sample.json"
            if path.is_file():
                targets = wc.read_json(path).get("targets", [])
                found = [t for t in targets if t.get("id") == target_id and t.get("bounds")]
                if found:
                    bounds = wc.target_bounds(found[0])
                    break
    return {"summary_sha256": wc.sha256_file(summary_path), "parameters": parameters, "order_by": order_by,
            "y": y, "unit": unit, "bounds": bounds, "samples": samples,
            "engine_build_id": ((summary.get("identity") or {}).get("engine") or {}).get("engine_build_id")}


def playback_order(samples, parameter):
    """Samples sorted by the value of ``parameter``; ties and non-numeric values keep the recorded order (last)."""
    def key(sample):
        value = sample["parameters"].get(parameter, {}).get("value")
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        return (0 if numeric else 1, value if numeric else 0.0, sample["recorded_index"])

    return sorted(samples, key=key)


def _with_unit(theme, value, unit):
    shown = theme.to_display(value, unit) if unit else value
    unit_text = theme.display_unit(unit) if unit and unit not in wc.UNITLESS else ""
    return f"{pr.fmt_number(shown)} {unit_text}".strip()


def sample_label(theme, sample, parameters, y):
    """``l2_shift = 0.54 mm · expanded_waist = 1.99645 mm · PASS`` in the theme language (no value: says so)."""
    pieces = []
    for parameter in parameters:
        entry = sample["parameters"][parameter["name"]]
        pieces.append(text("label.value", theme.language, name=theme.label(f"param:{parameter['name']}"),
                           value=_with_unit(theme, entry["value"], entry["unit"])))
    if sample["y"]["exact"]:
        pieces.append(text("label.value", theme.language, name=theme.label(y),
                           value=_with_unit(theme, sample["y"]["value"], sample["y"]["unit"])))
    else:
        pieces.append(text("label.no_value", theme.language, name=theme.label(y)))
    pieces.append(theme.outcome(sample["outcome"]))
    return SEPARATOR.join(pieces)


# --------------------------------------------------------------------------------------------- layout and drawing

def _px(theme, role, height):
    return theme.font_pt(role) * (height / pr.CANVAS_HEIGHT_IN) / 72.0


def layout(theme, size, mode, panels, show_title, figure_caption):
    """Pixel rectangles ``[x, y, w, h]`` (top-left origin) of the title band, the view, the plot and the text lines."""
    width, height = size
    pad = round(0.022 * height)
    header = round(pad * 1.6 + _px(theme, "title", height) * 1.35) if show_title else 0
    lines = []
    if mode == "playback":
        lines = ["body", "caption"]
    elif figure_caption:
        lines = ["caption"]
    footer = round(pad * 1.2 + sum(_px(theme, r, height) * 1.5 for r in lines)) if lines else 0
    body = [0, header, width, max(1, height - header - footer)]
    view = plot = None
    if mode != "playback" or panels == "view":
        view = body
    elif panels == "plot":
        plot = body
    elif width >= 1.2 * height:
        split = round(width * 0.56)
        view, plot = [0, header, split, body[3]], [split, header, width - split, body[3]]
    else:
        split = round(body[3] * 0.55)
        view, plot = [0, header, width, split], [0, header + split, width, body[3] - split]
    return {"size": [width, height], "pad": pad, "header": header, "footer": footer, "lines": lines,
            "view": view, "plot": plot}


def _figure(theme, size):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figsize, dpi = theme.canvas(tuple(size))
    figure = Figure(figsize=figsize, dpi=dpi)
    FigureCanvasAgg(figure)
    figure.patch.set_alpha(0.0)
    return figure


def _image(figure, size):
    from PIL import Image

    figure.canvas.draw()
    width, height = figure.canvas.get_width_height()
    image = Image.frombuffer("RGBA", (width, height), bytes(figure.canvas.buffer_rgba()), "raw", "RGBA", 0, 1)
    if (width, height) != tuple(size):
        exact = Image.new("RGBA", tuple(size), (0, 0, 0, 0))
        exact.paste(image, (0, 0))
        image = exact
    return image.copy()


def draw_overlay(theme, plan_layout, title, lines, plot=None, current=None, note=None):
    """Transparent RGBA overlay at the exact frame size: title band, text lines, plot panel, view note."""
    import matplotlib

    width, height = plan_layout["size"]
    pad = plan_layout["pad"]
    with matplotlib.rc_context(theme.rc()):
        figure = _figure(theme, (width, height))
        colors = theme.colors
        if title and plan_layout["header"]:
            figure.text(pad / width, 1 - pad / height, title, ha="left", va="top", color=colors["text"],
                        fontsize=theme.font_pt("title"))
        y = height - plan_layout["footer"] + pad * 0.5
        for role, line in zip(plan_layout["lines"], lines):
            figure.text(pad / width, 1 - y / height, line, ha="left", va="top",
                        color=colors["text"] if role == "body" else colors["muted"], fontsize=theme.font_pt(role))
            y += _px(theme, role, height) * 1.5
        if note and plan_layout["view"]:
            x0, y0, w0, h0 = plan_layout["view"]
            figure.text((x0 + w0 / 2) / width, 1 - (y0 + h0 / 2) / height, note, ha="center", va="center",
                        color=colors["muted"], fontsize=theme.font_pt("panel"))
        if plot is not None and plan_layout["plot"]:
            _draw_plot(figure, theme, plan_layout, plot, current)
        return _image(figure, (width, height))


def plot_model(theme, study, order):
    """Display values of the study curve and its fixed axis ranges (the same for every frame)."""
    parameter = next(p for p in study["parameters"] if p["name"] == study["order_by"])
    x_unit = parameter.get("unit")
    points = []
    for sample in order:
        x = sample["parameters"][parameter["name"]]["value"]
        x = theme.to_display(x, x_unit) if isinstance(x, (int, float)) and not isinstance(x, bool) else None
        yv = theme.to_display(sample["y"]["value"], study["unit"]) if sample["y"]["exact"] else None
        points.append({"sample_id": sample["sample_id"], "x": x, "y": yv, "outcome": sample["outcome"]})
    xs = [p["x"] for p in points if p["x"] is not None]
    ys = [p["y"] for p in points if p["y"] is not None]
    bounds = study["bounds"] or {}
    limits = [theme.to_display(v, study["unit"]) for v in (bounds.get("minimum"), bounds.get("maximum"))
              if v is not None]
    low, high = (min(ys + limits), max(ys + limits)) if ys or limits else (0.0, 1.0)
    span = (high - low) or abs(high) or 1.0
    no_value_row = low - 0.12 * span if any(p["y"] is None for p in points) else None
    bottom = (no_value_row if no_value_row is not None else low) - 0.06 * span
    x_low, x_high = (min(xs), max(xs)) if xs else (0.0, 1.0)
    x_span = (x_high - x_low) or abs(x_high) or 1.0
    shown_bounds = [theme.to_display(bounds.get(k), study["unit"]) if bounds.get(k) is not None else None
                    for k in ("minimum", "maximum")]
    return {"points": points, "xlim": [x_low - 0.05 * x_span, x_high + 0.05 * x_span],
            "ylim": [bottom, high + 0.08 * span], "no_value_row": no_value_row, "bounds": shown_bounds,
            "x_label": theme.axis_label(f"param:{parameter['name']}", x_unit), "y_label": theme.axis_label(
                study["y"], study["unit"])}


def _draw_plot(figure, theme, plan_layout, model, current):
    width, height = plan_layout["size"]
    pad = plan_layout["pad"]
    x0, y0, w0, h0 = plan_layout["plot"]
    small, body = _px(theme, "small", height), _px(theme, "body", height)
    outcomes = [code for code in MARKERS if any(p["outcome"] == code for p in model["points"])]
    show_legend = theme.legend != "none"
    left = pad + body * 1.8 + small * 5.0
    bottom = pad * 0.6 + small * 1.6 + body * 1.8
    right = pad * 1.6
    entries = legend_labels(theme, model, outcomes) if show_legend else []
    axes_width = w0 - left - right
    widest = max((_text_width(label, small) + small * 2.6 for label in entries), default=0.0)
    columns = next((c for c in (3, 2) if c * widest <= axes_width), 1)
    legend_rows = math.ceil(len(entries) / columns)
    top = pad * 1.2 + legend_rows * small * 1.7
    axes = figure.add_axes([(x0 + left) / width, 1 - (y0 + h0 - bottom) / height,
                            max(0.05, (w0 - left - right) / width), max(0.05, (h0 - top - bottom) / height)])
    if theme.background == "transparent":
        axes.set_facecolor("none")
    points = model["points"]
    valued = sorted((p for p in points if p["x"] is not None and p["y"] is not None), key=lambda p: p["x"])
    line_color = theme.series_color(0)
    if valued:
        axes.plot([p["x"] for p in valued], [p["y"] for p in valued], color=line_color, linewidth=1.6, zorder=2)
    low, high = model["bounds"]
    if low is not None and high is not None:
        axes.axhspan(low, high, color=line_color, alpha=0.12, zorder=1,
                     label=theme.text("legend.bounds", low=pr.fmt_number(low), high=pr.fmt_number(high)))
    else:
        for value, key in ((low, "legend.minimum"), (high, "legend.maximum")):
            if value is not None:
                axes.axhline(value, color=line_color, linewidth=0.9, linestyle="--",
                             label=theme.text(key, value=pr.fmt_number(value)))
    size = (theme.font_pt("small") * 0.85) ** 2  # points^2
    for code in outcomes:
        group = [p for p in points if p["outcome"] == code]
        with_value = [p for p in group if p["y"] is not None and p["x"] is not None]
        color = theme.status_color(code)
        if with_value:
            axes.scatter([p["x"] for p in with_value], [p["y"] for p in with_value], marker=MARKERS[code], s=size,
                         color=color, edgecolors=theme.colors["face"] if theme.colors["face"] != "none" else "white",
                         linewidths=0.8, zorder=3,
                         label=theme.text("legend.count", label=theme.outcome(code), count=len(group)))
        without = [p for p in group if p["y"] is None and p["x"] is not None]
        if without:
            axes.scatter([p["x"] for p in without], [model["no_value_row"]] * len(without), marker=MARKERS[code],
                         s=size, facecolors="none", edgecolors=color, linewidths=1.2, zorder=3, clip_on=False,
                         label=theme.text("legend.no_exact", label=theme.outcome(code), count=len(without)))
    if model["no_value_row"] is not None:
        axes.axhline(model["no_value_row"], color=theme.colors["muted"], linewidth=0.8, linestyle=":", zorder=1)
        axes.annotate(theme.text("note.no_exact_row"), xy=(0.0, model["no_value_row"]),
                      xycoords=("axes fraction", "data"), xytext=(3, 3), textcoords="offset points",
                      fontsize=theme.font_pt("caption"), color=theme.colors["muted"])
    if current is not None:
        point = points[current]
        if point["x"] is not None:
            axes.axvline(point["x"], color=theme.colors["muted"], linewidth=1.0, linestyle="--", zorder=1)
            y_value = point["y"] if point["y"] is not None else model["no_value_row"]
            axes.scatter([point["x"]], [y_value], marker="o", s=size * 4.0, facecolors="none",
                         edgecolors=theme.colors["text"], linewidths=2.0, zorder=4, clip_on=False)
    axes.set_xlim(*model["xlim"])
    axes.set_ylim(*model["ylim"])
    axes.set_xlabel(model["x_label"])
    axes.set_ylabel(model["y_label"])
    if entries:
        axes.legend(loc="lower left", bbox_to_anchor=(0.0, 1.01), ncol=columns, frameon=False,
                    borderaxespad=0.2, handletextpad=0.4, columnspacing=1.0)


def legend_labels(theme, model, outcomes):
    """Legend entries of the playback plot (bounds, outcomes with counts, samples without a value)."""
    points = model["points"]
    labels = []
    low, high = model["bounds"]
    if low is not None and high is not None:
        labels.append(theme.text("legend.bounds", low=pr.fmt_number(low), high=pr.fmt_number(high)))
    else:
        labels.extend(theme.text(key, value=pr.fmt_number(value))
                      for value, key in ((low, "legend.minimum"), (high, "legend.maximum")) if value is not None)
    for code in outcomes:
        group = [p for p in points if p["outcome"] == code]
        if any(p["y"] is not None and p["x"] is not None for p in group):
            labels.append(theme.text("legend.count", label=theme.outcome(code), count=len(group)))
        without = [p for p in group if p["y"] is None and p["x"] is not None]
        if without:
            labels.append(theme.text("legend.no_exact", label=theme.outcome(code), count=len(without)))
    return labels


def _text_width(value, font_px):
    """Approximate drawn width in pixels: wide (CJK) characters take one em, others about 0.6 em."""
    return sum(font_px if ord(char) > 0x2E80 else 0.6 * font_px for char in value)


def _base_color(theme):
    from matplotlib.colors import to_rgba

    face = theme.colors["face"]
    if face == "none":
        return (0, 0, 0, 0)
    return tuple(int(round(c * 255)) for c in to_rgba(face))


def compose(size, base, view_png, view_rect, overlay):
    """One frame: the theme background, the captured view pasted unscaled into its rectangle, then the overlay."""
    from PIL import Image

    frame = Image.new("RGBA", tuple(size), base)
    if view_png is not None and view_rect is not None:
        with Image.open(view_png) as captured:
            view = captured.convert("RGBA")
        if view.size != (view_rect[2], view_rect[3]):
            view = view.resize((view_rect[2], view_rect[3]), Image.LANCZOS)
        frame.paste(view, (view_rect[0], view_rect[1]))
    frame.alpha_composite(overlay)
    return frame


def _flatten(frame):
    """RGB for video: a transparent background becomes white."""
    from PIL import Image

    if frame.getextrema()[3][0] == 255:
        return frame.convert("RGB")
    flat = Image.new("RGBA", frame.size, (255, 255, 255, 255))
    flat.alpha_composite(frame)
    return flat.convert("RGB")


# --------------------------------------------------------------------------------------------- views and cache

def _rounded(vector, digits=9):
    return [round(float(v), digits) for v in vector]


def shot_fingerprint(identity, shot, view_size, background, beams, visibility):
    """Cache key of one captured view: the run, the camera, the framing, the size, the background and the beams."""
    script = hashlib.sha256(rv.SCRIPT.encode("utf-8")).hexdigest()[:16]
    return pr.fingerprint("animation-view", identity, _rounded(shot["direction"]), _rounded(shot["up"]),
                          _rounded(shot["box_min"], 6), _rounded(shot["box_max"], 6),
                          round(float(shot["distance_mm"]), 6) if shot.get("distance_mm") else None,
                          list(view_size), background, bool(beams), visibility, script)


def visibility(view, focuses):
    """``optics`` hides sources, detectors and planes (presentation views); ``scene`` keeps them."""
    for focus in focuses:
        if focus == "scene":
            return "scene"
        if focus.startswith("element:"):
            part = view.parts.get(focus.split(":", 1)[1])
            if part and part[0] in rv.NOT_OPTICS:
                return "scene"
    return "optics"


def _view_box(view, focus):
    try:
        return view.box(focus)
    except rv.RenderError as error:
        details = error.details.get("available")
        raise pr.PresentationError(error.code, str(error), recovery=(f"use one of the parts {details}" if details
                                   else "use focus optics, scene or element:<id>"), **error.details) from None


def _load_view(path, axis=None):
    try:
        return rv.ViewScene(path, axis=axis)
    except rv.RenderError as error:
        recovery = ('python -m pip install "os-optics>=0.13,<0.14"' if error.code == "ENGINE_NOT_INSTALLED"
                    else "check the run directory with 'os-optics-kit summary RUN'")
        raise pr.PresentationError(error.code, str(error), recovery=recovery) from None


def capture_views(ctx, asset_id, jobs, view_size, background, beams, visible, progress=None):
    """Captured view PNG per shot id: cached files are reused, the rest are captured in one browser session.

    ``jobs``: ``[(shot, run_dir, view, identity)]``. Returns ``({shot id: png path}, captured, reused)``."""
    cache = Path(ctx.cache_dir(VIEW_CACHE))
    paths: dict[str, Path] = {}
    pending: dict[str, list] = {}
    for shot, run_dir, view, identity in jobs:
        key = shot_fingerprint(identity, shot, view_size, background, beams, visible)
        shot["fingerprint"] = key
        path = cache / f"{key}.png"
        paths[shot["id"]] = path
        if not path.is_file():
            pending.setdefault(str(run_dir), []).append((shot, view, key))
    total = sum(len(v) for v in pending.values())
    if not total:
        return paths, 0, len(jobs)
    if not ctx.browser:
        raise pr.PresentationError("BROWSER_NOT_FOUND", "the view frames are captured with Chrome or Chromium, and "
                                   "none was found", recovery="install Google Chrome or Chromium, or set its path "
                                   "as the browser setting, then build again")
    done = 0
    try:
        with rv.BrowserSession(ctx.browser, size=tuple(view_size), timeout_s=RENDER_TIMEOUT_S) as session:
            for run_dir, items in pending.items():
                unique = {key: (shot, view) for shot, view, key in items}
                shots = [{"name": key, "direction": shot["direction"], "up": shot["up"],
                          "box": [shot["box_min"], shot["box_max"]], "distance_mm": shot.get("distance_mm"),
                          "roll_deg": 0.0} for key, (shot, view) in unique.items()]
                view = next(iter(unique.values()))[1]
                first = shots[0]["box"]
                records = rv.render_shots(run_dir, shots, cache, size=tuple(view_size), background=background,
                                          focus=visible, box=first, beams=beams, view=view, session=session,
                                          timeout_s=RENDER_TIMEOUT_S)
                failed = [r for r in records if r["status"] != "captured"]
                if failed:
                    error = failed[0].get("error") or {}
                    raise pr.PresentationError("CAPTURE_FAILED", f"{len(failed)} view frames were not captured: "
                                               f"{error.get('message', 'unknown error')}",
                                               recovery="open the run with 'os-optics view RUN' to check it")
                done += len(records)
                if progress:
                    progress(done, total)
    except rv.RenderError as error:
        recovery = {"BROWSER_NOT_FOUND": "install Google Chrome or Chromium, then build again",
                    "CAPTURE_NOT_INSTALLED": 'python -m pip install "os-optics-kit[render]"'}.get(
                        error.code, "open the run with 'os-optics view RUN' to check it, then build again")
        raise pr.PresentationError(error.code, str(error), recovery=recovery) from None
    return paths, total, len(jobs) - total


# --------------------------------------------------------------------------------------------- encoding

class _Mp4:
    def __init__(self, path, size, fps):
        import imageio_ffmpeg

        self.path = Path(path)
        self.tmp = self.path.with_name(f".{self.path.stem}.tmp-{os.getpid()}.mp4")
        self.writer = imageio_ffmpeg.write_frames(
            str(self.tmp), tuple(size), fps=fps, codec="libx264", quality=None, macro_block_size=1,
            pix_fmt_in="rgb24", pix_fmt_out="yuv420p", ffmpeg_log_level="error",
            output_params=["-crf", "20", "-preset", "medium", "-movflags", "+faststart"])
        self.writer.send(None)
        self.frames = 0

    def write(self, rgb, count):
        data = rgb.tobytes()
        for _ in range(count):
            self.writer.send(data)
        self.frames += count

    def close(self):
        self.writer.close()
        os.replace(self.tmp, self.path)

    def abort(self):
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001 - the encoder already stopped
            pass
        self.tmp.unlink(missing_ok=True)


def _quantize(rgb):
    from PIL import Image

    return rgb.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)


def _write_gif(path, frames, durations):
    path = Path(path)
    tmp = path.with_name(f".{path.stem}.tmp-{os.getpid()}.gif")
    frames[0].save(tmp, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0,
                   disposal=1, optimize=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------------- plans

def _view_aspect(rect):
    return rect[2] / rect[3]


def _fov(view):
    value = view.document.get("camera_fov_deg")
    return float(value) if isinstance(value, (int, float)) and value > 0 else DEFAULT_FOV_DEG


def _shot(shot_id, run, direction, up, box, distance, **extra):
    return {"id": shot_id, "run": run, "direction": _rounded(direction), "up": _rounded(up),
            "box_min": _rounded(box[0], 6), "box_max": _rounded(box[1], 6),
            "distance_mm": round(float(distance), 6), **extra}


def plan_orbit(cfg, view, source_id, view_rect):
    count = max(2, int(round(cfg["duration_s"] * cfg["fps"])))
    loop = abs(cfg["orbit"]["sweep_deg"]) % 360 == 0
    fractions = path_fractions(count, loop, cfg["easing"])
    poses = orbit_poses(view.axis, cfg["up_axis"], cfg["orbit"], fractions)
    box = _view_box(view, cfg["focus"])
    fov, aspect = _fov(view), _view_aspect(view_rect)
    distance = max(fit_distance(box, d, u, fov, aspect) for d, u, _angle in poses)
    shots = [_shot(f"v{i:04d}", source_id, d, u, box, distance, angle_deg=round(angle, 6))
             for i, (d, u, angle) in enumerate(poses)]
    segments = [{"index": i, "kind": "motion", "shot": shot["id"], "duration_s": 1.0 / cfg["fps"],
                 "fraction": round(fractions[i], 6)} for i, shot in enumerate(shots)]
    return shots, segments, [cfg["focus"]]


def plan_transition(cfg, view, source_id, view_rect):
    count = max(2, int(round(cfg["duration_s"] * cfg["fps"])))
    fractions = path_fractions(count, False, cfg["easing"])
    fov, aspect = _fov(view), _view_aspect(view_rect)
    ends = []
    for pose in (cfg["from"], cfg["to"]):
        direction, up = pose_vectors(view.axis, pose["camera"], cfg["up_axis"])
        box = _view_box(view, pose["focus"])
        ends.append((direction, up, box, fit_distance(box, direction, up, fov, aspect)))
    poses = transition_poses(ends[0], ends[1], fractions)
    shots = [_shot(f"v{i:04d}", source_id, d, u, box, distance) for i, (d, u, box, distance) in enumerate(poses)]
    segments = []
    if cfg["hold_s"] > 0:
        segments.append({"kind": "hold", "shot": shots[0]["id"], "duration_s": cfg["hold_s"], "fraction": 0.0})
    for i, shot in enumerate(shots):
        segments.append({"kind": "motion", "shot": shot["id"], "duration_s": 1.0 / cfg["fps"],
                         "fraction": round(fractions[i], 6)})
    if cfg["hold_s"] > 0:
        segments.append({"kind": "hold", "shot": shots[-1]["id"], "duration_s": cfg["hold_s"], "fraction": 1.0})
    for index, segment in enumerate(segments):
        segment["index"] = index
    return shots, segments, [cfg["from"]["focus"], cfg["to"]["focus"]]


def plan_playback(cfg, study_dir, study, view_rect, views):
    """Shots (one camera, one framing box: the union over the samples) and one segment per ordered sample."""
    order = playback_order(study["samples"], study["order_by"])
    shots: list[dict] = []
    segments = []
    if view_rect is not None and views:
        boxes = [_view_box(views[s["sample_id"]], cfg["focus"]) for s in order if s["sample_id"] in views]
        box = rv.union_box(boxes)
        reference = views[next(s["sample_id"] for s in order if s["sample_id"] in views)]
        direction, up = pose_vectors(reference.axis, cfg["camera"], cfg["up_axis"])
        distance = fit_distance(box, direction, up, _fov(reference), _view_aspect(view_rect))
        for sample in order:
            if sample["sample_id"] in views:
                shots.append(_shot(f"v-{sample['sample_id']}", f"sample:{sample['sample_id']}", direction, up, box,
                                   distance))
    by_sample = {shot["run"].split(":", 1)[1]: shot["id"] for shot in shots}
    for index, sample in enumerate(order):
        segments.append({"index": index, "kind": "sample", "sample_id": sample["sample_id"],
                         "shot": by_sample.get(sample["sample_id"]), "duration_s": cfg["hold_s"],
                         "recorded_index": sample["recorded_index"]})
    return shots, segments, order


def load_sequence(path):
    """A saved ``sequence.json`` (schema ``os-optics-kit.animation-sequence``) for replay."""
    path = Path(path)
    if not path.is_file():
        raise pr.PresentationError("SOURCE_MISSING", f"sequence {path.name} does not exist",
                                   recovery="point sequence at a sequence.json written by an earlier build, relative "
                                   "to presentation.json")
    document = wc.read_json(path)
    if document.get("schema") != SEQUENCE_SCHEMA or document.get("schema_version") != SCHEMA_VERSION:
        raise pr.PresentationError("SETTING_INVALID", f"{path.name} is not an animation sequence "
                                   f"({SEQUENCE_SCHEMA} v{SCHEMA_VERSION})", key="sequence")
    return document


# --------------------------------------------------------------------------------------------- the handler

def _both(fn):
    return {language: fn(language) for language in pr.LANGUAGES}


def _focus_name(theme, focus, language):
    if focus in ("optics", "scene"):
        return text(f"focus.{focus}", language)
    return dataclasses.replace(theme, language=language).label(focus)


def _num(value):
    return pr.fmt_number(value, 4)


def _texts(cfg, theme, source, study=None, order=None):
    """Automatic title and caption in both languages."""
    def source_name(language):
        return pr.localized(source.label, language, source.id)

    if cfg["mode"] == "orbit":
        orbit = cfg["orbit"]
        title = _both(lambda lang: text("title.orbit", lang, source=source_name(lang)))
        key = "caption.orbit_up" if orbit["about"] == "up" else "caption.orbit_axis"
        caption = _both(lambda lang: text(key, lang, source=source_name(lang), sweep=_num(abs(orbit["sweep_deg"])),
                                          elevation=_num(orbit["elevation_deg"]), angle=_num(orbit["angle_deg"]),
                                          duration=_num(cfg["duration_s"]), fps=_num(cfg["fps"])))
        return title, caption
    if cfg["mode"] == "transition":
        def pose(lang, end):
            camera = cfg[end]["camera"]
            return text("pose", lang, focus=_focus_name(theme, cfg[end]["focus"], lang), azimuth=_num(camera[0]),
                        elevation=_num(camera[1]))

        title = _both(lambda lang: text("title.transition", lang, source=source_name(lang),
                                        start=_focus_name(theme, cfg["from"]["focus"], lang),
                                        end=_focus_name(theme, cfg["to"]["focus"], lang)))
        caption = _both(lambda lang: text("caption.transition", lang, source=source_name(lang),
                                          start=pose(lang, "from"), end=pose(lang, "to"),
                                          duration=_num(cfg["duration_s"]), fps=_num(cfg["fps"])))
        return title, caption
    parameter = next(p for p in study["parameters"] if p["name"] == study["order_by"])
    values = [s["parameters"][parameter["name"]]["value"] for s in order]
    numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    missing = sum(1 for s in order if not s["has_run"]) if cfg["panels"] != "plot" else 0
    passed = sum(1 for s in order if s["outcome"] == "PASS")

    def caption(lang):
        local = dataclasses.replace(theme, language=lang)
        low = _with_unit(local, min(numeric), parameter.get("unit")) if numeric else "-"
        high = _with_unit(local, max(numeric), parameter.get("unit")) if numeric else "-"
        pieces = [text("caption.playback", lang, count=len(order), x=local.label(f"param:{parameter['name']}"),
                       low=low, high=high, hold=_num(cfg["hold_s"]), passed=passed)]
        if missing:
            pieces.append(text("caption.playback_missing", lang, count=missing))
        pieces.append(pr.text("caption.recorded_samples", lang))
        return ("" if lang == "zh-CN" else " ").join(pieces)

    title = _both(lambda lang: text("title.playback", lang,
                                    y=dataclasses.replace(theme, language=lang).label(study["y"]),
                                    x=dataclasses.replace(theme, language=lang).label(f"param:{parameter['name']}")))
    return title, _both(caption)


def build_animation(asset: dict, ctx) -> dict:
    """Handler of asset kind ``animation`` (see the module docstring and ``settings()``)."""
    asset_id = asset["id"]
    sequence_doc = None
    if asset.get("sequence"):
        sequence_doc = load_sequence(Path(ctx.spec_dir) / asset["sequence"])
        merged = {**sequence_doc["settings"], **{k: v for k, v in asset.items()
                                                 if k in COMMON_KEYS or k in ("formats", "gif_fps", "source",
                                                                              "panels", "sequence")}}
        cfg = normalize({k: v for k, v in merged.items() if v is not None})
    else:
        cfg = normalize(asset)
    theme = ctx.asset_theme(asset)
    size = check_size(tuple(theme.size), cfg["formats"])
    media_modules(cfg["formats"])
    source = ctx.source(cfg["source"])
    want = "study" if cfg["mode"] == "playback" else "run"
    if source.kind != want:
        raise pr.PresentationError("SOURCE_MISSING", f"asset {asset_id}: mode {cfg['mode']} needs a saved {want}; "
                                   f"source {source.id!r} is a {source.kind}",
                                   recovery="use mode playback for a study and orbit or transition for a run"
                                   if want == "run" else "point the source at a study directory")
    out_dir = Path(ctx.asset_dir(asset_id))
    started = _clock()
    plan_layout = layout(theme, size, cfg["mode"], cfg.get("panels"), theme.title, theme.captions == "figure")
    view_rect = plan_layout["view"]
    study: Any = None
    order: Any = None
    views: dict[str, Any] = {}
    jobs = []
    if cfg["mode"] == "playback":
        study = study_samples(source.path, cfg["y"], cfg["order_by"])
        if view_rect is not None:
            for sample in study["samples"]:
                if sample["has_run"]:
                    views[sample["sample_id"]] = _load_view(Path(source.path) / sample["run_dir"])
        shots, segments, order = plan_playback(cfg, source.path, study, view_rect, views)
        if sequence_doc is not None:
            shots, segments = _replay(sequence_doc, cfg, study, views if view_rect is not None else None, shots)
            order = [next(s for s in study["samples"] if s["sample_id"] == seg["sample_id"]) for seg in segments]
        focuses = [cfg["focus"]]
        if views:
            any_view = next(iter(views.values()))
            visible = visibility(any_view, focuses)
        else:
            visible = "optics"
        samples = {s["sample_id"]: s for s in study["samples"]}
        for shot in shots:
            sample = samples[shot["run"].split(":", 1)[1]]
            jobs.append((shot, Path(source.path) / sample["run_dir"], views[sample["sample_id"]], sample["identity"]))
    else:
        view = _load_view(source.path)
        planner = plan_orbit if cfg["mode"] == "orbit" else plan_transition
        shots, segments, focuses = planner(cfg, view, source.id, view_rect)
        if sequence_doc is not None:
            shots, segments = _replay(sequence_doc, cfg, None, None)
        visible = visibility(view, focuses)
        jobs = [(shot, source.path, view, source.identity) for shot in shots]
    duration = assign_timeline(segments, cfg["fps"])
    automatic_title, automatic_caption = _texts(cfg, theme, source, study, order)
    title = pr.title_text(asset, theme, automatic_title) if theme.title else ""
    caption = pr.caption_text(asset, theme, automatic_caption)
    background = theme.render_background()
    total_work = len(segments) + len(jobs)

    def report(done, total):
        ctx.progress(asset_id, done, total_work)

    # text first: a missing font is refused before any view is captured
    model = plot_model(theme, study, order) if cfg["mode"] == "playback" and plan_layout["plot"] else None
    overlays: dict[Any, Any] = {}
    strings = [title, caption]
    labels = []
    if cfg["mode"] == "playback":
        for position, segment in enumerate(segments):
            sample = order[position]
            label = sample_label(theme, sample, study["parameters"], study["y"])
            note = text("note.no_run", theme.language) if view_rect is not None and not segment["shot"] else None
            lines = [label, SEPARATOR.join([theme.text("caption.recorded_samples"),
                                            text("label.sample", theme.language, index=position + 1,
                                                 count=len(segments))])]
            segment["label"] = label
            segment["values"] = {"parameters": sample["parameters"], "y": sample["y"], "outcome": sample["outcome"]}
            labels.append((position, lines, note))
            strings.extend(lines + ([note] if note else []))
        if model is not None:
            strings.extend([model["x_label"], model["y_label"], theme.text("note.no_exact_row")])
            strings.extend(legend_labels(theme, model, list(MARKERS)))
    else:
        figure_lines = [caption] if theme.captions == "figure" else []
        strings.extend(figure_lines)
    pr.check_glyphs([s for s in strings if s], theme)
    view_size = [view_rect[2], view_rect[3]] if view_rect else None
    captured = reused = 0
    pngs: dict[str, Path] = {}
    if jobs:
        pngs, captured, reused = capture_views(ctx, asset_id, jobs, view_size, background, cfg["beams"], visible,
                                               progress=report)
    ctx.progress(asset_id, len(jobs), total_work)

    # overlays: one for camera motions, one per sample for a playback
    if cfg["mode"] == "playback":
        for position, lines, note in labels:
            overlays[position] = draw_overlay(theme, plan_layout, title, lines, plot=model, current=position,
                                              note=note)
    else:
        overlays[None] = draw_overlay(theme, plan_layout, title, [caption] if theme.captions == "figure" else [])

    def overlay_key(segment):
        return segment["index"] if cfg["mode"] == "playback" else None

    still = _still_index(cfg, segments, order, study)
    base = _base_color(theme)
    files = {}
    for fmt in MEDIA_FORMATS:  # formats no longer requested do not linger next to the new ones
        stale = out_dir / f"{asset_id}.{fmt}"
        if fmt not in cfg["formats"] and stale.exists():
            stale.unlink()
    mp4 = _Mp4(out_dir / f"{asset_id}.mp4", size, cfg["fps"]) if "mp4" in cfg["formats"] else None
    gif_plan = gif_timeline(segments, cfg["gif_fps"], key=lambda s: (s["shot"], overlay_key(s))) \
        if "gif" in cfg["formats"] else []
    gif_wanted = {index for index, _ms in gif_plan}
    gif_frames: dict[int, Any] = {}
    still_path = out_dir / f"{asset_id}.png"
    try:
        for segment in segments:
            frame = compose(size, base, pngs.get(segment["shot"]) if segment["shot"] else None, view_rect,
                            overlays[overlay_key(segment)])
            if segment["index"] == still:
                frame.save(still_path, format="PNG")
            if mp4 is not None or segment["index"] in gif_wanted:
                rgb = _flatten(frame)
                if mp4 is not None and segment["frames"]:
                    mp4.write(rgb, segment["frames"])
                if segment["index"] in gif_wanted:
                    gif_frames[segment["index"]] = _quantize(rgb)
            if segment["index"] % max(1, len(segments) // 10) == 0:
                ctx.progress(asset_id, len(jobs) + segment["index"] + 1, total_work)
        if mp4 is not None:
            mp4.close()
            files["mp4"] = out_dir / f"{asset_id}.mp4"
    except BaseException:
        if mp4 is not None:
            mp4.abort()
        raise
    if gif_plan:
        _write_gif(out_dir / f"{asset_id}.gif", [gif_frames[i] for i, _ms in gif_plan], [ms for _i, ms in gif_plan])
        files["gif"] = out_dir / f"{asset_id}.gif"
    ctx.progress(asset_id, total_work, total_work)

    data_files = []
    rel = lambda path: pr.portable_path(path, ctx.out_dir)  # noqa: E731
    if study is not None:
        data_path = out_dir / DATA_FILE
        wc.write_text_atomic(data_path, pr.manifest_text(_data_document(source, study, order, cfg)))
        data_files.append(data_path)
    sequence = {
        "schema": SEQUENCE_SCHEMA, "schema_version": SCHEMA_VERSION, "asset": asset_id, "mode": cfg["mode"],
        "source": {"id": source.id, "kind": source.kind, "label": source.label},
        "size_px": list(size), "fps": cfg["fps"], "gif_fps": cfg["gif_fps"], "duration_s": duration,
        "frame_count": sum(s["frames"] for s in segments), "language": theme.language,
        "background": theme.background, "title": title, "caption": caption,
        "settings": {k: v for k, v in cfg.items() if k != "sequence"},
        "layout": {"view_rect_px": view_rect, "plot_rect_px": plan_layout["plot"], "header_px": plan_layout["header"],
                   "footer_px": plan_layout["footer"]},
        "view": None if view_rect is None else {"size_px": view_size, "render_background": background,
                                                "visibility": visible, "beams": cfg["beams"]},
        "shots": [{k: v for k, v in shot.items() if k != "fingerprint"} | {"cache_key": shot.get("fingerprint")}
                  for shot in shots],
        "segments": segments, "still_segment": still,
        "gif": {"frames": len(gif_plan), "durations_ms": [ms for _i, ms in gif_plan],
                "segments": [i for i, _ms in gif_plan]} if gif_plan else None,
        "outputs": {"image": rel(still_path), **{k: rel(v) for k, v in files.items()},
                    **({"data": rel(data_files[0])} if data_files else {})},
        "replayed_from": Path(asset["sequence"]).as_posix() if asset.get("sequence") else None,
    }
    sequence_path = out_dir / SEQUENCE_FILE
    wc.write_text_atomic(sequence_path, pr.manifest_text(sequence))
    details = {"mode": cfg["mode"], "frames": sequence["frame_count"], "fps": cfg["fps"], "duration_s": duration,
               "size_px": list(size), "formats": cfg["formats"], "segments": len(segments), "shots": len(shots),
               "views_captured": captured, "views_reused": reused, "still_segment": still,
               "seconds": round(_clock() - started, 2)}
    if gif_plan:
        details["gif"] = {"fps": cfg["gif_fps"], "frames": len(gif_plan)}
    if cfg["mode"] == "orbit":
        details["orbit"] = cfg["orbit"]
        details["distance_mm"] = shots[0]["distance_mm"]
    elif cfg["mode"] == "transition":
        details["from"], details["to"] = cfg["from"], cfg["to"]
        details["hold_s"] = cfg["hold_s"]
    else:
        parameter = next(p for p in study["parameters"] if p["name"] == study["order_by"])
        details.update({"samples": len(order), "parameter": {"name": parameter["name"], "unit": parameter.get("unit")},
                        "y": {"column": study["y"], "unit": study["unit"]}, "panels": cfg["panels"],
                        "hold_s": cfg["hold_s"], "order": [s["sample_id"] for s in order],
                        "missing_samples": [s["sample_id"] for s in order if not s["has_run"]],
                        "no_value_samples": [s["sample_id"] for s in order if not s["y"]["exact"]],
                        "by_outcome": {code: sum(1 for s in order if s["outcome"] == code) for code in MARKERS
                                       if any(s["outcome"] == code for s in order)}})
    return {"files": {"image": still_path, **files, "sequence": sequence_path}, "data_files": data_files,
            "title": automatic_title, "caption": automatic_caption, "sources": [source.id], "depends_on": [],
            "details": details}


def _clock():
    import time

    return time.perf_counter()


def _still_index(cfg, segments, order, study):
    """Representative still: an orbit's start, a transition's end pose, a playback's best (else first) sample."""
    if cfg["mode"] == "orbit":
        return 0
    if cfg["mode"] == "transition":
        return segments[-1]["index"]
    with_run = [i for i, s in enumerate(order) if s["has_run"] or cfg["panels"] == "plot"]
    passing = [i for i in with_run if order[i]["outcome"] == "PASS" and order[i]["y"]["exact"]]
    if passing and study["bounds"] and None not in (study["bounds"].get("minimum"), study["bounds"].get("maximum")):
        middle = (study["bounds"]["minimum"] + study["bounds"]["maximum"]) / 2
        return min(passing, key=lambda i: abs(order[i]["y"]["value"] - middle))
    return (passing or with_run or [0])[0]


def _replay(document, cfg, study, views, planned=None):
    """Shots and segments of a saved sequence (camera vectors, framing, eye distance, order and durations)."""
    if document.get("mode") != cfg["mode"]:
        raise pr.PresentationError("SETTING_INVALID", f"the sequence is a {document.get('mode')} sequence, the asset "
                                   f"a {cfg['mode']}", key="sequence")
    shots = [{k: v for k, v in shot.items() if k != "cache_key"} for shot in document["shots"]]
    known = {shot["id"] for shot in shots}
    segments = []
    for index, segment in enumerate(document["segments"]):
        if segment.get("shot") is not None and segment["shot"] not in known:
            raise pr.PresentationError("SETTING_INVALID", f"sequence segment {index} names an unknown shot",
                                       key="sequence")
        kept = {k: segment[k] for k in ("kind", "shot", "duration_s", "fraction", "sample_id", "recorded_index")
                if k in segment}
        segments.append({**kept, "index": index})
    if study is not None:
        ids = {s["sample_id"] for s in study["samples"]}
        lost = [s["sample_id"] for s in segments if s.get("sample_id") not in ids]
        if lost:
            raise pr.PresentationError("SOURCE_MISSING", f"the study has no samples {lost[:5]}",
                                       recovery="replay the sequence with the study it was built from")
        if views is None:  # no view panel now: the sample order and timing are replayed, no camera
            shots = []
        elif not shots:  # the saved sequence had no view panel: the current camera settings frame the views
            shots = list(planned or [])
        by_sample = {shot["run"].split(":", 1)[1]: shot["id"] for shot in shots}
        for segment in segments:
            segment["shot"] = by_sample.get(segment["sample_id"])
        for shot in shots:
            sample_id = shot["run"].split(":", 1)[1]
            if sample_id not in (views or {}):
                raise pr.PresentationError("SOURCE_MISSING", f"sample {sample_id} has no saved run",
                                           recovery="replay the sequence with the study it was built from")
    return shots, segments


def _data_document(source, study, order, cfg):
    """The recorded values a playback shows, in playback order (``data.json``)."""
    return {"schema": DATA_SCHEMA, "schema_version": SCHEMA_VERSION,
            "source": {"id": source.id, "label": source.label, "summary_sha256": study["summary_sha256"]},
            "parameters": [{"name": p["name"], "unit": p.get("unit")} for p in study["parameters"]],
            "order_by": study["order_by"], "y": {"column": study["y"], "unit": study["unit"]},
            "bounds": study["bounds"], "hold_s": cfg["hold_s"],
            "samples": [{"order": position, "recorded_index": s["recorded_index"], "sample_id": s["sample_id"],
                         "parameters": s["parameters"], "y": s["y"], "outcome": s["outcome"],
                         "has_run": s["has_run"], "result_hash": s["identity"].get("result_hash")}
                        for position, s in enumerate(order)]}
