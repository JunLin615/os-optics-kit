"""Optical MTF of an os-optics imaging analysis: one table, one JSON document and one figure per spectral line.

Usage:
  os-optics-kit mtf RUN --monitor PLANE --out DIR [--request FILE] [--configuration ID] [--no-figures]
                    [--theme T] [--set KEY=VALUE ...] [--overwrite]
  os-optics-kit mtf --imaging FILE --out DIR [--run RUN [--configuration ID]] [--no-figures] [--theme T]
                    [--set KEY=VALUE ...] [--overwrite]

Optical MTF only: no pixel aperture, no sampling, no slanted-edge SFR (measured SFR: os-optics-kit sfr; design-side
SFR: JunLin615/os-optics#42). The Engine's tangential and sagittal MTF, its diffraction limit and the geometric MTF
where its regime holds, read as the Engine states them; one curve per spectral line (no polychromatic weighting);
no PASS/FAIL and no targets (MTF constraints belong to os-optics-kit optimize and the evaluation adapter).

RUN mode runs exactly one Engine imaging analysis, RunResults.imaging(PLANE, request) with the request of --request
(the JSON of 'os-optics schema imaging-request'; default: the Engine's), and writes it to DIR/imaging.json, so a
rerun can take --imaging. A run of a Project with configurations needs --configuration ID (its
configurations/<ID> directory). --imaging FILE reads a saved analysis (the output of 'os-optics results imaging' or
DIR/imaging.json) and runs nothing; with --run RUN the document must come from that run (engine_input_hash and
run_result_hash), and only the run's identity is read.

Lines are <source>#<spectral index> (SRC#0); each has its own frequency axis. A curve has values only when the Engine
states it KNOWN (or KNOWN_ZERO); a declined curve (OUT_OF_MODEL_SCOPE, UNKNOWN, NOT_COMPUTED) is reported with its
reason and never drawn; a curve whose convergence check failed keeps its values with status NOT_CONVERGED; a value
beyond the PSF map's Nyquist frequency is empty with BEYOND_NYQUIST. MTF(0) is the PSF window's light share (not
renormalized to 1).

Writes (DIR new or empty, else OUTPUT_EXISTS unless --overwrite):
  mtf.csv    one row per line x direction x frequency: field, source_id, wavelength_nm, direction,
             frequency_cyc_per_mm, mtf, mtf_status, diffraction_limited_mtf, diffraction_limited_mtf_status,
             geometric_mtf, geometric_mtf_status, unit (1: a modulation ratio). An empty value cell has a status
             saying why. Read it back with os_optics_kit.mtf.read_csv.
  mtf.json   schema os-optics-kit.mtf v1: input, identity, conventions, lines (curves, image point), not_analysed,
             counts.
  mtf-<source>-<index>.png   per line (unless --no-figures): T and S MTF, T and S diffraction limit (dashed);
             missing curves are named in the figure. Needs matplotlib: python -m pip install "os-optics-kit[plot]".
  imaging.json   RUN mode only: the Engine's analysis.

Stdout: one compact JSON summary. Exit 0 COMPLETED (every line's tangential and sagittal MTF is KNOWN and converged and
every requested field was analysed), 2 INCOMPLETE (written anyway; mtf.json says what is missing), 4 refused: JSON
error on stderr (BAD_ARGUMENT, OUTPUT_EXISTS, IMAGING_INVALID, MONITOR_NOT_FOUND, CONFIGURATION_REQUIRED,
IMAGING_RUN_MISMATCH, RUN_UNREADABLE, ANALYSIS_REFUSED, PLOT_EXTRA_MISSING, ENGINE_NOT_INSTALLED).

Python:
  from os_optics_kit import mtf
  summary = mtf.analyse("out", run="run", monitor="image_plane", request={"frequencies_cyc_per_mm": [10, 20, 40]})
  document, identity, run_dir = mtf.run_imaging("run", "image_plane", request)   # the Engine analysis only
  records = mtf.rows(imaging_results.load(document))   # or mtf.read_csv("out/mtf.csv")
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re
from typing import Any

from . import imaging_results as ir
from . import workflow_common as wc

PROG = "os-optics-kit mtf"
SCHEMA = "os-optics-kit.mtf"
SCHEMA_VERSION = 1
#: Exit codes: COMPLETED 0, INCOMPLETE 2 (everything written; what is missing is stated), refusals 4.
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "COMPLETED": 0, "INCOMPLETE": 2}
CSV_FILE = "mtf.csv"
JSON_FILE = "mtf.json"
IMAGING_FILE = "imaging.json"
#: The columns of mtf.csv, in order.
CSV_COLUMNS = ("field", "source_id", "wavelength_nm", "direction", "frequency_cyc_per_mm", "mtf", "mtf_status",
               "diffraction_limited_mtf", "diffraction_limited_mtf_status", "geometric_mtf", "geometric_mtf_status",
               "unit")
_NUMBER_COLUMNS = ("wavelength_nm", "frequency_cyc_per_mm", *ir.CURVE_KINDS)
#: MTF is a modulation ratio.
UNIT = "1"
DEFAULT_REFERENCE = "CENTROID"
CONVENTIONS = {
    "quantity": "optical MTF as the Engine computes it (os-optics imaging analysis): no pixel aperture, no sampling "
                "or aliasing term, no slanted-edge SFR (measured SFR: os-optics-kit sfr; design-side SFR: "
                "JunLin615/os-optics#42)",
    "spectrum": "one curve per spectral line (<source>#<spectral index>); no polychromatic or weighted MTF",
    "normalization": "MTF(0) is the share of the light inside the PSF window (arrays <line>.psf.window_light_share); "
                     "the curves are not renormalized to 1",
    "values": "the Engine's arrays as stored: no interpolation, resampling or sign change; a curve has values only "
              "when its quantity is KNOWN or KNOWN_ZERO; an unconverged curve keeps its values (NOT_CONVERGED)",
    "frequencies": "each line has its own frequency axis (the request's frequencies_cyc_per_mm, else 0 to that "
                   "line's cut-off 2 NA / wavelength); a value beyond the PSF map's Nyquist frequency is empty "
                   "(BEYOND_NYQUIST)",
    "requirements": "analysis quantities only: no PASS/FAIL, no targets",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_FIGURE_SUFFIXES = (".png", ".svg", ".pdf", ".caption.txt", ".provenance.txt")


class _Parser(wc.ArgumentParser):
    def error(self, message):
        raise wc.WorkflowError("BAD_ARGUMENT", message)


# --------------------------------------------------------------------------------------------- Engine access

def _engine_refusal(exc: Exception, **details: Any) -> wc.WorkflowError:
    """The kit's refusal for an Engine ``ResultsError``."""
    code = getattr(exc, "code", None)
    mapped = {"MONITOR_NOT_FOUND": "MONITOR_NOT_FOUND", "RUN_NOT_READABLE": "RUN_UNREADABLE",
              "CONFIGURATIONS_RUN": "CONFIGURATION_REQUIRED"}.get(str(code))
    if mapped:
        return wc.WorkflowError(mapped, str(exc), engine_code=code, **details)
    return wc.WorkflowError("ANALYSIS_REFUSED", f"the Engine refused the imaging analysis: {exc}", engine_code=code,
                            **details)


