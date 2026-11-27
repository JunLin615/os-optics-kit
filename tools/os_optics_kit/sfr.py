"""Measure the slanted-edge SFR (ISO 12233 e-SFR) of one image region; optionally beside a design MTF curve.

Usage:
  os-optics-kit sfr IMAGE --out DIR [--roi X,Y,W,H] [--channel luminance|R|G|B]
                    [--oecf linear|gamma|srgb|table] [--gamma G] [--oecf-table CSV] [--full-scale V]
                    [--fit-order 1|2] [--pixel-pitch-um P | --pixel-pitch-mm P] [--at F,...] [--at-mm F,...]
                    [--design FILE [--design-line ID] [--design-direction both|tangential|sagittal]
                     [--design-curve mtf|diffraction_limited_mtf|geometric_mtf] [--design-unit cy/mm|cy/px]]
                    [--no-plot] [--overwrite]

IMAGE: PNG or TIFF (8/16-bit grey, RGB/RGBA; read with Pillow), .npy or comma-separated .csv (2-D, no header).
The ROI (pixels, origin top-left, x = column) holds one straight edge, near-vertical or near-horizontal; the
orientation is detected and stated. Chain: OECF (linear by default) -> per-row centroid of the Hamming-windowed
derivative -> line (or quadratic) fit: edge angle and residuals -> projection onto the edge-normal distance, 4x
oversampled bins (empty bins filled by linear interpolation, counted) -> ESF -> central-difference LSF -> Hamming
window centred on the LSF peak -> |FFT|, normalized to 1 at zero frequency, divided by the derivative's
transfer function sinc(2 f d), d = 1/4 px. SFR is a non-negative magnitude, frequency in cycles/pixel along the
edge normal (cycles/mm with a pixel pitch). Scalars: sfr50 (first fall below 0.5, linear interpolation),
sfr_at_nyquist (0.5 cy/px), --at / --at-mm values.

--design adds the optical MTF of saved 'os-optics results imaging' JSON (or a frequency,value CSV) on the same
axes: comparison.csv / comparison.json / comparison.png. A cy/mm design curve needs the pixel pitch. The design
curve is optics only (no pixel aperture, sampling or image processing); the measured SFR is the whole system.

Writes sfr.csv, esf.csv, lsf.csv, edge.csv, summary.json (with provenance) and sfr.png (unless --no-plot; the plot
needs matplotlib: python -m pip install "os-optics-kit[plot]"). The engine is not needed. An existing non-empty
DIR is refused (OUTPUT_EXISTS) unless --overwrite. Stdout: the compact summary. Exit 0 PASS / WARNING (see
issues), 2 UNKNOWN (too few filled ESF bins: the edge angle gives too little sub-pixel phase coverage), 4 refused
input (JSON error on stderr).
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import platform

try:
    import numpy as np
except ModuleNotFoundError:  # numpy comes with os-optics; only an install without it lacks numpy
    np = None  # type: ignore[assignment]

from . import imaging_results
from ._engine import require_matplotlib
from .result_summary import ArgumentParser, emit_cli

PROG = "os-optics-kit sfr"
WORKFLOW = "sfr_slanted_edge_v1"

OVERSAMPLING = 4                 # ESF bins per pixel along the edge normal (ISO 12233)
REPORT_MAX_CY_PER_PX = 1.0       # the curve is written up to twice the Nyquist frequency
NYQUIST_CY_PER_PX = 0.5
LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)  # ITU-R BT.709 / sRGB primaries, applied to linearized R, G, B
DERIVATIVE_CORRECTION_CAP = 10.0
MIN_SIDE_PX = 8                  # smallest ROI side
MIN_ACROSS_PX = 16               # smallest ROI extent across the edge
MIN_ROWS = 20                    # fewer rows along the edge: FEW_ROWS
SHALLOW_ANGLE_DEG = 2.0          # below: EDGE_ANGLE_SHALLOW
STEEP_ANGLE_DEG = 30.0           # above: EDGE_ANGLE_STEEP (orientation becomes ambiguous towards 45 deg)
MAX_RESIDUAL_RMS_PX = 0.25       # above: LARGE_FIT_RESIDUAL
MIN_MICHELSON = 0.2              # below: LOW_EDGE_CONTRAST
MIN_HALF_SPAN_PX = 8.0           # ESF half span below: SHORT_ESF_SPAN
UNKNOWN_EMPTY_FRACTION = 0.25    # empty-bin share above: status UNKNOWN
IMAGE_SUFFIXES = (".png", ".tif", ".tiff")
ARRAY_SUFFIXES = (".npy", ".csv")
DIRECTIONS = ("tangential", "sagittal")
DESIGN_CURVES = ("mtf", "diffraction_limited_mtf", "geometric_mtf")
ARTIFACTS = {"sfr_csv": "sfr.csv", "esf_csv": "esf.csv", "lsf_csv": "lsf.csv", "edge_csv": "edge.csv",
             "plot": "sfr.png", "comparison_csv": "comparison.csv", "comparison_json": "comparison.json",
             "comparison_plot": "comparison.png", "summary": "summary.json"}

SFR_CONVENTION = ("system SFR magnitude |FFT(windowed LSF)| normalized to 1 at zero frequency, divided by the "
                  "central-difference derivative's sinc(2 f d), d = 1/4 px; non-negative (the edge loses phase)")
DESIGN_CONVENTION = ("optical MTF as written by the design source, signed values kept; compared as |MTF|, with the "
                     "frequencies where it is negative listed")


class SfrError(ValueError):
    """A refused input or setting, with a stable ``code``."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Parser(ArgumentParser):
    def error(self, message):
        raise SfrError("BAD_ARGUMENT", message)


@dataclass(frozen=True)
class SfrConfig:
    """Settings of one SFR measurement (and, with ``design``, one comparison)."""

    roi: tuple[int, int, int, int] | None = None
    channel: str = "luminance"
    oecf: str = "linear"
    gamma: float | None = None
    oecf_table: str | None = None
    full_scale: float | None = None
    fit_order: int = 1
    pixel_pitch_mm: float | None = None
    at: tuple[float, ...] = ()
    at_mm: tuple[float, ...] = ()
    design: str | None = None
    design_line: str | None = None
    design_direction: str = "both"
    design_curve: str = "mtf"
    design_unit: str | None = None
    plot: bool = True
    overwrite: bool = False

    def validate(self):
        if self.roi is not None:
            if len(self.roi) != 4 or any(not isinstance(v, int) for v in self.roi):
                raise SfrError("ROI_INVALID", "--roi takes four integers X,Y,W,H")
            if self.roi[0] < 0 or self.roi[1] < 0 or self.roi[2] <= 0 or self.roi[3] <= 0:
                raise SfrError("ROI_INVALID", "--roi needs X, Y >= 0 and W, H > 0")
        if self.channel not in ("luminance", "R", "G", "B"):
            raise SfrError("BAD_ARGUMENT", "--channel is luminance, R, G or B")
        if self.oecf not in ("linear", "gamma", "srgb", "table"):
            raise SfrError("BAD_ARGUMENT", "--oecf is linear, gamma, srgb or table")
        if (self.oecf == "gamma") != (self.gamma is not None):
            raise SfrError("BAD_ARGUMENT", "--gamma G goes with --oecf gamma, and only with it")
        if self.gamma is not None and not (math.isfinite(self.gamma) and self.gamma > 0):
            raise SfrError("BAD_ARGUMENT", "--gamma must be finite and positive")
        if (self.oecf == "table") != (self.oecf_table is not None):
            raise SfrError("BAD_ARGUMENT", "--oecf-table CSV goes with --oecf table, and only with it")
        if self.full_scale is not None and not (math.isfinite(self.full_scale) and self.full_scale > 0):
            raise SfrError("BAD_ARGUMENT", "--full-scale must be finite and positive")
        if self.fit_order not in (1, 2):
            raise SfrError("BAD_ARGUMENT", "--fit-order is 1 or 2")
        if self.pixel_pitch_mm is not None and not (math.isfinite(self.pixel_pitch_mm) and self.pixel_pitch_mm > 0):
            raise SfrError("BAD_ARGUMENT", "the pixel pitch must be finite and positive")
        for value in (*self.at, *self.at_mm):
            if not (math.isfinite(value) and value >= 0):
                raise SfrError("BAD_ARGUMENT", "--at / --at-mm frequencies must be finite and non-negative")
        if self.at_mm and self.pixel_pitch_mm is None:
            raise SfrError("PIXEL_PITCH_REQUIRED", "--at-mm needs --pixel-pitch-um or --pixel-pitch-mm")
        if self.design_direction not in ("both", *DIRECTIONS):
            raise SfrError("BAD_ARGUMENT", "--design-direction is both, tangential or sagittal")
        if self.design_curve not in DESIGN_CURVES:
            raise SfrError("BAD_ARGUMENT", f"--design-curve is one of {', '.join(DESIGN_CURVES)}")
        if self.design_unit not in (None, "cy/mm", "cy/px"):
            raise SfrError("BAD_ARGUMENT", "--design-unit is cy/mm or cy/px")
        if self.design is None and any((self.design_line, self.design_unit,
                                        self.design_direction != "both", self.design_curve != "mtf")):
            raise SfrError("BAD_ARGUMENT", "--design-* options need --design FILE")


