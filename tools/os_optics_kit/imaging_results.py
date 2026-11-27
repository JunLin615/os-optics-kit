"""Read an os-optics imaging analysis: one reader for every kit tool that uses its MTF curves.

The document is the output of ``os-optics results imaging RUN --monitor PLANE`` (or of
``RunResults.imaging(monitor, request)``): schema ``opensci.analysis_result``, analysis ``IMAGING``. The Engine states
one line per source and spectral line, ``<source>#<spectral index>`` (``SRC#0``, ``SRC1#2``), and for each line the
curves ``mtf_<direction>``, ``diffraction_limited_mtf_<direction>`` and ``geometric_mtf_<direction>`` with
``direction`` ``tangential`` or ``sagittal``, on that line's own frequency axis ``<line>.frequencies_cyc_per_mm``.

Nothing is computed here: the numbers are the Engine's, read as stored. What this module adds is the rule for when a
stored array is a curve:

* a curve is **usable** when its quantity's ``state`` is ``KNOWN`` or ``KNOWN_ZERO`` and its array exists. The Engine
  keeps arrays for quantities it declines (a geometric MTF outside its regime, a PSF window too small, a stand-in
  pupil): their quantity is ``OUT_OF_MODEL_SCOPE`` or ``UNKNOWN``, and such an array is never returned as a curve
  (``values`` is ``None``, ``reason`` and ``message`` say why; the raw array stays in the document);
* a usable curve whose convergence check failed (``convergence.converged`` false) keeps its values; its
  ``value_kind`` is ``QUANTITY_UNKNOWN`` and its ``status`` ``NOT_CONVERGED``;
* a ``None`` inside a usable array is a frequency beyond the PSF map's Nyquist frequency (``BEYOND_NYQUIST``).

``value_kind`` follows the evaluation adapter's rules (:func:`quantity_kind`): ``EXACT``, ``BOUNDED``,
``QUANTITY_UNKNOWN``, ``OUTSIDE_MODEL`` or ``NO_VALUE``.

Use :func:`load` for a document (a path, a parsed dict or the Engine's result object), :func:`lines` for the analysed
lines and :func:`curves` / :func:`curve` for the curves. :func:`value_at` reads a usable curve at a frequency of its own
axis (no interpolation). Pure Python: no Engine, numpy or matplotlib import.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = "opensci.analysis_result"
ANALYSIS = "IMAGING"
DIRECTIONS = ("tangential", "sagittal")
#: Curve kinds, in the order tables list them.
CURVE_KINDS = ("mtf", "diffraction_limited_mtf", "geometric_mtf")
#: Engine states whose stored value is the model's value.
USABLE_STATES = ("KNOWN", "KNOWN_ZERO")
FREQUENCY_SUFFIX = ".frequencies_cyc_per_mm"
#: Frequency match of :func:`value_at`: relative to max(1, frequency) cycles/mm.
FREQUENCY_MATCH = 1e-9

# Statuses of a curve and of one of its values (the status column of the kit's MTF tables).
KNOWN = "KNOWN"
NOT_CONVERGED = "NOT_CONVERGED"
BEYOND_NYQUIST = "BEYOND_NYQUIST"
ABSENT = "ABSENT"


class ImagingDocumentError(ValueError):
    """A document that is not an os-optics imaging analysis (``code`` ``IMAGING_INVALID``)."""

    code = "IMAGING_INVALID"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


def _get(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def basis_facts(entry: Any) -> tuple[str | None, bool]:
    """``(kind, a validity check failed)`` of a value-basis entry (``(None, False)`` without one)."""
    if entry is None:
        return None, False
    kind = _get(entry, "kind")
    kind = None if kind is None else str(getattr(kind, "value", kind))
    failed = any(_get(check, "within") is False for check in _get(entry, "validity") or ())
    return kind, failed


def quantity_kind(state: str, value: Any, basis_kind: str | None, basis_failed: bool, converged: bool | None) -> str:
    """The kind of an analysis quantity's number: ``NO_VALUE`` without a finite number; ``OUTSIDE_MODEL`` for
    ``OUT_OF_MODEL_SCOPE`` or a failed validity check; ``BOUNDED`` for a bound; ``QUANTITY_UNKNOWN`` for ``UNKNOWN``,
    an ``ESTIMATE`` basis or a failed convergence check; ``EXACT`` for ``KNOWN`` / ``KNOWN_ZERO``."""
    if not _finite(value):
        return "NO_VALUE"
    if state == "OUT_OF_MODEL_SCOPE" or basis_failed:
        return "OUTSIDE_MODEL"
    if state == "BOUNDED" or basis_kind in ("LOWER_BOUND", "UPPER_BOUND"):
        return "BOUNDED"
    if state == "UNKNOWN" or basis_kind == "ESTIMATE" or converged is False:
        return "QUANTITY_UNKNOWN"
    if state in USABLE_STATES:
        return "EXACT"
    return "NO_VALUE"


def _decode(data: bytes) -> Any:
    # A Windows shell redirect ('>') writes UTF-16 with a byte-order mark.
    return json.loads(data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"))


def load(source: Any) -> dict[str, Any]:
    """The imaging document as a dict: from a path, a parsed dict, or the Engine's ``AnalysisResultV1``.

    Raises :class:`ImagingDocumentError` when it is not an imaging analysis."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            document = _decode(path.read_bytes())
        except OSError as exc:
            raise ImagingDocumentError(f"{path}: {exc}") from None
        except (UnicodeDecodeError, ValueError) as exc:
            raise ImagingDocumentError(f"{path.name} is not JSON: {exc}") from None
    elif isinstance(source, dict):
        document = source
    else:
        dump = getattr(source, "model_dump", None)
        if not callable(dump):
            raise ImagingDocumentError(f"not an imaging analysis: {type(source).__name__}")
        document = dump(mode="json")
    if not isinstance(document, dict) or document.get("schema") != SCHEMA or document.get("analysis") != ANALYSIS \
            or not isinstance(document.get("arrays"), dict) or not isinstance(document.get("quantities"), list):
        raise ImagingDocumentError("not the output of 'os-optics results imaging' (schema opensci.analysis_result, "
                                   "analysis IMAGING, with quantities and arrays)")
    return document


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(document: dict[str, Any]) -> dict[str, Any]:
    """The analysis identity: ``identity.physical`` (engine build, input hash, run result hash, settings hash), the
    analysis version, the execution outcome, the monitor and the request as the Engine states it."""
    settings = document.get("settings") or {}
    return {**((document.get("identity") or {}).get("physical") or {}),
            "analysis_version": document.get("analysis_version"),
            "execution_outcome": (document.get("execution") or {}).get("outcome"),
            "monitor": settings.get("monitor"),
            "request": settings.get("request")}