def _configurations(run: Path) -> list[dict[str, Any]] | None:
    """The configurations of a run of a Project with configurations (its manifest's entries), else ``None``."""
    from os_optics.local.configurations import is_configurations_manifest

    try:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not is_configurations_manifest(manifest):
        return None
    return [entry for entry in manifest.get("configurations") or [] if isinstance(entry, dict)]


def resolve_run(run: str | Path, configuration: str | None = None) -> Path:
    """The run directory holding the results: ``run`` itself, or ``run/configurations/<id>`` of a run of a Project
    with configurations (``CONFIGURATION_REQUIRED`` without a known ``configuration``; ``BAD_ARGUMENT`` for a
    ``configuration`` on an ordinary run)."""
    run = Path(run)
    if not run.is_dir():
        raise wc.WorkflowError("RUN_UNREADABLE", f"{run} is not a run directory", run=run.as_posix())
    entries = _configurations(run)
    if entries is None:
        if configuration is not None:
            raise wc.WorkflowError("BAD_ARGUMENT", f"{run} is not a run of a Project with configurations; omit "
                                                   "--configuration", run=run.as_posix())
        return run
    ids = [str(entry.get("configuration_id")) for entry in entries]
    if configuration is None or configuration not in ids:
        what = "needs --configuration ID" if configuration is None else f"has no configuration {configuration!r}"
        raise wc.WorkflowError("CONFIGURATION_REQUIRED", f"{run} is a run of a Project with configurations and {what}; "
                               f"configurations: {', '.join(ids) or 'none'}", run=run.as_posix(), configurations=ids)
    entry = entries[ids.index(configuration)]
    if entry.get("manifest_sha256") is None:
        raise wc.WorkflowError("RUN_UNREADABLE", f"configuration {configuration} of {run} was not written",
                               run=run.as_posix(), configuration_id=configuration)
    return run / str(entry.get("directory") or f"configurations/{configuration}")