# ------------------------------------------------------------------------------------------------ reading inputs


@dataclass
class ImageInput:
    """Raw pixel values: (H, W) grey or (H, W, 3) R, G, B, with the facts needed to linearize and check clipping."""

    values: np.ndarray
    channels: tuple[str, ...]
    full_scale: float | None
    bit_depth: int | None
    format: str
    mode: str
    notes: tuple[str, ...] = ()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _tile_rawmode(image):
    try:
        args = image.tile[0][3] if image.tile else None
    except (IndexError, TypeError, AttributeError):
        return ""
    if isinstance(args, tuple):
        args = args[0] if args else ""
    return str(args or "")


def read_image(path, data=None):
    """Read IMAGE into an ``ImageInput``; refuse anything that is not plain grey or RGB measurement data."""
    path = Path(path)
    if data is None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise SfrError("INPUT_NOT_FOUND", f"{path} does not exist") from None
        except OSError as exc:
            raise SfrError("INPUT_UNREADABLE", f"{path}: {exc}") from None
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return _read_pillow(data, suffix)
    if suffix == ".npy":
        try:
            array = np.load(io.BytesIO(data), allow_pickle=False)
        except (ValueError, OSError, EOFError) as exc:
            raise SfrError("INPUT_UNREADABLE", f"{path.name} is not a readable .npy array: {exc}") from None
        return _from_array(array, "npy")
    if suffix == ".csv":
        try:
            text = data.decode("utf-8-sig")
            array = np.loadtxt(io.StringIO(text), delimiter=",", ndmin=2, dtype=np.float64)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SfrError("INPUT_UNREADABLE", f"{path.name} is not a numeric comma-separated 2-D array without a "
                                               f"header: {exc}") from None
        return _from_array(array, "csv")
    raise SfrError("UNSUPPORTED_INPUT", f"{path.name}: use PNG, TIFF, .npy or .csv")


def _from_array(array, kind):
    if array.dtype.kind not in "uif":
        raise SfrError("UNSUPPORTED_IMAGE_MODE", f"the {kind} array has dtype {array.dtype}; use numbers")
    if array.ndim == 3 and array.shape[2] in (3, 4):
        values, channels = array[:, :, :3], ("R", "G", "B")
    elif array.ndim == 2:
        values, channels = array, ("Y",)
    else:
        raise SfrError("INPUT_NOT_2D", f"the {kind} array has shape {list(array.shape)}; use (H, W) or (H, W, 3)")
    full_scale, depth = {"u1": (255.0, 8), "u2": (65535.0, 16)}.get(f"{array.dtype.kind}{array.dtype.itemsize}",
                                                                  (None, None))
    return ImageInput(values.astype(np.float64), channels, full_scale, depth, kind, str(array.dtype))


def _read_pillow(data, suffix):
    try:
        from PIL import Image
    except ImportError:
        raise SfrError("IMAGE_READER_MISSING", "reading PNG/TIFF needs Pillow (it comes with the plot extra): "
                                               "python -m pip install \"os-optics-kit[plot]\"; or give .npy/.csv"
                       ) from None
    notes = []
    try:
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "n_frames", 1) > 1:
                notes.append("multi-frame file: the first frame is read")
            mode, rawmode = image.mode, _tile_rawmode(image)
            image.load()
            if mode in ("L", "LA"):
                values, channels, full_scale, depth = np.asarray(image.convert("L")), ("Y",), 255.0, 8
            elif mode.startswith("I;16"):
                values, channels, full_scale, depth = np.asarray(image), ("Y",), 65535.0, 16
            elif mode == "I":
                values, channels = np.asarray(image), ("Y",)
                full_scale, depth = (65535.0, 16) if "16" in rawmode else (None, 32)
            elif mode == "F":
                values, channels, full_scale, depth = np.asarray(image), ("Y",), None, 32
            elif mode in ("RGB", "RGBA"):
                values, channels, full_scale, depth = np.asarray(image.convert("RGB")), ("R", "G", "B"), 255.0, 8
                if "16" in rawmode:
                    notes.append("16-bit colour is read by Pillow as 8-bit; save 16-bit data as grey or .npy")
                if mode == "RGBA":
                    notes.append("alpha channel ignored")
            else:
                raise SfrError("UNSUPPORTED_IMAGE_MODE", f"image mode {mode} is not grey or RGB measurement data")
    except SfrError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for a damaged file
        raise SfrError("INPUT_UNREADABLE", f"cannot decode the {suffix} image: {exc}") from None
    return ImageInput(np.asarray(values, dtype=np.float64), channels, full_scale, depth, suffix.lstrip("."),
                      mode, tuple(notes))


