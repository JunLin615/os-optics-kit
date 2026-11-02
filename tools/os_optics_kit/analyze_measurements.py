"""Subtract backgrounds, optionally normalize a reference, and fit a CSV response.

Run from the repository root; see docs/en/measurements.md for runnable examples.
Original inputs are never modified. Output directories must not already exist.
"""

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import platform

if __package__:
    from .result_summary import ArgumentParser, emit_cli
else:
    from result_summary import ArgumentParser, emit_cli


PREFIX = "_analysis_"
SOURCE_REVISION = "e72ad3a9b6be12c7f0dbb00121fda23289c4e908"
SOURCE_BASE = ("https://github.com/JunLin615/free-space-optical-experiment-benchmark"
               f"/blob/{SOURCE_REVISION}/benchmark/case_library/")


@dataclass(frozen=True)
class MeasurementConfig:
    """Explicit column mapping and units; no unit conversion or implied offsets."""

    x: str
    signal: str
    background: str
    x_unit: str
    signal_unit: str
    reference: str | None = None
    reference_background: str | None = None
    reference_unit: str | None = None
    minimum_reference: float = 0.0
    maximum_rmse: float | None = None

    @property
    def columns(self):
        columns = [self.x, self.signal, self.background]
        if self.reference is not None:
            columns += [self.reference, self.reference_background]
        return columns

    @property
    def response_unit(self):
        if self.reference is None:
            return self.signal_unit
        if self.signal_unit == self.reference_unit:
            return "1"
        return f"({self.signal_unit})/({self.reference_unit})"

    def validate(self):
        reference_fields = [self.reference, self.reference_background, self.reference_unit]
        if any(value is not None for value in reference_fields) and not all(reference_fields):
            raise ValueError("Reference normalization requires reference, reference-background and reference-unit")
        units = [self.x_unit, self.signal_unit] + ([self.reference_unit] if self.reference else [])
        if any(not isinstance(value, str) or not value.strip()
               for value in [*self.columns, *units]):
            raise ValueError("Column names and units must be nonempty strings")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Map x, signal, backgrounds and reference to distinct columns")
        if not math.isfinite(self.minimum_reference) or self.minimum_reference < 0:
            raise ValueError("minimum-reference must be finite and nonnegative")
        if self.reference is None and self.minimum_reference != 0:
            raise ValueError("minimum-reference requires a reference channel")
        if self.maximum_rmse is not None and (
                not math.isfinite(self.maximum_rmse) or self.maximum_rmse < 0):
            raise ValueError("maximum-rmse must be finite and nonnegative")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def preprocess_csv(data, config):
    """Return header and all logical CSV records, including excluded records.

    Blank fields are missing; nonnumeric/NaN/infinite fields are invalid. A row
    can have several reasons, so per-reason counts need not sum to exclusions.
    Blank lines after the header are records too. Original text stays in raw.
    """
    config.validate()
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = next(reader, None)
    if not header or any(not name.strip() for name in header) or len(set(header)) != len(header):
        raise ValueError("CSV must have a header with unique nonempty column names")
    if any(name.startswith(PREFIX) for name in header):
        raise ValueError(f"Input column names beginning {PREFIX} are reserved for audit output")
    if missing := set(config.columns) - set(header):
        raise ValueError(f"Missing required CSV columns: {', '.join(sorted(missing))}")
    records = []
    for number, cells in enumerate(reader, 1):
        record = {"record": number, "line_end": reader.line_num, "raw": cells,
                  "reasons": [], "x": None, "corrected_signal": None,
                  "corrected_reference": None, "response": None,
                  "fit_response": None, "residual": None}
        reasons = record["reasons"]
        if len(cells) != len(header):
            reasons.append("invalid_row_width")
        raw = dict(zip(header, cells))
        values = {}
        for column in config.columns:
            cell = raw.get(column, "").strip()
            if not cell:
                reasons.append(f"missing:{column}")
                continue
            try:
                value = float(cell)
            except ValueError:
                reasons.append(f"invalid:{column}")
                continue
            if not math.isfinite(value):
                reasons.append(f"invalid:{column}")
                continue
            values[column] = value
        record["x"] = values.get(config.x)
        if not reasons:
            signal = values[config.signal] - values[config.background]
            reference = (values[config.reference] - values[config.reference_background]
                         if config.reference else None)
            if not math.isfinite(signal) or (reference is not None and not math.isfinite(reference)):
                reasons.append("nonfinite_correction")
            else:
                record["corrected_signal"] = signal
                record["corrected_reference"] = reference
                if reference is not None and reference <= config.minimum_reference:
                    reasons.append("reference_at_or_below_minimum")
                else:
                    response = signal / reference if reference is not None else signal
                    if not math.isfinite(response):
                        reasons.append("nonfinite_response")
                    else:
                        record["response"] = response
        record["used"] = not reasons
        records.append(record)
    return header, records