def _load(run_dir: Path) -> Any:
    from os_optics.results import ResultsError, load_run

    try:
        return load_run(run_dir)
    except ResultsError as exc:
        raise _engine_refusal(exc, run=run_dir.as_posix()) from None


def _imaging(results: Any, run_dir: Path, monitor: str, request: dict[str, Any] | None) -> dict[str, Any]:
    from os_optics.results import ResultsError

    try:
        return ir.load(results.imaging(monitor, request))
    except ResultsError as exc:
        raise _engine_refusal(exc, run=run_dir.as_posix(), monitor=monitor) from None


def _monitor(results: Any, run_dir: Path, monitor: str) -> dict[str, Any]:
    from os_optics.results import ResultsError

    try:
        return results.monitor(monitor)
    except ResultsError as exc:
        raise _engine_refusal(exc, run=run_dir.as_posix(), monitor=monitor) from None


def run_identity(run: str | Path, configuration: str | None = None) -> tuple[dict[str, Any], Path]:
    """``(RunResults.identity(), run directory)`` of a saved run: its hashes, Engine build and catalog release. Reads
    the run only; no Engine analysis."""
    run_dir = resolve_run(run, configuration)
    return _load(run_dir).identity(), run_dir


def run_imaging(run: str | Path, monitor: str, request: dict[str, Any] | None = None, *,
                configuration: str | None = None) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Exactly one Engine imaging analysis of a saved run: ``(document, run identity, run directory)``. The document
    is ``RunResults.imaging(monitor, request)`` as JSON, as 'os-optics results imaging' prints it."""
    run_dir = resolve_run(run, configuration)
    results = _load(run_dir)
    return _imaging(results, run_dir, monitor, request), results.identity(), run_dir


def imaging_of(run: str | Path, monitor: str, request: dict[str, Any] | None = None, *,
               saved: str | Path | None = None, configuration: str | None = None) -> dict[str, Any]:
    """The imaging document of a saved run, for scripts: ``saved`` (a saved analysis, checked to be of ``run`` and of
    ``monitor``; no Engine analysis), else one in-process Engine analysis with ``request``. Refusals raise
    ``WorkflowError`` (``IMAGING_RUN_MISMATCH``, ``MONITOR_NOT_FOUND``, ...)."""
    if saved is None:
        return run_imaging(run, monitor, request, configuration=configuration)[0]
    document = ir.load(saved)
    stated = (document.get("settings") or {}).get("monitor")
    if stated != monitor:
        raise wc.WorkflowError("MONITOR_NOT_FOUND", f"the saved imaging analysis is of {stated}, not {monitor}")
    identity, _run_dir = run_identity(run, configuration)
    check_run(document, identity)
    return document


def check_run(document: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    """Whether an imaging document was computed from the run of ``identity`` (``RunResults.identity()``): its
    ``identity.physical`` ``engine_input_hash`` and ``run_result_hash`` against the run's. Returns ``{status MATCHED,
    document, run}``; anything else raises ``IMAGING_RUN_MISMATCH`` (a document that states no run cannot be shown
    to belong to it)."""
    from .ray_fans import imaging_run_check

    hashes = {"engine_input_hash": identity.get("engine_input_hash"), "run_result_hash": identity.get("result_hash")}
    check = imaging_run_check(document, {key: value for key, value in hashes.items() if isinstance(value, str)})
    if check["status"] != "MATCHED":
        reason = {"MISMATCHED": "its engine_input_hash / run_result_hash differ from the run's",
                  "NOT_STATED": "it states no engine_input_hash or run_result_hash",
                  "NOT_VERIFIED": "the run states no input or result hash"}.get(check["status"], check["status"])
        raise wc.WorkflowError("IMAGING_RUN_MISMATCH", f"the imaging analysis is not shown to be of this run: {reason}",
                               check=check)
    return check


def clipped_fields(results_monitor: dict[str, Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines of a run that reach the plane only outside its active area and that an imaging request without
    ``fields`` therefore leaves out: ``{line_id, source_id, state, reason OUTSIDE_ACTIVE_AREA, uv_mm,
    miss_distance_mm, message}`` from the run monitor's ``outside_active_area`` (none when the request names its
    fields: the Engine then analyses them wherever they land)."""
    request = (document.get("settings") or {}).get("request") or {}
    if request.get("fields"):
        return []
    analysed = {line["line_id"] for line in ir.lines(document)}
    out: dict[str, dict[str, Any]] = {}
    for crossing in results_monitor.get("outside_active_area") or []:
        line = ir.line_of_path(crossing.get("path_id"))
        if line is None or line in analysed or line in out:
            continue
        out[line] = {"line_id": line, "source_id": line.rpartition("#")[0], "state": "NOT_ANALYSED",
                     "reason": "OUTSIDE_ACTIVE_AREA", "uv_mm": crossing.get("uv_mm"),
                     "miss_distance_mm": crossing.get("miss_distance_mm"),
                     "message": "the line reaches the plane only outside its active area, so an imaging request "
                                "without fields leaves it out; name it in the request's fields, or widen the "
                                "plane's aperture"}
    return [out[key] for key in sorted(out)]