def _quantities(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(q.get("quantity_id")): q for q in document.get("quantities") or [] if isinstance(q, dict)}


def _split(line_id: str) -> tuple[str, int | None]:
    source, _, index = line_id.rpartition("#")
    try:
        return source, int(index)
    except ValueError:
        return line_id, None


def lines(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every analysed line, sorted by source and spectral index: ``line_id``, ``source_id``, ``spectral_index``,
    ``wavelength_nm``, ``observation_id``, ``frequencies_cyc_per_mm`` (or ``None``), ``rays`` (the Engine's
    ``<line>.rays`` record or ``None``) and ``field_state`` / ``field_reason`` for a line the Engine reports without
    an arrival (``<line>.field``, ``NO_ARRIVAL``).

    A requested source that is not a beam source (``<source>.field``, ``NOT_APPLICABLE``) is not a line: see
    :func:`unanalysed_fields`."""
    arrays = document.get("arrays") or {}
    found: dict[str, dict[str, Any]] = {}
    for qid, q in _quantities(document).items():
        prefix, _, name = qid.partition(".")
        if "#" not in prefix:
            continue
        scope = q.get("scope") or {}
        entry = found.setdefault(prefix, {"line_id": prefix, "source_id": scope.get("source_id"),
                                          "wavelength_nm": scope.get("wavelength_nm"),
                                          "observation_id": scope.get("observation_id")})
        if name == "field":
            entry["field_state"], entry["field_reason"] = q.get("state"), q.get("reason")
    for key in arrays:
        if key.endswith(FREQUENCY_SUFFIX):
            found.setdefault(key[:-len(FREQUENCY_SUFFIX)], {"line_id": key[:-len(FREQUENCY_SUFFIX)]})
    out = []
    for line_id, entry in found.items():
        source, index = _split(line_id)
        frequencies = arrays.get(line_id + FREQUENCY_SUFFIX)
        rays = arrays.get(line_id + ".rays")
        out.append({"line_id": line_id, "source_id": entry.get("source_id") or source, "spectral_index": index,
                    "wavelength_nm": entry.get("wavelength_nm"), "observation_id": entry.get("observation_id"),
                    "frequencies_cyc_per_mm": list(frequencies) if isinstance(frequencies, list) else None,
                    "rays": rays if isinstance(rays, dict) else None,
                    "field_state": entry.get("field_state"), "field_reason": entry.get("field_reason")})
    out.sort(key=lambda e: (str(e["source_id"]), -1 if e["spectral_index"] is None else e["spectral_index"]))
    return out


def unanalysed_fields(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Requested fields the Engine did not analyse as a line: ``<source>.field`` quantities (for example
    ``NOT_APPLICABLE`` / ``FIELD_NOT_A_BEAM_SOURCE``), with their state, reason and message."""
    out = []
    for qid, q in _quantities(document).items():
        prefix, _, name = qid.partition(".")
        if name == "field" and "#" not in prefix:
            out.append({"source_id": prefix, "state": q.get("state"), "reason": q.get("reason"),
                        "message": q.get("message")})
    return sorted(out, key=lambda e: e["source_id"])


def curve(document: dict[str, Any], line_id: str, kind: str = "mtf", direction: str = "tangential", *,
          _quantities_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """One curve of one line: ``line_id``, ``source_id``, ``wavelength_nm``, ``kind``, ``direction``,
    ``quantity_id``, ``unit``, ``state``, ``reason``, ``message``, ``model_basis`` (the Engine's model id),
    ``value_basis``, ``converged``, ``convergence_difference``, ``value_kind``, ``status``, ``usable``,
    ``frequencies_cyc_per_mm`` and ``values``.

    ``values`` is the Engine's array (``None`` entries beyond the Nyquist frequency) when the curve is usable, else
    ``None``; ``status`` is ``KNOWN``, ``NOT_CONVERGED``, ``ABSENT`` (no such quantity) or the Engine's state."""
    if kind not in CURVE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(CURVE_KINDS)}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {', '.join(DIRECTIONS)}")
    arrays = document.get("arrays") or {}
    quantities = _quantities_by_id if _quantities_by_id is not None else _quantities(document)
    qid = f"{line_id}.{kind}_{direction}"
    q = quantities.get(qid)
    source, _ = _split(line_id)
    frequencies = arrays.get(line_id + FREQUENCY_SUFFIX)
    record: dict[str, Any] = {"line_id": line_id, "source_id": source, "wavelength_nm": None, "kind": kind,
                              "direction": direction, "quantity_id": qid, "unit": None, "state": None,
                              "reason": None, "message": None, "model_basis": None, "value_basis": None,
                              "converged": None, "convergence_difference": None, "value_kind": "NO_VALUE",
                              "status": ABSENT, "usable": False,
                              "frequencies_cyc_per_mm": list(frequencies) if isinstance(frequencies, list) else None,
                              "values": None}
    if q is None:
        record["reason"] = "ABSENT"
        record["message"] = f"the imaging analysis states no {qid}"
        return record
    scope = q.get("scope") or {}
    convergence = q.get("convergence") or {}
    state = str(q.get("state"))
    record.update(source_id=scope.get("source_id") or source, wavelength_nm=scope.get("wavelength_nm"),
                  unit=q.get("unit"), state=state, reason=q.get("reason"), message=q.get("message"),
                  model_basis=q.get("basis"), value_basis=q.get("value_basis"),
                  converged=convergence.get("converged"), convergence_difference=convergence.get("difference"))
    raw = arrays.get(q.get("values_ref") or qid)
    usable = state in USABLE_STATES and isinstance(raw, list) and isinstance(frequencies, list) \
        and len(raw) == len(frequencies)
    basis_kind, basis_failed = basis_facts(q.get("value_basis"))
    probe = next((v for v in raw if _finite(v)), None) if isinstance(raw, list) else None
    record["value_kind"] = quantity_kind(state, probe, basis_kind, basis_failed, record["converged"])
    if not usable:
        record["status"] = state
        if state in USABLE_STATES and record["reason"] is None:
            record["reason"] = "NO_CURVE"
            record["message"] = f"{qid} is {state} but the analysis holds no curve of the frequency axis's length"
        return record
    assert isinstance(raw, list)
    record.update(usable=True, values=list(raw), status=NOT_CONVERGED if record["converged"] is False else KNOWN)
    return record


def curves(document: dict[str, Any], *, kinds: Sequence[str] = CURVE_KINDS,
           directions: Sequence[str] = DIRECTIONS, line_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """:func:`curve` for every line (or ``line_ids``) x kind x direction, in that order."""
    quantities = _quantities(document)
    chosen = [e["line_id"] for e in lines(document)] if line_ids is None else list(line_ids)
    return [curve(document, line_id, kind, direction, _quantities_by_id=quantities)
            for line_id in chosen for kind in kinds for direction in directions]


def value_status(record: dict[str, Any], index: int) -> tuple[float | None, str]:
    """``(value, status)`` of a curve at one index of its frequency axis: the value when the curve is usable and the
    entry is finite (status ``KNOWN`` or ``NOT_CONVERGED``), else ``None`` with ``BEYOND_NYQUIST`` (a null entry of a
    usable curve) or the curve's status."""
    values = record.get("values")
    if not record.get("usable") or values is None:
        return None, str(record.get("status"))
    value = values[index] if 0 <= index < len(values) else None
    if not isinstance(value, (int, float)) or not _finite(value):
        return None, BEYOND_NYQUIST
    return float(value), str(record["status"])


def frequency_index(frequencies: Sequence[Any] | None, frequency: float) -> int | None:
    """The index of ``frequency`` on a frequency axis (within :data:`FREQUENCY_MATCH`), or ``None``."""
    for i, f in enumerate(frequencies or ()):
        if _finite(f) and abs(float(f) - float(frequency)) <= FREQUENCY_MATCH * max(1.0, abs(float(frequency))):
            return i
    return None


def value_at(record: dict[str, Any], frequency: float) -> tuple[float | None, str]:
    """``(value, status)`` of a curve at a frequency of its own axis; ``(None, "FREQUENCY_NOT_SAMPLED")`` when the
    axis does not hold it (no interpolation: request the frequency in ``frequencies_cyc_per_mm``)."""
    index = frequency_index(record.get("frequencies_cyc_per_mm"), frequency)
    if index is None:
        if not record.get("usable"):
            return None, str(record.get("status"))
        return None, "FREQUENCY_NOT_SAMPLED"
    return value_status(record, index)


def scalar(document: dict[str, Any], line_id: str, name: str) -> dict[str, Any]:
    """A scalar quantity of a line (``strehl_ratio``, ``geometric_spot_rms_radius``, ``image_space_na``, ...):
    ``quantity_id``, ``value`` (``None`` unless the state is usable), ``unit``, ``state``, ``reason``, ``message``,
    ``value_kind`` and ``converged``."""
    q = _quantities(document).get(f"{line_id}.{name}")
    if q is None:
        return {"quantity_id": f"{line_id}.{name}", "value": None, "unit": None, "state": None, "reason": "ABSENT",
                "message": None, "value_kind": "NO_VALUE", "converged": None}
    state = str(q.get("state"))
    convergence = q.get("convergence") or {}
    value = q.get("value")
    basis_kind, basis_failed = basis_facts(q.get("value_basis"))
    kind = quantity_kind(state, value, basis_kind, basis_failed, convergence.get("converged"))
    return {"quantity_id": q.get("quantity_id"), "value": value if state in USABLE_STATES and _finite(value) else None,
            "unit": q.get("unit"), "state": state, "reason": q.get("reason"), "message": q.get("message"),
            "value_kind": kind, "converged": convergence.get("converged")}


__all__ = ["ABSENT", "ANALYSIS", "BEYOND_NYQUIST", "CURVE_KINDS", "DIRECTIONS", "FREQUENCY_MATCH", "ImagingDocumentError",
           "KNOWN", "NOT_CONVERGED", "SCHEMA", "USABLE_STATES", "basis_facts", "curve", "curves", "file_sha256",
           "frequency_index", "identity", "lines", "load", "quantity_kind", "scalar", "unanalysed_fields", "value_at",
           "value_status"]