def fit_linear(x, y):
    """Unweighted OLS with intercept; return diagnostics and signed residuals.

    At least three finite observations and two distinct x values are required.
    Standard errors assume independent, equal-variance response noise and exact x.
    """
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("A linear fit requires at least three paired observations")
    if not all(math.isfinite(value) for value in [*x, *y]):
        raise ValueError("Fit values must be finite")
    if min(x) == max(x):
        raise ValueError("A linear fit requires at least two distinct x values")
    # Subtract a representable observation before taking means. Subtracting a
    # rounded absolute mean can bias covariance for large timestamp/DC offsets.
    x_origin, response_origin = x[0], y[0]
    x_offsets = [value - x_origin for value in x]
    y_offsets = [value - response_origin for value in y]
    if not all(math.isfinite(value) for value in [*x_offsets, *y_offsets]):
        raise ValueError("Fit exceeds numeric range; rescale input values and units")
    x_mean = math.fsum(value / len(x) for value in x_offsets)
    y_mean = math.fsum(value / len(y) for value in y_offsets)
    scale = max(abs(value - x_mean) for value in x_offsets)
    centered = [(value - x_mean) / scale for value in x_offsets]
    ssx = math.fsum(value * value for value in centered)
    deviations = [value - y_mean for value in y_offsets]
    y_scale = max(map(abs, deviations)) or 1.0
    if not math.isfinite(y_scale):
        raise ValueError("Fit exceeds numeric range; rescale input values and units")
    beta = math.fsum(a * (b / y_scale) for a, b in zip(centered, deviations)) / ssx * y_scale
    slope = beta / scale
    offset_at_origin = math.fsum([y_mean, -slope * x_mean])
    intercept = math.fsum([response_origin, offset_at_origin, -slope * x_origin])
    predicted = [math.fsum([response_origin, y_mean, beta * value]) for value in centered]
    residuals = [math.fsum([observed, -y_mean, -beta * value]) for observed, value in zip(y_offsets, centered)]
    # Norms avoid squaring tiny/large measurements into zero/infinity.
    residual_norm = math.hypot(*residuals)
    response_norm = math.hypot(*deviations)
    dof = len(x) - 2
    residual_sd = residual_norm / math.sqrt(dof)
    constant_response = min(y) == max(y)
    fit = {
        "method": "unweighted_ordinary_least_squares_with_intercept",
        "n": len(x), "degrees_of_freedom": dof,
        "slope": slope, "intercept": intercept,
        "x_origin": x_origin, "response_origin": response_origin,
        "response_offset_at_x_origin": offset_at_origin,
        "slope_standard_error": residual_sd / math.sqrt(ssx) / scale,
        "intercept_standard_error": residual_sd * math.hypot(1 / math.sqrt(len(x)), math.fsum([x_origin / scale, x_mean / scale]) / math.sqrt(ssx)),
        "rmse": residual_norm / math.sqrt(len(x)), "residual_standard_deviation": residual_sd,
        "r_squared": None if constant_response else 1 - (residual_norm / response_norm) ** 2,
        "max_abs_residual": max(abs(value) for value in residuals),
        "mean_residual": math.fsum(residuals) / len(x),
        "x_range": [min(x), max(x)],
    }
    if not all(math.isfinite(value) for value in fit.values() if isinstance(value, (float, int))):
        raise ValueError("Fit exceeds numeric range; rescale input values and units")
    return fit, predicted, residuals