# --------------------------------------------------------------------------------------------- table

def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) else None


def rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The records of mtf.csv: one per line x direction x frequency of the line's own axis, keys ``CSV_COLUMNS``. A
    value is ``None`` when the curve has none there; its ``*_status`` says why (``imaging_results.value_status``)."""
    out = []
    for line in ir.lines(document):
        frequencies = line["frequencies_cyc_per_mm"]
        if not frequencies:
            continue
        for direction in ir.DIRECTIONS:
            records = {kind: ir.curve(document, line["line_id"], kind, direction) for kind in ir.CURVE_KINDS}
            for index, frequency in enumerate(frequencies):
                row: dict[str, Any] = {"field": line["line_id"], "source_id": line["source_id"],
                                       "wavelength_nm": _number(line["wavelength_nm"]), "direction": direction,
                                       "frequency_cyc_per_mm": _number(frequency), "unit": UNIT}
                for kind, record in records.items():
                    value, status = ir.value_status(record, index)
                    row[kind], row[f"{kind}_status"] = value, status
                out.append(row)
    return out


def write_csv(path: str | Path, records: list[dict[str, Any]]) -> Path:
    return wc.write_csv(path, records, CSV_COLUMNS)


def read_csv(path: str | Path) -> list[dict[str, Any]]:
    """The records of an mtf.csv as :func:`rows` returns them: numbers as floats, empty cells as ``None``."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
            raise ValueError(f"{path}: not an mtf.csv (columns {reader.fieldnames})")
        out = []
        for raw in reader:
            row: dict[str, Any] = {}
            for column in CSV_COLUMNS:
                cell = raw[column]
                row[column] = (float(cell) if cell != "" else None) if column in _NUMBER_COLUMNS else cell
            out.append(row)
    return out


# --------------------------------------------------------------------------------------------- document

def _image_point(line: dict[str, Any], reference: str) -> dict[str, Any]:
    rays = line.get("rays") or {}
    return {"reference": reference, "point_mm": rays.get("reference_point_mm"), "image_axis": rays.get("image_axis"),
            "tangential_axis": rays.get("tangential_axis"), "path_id": rays.get("path_id"),
            "crossing_index": rays.get("crossing_index")}


def _psf(document: dict[str, Any], line_id: str) -> dict[str, Any] | None:
    psf = (document.get("arrays") or {}).get(f"{line_id}.psf")
    if not isinstance(psf, dict):
        return None
    share = psf.get("window_light_share")
    return {"window_light_share": share[-1] if isinstance(share, list) and share else share,
            "mtf_nyquist_cyc_per_mm": psf.get("mtf_nyquist_cyc_per_mm"),
            "cutoff_cyc_per_mm": psf.get("cutoff_cyc_per_mm"), "kernel": psf.get("kernel")}


def _curve_entry(record: dict[str, Any]) -> dict[str, Any]:
    entry = dict(record)
    if not entry["usable"]:
        entry["frequencies_cyc_per_mm"] = None
    return entry


