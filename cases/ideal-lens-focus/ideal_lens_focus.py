"""Focus a Gaussian beam with an ideal thin lens, without a catalog download.

Usage: python examples/ideal_lens_focus.py --out run-ideal-focus
"""

import json
import math
from pathlib import Path

from os_optics import ValidationRequest, builders as B, validate_with_ray_paths
from os_optics.contracts import ProjectSnapshot, Target
from os_optics.results import results_from_engine

if __package__:
    from .result_summary import ArgumentParser, emit_cli, summarize_results
else:
    from result_summary import ArgumentParser, emit_cli, summarize_results


def build_project(maximum_radius_mm=0.05):
    """Construct the ideal-lens example; the bound is a 1/e^2 intensity radius."""
    if not math.isfinite(maximum_radius_mm) or maximum_radius_mm < 0:
        raise ValueError("maximum_radius_mm must be finite and nonnegative")
    return ProjectSnapshot(
        components=(
            B.laser("L", wavelength_nm=532.0, waist_radius_mm=1.0,
                    position=(0, 0, 20), direction=(1, 0, 0)),
            B.thin_lens("Lens", focal_length_mm=100.0, clear_radius_mm=12.7,
                        position=(10, 0, 20), axis=(1, 0, 0)),
        ),
        observations=(
            B.observation_plane("focus", position=(110, 0, 20),
                                normal=(1, 0, 0), radius_mm=5.0),
        ),
        targets=(
            Target(target_id="spot_radius", metric="beam_radius_mm",
                   observation_id="focus", maximum=maximum_radius_mm),
        ),
    )


def run_example(output_dir=Path("run-ideal-focus"), *, maximum_radius_mm=0.05, details=False):
    """Run once and save the full low-level input, result, rays, and arrivals."""
    request = ValidationRequest(project=build_project(maximum_radius_mm))
    result, ray_paths = validate_with_ray_paths(request)
    results = results_from_engine(result, ray_paths, numerical=request.configuration.numerical)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {"run_directory": output_dir}
    for role, document in (
        ("engine_request", request.model_dump(mode="json")),
        ("simulation_result", result.model_dump(mode="json")),
        ("ray_data", results.ray_data),
    ):
        path = output_dir / f"{role}.json"
        path.write_text(json.dumps(document, ensure_ascii=True, allow_nan=False), encoding="utf-8")
        artifacts[role] = path
    csv_path = output_dir / "arrivals.csv"
    csv_path.write_bytes(results.export("arrivals", "csv"))
    summary = summarize_results(results, artifacts=artifacts, details=details)
    summary["exports"] = {"arrivals_csv": str(csv_path)}
    return summary


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("--out", type=Path, default=Path("run-ideal-focus"), help="Artifact directory")
        parser.add_argument("--maximum-radius-mm", type=float, default=0.05,
                            help="Maximum 1/e^2 intensity radius in mm (default: 0.05)")
        parser.add_argument("--details", action="store_true", help="Include the complete power ledger")
        args = parser.parse_args(argv)
        return run_example(args.out, maximum_radius_mm=args.maximum_radius_mm, details=args.details)

    return emit_cli(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