def _plot(records, fit, config, path, input_name, input_hash):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(9, 9), layout="constrained")
    FigureCanvasAgg(figure)
    raw_ax, fit_ax, residual_ax = figure.subplots(3, 1, sharex=True)
    used = [record for record in records if record["used"]]
    x = [record["x"] for record in used]
    raw_ax.plot(x, [record["corrected_signal"] for record in used], "o", label="Signal minus background")
    raw_ax.set_ylabel(f"Corrected signal [{config.signal_unit}]")
    if config.reference:
        ref_ax = raw_ax if config.reference_unit == config.signal_unit else raw_ax.twinx()
        ref_ax.plot(x, [record["corrected_reference"] for record in used], "s", color="tab:orange", label="Reference minus background")
        if ref_ax is raw_ax:
            raw_ax.set_ylabel(f"Corrected channels [{config.signal_unit}]")
        else:
            ref_ax.set_ylabel(f"Corrected reference [{config.reference_unit}]")
            ref_ax.legend(loc="upper right")
    raw_ax.legend(loc="best")
    fit_ax.plot(x, [record["response"] for record in used], "o", label="Processed response")
    if fit:
        ordered = sorted(used, key=lambda record: record["x"])
        fit_ax.plot([record["x"] for record in ordered], [record["fit_response"] for record in ordered], label="Linear OLS fit")
        residual_ax.plot(x, [record["residual"] for record in used], "o")
    else:
        fit_ax.text(0.5, 0.5, "Fit unavailable: insufficient usable data", transform=fit_ax.transAxes, ha="center")
    fit_ax.set_ylabel(f"Response [{config.response_unit}]")
    fit_ax.legend()
    residual_ax.axhline(0, color="gray", linewidth=1)
    residual_ax.set_ylabel(f"Observed - fitted [{config.response_unit}]")
    residual_ax.set_xlabel(f"{config.x} [{config.x_unit}]")
    for axis in (raw_ax, fit_ax, residual_ax):
        axis.grid(alpha=0.25)
    figure.suptitle(f"{input_name}\n{len(used)} used / {len(records) - len(used)} excluded; input SHA-256 {input_hash[:12]}")
    figure.savefig(path, dpi=140, metadata={"Description": f"Input SHA-256: {input_hash}; see provenance.json"})