def not_analysed(document: dict[str, Any], clipped: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Everything the analysis was asked for, or should have covered, without a curve: requested fields that are no
    beam source (``FIELD_NOT_A_BEAM_SOURCE``), lines without an arrival (``NO_ARRIVAL``), requested fields or
    wavelengths that gave no line (``NO_LINE`` / ``WAVELENGTH_NOT_ANALYSED``: the Engine drops them silently) and
    ``clipped`` lines (:func:`clipped_fields`)."""
    lines = ir.lines(document)
    out = [{"line_id": None, **entry} for entry in ir.unanalysed_fields(document)]
    for line in lines:
        if line["field_state"] is not None or not line["frequencies_cyc_per_mm"]:
            field = ir.scalar(document, line["line_id"], "field")
            out.append({"line_id": line["line_id"], "source_id": line["source_id"],
                        "state": line["field_state"] or field["state"] or "NO_CURVE",
                        "reason": line["field_reason"] or field["reason"] or "NO_FREQUENCY_AXIS",
                        "message": field["message"] or "the analysis holds no frequency axis for this line"})
    request = (document.get("settings") or {}).get("request") or {}
    named = {entry["source_id"] for entry in out}
    sources = {line["source_id"] for line in lines}
    for source in request.get("fields") or ():
        if source not in sources and source not in named:
            out.append({"line_id": None, "source_id": source, "state": "NOT_ANALYSED", "reason": "NO_LINE",
                        "message": "a requested field with no analysed line (does any of its spectral lines match "
                                   "the request's wavelengths_nm?)"})
    wavelengths = [line["wavelength_nm"] for line in lines if isinstance(line["wavelength_nm"], (int, float))]
    for wavelength in request.get("wavelengths_nm") or ():
        if not any(abs(float(wavelength) - w) <= 1e-6 for w in wavelengths):
            out.append({"line_id": None, "source_id": None, "wavelength_nm": wavelength, "state": "NOT_ANALYSED",
                        "reason": "WAVELENGTH_NOT_ANALYSED",
                        "message": "a requested wavelength that matches no spectral line of the analysed fields"})
    return out + list(clipped or [])


def figure_name(line_id: str) -> str:
    """``mtf-<source>-<spectral index>`` (characters other than letters, digits, ``_ . -`` become ``_``)."""
    source, _, index = line_id.rpartition("#")
    return f"mtf-{_SAFE_NAME.sub('_', source or line_id)}-{_SAFE_NAME.sub('_', index)}"


def build(document: dict[str, Any], *, source: dict[str, Any], run: dict[str, Any] | None = None,
          clipped: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The mtf.json document of an imaging analysis (figures not yet named). ``source``: the ``input`` record;
    ``run``: ``RunResults.identity()`` of the run, when known; ``clipped``: :func:`clipped_fields`."""
    request = (document.get("settings") or {}).get("request") or {}
    reference = str(request.get("reference") or DEFAULT_REFERENCE)
    lines, counts = [], {"lines": 0, "curves": 0, "usable_curves": 0, "not_converged_curves": 0,
                         "declined_curves": 0, "beyond_nyquist_values": 0, "rows": 0}
    for line in ir.lines(document):
        if line["field_state"] is not None or not line["frequencies_cyc_per_mm"]:
            continue
        curves = ir.curves(document, line_ids=[line["line_id"]])
        by = {(c["kind"], c["direction"]): c for c in curves}
        counts["lines"] += 1
        counts["curves"] += len(curves)
        counts["usable_curves"] += sum(c["usable"] for c in curves)
        counts["not_converged_curves"] += sum(c["status"] == ir.NOT_CONVERGED for c in curves)
        counts["declined_curves"] += sum(not c["usable"] for c in curves)
        counts["beyond_nyquist_values"] += sum(v is None for c in curves for v in c["values"] or ())
        counts["rows"] += 2 * len(line["frequencies_cyc_per_mm"])
        lines.append({"line_id": line["line_id"], "source_id": line["source_id"],
                      "spectral_index": line["spectral_index"], "wavelength_nm": line["wavelength_nm"],
                      "observation_id": line["observation_id"],
                      "frequencies_cyc_per_mm": line["frequencies_cyc_per_mm"],
                      "image_point": _image_point(line, reference), "psf": _psf(document, line["line_id"]),
                      "tangential_status": by[("mtf", "tangential")]["status"],
                      "sagittal_status": by[("mtf", "sagittal")]["status"],
                      "curves": [_curve_entry(c) for c in curves], "figure": None})
    missing = not_analysed(document, clipped)
    complete = bool(lines) and not missing and all(
        line["tangential_status"] == ir.KNOWN and line["sagittal_status"] == ir.KNOWN for line in lines)
    conventions = {**CONVENTIONS, **{f"engine_{key}": value for key, value in
                                     ((document.get("arrays") or {}).get("conventions") or {}).items()}}
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "status": "COMPLETED" if complete else "INCOMPLETE",
            "input": source, "identity": {"run": run, "analysis": ir.identity(document)},
            "conventions": conventions, "lines": lines, "not_analysed": missing,
            "counts": {**counts, "not_analysed": len(missing)}, "files": {"csv": CSV_FILE, "imaging": None}}