def read_oecf_table(path):
    """Two-column CSV (code value, linear value), optional header; codes strictly increasing."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SfrError("OECF_INVALID", f"--oecf-table {path}: {exc}") from None
    rows = [row for row in csv.reader(io.StringIO(text)) if row and any(cell.strip() for cell in row)]
    try:
        float(rows[0][0])
    except (ValueError, IndexError):
        rows = rows[1:]
    try:
        table = np.array([[float(row[0]), float(row[1])] for row in rows if len(row) == 2], dtype=np.float64)
    except ValueError as exc:
        raise SfrError("OECF_INVALID", f"--oecf-table: {exc}") from None
    if len(table) != len(rows) or len(table) < 2 or not np.isfinite(table).all() \
            or np.any(np.diff(table[:, 0]) <= 0) or np.any(np.diff(table[:, 1]) < 0):
        raise SfrError("OECF_INVALID", "--oecf-table needs at least two rows code,linear with strictly increasing "
                                       "codes and non-decreasing linear values")
    return table


def linearize(values, oecf="linear", *, gamma=None, table=None, full_scale=None):
    """Map code values to values linear in exposure. gamma: (v / full_scale) ** gamma; srgb: the IEC 61966-2-1
    inverse transfer on v / full_scale; table: piecewise-linear interpolation of the OECF table."""
    values = np.asarray(values, dtype=np.float64)
    if oecf == "linear":
        return values
    if oecf in ("gamma", "srgb"):
        if full_scale is None:
            raise SfrError("OECF_NEEDS_FULL_SCALE", f"--oecf {oecf} needs the full-scale code value: give --full-scale "
                                                    "for float or .csv data")
        scaled = values / full_scale
        if scaled.min() < 0 or scaled.max() > 1:
            raise SfrError("OECF_RANGE", f"values lie outside 0..{full_scale:g}; the {oecf} OECF is undefined there")
        if oecf == "gamma":
            return scaled ** gamma
        return np.where(scaled <= 0.04045, scaled / 12.92, ((scaled + 0.055) / 1.055) ** 2.4)
    if values.min() < table[0, 0] or values.max() > table[-1, 0]:
        raise SfrError("OECF_RANGE", f"values {values.min():g}..{values.max():g} leave the OECF table's code range "
                                     f"{table[0, 0]:g}..{table[-1, 0]:g}")
    return np.interp(values, table[:, 0], table[:, 1])


# ------------------------------------------------------------------------------------------------ the e-SFR chain


def _hamming(length, centre):
    """ISO 12233 / sfrmat ``ahamming``: a Hamming window over ``length`` samples centred at ``centre`` (may be
    fractional), reaching 0.08 at the farther end."""
    index = np.arange(length, dtype=np.float64)
    centre = np.asarray(centre, dtype=np.float64)
    width = np.maximum(centre, length - 1 - centre)
    width = np.where(width > 0, width, 1.0)
    return 0.54 + 0.46 * np.cos(np.pi * (index - centre[..., None]) / width[..., None])


def _robust_noise(work):
    """Pixel noise SD from row-to-row differences along the edge (median absolute deviation)."""
    differences = np.diff(work, axis=0).ravel()
    if differences.size == 0:
        return 0.0
    return float(np.median(np.abs(differences - np.median(differences))) * 1.4826 / math.sqrt(2.0))


def crossing(frequency, values, level=0.5):
    """First frequency where ``values`` falls below ``level`` (linear interpolation), or None."""
    below = np.nonzero(np.asarray(values) < level)[0]
    below = below[below > 0]
    if below.size == 0:
        return None
    k = int(below[0])
    f0, f1, v0, v1 = frequency[k - 1], frequency[k], values[k - 1], values[k]
    return float(f0 + (v0 - level) * (f1 - f0) / (v0 - v1))


def value_at(frequency, values, f):
    """Linear interpolation of ``values`` at ``f`` inside the sampled range, else None."""
    if f < frequency[0] - 1e-12 or f > frequency[-1] + 1e-12:
        return None
    return float(np.interp(f, frequency, values))


def measure_sfr(roi, *, fit_order=1, oversampling=OVERSAMPLING):
    """The e-SFR of a 2-D array (values linear in exposure) holding one slanted edge; no files, no plotting.

    Returns a dict with the edge fit, the ESF/LSF/SFR arrays and diagnostics. Raises ``SfrError`` for an ROI
    without a usable edge."""
    a = np.asarray(roi, dtype=np.float64)
    if a.ndim != 2:
        raise SfrError("INPUT_NOT_2D", "the ROI must be a 2-D array")
    if min(a.shape) < MIN_SIDE_PX:
        raise SfrError("ROI_TOO_SMALL", f"the ROI is {a.shape[1]} x {a.shape[0]} px; each side needs >= {MIN_SIDE_PX}")
    if not np.isfinite(a).all():
        raise SfrError("NONFINITE_VALUES", "the ROI holds NaN or infinite values")
    gradient_x = float(np.abs(np.diff(a, axis=1)).mean())
    gradient_y = float(np.abs(np.diff(a, axis=0)).mean())
    orientation = "vertical" if gradient_x >= gradient_y else "horizontal"
    work = a if orientation == "vertical" else a.T  # rows run along the edge, columns across it
    n_rows, n_cols = work.shape
    if n_cols < MIN_ACROSS_PX:
        raise SfrError("ROI_TOO_SMALL", f"the ROI spans {n_cols} px across the {orientation} edge; it needs "
                                        f">= {MIN_ACROSS_PX}")
    tail = max(2, n_cols // 10)
    first, last = float(work[:, :tail].mean()), float(work[:, -tail:].mean())
    noise = _robust_noise(work)
    step = last - first
    scale = max(abs(first), abs(last), 1e-300)
    if abs(step) <= max(5.0 * noise, 1e-9 * scale):
        raise SfrError("EDGE_NOT_FOUND", f"no edge: the two sides differ by {abs(step):.4g}, noise SD {noise:.3g}")
    sign = 1.0 if step > 0 else -1.0

    derivative = np.zeros_like(work)
    derivative[:, 1:-1] = sign * (work[:, 2:] - work[:, :-2]) / 2.0
    x = np.arange(n_cols, dtype=np.float64)
    rows = np.arange(n_rows, dtype=np.float64)
    row_centre = (n_rows - 1) / 2.0
    location = np.full(n_rows, (n_cols - 1) / 2.0)
    for _ in range(3):  # window at the ROI centre, then twice at the fitted edge
        weighted = derivative * _hamming(n_cols, location)
        strength = weighted.sum(axis=1)
        valid = strength > 0.25 * float(np.median(strength))
        if valid.sum() < max(3, fit_order + 2, n_rows // 2):
            raise SfrError("EDGE_NOT_FOUND", f"the edge step is found in only {int(valid.sum())} of {n_rows} rows")
        centroid = np.full(n_rows, np.nan)
        centroid[valid] = (weighted[valid] * x).sum(axis=1) / strength[valid]
        coefficients = np.polyfit(rows[valid] - row_centre, centroid[valid], fit_order)
        location = np.polyval(coefficients, rows - row_centre)
        if location.min() < 0 or location.max() > n_cols - 1:
            raise SfrError("EDGE_OUTSIDE_ROI", "the fitted edge leaves the ROI in some rows; centre the ROI on the edge")
    residual = centroid - location
    residual_rms = float(np.sqrt(np.mean(residual[valid] ** 2)))
    local_slope = np.polyval(np.polyder(coefficients), rows - row_centre)
    slope = float(np.polyval(np.polyder(coefficients), 0.0))
    angle = math.degrees(math.atan(slope))

    # projection onto the edge-normal distance and 4x oversampled binning
    cosine = 1.0 / np.sqrt(1.0 + local_slope ** 2)
    distance = (x[None, :] - location[:, None]) * cosine[:, None]
    half = min(-float(distance[:, 0].max()), float(distance[:, -1].min()))
    delta = 1.0 / oversampling
    n_bins = 2 * int(math.floor(half / delta))
    if n_bins < 4 * oversampling:
        raise SfrError("EDGE_OUTSIDE_ROI", f"the edge leaves only {max(half, 0):.2f} px on one side in some rows")
    index = np.floor(distance / delta).astype(np.int64) + n_bins // 2
    keep = (index >= 0) & (index < n_bins)
    counts = np.bincount(index[keep], minlength=n_bins)
    sums = np.bincount(index[keep], weights=work[keep], minlength=n_bins)
    centres = (np.arange(n_bins) - n_bins / 2 + 0.5) * delta
    empty = counts == 0
    esf = np.zeros(n_bins)
    esf[~empty] = sums[~empty] / counts[~empty]
    if empty.any():
        esf[empty] = np.interp(centres[empty], centres[~empty], esf[~empty])

    lsf = np.zeros(n_bins)
    lsf[1:-1] = sign * (esf[2:] - esf[:-2]) / (2.0 * delta)
    lsf[0], lsf[-1] = lsf[1], lsf[-2]
    peak = int(np.argmax(lsf))
    window = _hamming(n_bins, np.array(float(peak)))
    spectrum = np.abs(np.fft.rfft(lsf * window))
    if not spectrum[0] > 0:
        raise SfrError("EDGE_NOT_FOUND", "the windowed LSF has no positive area")
    frequency = np.arange(spectrum.size) / (n_bins * delta)
    correction = 1.0 / np.maximum(np.sinc(2.0 * frequency * delta), 1.0 / DERIVATIVE_CORRECTION_CAP)
    sfr = spectrum / spectrum[0] * correction
    reported = frequency <= REPORT_MAX_CY_PER_PX + 1e-12

    tails = max(2, n_bins // 10)
    sides = sorted((float(esf[:tails].mean()), float(esf[-tails:].mean())))
    low, high = sides
    michelson = (high - low) / (high + low) if high + low > 0 else None
    return {
        "orientation": orientation,
        "rows": n_rows, "columns": n_cols, "rows_used": int(valid.sum()),
        "polarity": "dark_to_bright" if sign > 0 else "bright_to_dark",
        "coefficients": coefficients, "row_centre": row_centre,
        "angle_deg": angle, "slope": slope,
        "position_at_centre": float(np.polyval(coefficients, 0.0)),
        "centroid": centroid, "location": location, "residual": residual, "valid": valid,
        "residual_rms_px": residual_rms,
        "phase_cycles": float(abs(location[-1] - location[0])),
        "noise_sd": noise,
        "contrast": {"dark": low, "bright": high, "michelson": michelson,
                     "ratio": high / low if low > 0 else None, "snr": (high - low) / noise if noise > 0 else None},
        "bin_width_px": delta, "half_span_px": n_bins / 2 * delta,
        "esf_distance": centres, "esf": esf, "counts": counts, "empty": empty,
        "lsf": lsf, "window": window, "lsf_peak_px": float(centres[peak]),
        "frequency": frequency[reported], "sfr": sfr[reported], "correction": correction[reported],
    }


# ------------------------------------------------------------------------------------------------ synthetic edge


def synthetic_edge(shape=(100, 80), angle_deg=5.0, sigma_px=0.5, *, orientation="vertical", low=0.2, high=0.8,
                   centre=None, noise_sd=0.0, seed=0, supersample=32):
    """A slanted edge with a known SFR: a step from ``low`` to ``high``, blurred by an isotropic Gaussian PSF of SD
    ``sigma_px``, integrated over square pixels (100 % fill), plus optional Gaussian noise.

    Pixel (row r, column c) covers [c - 0.5, c + 0.5] x [r - 0.5, r + 0.5]. A vertical edge passes through
    ``centre`` (row, column; default the array centre) at position c = c0 + tan(angle) (r - r0); ``horizontal``
    transposes the roles. The integral across the edge is exact (the antiderivative of the normal CDF); along it
    the pixel is sampled at ``supersample`` rows. True SFR: ``true_sfr``."""
    from scipy.special import ndtr

    rows, cols = shape if orientation == "vertical" else shape[::-1]
    if centre is not None and orientation == "horizontal":
        centre = centre[::-1]
    r0, c0 = centre if centre is not None else ((rows - 1) / 2.0, (cols - 1) / 2.0)
    tangent = math.tan(math.radians(angle_deg))
    cosine = math.cos(math.radians(angle_deg))
    sub = (np.arange(supersample) + 0.5) / supersample - 0.5
    y = np.arange(rows)[:, None] + sub[None, :]
    edge = c0 + tangent * (y - r0)                                   # (rows, supersample)
    left = (np.arange(cols)[None, None, :] - 0.5 - edge[:, :, None]) * cosine
    right = left + cosine
    if sigma_px > 0:
        def antiderivative(t):
            return t * ndtr(t) + np.exp(-0.5 * t * t) / math.sqrt(2.0 * math.pi)
        share = sigma_px / cosine * (antiderivative(right / sigma_px) - antiderivative(left / sigma_px))
    else:
        share = (np.clip(right, 0.0, None) - np.clip(left, 0.0, None)) / cosine
    values = low + (high - low) * share.mean(axis=1)
    if noise_sd:
        values = values + np.random.default_rng(seed).normal(0.0, noise_sd, values.shape)
    return values if orientation == "vertical" else values.T


def true_sfr(frequency, sigma_px, angle_deg=0.0):
    """SFR along the edge normal of ``synthetic_edge``: Gaussian PSF times the square pixel projected on the normal,
    exp(-2 pi^2 sigma^2 f^2) |sinc(f cos a) sinc(f sin a)|; sinc(x) = sin(pi x)/(pi x). It is
    exp(-2 pi^2 sigma^2 f^2) |sinc(f)| for a = 0."""
    f = np.asarray(frequency, dtype=np.float64)
    a = math.radians(angle_deg)
    return np.exp(-2.0 * math.pi ** 2 * sigma_px ** 2 * f ** 2) * np.abs(np.sinc(f * math.cos(a)) *
                                                                         np.sinc(f * math.sin(a)))


# ------------------------------------------------------------------------------------------------ design curves


def read_design(path, *, line=None, direction="both", curve="mtf", unit=None):
    """A design curve set: ``{"kind", "frequency_unit", "curves": {name: (frequency, signed value)}, ...}``.

    JSON: the document of ``os-optics results imaging RUN --monitor PLANE`` (schema opensci.analysis_result,
    analysis IMAGING); ``line`` selects ``SOURCE#INDEX`` (or a source id with one line) when it holds several.
    CSV: two columns frequency,value (optional header); ``unit`` is required."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SfrError("DESIGN_INVALID", f"--design {path}: {exc}") from None
    record = {"path": str(path), "sha256": _sha256(data), "bytes": len(data)}
    if path.suffix.lower() == ".csv":
        if unit is None:
            raise SfrError("DESIGN_UNIT_REQUIRED", "a CSV design curve needs --design-unit cy/mm or cy/px")
        if line is not None or direction != "both" or curve != "mtf":
            raise SfrError("BAD_ARGUMENT", "--design-line/-direction/-curve select from engine JSON, not a CSV")
        frequency, values = _design_csv(data)
        record.update(kind="csv", frequency_unit=unit, curves={"csv": (frequency, values)},
                      description="frequency,value CSV")
        return record
    try:
        document = imaging_results.load(path)
    except imaging_results.ImagingDocumentError as exc:
        raise SfrError("DESIGN_INVALID", f"--design must be the output of 'os-optics results imaging' or a .csv: "
                                         f"{exc}") from None
    if unit not in (None, "cy/mm"):
        raise SfrError("BAD_ARGUMENT", "engine imaging frequencies are cycles/mm; omit --design-unit")
    lines = imaging_results.lines(document)
    if not any(entry["frequencies_cyc_per_mm"] for entry in lines):
        raise SfrError("DESIGN_CURVE_UNAVAILABLE", "the imaging result holds no MTF frequencies (no field was analysed)")
    available = ", ".join(f"{entry['line_id']} ({entry['wavelength_nm']} nm)" for entry in lines)
    names = [entry["line_id"] for entry in lines]
    if line is None:
        if len(names) > 1:
            raise SfrError("DESIGN_SELECTION_REQUIRED", f"the imaging result holds {len(names)} lines; choose one "
                                                        f"with --design-line: {available}")
        chosen = names[0]
    elif line in names:
        chosen = line
    else:
        matches = [name for name in names if name.rpartition("#")[0] == line]
        if len(matches) != 1:
            raise SfrError("DESIGN_SELECTION_UNKNOWN", f"--design-line {line} is not one line of: {available}")
        chosen = matches[0]
    entry = lines[names.index(chosen)]
    curves, dropped, status = {}, {}, {}
    for name in (DIRECTIONS if direction == "both" else (direction,)):
        # only a curve the Engine states as KNOWN: a declined one keeps an array in the document, never a curve here
        found = imaging_results.curve(document, chosen, curve, name)
        if not found["usable"]:
            raise SfrError("DESIGN_CURVE_UNAVAILABLE", f"{found['quantity_id']} is {found['status']} in the imaging "
                                                       f"result ({found['reason'] or 'no reason stated'}: "
                                                       f"{found['message'] or 'no values'})")
        frequency = np.array(found["frequencies_cyc_per_mm"], dtype=np.float64)
        keep = np.array([value is not None for value in found["values"]], dtype=bool)
        values = np.array([np.nan if value is None else value for value in found["values"]], dtype=np.float64)
        dropped[name] = [float(f) for f in frequency[~keep]]
        status[name] = {"status": found["status"], "converged": found["converged"],
                        "value_kind": found["value_kind"], "model_basis": found["model_basis"]}
        curves[name] = _checked_curve(frequency[keep], values[keep], found["quantity_id"])
    identity = imaging_results.identity(document)
    record.update(kind="os_optics_results_imaging", frequency_unit="cy/mm", curves=curves,
                  description=f"os-optics results imaging: {curve}, line {chosen}",
                  line=chosen, source_id=entry["source_id"], wavelength_nm=entry["wavelength_nm"],
                  observation_id=entry["observation_id"], curve=curve, curve_status=status,
                  dropped_null_frequencies=dropped,
                  identity=(document.get("identity") or {}).get("physical"),
                  analysis_version=identity["analysis_version"], execution_outcome=identity["execution_outcome"],
                  axes=((document.get("arrays") or {}).get("conventions") or {}).get("axes"))
    return record


