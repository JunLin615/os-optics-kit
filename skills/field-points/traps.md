# field-points: four ways a hand-made field point goes wrong

Read this when you build field points yourself, or when a run "has no field effect". The Engine has no field object:
a field point is a source turned to the field. `field-points` builds them so none of these happens; the Engine-side
statement of the method is JunLin615/os-optics#87.

## 1. Shifted, not turned

- Done: the source is moved across the beam and keeps the on-axis quaternion.
- Symptom: the run is `COMPLETED`; the chief ray still lands near the axis while `aoi_deg` changes, or a model-scope
  finding such as `GAUSSIAN_SURFACE_TOO_STRONG` appears. Nothing says the field was not applied.
- Right: turn the emission axis (local +Z) about a design-frame axis across the beam, `q_field = q_turn * q_axis`,
  then place the source so its axis passes the stop centre.

## 2. Turned about the beam's own axis

- Done: the turn is about the source's local +Z (`q_axis * q_turn`, or `transform` with frame `{"component": SRC}`
  and axis [0, 0, 1]).
- Symptom: the quaternion changed, the result is identical to 0 deg (only the polarization axes turned).
- Right: the turn axis is across the beam (`axis x field direction`). `field-points` checks each built source's
  angle to the axis (`FIELD_POINT_NOT_TURNED` would be a bug).

## 3. The plane's aperture clips the off-axis beam

- Done: an image plane sized for the on-axis spot.
- Symptom: the field point has no arrival (it is under the monitor's `outside_active_area`), and the imaging
  analysis with default `fields` silently leaves it out.
- Right: `field-points` widens the plane in the evaluated Project (pose unchanged; `plane.widened`, the declared
  aperture kept beside it), reports `within_declared_aperture` per point and always names its imaging fields.

## 4. `readings` leaves `quantity_state` NOT_COMPUTED

- Done: `os-optics project evaluate --outputs readings` (or `EvaluationSession().evaluate`).
- Symptom: the plane's `quantity_state` is `NOT_COMPUTED` and `power_complete` false, which reads as "not computed"
  although the arrivals (`uv_mm`, `aoi_deg`) are there: the plane's power bounds are the `plane_bounds` capability.
- Right: read arrivals, not `quantity_state`; add `plane_bounds` when the plane's power is wanted.

## Not aimed

A turned source whose axis misses the stop centre is not the field's chief ray: its image height and CRA are wrong
off axis, and the imaging pupil is off-centre. Check `chief_ray.stop_offset_mm` and `aim` (`AIMED`).