# --------------------------------------------------------------------------------------------- figures

def _point_text(point: Any) -> str:
    from .presentation import fmt_number

    if not isinstance(point, list):
        return "-"
    return "(" + ", ".join(fmt_number(0.0 if abs(v) < 5e-13 else v, 6) for v in point) + ") mm"


def _frequencies_text(frequencies: list[Any]) -> str:
    from .presentation import fmt_number

    shown = [fmt_number(f, 6) for f in frequencies]
    return ", ".join(shown) if len(shown) <= 3 else f"{shown[0]} to {shown[-1]}"


def plot_line(line: dict[str, Any], out_dir: str | Path, *, style: Any, provenance_text: str) -> dict[str, Any]:
    """One line's figure: tangential and sagittal MTF (solid) and their diffraction limits (dashed); a curve without
    values is not drawn and is named in the figure, an unconverged one is labelled. Returns ``finish``'s paths."""
    from . import plot_results as pr

    by = {(c["kind"], c["direction"]): c for c in line["curves"]}
    drawn, missing, top = [], [], 1.0
    with pr.styled(style) as plt:
        fig, ax = style.figure(plt, (7.6, 4.6))
        for kind, dash in (("mtf", "-"), ("diffraction_limited_mtf", "--")):
            for index, direction in enumerate(("tangential", "sagittal")):
                record = by[(kind, direction)]
                label = style.t(f"legend.{kind}_{direction}")
                if not record["usable"]:
                    missing.append(f"{label} ({record['status']}"
                                   + (f": {record['reason']}" if record["reason"] else "") + ")")
                    continue
                values = [math.nan if v is None else float(v) for v in record["values"]]
                beyond = [f for f, v in zip(record["frequencies_cyc_per_mm"], record["values"]) if v is None]
                if beyond:
                    missing.append(f"{label} {_frequencies_text(beyond)} cycles/mm ({ir.BEYOND_NYQUIST})")
                top = max([top, *(v for v in values if math.isfinite(v))])
                if record["status"] == ir.NOT_CONVERGED:
                    label = style.t("legend.not_converged", label=label)
                ax.plot(record["frequencies_cyc_per_mm"], values, dash, color=style.theme.series_color(index),
                        marker="o" if kind == "mtf" and len(values) <= 40 else None, markersize=3, label=label)
                drawn.append(record["quantity_id"])
        ax.set_xlim(0.0, max([*line["frequencies_cyc_per_mm"], 1e-9]) * 1.04)
        ax.set_ylim(0.0, max(1.02, top + 0.02))
        ax.set_xlabel(style.t("axis.spatial_frequency"))
        ax.set_ylabel(style.t("axis.mtf"))
        point = line["image_point"]
        style.title(ax, style.t("title.mtf_line", line=line["line_id"],
                                wavelength=f"{line['wavelength_nm']:.6g}" if line["wavelength_nm"] is not None
                                else "-", reference=str(point["reference"]).lower(),
                                point=_point_text(point["point_mm"])), fontsize=style.fs(8, "body"))
        if missing:
            ax.text(0.01, 0.02, style.t("note.mtf_not_drawn", curves="; ".join(missing)), transform=ax.transAxes,
                    ha="left", va="bottom", fontsize=style.fs(6.5, "small"), wrap=True,
                    color="#555555" if style.legacy else style.theme.colors["muted"])
        if drawn:
            style.legend(ax, fontsize=style.fs(7), loc="best")
        if style.legacy:
            fig.tight_layout(rect=(0, 0.06, 1, 1))
        caption_text = style.t("caption.mtf", line=line["line_id"], wavelength=f"{line['wavelength_nm']:.6g}"
                               if line["wavelength_nm"] is not None else "-")
        written = pr.finish(fig, out_dir, figure_name(line["line_id"]), style, provenance_text, caption_text)
    return {**written, "drawn": drawn, "not_drawn": missing}


