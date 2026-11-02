"""Bounded table of catalog products that pass explicit filters, from the installed public catalog release.

Usage:
  python examples/catalog_candidates.py --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \\
      --range focal_length_mm=90..110 --range outer_diameter_mm=12.7.. --near focal_length_mm=100 --limit 8 \\
      [--columns optical_sku,focal_length_mm,...] [--out candidates.csv]

Filters:
  --category, --form, --readiness (ORDERABLE | SIMULATION_ONLY), --mounting (AVAILABLE | NOT_AVAILABLE) and
  --wavelength-nm (a published simulation model of the product is valid at that wavelength) are applied by the
  Engine (json_api.catalog_products, summary rows).
  --range FIELD=MIN..MAX[UNIT] keeps products whose summary field lies within [MIN, MAX] (either side may be empty).
  FIELD is a scalar summary field (focal_length_mm, outer_diameter_mm, clear_aperture_diameter_mm, thickness_mm,
  design_wavelength_nm, center_wavelength_nm, bandwidth_fwhm_nm, edge_wavelength_nm, parent_focal_length_mm,
  off_axis_angle_deg, deviation_angle_deg, wedge_angle_deg, cone_angle_deg, reflectance, transmittance,
  retardance_waves, extinction_ratio, optical_density). MIN and MAX are in the field's unit (name suffix) unless UNIT
  is given (nm/um/mm/m, deg/rad/mrad/urad), e.g. focal_length_mm=0.09..0.11m.
Sorting: --near FIELD=VALUE (closest first), or --sort FIELD [--descending]; ties by SKU. Default: SKU.
Products without a --range, --near or --sort field are excluded and counted per field in excluded_missing_field
(engine_matches = matched + excluded by a missing field + outside a range).
--limit (default 10, at most 100) bounds the table; --out writes every matched row (sorted) as CSV.

Values are the catalog's decimal strings, compared exactly. Output: one JSON document with catalog_version, the
filters, engine_matches (products the Engine filters return), matched (after ranges), shown, omitted, units per
column and the rows. Exit 0 when rows match, 1 when no product matches, 4 on invalid input or when no catalog
release is installed (code CATALOG_NOT_AVAILABLE; install one with "os-optics catalog sync" or
"os-optics catalog import FILE").
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

if __package__:
    from . import workflow_common as wc
else:
    import workflow_common as wc

SCALAR_FIELDS = ("focal_length_mm", "outer_diameter_mm", "clear_aperture_diameter_mm", "thickness_mm",
                 "design_wavelength_nm", "center_wavelength_nm", "bandwidth_fwhm_nm", "edge_wavelength_nm",
                 "parent_focal_length_mm", "off_axis_angle_deg", "deviation_angle_deg", "wedge_angle_deg",
                 "cone_angle_deg", "reflectance", "transmittance", "retardance_waves", "extinction_ratio",
                 "optical_density")
DEFAULT_COLUMNS = ("optical_sku", "form", "outer_diameter_mm", "clear_aperture_diameter_mm", "working_band_nm",
                   "commercial_readiness", "model_kind")
MAX_LIMIT = 100
_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_RANGE = re.compile(rf"^(?P<field>[a-z0-9_]+)=(?P<min>{_NUMBER})?\.\.(?P<max>{_NUMBER})?(?P<unit>[A-Za-zµ]*)$")


def parse_range(text):
    """``FIELD=MIN..MAX[UNIT]`` -> ``(field, {"min", "max", "unit"})``."""
    match = _RANGE.match(text.strip())
    if not match:
        raise wc.WorkflowError("RANGE_INVALID", f"range {text!r} is not FIELD=MIN..MAX[UNIT]")
    return match["field"], {"min": float(match["min"]) if match["min"] else None,
                            "max": float(match["max"]) if match["max"] else None, "unit": match["unit"] or None}


def _field(field):
    if field not in SCALAR_FIELDS:
        raise wc.WorkflowError("FIELD_UNKNOWN", f"{field!r} is not a scalar summary field; use one of "
                               f"{', '.join(SCALAR_FIELDS)}", field=field)
    return field


def _limit(field, value, unit):
    if value is None:
        return None
    target = wc.field_unit(field)
    if unit is not None:
        if target is None:
            raise wc.WorkflowError("UNIT_INCOMPATIBLE", f"{field} has no unit; give a bare number", field=field)
        value = wc.convert(value, unit, target, path=field)
    return Decimal(repr(float(value)))


def _text(value):
    """Plain decimal text of a limit (``90.0`` -> ``"90"``), None stays None."""
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def candidates(*, category=None, form=None, wavelength_nm=None, readiness=None, mounting=None, ranges=None,
               near=None, sort=None, descending=False, limit=10, columns=None):
    """Filtered, sorted, bounded candidate rows (module docstring). ``ranges``: ``{field: {"min", "max", "unit"}}``;
    ``near``: ``(field, value)`` in the field's unit or ``(field, {"value", "unit"})``."""
    from os_optics.local import json_api

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise wc.WorkflowError("BAD_ARGUMENT", f"limit must be an integer from 1 to {MAX_LIMIT}")
    limits = {_field(f): (_limit(f, r.get("min"), r.get("unit")), _limit(f, r.get("max"), r.get("unit")))
              for f, r in (ranges or {}).items()}
    near_field = near_value = None
    if near is not None:
        near_field, value = _field(near[0]), near[1]
        near_value = _limit(near_field, value["value"], value["unit"]) if wc.is_quantity(value) else \
            _limit(near_field, value, None)
    if sort is not None:
        _field(sort)
    reply = json_api.catalog_products(category=category, form=form, wavelength_nm=wavelength_nm, summary=True,
                                      readiness=readiness, mounting=mounting)
    if not reply.get("ok"):
        problems = reply["error"].get("problems", [])
        code = problems[0]["code"] if problems else reply["error"].get("code")
        raise wc.WorkflowError(code, problems[0]["message"] if problems else "catalog query refused",
                               problems=problems)
    missing = {field: 0 for field in limits}
    matched = []
    for row in reply["products"]:
        keep = True
        for field, (low, high) in limits.items():
            value = _decimal(row.get(field))
            if value is None:
                missing[field] += 1
                keep = False
                break
            if (low is not None and value < low) or (high is not None and value > high):
                keep = False
                break
        if keep:
            matched.append(row)
    order_field = near_field or sort
    if order_field is not None:  # products without the ordering field cannot be placed: excluded and counted
        dropped = sum(1 for r in matched if _decimal(r.get(order_field)) is None)
        if dropped:
            missing[order_field] = missing.get(order_field, 0) + dropped
        matched = [r for r in matched if _decimal(r.get(order_field)) is not None]
    if near_field is not None:
        matched.sort(key=lambda r: (abs(_decimal(r[near_field]) - near_value), r["optical_sku"]))
    elif sort is not None:
        matched.sort(key=lambda r: r["optical_sku"])
        matched.sort(key=lambda r: _decimal(r[sort]), reverse=descending)
    else:
        matched.sort(key=lambda r: r["optical_sku"])
    chosen = list(columns) if columns else list(DEFAULT_COLUMNS[:2]) + [
        f for f in [*limits, near_field, sort] if f and f not in DEFAULT_COLUMNS] + list(DEFAULT_COLUMNS[2:])
    chosen = list(dict.fromkeys(chosen))
    table = [{c: row.get(c) for c in chosen} for row in matched]
    return {
        "status": "COMPLETED" if matched else "NO_MATCH",
        "catalog_version": reply["catalog_version"],
        "filters": {"category": category, "form": form, "wavelength_nm": wavelength_nm, "readiness": readiness,
                    "mounting": mounting,
                    "ranges": {f: {"min": _text(lo), "max": _text(hi),
                                   "unit": wc.field_unit(f)} for f, (lo, hi) in limits.items()},
                    "near": None if near_field is None else {"field": near_field, "value": _text(near_value),
                                                             "unit": wc.field_unit(near_field)},
                    "sort": sort, "descending": descending},
        "engine_matches": reply["total"], "matched": len(matched), "excluded_missing_field": missing,
        "shown": min(limit, len(matched)), "omitted": max(0, len(matched) - limit),
        "units": {c: wc.field_unit(c) for c in chosen if wc.field_unit(c)},
        "rows": table[:limit], "all_rows": table,
    }


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("--category", help="Catalog category, e.g. LENS, MIRROR, WAVEPLATE, POLARIZER")
        parser.add_argument("--form", help="Catalog form, e.g. PLANO_CONVEX_LENS")
        parser.add_argument("--wavelength-nm", help="Wavelength in nm at which a simulation model must be valid")
        parser.add_argument("--readiness", choices=["ORDERABLE", "SIMULATION_ONLY"])
        parser.add_argument("--mounting", choices=["AVAILABLE", "NOT_AVAILABLE"])
        parser.add_argument("--range", action="append", default=[], metavar="FIELD=MIN..MAX[UNIT]",
                            help="Numeric range on a summary field (repeatable)")
        parser.add_argument("--near", metavar="FIELD=VALUE[UNIT]", help="Sort by distance to VALUE")
        parser.add_argument("--sort", metavar="FIELD", help="Sort by a summary field (ascending)")
        parser.add_argument("--descending", action="store_true", help="With --sort: largest first")
        parser.add_argument("--limit", type=int, default=10, help=f"Rows shown (1..{MAX_LIMIT}, default 10)")
        parser.add_argument("--columns", help="Comma-separated row keys to show")
        parser.add_argument("--out", type=Path, help="CSV file for every matched row")
        args = parser.parse_args(argv)
        ranges = dict(parse_range(text) for text in args.range)
        near = None
        if args.near:
            match = re.match(rf"^([a-z0-9_]+)=({_NUMBER})([A-Za-zµ]*)$", args.near.strip())
            if not match:
                raise wc.WorkflowError("RANGE_INVALID", f"--near {args.near!r} is not FIELD=VALUE[UNIT]")
            near = (match[1], {"value": float(match[2]), "unit": match[3]} if match[3] else float(match[2]))
        result = candidates(category=args.category, form=args.form, wavelength_nm=args.wavelength_nm,
                            readiness=args.readiness, mounting=args.mounting, ranges=ranges, near=near,
                            sort=args.sort, descending=args.descending, limit=args.limit,
                            columns=args.columns.split(",") if args.columns else None)
        rows = result.pop("all_rows")
        if args.out:
            columns = list(rows[0]) if rows else list(result["units"])
            wc.write_csv(args.out, rows, columns)
            result["csv"] = args.out.as_posix()
        return result

    return wc.emit(workflow, {**wc.WORKFLOW_EXIT_CODES, "NO_MATCH": 1})


if __name__ == "__main__":
    raise SystemExit(main())
