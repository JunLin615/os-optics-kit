# present: animations

Read this page when the user asks for a short animation. Add an asset of kind `animation` to
`presentation.json` (or `present init ... --animate`), then `present build`. Nothing is simulated.

## Modes

```json
{"id": "orbit", "kind": "animation", "mode": "orbit", "source": "nominal",
 "orbit": {"about": "up", "elevation_deg": 20, "sweep_deg": 360}, "duration_s": 6}
{"id": "zoom-l2", "kind": "animation", "mode": "transition", "source": "nominal",
 "from": {"camera": [55, 22], "focus": "optics"}, "to": {"camera": [35, 12], "focus": "element:L2"}}
{"id": "l2-playback", "kind": "animation", "mode": "playback", "source": "l2-sweep",
 "y": "target:expanded_waist", "panels": "view+plot", "hold_s": 1.0}
```

- `orbit`: the camera turns about the vertical (`about: "up"`) or the optical axis
  (`"optical_axis"`, `angle_deg` to the axis) at one distance, so the scale stays constant.
- `transition`: a smooth move between two camera poses, e.g. into one element (`element:<id>`).
- `playback`: one segment per saved sample of a sweep study, ordered by the swept parameter
  (`order_by`). Every frame shows the recorded parameter value, the `y` value with units and the
  outcome; views share one camera and framing, plots fixed axes. Each segment shows one saved
  sample with its recorded values, and the frames carry the label "Recorded parameter samples".

Common fields: `formats` (`["mp4", "gif"]`), `fps` (24), `gif_fps` (at most 15), `duration_s`,
`hold_s`, `beams`, `easing`. Size, language and background come from the theme
(`assets.<id>.theme.size` for one animation). `present settings` lists every field.

## Outputs

`assets/<id>/<id>.mp4`, `<id>.gif`, a still `<id>.png` (the preview), `sequence.json` (camera
poses, framing, sample order, durations and labels) and, for playback, `data.json` (the values
shown). Set `"sequence": "assets/<id>/sequence.json"` on an asset to replay the same motion or
sample order with other text or styling.

## Reuse and progress

Rendered views are cached in `.cache/` of the output folder: a rebuild that changes only language,
labels, captions or formats encodes again without opening the browser. Long exports print coarse
progress lines (`{"progress": {"asset", "done", "total"}}`) on stderr.

## Requirements and recovery

- Views need Chrome or Chromium and `os-optics-kit[render]` (`BROWSER_NOT_FOUND`).
- MP4 needs `python -m pip install "os-optics-kit[media]"` (`MEDIA_EXTRA_MISSING`) and an even width
  and height; GIF needs only the plot extra.
- A study sample without a saved run or exact value stays in the playback, labelled as such.