def _figures(report: dict[str, Any], out_dir: Path, imaging_path: Path, theme: Any) -> list[dict[str, Any]]:
    from . import plot_results as pr

    style = pr.Style(theme)
    identity = report["identity"]
    text = pr.caption(imaging_path, out_dir, engine_build=identity["analysis"].get("engine_build_id"),
                      catalog_version=(identity["run"] or {}).get("catalog_version") if identity["run"] else "unknown",
                      language=style.language)
    out = []
    for line in report["lines"]:
        written = plot_line(line, out_dir, style=style, provenance_text=text)
        line["figure"] = wc.relative(written["png"], out_dir)
        out.append({"line_id": line["line_id"], "png": line["figure"], "not_drawn": written["not_drawn"]})
    return out


# --------------------------------------------------------------------------------------------- command

#: the entries mtf writes (figures: mtf-<source>-<index> with each format and companion file)
FILES = (CSV_FILE, JSON_FILE, IMAGING_FILE,
         re.compile(r"mtf-.+(" + "|".join(re.escape(suffix) for suffix in _FIGURE_SUFFIXES) + ")"))


def _read_request(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        request = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise wc.WorkflowError("BAD_ARGUMENT", f"--request {path}: {exc}") from None
    if not isinstance(request, dict):
        raise wc.WorkflowError("BAD_ARGUMENT", f"--request {path}: an imaging request is a JSON object "
                                               "('os-optics schema imaging-request')")
    return request


def write(document: dict[str, Any], out_dir: str | Path, *, source: dict[str, Any], imaging_path: Path,
          run: dict[str, Any] | None = None, clipped: list[dict[str, Any]] | None = None, figures: bool = True,
          theme: Any = None) -> dict[str, Any]:
    """Write mtf.csv, mtf.json and (with ``figures``) one figure per line for an imaging ``document`` whose JSON is
    ``imaging_path``; returns the mtf.json document."""
    out_dir = Path(out_dir)
    report = build(document, source=source, run=run, clipped=clipped)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / CSV_FILE, rows(document))
    if imaging_path.resolve().parent == out_dir.resolve():
        report["files"]["imaging"] = imaging_path.name
    report["files"]["figures"] = _figures(report, out_dir, imaging_path, theme) if figures else []
    wc.write_json(out_dir / JSON_FILE, report)
    return report