def _checked_curve(frequency, values, label):
    if frequency.size < 2 or not (np.isfinite(frequency).all() and np.isfinite(values).all()):
        raise SfrError("DESIGN_INVALID", f"{label}: at least two finite points are needed")
    if frequency.min() < 0 or np.any(np.diff(frequency) <= 0):
        raise SfrError("DESIGN_INVALID", f"{label}: frequencies must be non-negative and strictly increasing")
    return frequency, values


def _design_csv(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SfrError("DESIGN_INVALID", f"design CSV: {exc}") from None
    rows = [row for row in csv.reader(io.StringIO(text)) if row and any(cell.strip() for cell in row)]
    if rows:
        try:
            float(rows[0][0])
        except ValueError:
            rows = rows[1:]
    if any(len(row) != 2 for row in rows):
        raise SfrError("DESIGN_INVALID", "design CSV rows must have exactly two columns: frequency,value")
    try:
        table = np.array([[float(a), float(b)] for a, b in rows], dtype=np.float64).reshape(-1, 2)
    except ValueError as exc:
        raise SfrError("DESIGN_INVALID", f"design CSV: {exc}") from None
    return _checked_curve(table[:, 0], table[:, 1], "design CSV")


def compare(measured, design, pitch_mm):
    """Measured SFR beside each design curve on the measured frequency grid (cy/px) where both exist."""
    if design["frequency_unit"] == "cy/mm" and pitch_mm is None:
        raise SfrError("PIXEL_PITCH_REQUIRED", "a cy/mm design curve needs --pixel-pitch-um or --pixel-pitch-mm to "
                                               "meet a cy/px measurement")
    to_px = pitch_mm if design["frequency_unit"] == "cy/mm" else 1.0
    frequency, sfr = measured["frequency"], measured["sfr"]
    curves, rows_by_curve, issues = {}, {}, []
    grid_mask = np.zeros(frequency.size, dtype=bool)
    for name, (design_f, design_v) in design["curves"].items():
        design_px = design_f * to_px
        inside = (frequency >= design_px[0] - 1e-12) & (frequency <= design_px[-1] + 1e-12)
        grid_mask |= inside
        negative = [float(f) for f, v in zip(design_f, design_v) if v < 0]
        native = []
        for f_native, f_px, value in zip(design_f, design_px, design_v):
            measured_value = value_at(frequency, sfr, float(f_px))
            native.append({"frequency_cy_per_px": _r(f_px, 9), "frequency_cy_per_mm": _r(f_px / pitch_mm, 9)
                           if pitch_mm else None, "design": _r(value, 9), "design_abs": _r(abs(value), 9),
                           "measured_sfr": _r(measured_value),
                           "measured_minus_design_abs": _r(None if measured_value is None
                                                           else measured_value - abs(value))})
        curves[name] = {"design_px": design_px, "design_v": design_v, "inside": inside, "negative": negative,
                        "native": native}
        if negative:
            issues.append({"code": "DESIGN_NEGATIVE", "message": f"{name}: the design value is negative (phase "
                           f"reversal) at {len(negative)} frequencies; compared as |MTF|"})
        if design_px[-1] < NYQUIST_CY_PER_PX:
            issues.append({"code": "DESIGN_ENDS_BELOW_NYQUIST", "message": f"{name}: the design curve ends at "
                           f"{design_px[-1]:.4g} cy/px, below the pixel Nyquist frequency 0.5 cy/px"})
        if design_px[0] > 0:
            issues.append({"code": "DESIGN_STARTS_ABOVE_ZERO", "message": f"{name}: the design curve starts at "
                           f"{design_px[0]:.4g} cy/px; nothing is extrapolated below it"})
    if not grid_mask.any():
        raise SfrError("NO_FREQUENCY_OVERLAP", "the design and measured frequency ranges do not overlap (check the "
                                               "pixel pitch and --design-unit)")
    grid = frequency[grid_mask]
    table = {"frequency_cy_per_px": grid, "measured_sfr": sfr[grid_mask]}
    if pitch_mm:
        table["frequency_cy_per_mm"] = grid / pitch_mm
    scalars = {}
    measured_50 = crossing(frequency, sfr)
    measured_nyquist = value_at(frequency, sfr, NYQUIST_CY_PER_PX)
    for name, curve in curves.items():
        inside = curve["inside"][grid_mask]
        signed = np.where(inside, np.interp(grid, curve["design_px"], curve["design_v"]), np.nan)
        table[f"design_{name}"] = signed
        table[f"design_{name}_abs"] = np.abs(signed)
        table[f"measured_minus_design_{name}_abs"] = sfr[grid_mask] - np.abs(signed)
        difference = (sfr[grid_mask] - np.abs(signed))[inside]
        design_abs = np.abs(curve["design_v"])
        design_50 = crossing(curve["design_px"], design_abs)
        design_nyquist = value_at(curve["design_px"], design_abs, NYQUIST_CY_PER_PX)
        scalars[name] = {
            "design_abs_at_nyquist": _r(design_nyquist), "measured_at_nyquist": _r(measured_nyquist),
            "design_abs_50_cy_per_px": _r(design_50), "measured_sfr50_cy_per_px": _r(measured_50),
            "design_abs_50_cy_per_mm": _r(design_50 / pitch_mm) if design_50 is not None and pitch_mm else None,
            "measured_sfr50_cy_per_mm": _r(measured_50 / pitch_mm) if measured_50 is not None and pitch_mm else None,
            "max_abs_difference": _r(float(np.abs(difference).max())) if difference.size else None,
            "rms_difference": _r(float(np.sqrt(np.mean(difference ** 2)))) if difference.size else None,
            "points": int(inside.sum()),
            "negative_design_frequencies": [_r(f) for f in curve["negative"]],
            "at_design_frequencies": curve["native"],
        }
        rows_by_curve[name] = curve
    return {"table": table, "scalars": scalars, "issues": issues, "curves": rows_by_curve, "to_px": to_px}


# ------------------------------------------------------------------------------------------------ outputs


def _r(value, digits=6):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return float(f"{value:.{digits}g}")


def _write_csv(path, columns):
    names = list(columns)
    length = len(next(iter(columns.values())))
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(names)
        for i in range(length):
            row = []
            for name in names:
                value = columns[name][i]
                if isinstance(value, (bool, np.bool_)):
                    row.append("true" if value else "false")
                elif isinstance(value, (int, np.integer)):
                    row.append(str(int(value)))
                else:
                    value = float(value)
                    row.append("" if not math.isfinite(value) else f"{value:.9g}")
            writer.writerow(row)


def _issues(result, clipping, config, image):
    issues = []
    angle = abs(result["angle_deg"])
    if angle < SHALLOW_ANGLE_DEG:
        issues.append({"code": "EDGE_ANGLE_SHALLOW", "message": f"edge angle {angle:.3g} deg is below "
                       f"{SHALLOW_ANGLE_DEG:g} deg: little sub-pixel phase diversity; tilt the target (about 5 deg)"})
    if angle > STEEP_ANGLE_DEG:
        issues.append({"code": "EDGE_ANGLE_STEEP", "message": f"edge angle {angle:.3g} deg is above "
                       f"{STEEP_ANGLE_DEG:g} deg: towards 45 deg the orientation is ambiguous and the pixel "
                       "aperture is anisotropic; use about 5 deg"})
    if result["phase_cycles"] < 1.0:
        issues.append({"code": "INCOMPLETE_PHASE_COVERAGE", "message": f"the edge moves {result['phase_cycles']:.3g} px "
                       "over the rows (< 1 px): the oversampled bins are not all filled from distinct phases"})
    if result["rows"] < MIN_ROWS:
        issues.append({"code": "FEW_ROWS", "message": f"{result['rows']} rows along the edge (< {MIN_ROWS}); the "
                       "angle fit and the ESF are noisy"})
    if result["rows_used"] < result["rows"]:
        issues.append({"code": "ROWS_WITHOUT_EDGE", "message": f"{result['rows'] - result['rows_used']} rows had no "
                       "clear edge step and were left out of the edge fit"})
    if result["residual_rms_px"] > MAX_RESIDUAL_RMS_PX:
        issues.append({"code": "LARGE_FIT_RESIDUAL", "message": f"edge fit residual RMS {result['residual_rms_px']:.3g} "
                       f"px (> {MAX_RESIDUAL_RMS_PX:g}): noise, a curved edge (try --fit-order 2) or a second edge"})
    empty = int(result["empty"].sum())
    if empty:
        issues.append({"code": "EMPTY_BINS", "message": f"{empty} of {result['empty'].size} ESF bins were empty and "
                       "filled by linear interpolation between neighbouring bins"})
    michelson = result["contrast"]["michelson"]
    if michelson is not None and michelson < MIN_MICHELSON:
        issues.append({"code": "LOW_EDGE_CONTRAST", "message": f"edge Michelson contrast {michelson:.3g} "
                       f"(< {MIN_MICHELSON:g}); noise dominates the high frequencies"})
    if result["half_span_px"] < MIN_HALF_SPAN_PX:
        issues.append({"code": "SHORT_ESF_SPAN", "message": f"the ESF spans +/-{result['half_span_px']:.3g} px "
                       f"(< {MIN_HALF_SPAN_PX:g}); LSF tails are cut and low frequencies are biased; widen the ROI"})
    if clipping["checked"] and (clipping["at_zero"] or clipping["at_full_scale"]):
        issues.append({"code": "CLIPPED_PIXELS", "message": f"{clipping['at_zero']} ROI pixels at 0 and "
                       f"{clipping['at_full_scale']} at full scale {clipping['full_scale']:g}: clipping distorts the "
                       "ESF; reduce exposure or the target contrast"})
    for note in image.notes:
        issues.append({"code": "INPUT_NOTE", "message": note})
    return issues


def _clipping(raw, full_scale):
    if full_scale is None:
        return {"checked": False, "full_scale": None, "at_zero": int((raw <= 0).any(axis=-1).sum())
                if raw.ndim == 3 else int((raw <= 0).sum()), "at_full_scale": None}
    low = raw <= 0
    high = raw >= full_scale
    if raw.ndim == 3:
        low, high = low.any(axis=-1), high.any(axis=-1)
    return {"checked": True, "full_scale": full_scale, "at_zero": int(low.sum()), "at_full_scale": int(high.sum())}


def _plot(path, roi_values, result, summary, pitch_mm, input_name, input_hash):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 8.5), layout="constrained")
    FigureCanvasAgg(figure)
    (image_ax, esf_ax), (lsf_ax, sfr_ax) = figure.subplots(2, 2)
    image_ax.imshow(roi_values, cmap="gray", interpolation="nearest")
    rows = np.arange(result["rows"])
    if result["orientation"] == "vertical":
        image_ax.plot(result["location"], rows, color="tab:red", linewidth=1, label="fitted edge")
    else:
        image_ax.plot(rows, result["location"], color="tab:red", linewidth=1, label="fitted edge")
    image_ax.set_title(f"ROI, {result['orientation']} edge, {result['angle_deg']:.3f} deg; "
                       f"fit RMS {result['residual_rms_px']:.3g} px")
    image_ax.legend(loc="upper right")
    distance, empty = result["esf_distance"], result["empty"]
    esf_ax.plot(distance, result["esf"], color="tab:blue", label="ESF (4x oversampled)")
    if empty.any():
        esf_ax.plot(distance[empty], result["esf"][empty], "x", color="tab:orange", label="empty bin, interpolated")
    esf_ax.set_xlabel("distance from the edge along its normal [px]")
    esf_ax.set_ylabel("linearized signal")
    esf_ax.legend(loc="best")
    lsf = result["lsf"]
    lsf_ax.plot(distance, lsf / lsf.max(), color="tab:blue", label="LSF / peak")
    lsf_ax.plot(distance, result["window"], "--", color="tab:gray", label="Hamming window")
    lsf_ax.set_xlabel("distance from the edge along its normal [px]")
    lsf_ax.legend(loc="best")
    frequency, sfr = result["frequency"], result["sfr"]
    sfr_ax.plot(frequency, sfr, color="tab:blue", label="SFR (magnitude)")
    sfr_ax.axvline(NYQUIST_CY_PER_PX, color="tab:gray", linestyle=":", label="Nyquist 0.5 cy/px")
    sfr50 = summary["sfr"]["sfr50_cy_per_px"]
    if sfr50 is not None:
        sfr_ax.plot([sfr50], [0.5], "o", color="tab:red", label=f"SFR50 {sfr50:.3g} cy/px")
    sfr_ax.set_xlim(0, REPORT_MAX_CY_PER_PX)
    sfr_ax.set_ylim(0, max(1.05, float(sfr.max()) * 1.05))
    sfr_ax.set_xlabel("spatial frequency [cycles/pixel]")
    sfr_ax.set_ylabel("SFR")
    if pitch_mm:
        top = sfr_ax.secondary_xaxis("top", functions=(lambda f: f / pitch_mm, lambda f: f * pitch_mm))
        top.set_xlabel("spatial frequency [cycles/mm]")
    sfr_ax.legend(loc="upper right")
    for axis in (esf_ax, lsf_ax, sfr_ax):
        axis.grid(alpha=0.25)
    figure.suptitle(f"{input_name}: slanted-edge SFR (ISO 12233 e-SFR); input SHA-256 {input_hash[:12]}")
    figure.savefig(path, dpi=120, metadata={"Description": f"Input SHA-256: {input_hash}; see summary.json"})