def analyze_measurements(input_path, output_dir, config):
    """Create a new auditable analysis bundle and return its compact JSON summary."""
    config.validate()
    input_path, output_dir = Path(input_path).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise ValueError("Output directory already exists; choose a new path to preserve earlier results")
    data = input_path.read_bytes()
    input_hash = _sha256(data)
    header, records = preprocess_csv(data, config)
    used = [record for record in records if record["used"]]
    counts = dict(sorted(Counter(reason for record in records for reason in record["reasons"]).items()))
    issues = []
    fit = None
    if len(used) < 3 or len({record["x"] for record in used}) < 2:
        issues.append({"code": "INSUFFICIENT_FIT_DATA", "message": "Provide at least three usable rows spanning at least two distinct x values"})
    else:
        fit, predicted, residuals = fit_linear([record["x"] for record in used], [record["response"] for record in used])
        fit["x_unit"] = config.x_unit
        fit["response_unit"] = config.response_unit
        fit["slope_unit"] = f"({config.response_unit})/({config.x_unit})"
        for record, prediction, residual in zip(used, predicted, residuals):
            record["fit_response"], record["residual"] = prediction, residual
        if fit["r_squared"] is None:
            issues.append({"code": "CONSTANT_RESPONSE", "message": "R-squared is undefined for a constant response; inspect residuals and slope instead"})
    excluded = len(records) - len(used)
    if excluded:
        issues.append({"code": "ROWS_EXCLUDED", "message": "Review exclusions in processed.csv; reason counts can overlap"})
    targets = []
    if config.maximum_rmse is not None:
        targets.append({"id": "response_rmse", "metric": "rmse", "unit": config.response_unit,
                        "value": fit["rmse"] if fit else None,
                        "bounds": {"maximum": config.maximum_rmse},
                        "status": "UNKNOWN" if fit is None else "PASS" if fit["rmse"] <= config.maximum_rmse else "FAIL"})
    status = ("UNKNOWN" if fit is None else "FAIL" if any(target["status"] == "FAIL" for target in targets)
              else "WARNING" if issues else "PASS")
    artifacts = {name: str(output_dir / filename) for name, filename in (
        ("original_input", "original-input.csv"), ("processed_csv", "processed.csv"),
        ("plot", "measurements.png"), ("provenance", "provenance.json"), ("summary", "summary.json"))}
    summary = {"status": status, "rows": {"total": len(records), "used": len(used), "excluded": excluded,
                                             "exclusion_counts": counts},
               "fit": fit, "targets": targets, "issues": issues,
               "identity": {"workflow": "measurement_analysis_v1", "input_sha256": input_hash},
               "artifacts": artifacts}
    # Validate JSON and the optional dependency before creating any output files.
    json.dumps(summary, allow_nan=False)
    import matplotlib

    output_dir.mkdir(parents=True, exist_ok=False)
    Path(artifacts["original_input"]).write_bytes(data)
    audit_fields = ["record", "line_end", "used", "reasons", "extra_cells", "x", "corrected_signal",
                    "corrected_reference", "response", "fit_response", "residual", "input_sha256"]
    with Path(artifacts["processed_csv"]).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header + [PREFIX + field for field in audit_fields])
        for record in records:
            audit = {**record, "reasons": json.dumps(record["reasons"]),
                     "extra_cells": json.dumps(record["raw"][len(header):]), "input_sha256": input_hash}
            cells = (record["raw"] + [""] * len(header))[:len(header)]
            writer.writerow(cells + [audit[field] for field in audit_fields])
    _plot(records, fit, config, artifacts["plot"], input_path.name, input_hash)
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(input_path), "sha256": input_hash, "bytes": len(data), "encoding": "UTF-8, optional BOM"},
        "configuration": asdict(config), "response_unit": config.response_unit,
        "method": {"correction": "signal - background", "normalization": "corrected_signal / (reference - reference_background)" if config.reference else None,
                   "exclusion_policy": "No imputation or outlier rejection; exclude rows with missing/invalid required fields, invalid width, nonfinite correction/ratio, or corrected reference <= minimum_reference",
                   "fit": "Unweighted OLS with intercept; no x uncertainty or correlated/heteroscedastic error model",
                   "stable_fit_form": "response = response_origin + response_offset_at_x_origin + slope * (x - x_origin); residuals computed in offset coordinates",
                   "residual": "observed response minus fitted response", "rmse": "sqrt(sum(residual**2) / n)",
                   "standard_errors": "Based on residual variance with n - 2 degrees of freedom"},
        "software": {"python": platform.python_version(), "matplotlib": matplotlib.__version__,
                     "helper_sha256": _sha256(Path(__file__).read_bytes()), "engine_required": False},
        "workflow_sources": {"author": "JunLin615 and contributors", "license": "CC BY 4.0",
                             "license_url": "https://creativecommons.org/licenses/by/4.0/", "revision": SOURCE_REVISION,
                             "urls": [SOURCE_BASE + case + ".md" for case in ("EX-DETECT-01", "EX-DRIFT-01")],
                             "adaptation": "Workflow ideas adapted; implementation and synthetic datasets created for this repository"},
        "artifact_sha256": {role: _sha256(Path(artifacts[role]).read_bytes()) for role in ("original_input", "processed_csv", "plot")},
    }
    Path(artifacts["provenance"]).write_text(json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    Path(artifacts["summary"]).write_text(json.dumps(summary, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("input", type=Path, help="UTF-8 CSV with a unique header row")
        parser.add_argument("--out", required=True, type=Path, help="New output directory (must not exist)")
        for name in ("x", "signal", "background"):
            parser.add_argument("--" + name, required=True, help=f"Name of the {name} column")
        parser.add_argument("--x-unit", required=True, help="Unit for the independent variable, e.g. mW or s")
        parser.add_argument("--signal-unit", required=True, help="Shared unit for signal and its background, e.g. V")
        parser.add_argument("--reference", help="Reference column; enables normalization after dark subtraction")
        parser.add_argument("--reference-background", help="Reference background column in reference-unit")
        parser.add_argument("--reference-unit", help="Shared unit for reference and its background")
        parser.add_argument("--minimum-reference", type=float, default=0, help="Exclude corrected reference <= this nonnegative value (reference units; default 0)")
        parser.add_argument("--maximum-rmse", type=float, help="Optional maximum fit RMSE in response units; unmet bound exits 1")
        args = vars(parser.parse_args(argv))
        input_path, output_dir = args.pop("input"), args.pop("out")
        return analyze_measurements(input_path, output_dir, MeasurementConfig(**args))

    return emit_cli(workflow, exit_status_key="status")


if __name__ == "__main__":
    raise SystemExit(main())