def summary(report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """The compact stdout document: status, files, one row per line (statuses only, not the curves)."""
    files = {"csv": (out_dir / CSV_FILE).as_posix(), "json": (out_dir / JSON_FILE).as_posix(),
             "imaging": (out_dir / report["files"]["imaging"]).as_posix() if report["files"]["imaging"]
             else report["input"].get("imaging_file"),
             "figures": [(out_dir / f["png"]).as_posix() for f in report["files"]["figures"]]}
    return {"status": report["status"], "out": out_dir.as_posix(), "files": files,
            "lines": [{"line": line["line_id"], "wavelength_nm": line["wavelength_nm"],
                       "tangential_status": line["tangential_status"], "sagittal_status": line["sagittal_status"]}
                      for line in report["lines"]],
            "not_analysed": [{key: entry.get(key) for key in ("line_id", "source_id", "reason")}
                             for entry in report["not_analysed"]],
            "counts": report["counts"]}


def analyse(out_dir: str | Path, *, run: str | Path | None = None, monitor: str | None = None,
            request: dict[str, Any] | None = None, imaging: str | Path | None = None,
            configuration: str | None = None, figures: bool = True, theme: Any = None, overwrite: bool = False,
            request_file: str | Path | None = None) -> dict[str, Any]:
    """The whole command: from a run (one Engine imaging analysis) or a saved ``imaging`` document (no Engine
    analysis; ``run`` then checks that the document is of that run). Returns the stdout summary."""
    out_dir = Path(out_dir)
    imaging_path = Path(imaging) if imaging is not None else None
    if figures:
        from ._engine import require_matplotlib

        require_matplotlib()
    inputs = (imaging_path, request_file)  # never removed; imaging.json is written only from a run
    wc.prepare_output(out_dir, overwrite, FILES, keep=inputs,
                      writes=FILES if imaging_path is None else tuple(f for f in FILES if f != IMAGING_FILE))
    results = identity = run_dir = clipped = None
    if run is not None:
        run_dir = resolve_run(run, configuration)
        results = _load(run_dir)
        identity = results.identity()
    elif configuration is not None:
        raise wc.WorkflowError("BAD_ARGUMENT", "--configuration selects a configuration of a run")
    if imaging_path is None:
        if results is None or run_dir is None or monitor is None:
            raise wc.WorkflowError("BAD_ARGUMENT", "give RUN and --monitor PLANE, or --imaging FILE")
        document = _imaging(results, run_dir, monitor, request)
        imaging_path = wc.write_json(out_dir / IMAGING_FILE, document)
    else:
        try:
            document = ir.load(imaging_path)
        except ir.ImagingDocumentError as exc:
            raise wc.WorkflowError(exc.code, str(exc), imaging=imaging_path.as_posix()) from None
        stated = (document.get("settings") or {}).get("monitor")
        if monitor is not None and monitor != stated:
            raise wc.WorkflowError("BAD_ARGUMENT", f"--monitor {monitor}: the imaging analysis is of {stated}")
        monitor = stated
    if results is not None and identity is not None and run_dir is not None:
        check_run(document, identity)
        if monitor is not None:
            clipped = clipped_fields(_monitor(results, run_dir, monitor), document)
    source = {"mode": "imaging" if imaging is not None else "run",
              "run": Path(run).as_posix() if run is not None else None,
              "run_directory": run_dir.as_posix() if run_dir is not None else None,
              "monitor": monitor, "configuration_id": configuration,
              "imaging_file": imaging_path.as_posix(), "imaging_sha256": wc.sha256_file(imaging_path),
              "request_file": Path(request_file).as_posix() if request_file is not None else None,
              "request_sha256": wc.sha256_file(request_file) if request_file is not None else None,
              "outside_active_area_checked": clipped is not None}
    report = write(document, out_dir, source=source, imaging_path=imaging_path, run=identity, clipped=clipped,
                   figures=figures, theme=theme)
    return summary(report, out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", nargs="?", type=Path, help="run directory (os-optics project run --out-dir RUN)")
    parser.add_argument("--monitor", help="observation plane of the analysis (RUN mode)")
    parser.add_argument("--out", type=Path, required=True, help="output directory (new or empty)")
    parser.add_argument("--request", type=Path, help="imaging request JSON ('os-optics schema imaging-request')")
    parser.add_argument("--imaging", type=Path, help="saved imaging analysis JSON: no Engine analysis is run")
    parser.add_argument("--run", dest="check_run", type=Path,
                        help="with --imaging: the run the document must come from (its identity is checked)")
    parser.add_argument("--configuration", help="configuration id of a run of a Project with configurations")
    parser.add_argument("--no-figures", action="store_true", help="write no figures (needs no matplotlib)")
    parser.add_argument("--theme", help="presentation, report, or a JSON file with a theme or a presentation.json")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="theme setting, e.g. language=zh-CN")
    parser.add_argument("--overwrite", action="store_true", help="replace this command's files in a non-empty --out")

    def workflow():
        args = parser.parse_args(argv)
        if args.imaging is not None:
            if args.run is not None:
                raise wc.WorkflowError("BAD_ARGUMENT", "with --imaging, name the run with --run RUN")
            if args.request is not None:
                raise wc.WorkflowError("BAD_ARGUMENT", "--request needs RUN mode: --imaging runs no analysis")
        else:
            if args.check_run is not None:
                raise wc.WorkflowError("BAD_ARGUMENT", "--run RUN goes with --imaging FILE; in RUN mode give RUN")
            if args.run is None or not args.monitor:
                raise wc.WorkflowError("BAD_ARGUMENT", "give RUN and --monitor PLANE, or --imaging FILE")
        theme = None
        if not args.no_figures:
            from .plot_results import theme_from_arguments

            theme = theme_from_arguments(args.theme, args.set)
        return analyse(args.out, run=args.run if args.imaging is None else args.check_run, monitor=args.monitor,
                       request=_read_request(args.request), imaging=args.imaging, configuration=args.configuration,
                       figures=not args.no_figures, theme=theme, overwrite=args.overwrite,
                       request_file=args.request)

    return wc.emit(workflow, EXIT_CODES)


__all__ = ["CONVENTIONS", "CSV_COLUMNS", "EXIT_CODES", "SCHEMA", "analyse", "build", "check_run", "clipped_fields",
           "figure_name", "imaging_of", "main", "not_analysed", "plot_line", "read_csv", "resolve_run", "rows",
           "run_identity", "run_imaging", "summary", "write", "write_csv"]