def _plot_comparison(path, result, comparison, design, pitch_mm, input_hash):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10, 6.4), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    scale = (1.0 / pitch_mm) if pitch_mm else 1.0
    unit = "cycles/mm" if pitch_mm else "cycles/pixel"
    axis.plot(result["frequency"] * scale, result["sfr"], color="black", linewidth=2,
              label="measured SFR (system: optics x pixel x sampling x processing; magnitude)")
    styles = {"tangential": ("tab:blue", "-"), "sagittal": ("tab:orange", "--"), "csv": ("tab:blue", "-")}
    for name, curve in comparison["curves"].items():
        color, style = styles.get(name, ("tab:green", "-"))
        x = curve["design_px"] * scale
        axis.plot(x, np.abs(curve["design_v"]), style, marker="o", markersize=3, color=color,
                  label=f"design |MTF| {name} (optics only)")
        negative = curve["design_v"] < 0
        if negative.any():
            axis.plot(x[negative], np.abs(curve["design_v"][negative]), "x", markersize=9, color="tab:red",
                      label=f"{name}: MTF < 0 (phase reversal)")
    axis.axvline(NYQUIST_CY_PER_PX * scale, color="tab:gray", linestyle=":", label="pixel Nyquist")
    axis.set_xlim(0, REPORT_MAX_CY_PER_PX * scale)
    axis.set_ylim(0, 1.05)
    axis.set_xlabel(f"spatial frequency [{unit}]")
    axis.set_ylabel("SFR, |MTF|")
    axis.grid(alpha=0.25)
    axis.legend(loc="upper right", fontsize="small")
    pitch_text = f"pixel pitch {pitch_mm * 1000:g} um" if pitch_mm else "no pixel pitch"
    figure.suptitle(f"Measured SFR and design MTF; input SHA-256 {input_hash[:12]}\n"
                    "Design curve: optical MTF only; pixel aperture, sampling and image processing not included.\n"
                    f"{design['description']}; {pitch_text}; edge {result['orientation']} "
                    f"{result['angle_deg']:.2f} deg", fontsize="medium")
    figure.savefig(path, dpi=120, metadata={"Description": f"Input SHA-256: {input_hash}; see comparison.json"})


def _prepare_output(output_dir, overwrite):
    if output_dir.exists():
        if not output_dir.is_dir():
            raise SfrError("OUTPUT_EXISTS", f"{output_dir} is a file; choose a new --out directory")
        if any(output_dir.iterdir()) and not overwrite:
            raise SfrError("OUTPUT_EXISTS", f"{output_dir} is not empty; choose a new --out or use --overwrite")


def analyze_sfr(input_path, output_dir, config):
    """Measure the SFR of IMAGE's ROI, write the bundle to ``output_dir`` and return the compact summary."""
    config.validate()
    input_path, output_dir = Path(input_path), Path(output_dir)
    _prepare_output(output_dir, config.overwrite)
    design = None
    if config.design is not None:
        design = read_design(config.design, line=config.design_line, direction=config.design_direction,
                             curve=config.design_curve, unit=config.design_unit)
        if design["frequency_unit"] == "cy/mm" and config.pixel_pitch_mm is None:
            raise SfrError("PIXEL_PITCH_REQUIRED", "a cy/mm design curve needs --pixel-pitch-um or --pixel-pitch-mm "
                                                   "to meet a cy/px measurement")
    try:
        data = input_path.read_bytes()
    except FileNotFoundError:
        raise SfrError("INPUT_NOT_FOUND", f"{input_path} does not exist") from None
    except OSError as exc:
        raise SfrError("INPUT_UNREADABLE", f"{input_path}: {exc}") from None
    input_hash = _sha256(data)
    image = read_image(input_path, data)
    height, width = image.values.shape[:2]
    if config.channel != "luminance" and image.channels == ("Y",):
        raise SfrError("BAD_ARGUMENT", f"--channel {config.channel} needs RGB input; this input has one channel")
    x, y, w, h = config.roi if config.roi is not None else (0, 0, width, height)
    if x + w > width or y + h > height:
        raise SfrError("ROI_INVALID", f"--roi {x},{y},{w},{h} leaves the {width} x {height} image")
    raw = image.values[y:y + h, x:x + w]
    if not np.isfinite(raw).all():
        raise SfrError("NONFINITE_VALUES", "the ROI holds NaN or infinite values")
    full_scale = config.full_scale if config.full_scale is not None else image.full_scale
    clipping = _clipping(raw, full_scale)
    table = read_oecf_table(config.oecf_table) if config.oecf == "table" else None
    linear = linearize(raw, config.oecf, gamma=config.gamma, table=table, full_scale=full_scale)
    if image.channels == ("Y",):
        roi_values, channel_rule = linear, "single channel"
    elif config.channel == "luminance":
        roi_values = linear @ np.array(LUMINANCE_WEIGHTS)
        channel_rule = "luminance Y = 0.2126 R + 0.7152 G + 0.0722 B of the linearized channels (ITU-R BT.709)"
    else:
        roi_values = linear[:, :, "RGB".index(config.channel)]
        channel_rule = f"channel {config.channel}"
    result = measure_sfr(roi_values, fit_order=config.fit_order)
    frequency, sfr = result["frequency"], result["sfr"]
    pitch = config.pixel_pitch_mm

    sfr50 = crossing(frequency, sfr)
    at = [{"frequency_cy_per_px": _r(f), "frequency_cy_per_mm": _r(f / pitch) if pitch else None,
           "sfr": _r(value_at(frequency, sfr, f))} for f in config.at]
    at += [{"frequency_cy_per_px": _r(f * pitch), "frequency_cy_per_mm": _r(f),
            "sfr": _r(value_at(frequency, sfr, f * pitch))} for f in config.at_mm]
    issues = _issues(result, clipping, config, image)
    if sfr50 is None:
        issues.append({"code": "SFR50_NOT_REACHED", "message": f"the SFR stays at or above 0.5 up to "
                       f"{frequency[-1]:.3g} cy/px"})
    empty_fraction = float(result["empty"].mean())
    status = "UNKNOWN" if empty_fraction > UNKNOWN_EMPTY_FRACTION else "WARNING" if issues else "PASS"
    if status == "UNKNOWN":
        issues.insert(0, {"code": "INSUFFICIENT_PHASE_COVERAGE", "message": f"{empty_fraction:.0%} of the ESF bins "
                          "were empty: the edge angle and row count do not sample sub-pixel phases; the SFR is "
                          "not determined"})
    comparison = compare(result, design, pitch) if design is not None else None

    artifacts = {key: str(output_dir / name) for key, name in ARTIFACTS.items()
                 if (config.plot or "plot" not in key) and (design is not None or not key.startswith("comparison"))}
    measured_direction = "x (columns)" if result["orientation"] == "vertical" else "y (rows)"
    summary = {
        "status": status,
        "edge": {"orientation": result["orientation"], "sfr_direction": measured_direction,
                 "angle_deg": _r(result["angle_deg"]),
                 "position_at_roi_centre_px": _r(result["position_at_centre"] + (x if result["orientation"] ==
                                                                                 "vertical" else y)),
                 "polarity": result["polarity"], "rows": result["rows"], "rows_used": result["rows_used"],
                 "fit_order": config.fit_order, "fit_residual_rms_px": _r(result["residual_rms_px"]),
                 "phase_cycles": _r(result["phase_cycles"]),
                 "contrast": {key: _r(value) for key, value in result["contrast"].items()},
                 "noise_sd": _r(result["noise_sd"])},
        "sfr": {"convention": "magnitude (non-negative), 1 at zero frequency",
                "frequency_unit": "cycles/pixel along the edge normal",
                "sfr50_cy_per_px": _r(sfr50), "sfr50_cy_per_mm": _r(sfr50 / pitch) if sfr50 is not None and pitch
                else None, "sfr_at_nyquist": _r(value_at(frequency, sfr, NYQUIST_CY_PER_PX)),
                "nyquist_cy_per_mm": _r(NYQUIST_CY_PER_PX / pitch) if pitch else None,
                "at": at, "points": int(frequency.size), "frequency_step_cy_per_px": _r(frequency[1])},
        "esf": {"oversampling": OVERSAMPLING, "bin_width_px": result["bin_width_px"], "bins": int(result["empty"].size),
                "half_span_px": _r(result["half_span_px"]), "empty_bins": int(result["empty"].sum()),
                "empty_bin_fill": "linear interpolation between the nearest filled bins"},
        "clipping": clipping,
        "issues": issues,
        "identity": {"workflow": WORKFLOW, "input_sha256": input_hash},
        "artifacts": artifacts,
    }
    comparison_doc = None
    if comparison is not None:
        comparison_doc = _comparison_document(summary, result, comparison, design, pitch, input_path, input_hash)
        summary["comparison"] = {
            "design": design["description"], "frequency_unit": design["frequency_unit"],
            "scalars": {name: {key: value for key, value in values.items() if key != "at_design_frequencies"}
                        for name, values in comparison["scalars"].items()},
            "issues": comparison["issues"],
            "statement": "measured: system SFR magnitude; design: optical MTF only (pixel aperture, sampling and "
                         "image processing not included)"}
    settings = {**asdict(config), "roi": [x, y, w, h], "roi_source": "given" if config.roi else "whole image"}
    provenance = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(input_path), "sha256": input_hash, "bytes": len(data), "format": image.format,
                  "mode": image.mode, "bit_depth": image.bit_depth, "shape": [height, width],
                  "channels": list(image.channels)},
        "settings": settings,
        "method": {
            "linearization": {"linear": "values used as given (assumed linear in exposure)",
                              "gamma": f"(v / full_scale) ** {config.gamma}",
                              "srgb": "IEC 61966-2-1 inverse transfer on v / full_scale",
                              "table": "piecewise-linear OECF table, code -> linear"}[config.oecf],
            "full_scale": full_scale, "channel": channel_rule,
            "orientation": "mean |d/dx| against mean |d/dy| of the ROI; the larger names the edge across it",
            "edge_angle": "atan of the fitted edge-position slope from the image column (vertical edge) or row "
                          "(horizontal edge) direction; positive when the position grows with the row (vertical) "
                          "or column (horizontal) index",
            "edge_location": "per row: centroid of the central-difference derivative times a Hamming window "
                             "(first at the ROI centre, then twice at the fitted edge)",
            "edge_fit": f"least-squares polynomial of order {config.fit_order} of position against row",
            "projection": "distance along the local edge normal, (x - e(r)) cos(atan e'(r))",
            "binning": f"{OVERSAMPLING} bins per px, symmetric about the edge over the span every row covers",
            "lsf": "central difference of the ESF",
            "window": "Hamming (0.54 + 0.46 cos) centred on the LSF peak, 0.08 at the farther end",
            "sfr": SFR_CONVENTION,
            "not_corrected": "the 1/4 px bin average (sinc(f / 4), 0.974 at 0.5 cy/px) is part of the estimate, "
                             "as in ISO 12233",
        },
        "software": {"python": platform.python_version(), "numpy": np.__version__,
                     "tool_sha256": _sha256(Path(__file__).read_bytes()), "engine_required": False},
    }
    json.dumps(summary, allow_nan=False)
    if config.plot:
        require_matplotlib()
        import matplotlib

        provenance["software"]["matplotlib"] = matplotlib.__version__
    if design is not None:
        provenance["design"] = {key: design.get(key) for key in ("path", "sha256", "bytes", "kind")}

    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS.values():  # an overwritten directory keeps no stale artifact of an earlier run
        stale = output_dir / name
        if stale.is_file():
            stale.unlink()
    sfr_columns = {"frequency_cy_per_px": frequency}
    if pitch:
        sfr_columns["frequency_cy_per_mm"] = frequency / pitch
    sfr_columns.update(sfr=sfr, derivative_correction=result["correction"])
    _write_csv(artifacts["sfr_csv"], sfr_columns)
    _write_csv(artifacts["esf_csv"], {"distance_px": result["esf_distance"], "esf": result["esf"],
                                      "count": result["counts"], "filled": result["empty"]})
    _write_csv(artifacts["lsf_csv"], {"distance_px": result["esf_distance"], "lsf": result["lsf"],
                                      "window": result["window"], "lsf_windowed": result["lsf"] * result["window"]})
    _write_csv(artifacts["edge_csv"], {"row": np.arange(result["rows"]), "centroid_px": result["centroid"],
                                       "fit_px": result["location"], "residual_px": result["residual"],
                                       "used": result["valid"]})
    if config.plot:
        try:
            _plot(artifacts["plot"], roi_values, result, summary, pitch, input_path.name, input_hash)
            if comparison is not None:
                _plot_comparison(artifacts["comparison_plot"], result, comparison, design, pitch, input_hash)
        except ImportError as exc:  # matplotlib importable but broken
            raise SfrError("PLOT_EXTRA_MISSING", f"plotting failed: {exc}") from None
    if comparison is not None:
        _write_csv(artifacts["comparison_csv"], comparison["table"])
        Path(artifacts["comparison_json"]).write_text(json.dumps(comparison_doc, indent=2, allow_nan=False) + "\n",
                                                      encoding="utf-8")
    full = {**summary, "provenance": provenance}
    Path(artifacts["summary"]).write_text(json.dumps(full, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def _comparison_document(summary, result, comparison, design, pitch, input_path, input_hash):
    measured_direction = summary["edge"]["sfr_direction"]
    design_info = {key: design.get(key) for key in (
        "path", "sha256", "kind", "description", "frequency_unit", "line", "source_id", "wavelength_nm",
        "observation_id", "curve", "dropped_null_frequencies", "identity", "analysis_version", "execution_outcome",
        "axes") if design.get(key) is not None}
    differences = [
        {"aspect": "quantity", "measured": "system SFR: optics x pixel aperture x sampling x any processing in the "
                                           "image", "design": "optical MTF only"},
        {"aspect": "pixel aperture", "measured": "included", "design": "not included"},
        {"aspect": "sampling", "measured": "pre-sampling SFR by 4x oversampling; aliasing above Nyquist not "
                                           "separated", "design": "not included"},
        {"aspect": "image processing (demosaic, sharpening, noise reduction)", "measured": "whatever the image "
         "holds; not declared", "design": "not included"},
        {"aspect": "sign", "measured": "magnitude (non-negative)", "design": "signed values kept; compared as |MTF|"},
        {"aspect": "direction", "measured": f"edge normal along image {measured_direction}, edge angle "
         f"{summary['edge']['angle_deg']} deg", "design": "tangential / sagittal of the design field; which one "
         "matches the image x or y depends on where the ROI lies in the field"},
        {"aspect": "spectrum and field", "measured": "the light and field position of the image (not stated)",
         "design": f"line {design.get('line')}, {design.get('wavelength_nm')} nm" if design.get("line")
         else "as stated by the CSV's author"},
        {"aspect": "frequency unit", "measured": "cycles/pixel", "design": design["frequency_unit"] + (
            f", converted with f[cy/px] = f[cy/mm] x {pitch:g} mm" if design["frequency_unit"] == "cy/mm" else "")},
    ]
    return {
        "measured": {"input": str(input_path), "input_sha256": input_hash, "convention": SFR_CONVENTION,
                     "sfr_direction": measured_direction, "edge_angle_deg": summary["edge"]["angle_deg"],
                     "status": summary["status"]},
        "design": {**design_info, "convention": DESIGN_CONVENTION,
                   "includes": ["optics"], "not_included": ["pixel aperture", "sampling", "image processing"]},
        "pixel_pitch_mm": pitch,
        "grid": {"unit": "cy/px", "rule": "the measured frequencies inside each design curve's range; the design "
                                          "curve is interpolated linearly between its own frequencies, never "
                                          "extrapolated",
                 "points": len(comparison["table"]["frequency_cy_per_px"])},
        "input_differences": differences,
        "curves": comparison["scalars"],
        "issues": comparison["issues"],
    }


def _floats(text):
    try:
        return tuple(float(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise SfrError("BAD_ARGUMENT", f"expected comma-separated numbers, got {text!r}") from None


def _roi(text):
    parts = text.split(",")
    try:
        values = tuple(int(part) for part in parts)
    except ValueError:
        raise SfrError("ROI_INVALID", f"--roi takes four integers X,Y,W,H, got {text!r}") from None
    if len(values) != 4:
        raise SfrError("ROI_INVALID", f"--roi takes four integers X,Y,W,H, got {text!r}")
    return values


def main(argv=None):
    def workflow():
        parser = _Parser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("input", type=Path, help="PNG/TIFF image, .npy or .csv 2-D array")
        parser.add_argument("--out", required=True, type=Path, help="output directory (new, or empty, or --overwrite)")
        parser.add_argument("--roi", help="X,Y,W,H in pixels, origin top-left (default: whole image)")
        parser.add_argument("--channel", default="luminance", choices=("luminance", "R", "G", "B"),
                            help="RGB input: BT.709 luminance of the linearized channels (default) or one channel")
        parser.add_argument("--oecf", default="linear", choices=("linear", "gamma", "srgb", "table"),
                            help="linearization before the analysis (default linear: values used as given)")
        parser.add_argument("--gamma", type=float, help="with --oecf gamma: linear = (v / full_scale) ** GAMMA")
        parser.add_argument("--oecf-table", help="with --oecf table: CSV code,linear (piecewise linear)")
        parser.add_argument("--full-scale", type=float,
                            help="full-scale code (default 255 / 65535 for 8 / 16-bit data); for clipping and OECF")
        parser.add_argument("--fit-order", type=int, default=1, choices=(1, 2), help="edge fit: 1 line, 2 quadratic")
        pitch = parser.add_mutually_exclusive_group()
        pitch.add_argument("--pixel-pitch-um", type=float, help="square pixel pitch in micrometres: adds cycles/mm")
        pitch.add_argument("--pixel-pitch-mm", type=float, help="square pixel pitch in millimetres")
        parser.add_argument("--at", type=_floats, default=(), help="also report SFR at these cycles/pixel")
        parser.add_argument("--at-mm", type=_floats, default=(), help="also report SFR at these cycles/mm (needs pitch)")
        parser.add_argument("--design", help="'os-optics results imaging' JSON or frequency,value CSV to compare")
        parser.add_argument("--design-line", help="SOURCE#INDEX (or SOURCE) of the imaging result's line")
        parser.add_argument("--design-direction", default="both", choices=("both", *DIRECTIONS))
        parser.add_argument("--design-curve", default="mtf", choices=DESIGN_CURVES)
        parser.add_argument("--design-unit", choices=("cy/mm", "cy/px"), help="frequency unit of a design CSV")
        parser.add_argument("--no-plot", action="store_true", help="write no PNG (no matplotlib needed)")
        parser.add_argument("--overwrite", action="store_true", help="write into a non-empty --out")
        args = parser.parse_args(argv)
        if np is None:
            raise SfrError("NUMPY_MISSING", "sfr needs numpy, which is installed with os-optics: "
                                              "python -m pip install numpy")
        pitch_mm = args.pixel_pitch_mm if args.pixel_pitch_mm is not None else (
            args.pixel_pitch_um / 1000.0 if args.pixel_pitch_um is not None else None)
        config = SfrConfig(roi=_roi(args.roi) if args.roi is not None else None, channel=args.channel, oecf=args.oecf, gamma=args.gamma,
                           oecf_table=args.oecf_table, full_scale=args.full_scale, fit_order=args.fit_order,
                           pixel_pitch_mm=pitch_mm, at=args.at, at_mm=args.at_mm, design=args.design,
                           design_line=args.design_line, design_direction=args.design_direction,
                           design_curve=args.design_curve, design_unit=args.design_unit, plot=not args.no_plot,
                           overwrite=args.overwrite)
        return analyze_sfr(args.input, args.out, config)

    return emit_cli(workflow, exit_status_key="status")


__all__ = ["SfrConfig", "SfrError", "analyze_sfr", "compare", "crossing", "linearize", "measure_sfr",
           "read_design", "read_image", "synthetic_edge", "true_sfr"]


if __name__ == "__main__":
    raise SystemExit(main())
