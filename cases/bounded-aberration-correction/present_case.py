"""Create a gallery of the Gaussian relay and saved scalar Fourier aberration results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from os_optics_kit import gallery, present, presentation as pr
from os_optics_kit.render_views import camera_direction, left_to_right, render_shots


TITLES = {
    "relay": {"en": "Gaussian relay and pupil plane",
              "zh-CN": "高斯扩束光路与光瞳平面"},
    "psf": {"en": "Five controls recover the focal core",
            "zh-CN": "五个控制量恢复焦斑中心强度"},
    "response": {"en": "Uncontrolled spherical aberration limits recovery",
                 "zh-CN": "不受控球差限制强度恢复"},
}
CAPTIONS = {
    "relay": {"en": "The Gaussian relay provides a 1.300313 mm beam radius at the pupil plane, within the 1.29–1.31 mm target.",
              "zh-CN": "高斯扩束光路在光瞳平面提供 1.300313 mm 光束半径，满足 1.29–1.31 mm 目标。"},
    "psf": {"en": "External scalar Fourier PSFs from the first saved run. Equal transmitted power; "
                   "one shared linear scale normalized to the ideal on-axis intensity.",
            "zh-CN": "第一组保存结果的外部标量傅里叶 PSF。透射功率相同，三图共用以理想轴上强度归一化的线性色标。"},
    "response": {"en": "Intensity at the fixed optical-axis position, normalized to each run's equal-power ideal "
                        "on-axis intensity. Five modes are controlled; the spherical coefficient stays fixed during correction.",
                 "zh-CN": "固定光轴位置的强度，以每组等功率理想轴上强度归一化。控制五种模式，球差系数在校正过程中保持固定。"},
}
STATES = {"en": ["Ideal", "Disturbed", "Corrected"], "zh-CN": ["理想", "受扰", "校正后"]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_run(path):
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    with np.load(path / "science.npz", allow_pickle=False) as saved:
        axis = saved["focal_x_mm"]
        intensity = saved["focal_intensity_W_per_mm2"]
        peak = float(saved["ideal_peak_W_per_mm2"])
        fields = saved["pupil_fields_sqrt_W_per_mm"]
        coherent = np.abs(fields.sum(axis=(1, 2))) ** 2
        ratios = coherent / coherent[0]
    expected = [metrics["onaxis_intensity_ratio"][s] for s in ("ideal", "disturbed", "corrected")]
    if not np.allclose(ratios, expected, rtol=0, atol=1e-10):
        raise ValueError(f"Saved fields and metrics disagree: {path.name}")
    if intensity.shape != (3, len(axis), len(axis)) or not np.isfinite(intensity).all() or peak <= 0:
        raise ValueError(f"Invalid saved PSF arrays: {path.name}")
    center = int(np.argmin(np.abs(axis)))
    if not np.allclose(intensity[:, center, center] / peak, expected, rtol=0, atol=1e-10):
        raise ValueError(f"Saved PSF centers and metrics disagree: {path.name}")
    return {"path": path, "config": config, "metrics": metrics, "axis_mm": axis,
            "intensity": intensity, "peak": peak, "ratios": ratios,
            "hashes": {name: digest(path / name) for name in ("science.npz", "metrics.json", "config.json")}}


def psf_figure(data, theme):
    lang = theme.language
    axis = data["axis_mm"] * 1000
    step = (axis[1] - axis[0]) / 2
    extent = [axis[0] - step, axis[-1] + step, axis[0] - step, axis[-1] + step]
    figsize, dpi = theme.canvas()
    fig, axes = plt.subplots(1, 3, figsize=figsize, dpi=dpi)
    fig.subplots_adjust(left=.055, right=.87, bottom=.19, top=.80, wspace=.14)
    fig.suptitle(TITLES["psf"][lang], y=.94)
    for index, ax in enumerate(axes):
        im = ax.imshow(data["intensity"][index] / data["peak"], origin="lower", extent=extent,
                       vmin=0, vmax=1, cmap="magma", interpolation="nearest")
        ax.set_title(f"{STATES[lang][index]}\n{data['ratios'][index]:.3f}", fontsize=theme.font_pt("panel"))
        ax.set_xlabel("x [µm]")
        ax.set_xticks([-60, 0, 60])
        ax.set_yticks([-60, 0, 60])
        ax.grid(False)
        if index == 0:
            ax.set_ylabel("y [µm]")
        else:
            ax.set_yticklabels([])
    cax = fig.add_axes([.90, .24, .018, .46])
    bar = fig.colorbar(im, cax=cax)
    bar.set_label("I / I ideal(0)" if lang == "en" else "强度 / 理想轴上强度")
    note = (f"External scalar PSF · spherical {data['config']['spherical_waves']:.3f} waves · "
            f"{data['metrics']['transmitted_power_W'] * 1000:.3f} mW") if lang == "en" else (
            f"外部标量 PSF · 球差 {data['config']['spherical_waves']:.3f} waves · "
            f"{data['metrics']['transmitted_power_W'] * 1000:.3f} mW")
    fig.text(.5, .09, note, ha="center", fontsize=theme.font_pt("small"))
    return fig


def response_figure(runs, theme):
    lang = theme.language
    figsize, dpi = theme.canvas()
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.subplots_adjust(left=.13, right=.96, bottom=.22, top=.83)
    fig.suptitle(TITLES["response"][lang], y=.95)
    x = np.arange(len(runs))
    for index in (1, 2):
        values = [float(run["ratios"][index]) for run in runs]
        bars = ax.bar(x + (index - 1.5) * .30, values, .28,
                      label=STATES[lang][index], color=theme.series_color(index - 1))
        ax.bar_label(bars, labels=[f"{value:.3f}" for value in values], padding=5,
                     fontsize=theme.font_pt("small"), color=theme.colors["text"])
    ax.axhline(1, linestyle="--", color=theme.colors["muted"], linewidth=1)
    ax.set_ylim(0, 1.16)
    ax.set_xticks(x, [f"{run['config']['spherical_waves']:.3f}" for run in runs])
    ax.set_xlabel("Uncontrolled spherical coefficient [waves]" if lang == "en" else "不受控球差系数 [waves]")
    ax.set_ylabel("On-axis intensity / ideal" if lang == "en" else "轴上强度 / 理想值")
    ax.legend(loc="upper right", ncols=2)
    ax.grid(axis="x", visible=False)
    note = "External scalar Fourier model · equal-power ideal = 1" if lang == "en" else "外部标量傅里叶模型 · 等功率理想值 = 1"
    fig.text(.5, .065, note, ha="center", fontsize=theme.font_pt("small"))
    return fig


def add_card(out, asset_id, figure, runs, theme, theme_config):
    folder = out / "assets" / asset_id
    folder.mkdir(parents=True)
    files = {}
    for fmt in ("png", "svg"):
        path = folder / f"{asset_id}.{fmt}"
        figure.savefig(path, format=fmt)
        files[fmt] = pr.file_record(path, out)
    plt.close(figure)
    selected = runs[:1] if asset_id == "psf" else runs
    snapshot = folder / "plot-data.npz"
    arrays = {"onaxis_intensity_ratio": np.array([r["ratios"] for r in selected]),
              "spherical_waves": np.array([r["config"]["spherical_waves"] for r in selected])}
    if asset_id == "psf":
        arrays.update(focal_x_mm=runs[0]["axis_mm"], focal_intensity_W_per_mm2=runs[0]["intensity"],
                      ideal_peak_W_per_mm2=np.array(runs[0]["peak"]))
    np.savez_compressed(snapshot, **arrays)
    details = {"origin": "external scalar Fourier calculation from the Gaussian relay handoff",
               "selection": "first saved run, all three states" if asset_id == "psf" else "all saved runs, on-axis ratios",
               "states": ["ideal", "disturbed", "corrected"],
               "units": {"focal_x_mm": "mm (displayed as µm)", "focal_intensity_W_per_mm2": "W/mm²",
                         "ideal_peak_W_per_mm2": "W/mm²", "onaxis_intensity_ratio": "1", "spherical_waves": "waves"},
               "normalization": "each run's equal-power ideal on-axis intensity",
               "color_scale": {"minimum": 0, "maximum": 1, "linear": True} if asset_id == "psf" else None}
    redraw = {"schema": "bounded-aberration-correction.redraw", "schema_version": 1,
              "helper": "present_case.py", "theme": theme_config, "asset": asset_id, "data": "plot-data.npz",
              "saved_runs": [pr.portable_path(r["path"], folder) for r in runs],
              "run_metadata": [{"config": r["config"], "transmitted_power_W": r["metrics"]["transmitted_power_W"]}
                               for r in selected],
              "rebuild": "python present_case.py RUN [VARIANT...] --out NEW --language LANGUAGE --background BACKGROUND --size WIDTHxHEIGHT",
              "scope": "presentation.json rebuilds only the native radius target; this case helper redraws the view and external plots",
              **details}
    write_json(folder / "redraw.json", redraw)
    shutil.copyfile(__file__, folder / "present_case.py")
    provenance = {"schema": "bounded-aberration-correction.external-provenance", "schema_version": 1,
                  "asset": asset_id, "theme": theme.summary(), "details": details,
                  "saved_inputs": [{"label": r["path"].name, "sha256": r["hashes"],
                                    "config": r["config"], "versions": r["metrics"]["versions"]} for r in selected],
                  "data": pr.file_record(snapshot, out), "no_solver_invoked": True}
    write_json(folder / "provenance.json", provenance)
    for role, name in (("data", "plot-data.npz"), ("redraw", "redraw.json"),
                       ("helper", "present_case.py"), ("provenance", "provenance.json")):
        files[role] = pr.file_record(folder / name, out)
    captions = {}
    for language in ("en", "zh-CN"):
        path = folder / f"caption.{language}.txt"
        path.write_text(CAPTIONS[asset_id][language] + "\n", encoding="utf-8")
        captions[language] = pr.portable_path(path, out)
        files[f"caption_{language}"] = pr.file_record(path, out)
    preview = out / "previews" / f"{asset_id}.png"
    pr.write_preview(folder / f"{asset_id}.png", preview)
    return {"id": asset_id, "kind": "plot", "status": "written", "title": TITLES[asset_id][theme.language],
            "titles": TITLES[asset_id], "caption": CAPTIONS[asset_id][theme.language], "captions": CAPTIONS[asset_id],
            "files": files, "preview": pr.portable_path(preview, out), "size_px": list(theme.size),
            "caption_files": captions, "provenance": pr.portable_path(folder / "provenance.json", out),
            "sources": [], "depends_on": [], "details": details,
            "fingerprint": pr.fingerprint(redraw, [r["hashes"] for r in selected])}


def relay_card(out, run, theme, spec):
    folder = out / "assets" / "relay"
    direction, up = camera_direction([0, 0, 1], 80, 10, up_axis=[0, 1, 0])
    direction, up = left_to_right(direction, up, [0, 0, 1], world_up=[0, 1, 0])
    shot = {"name": "relay", "direction": direction, "up": up, "roll_deg": 0}
    camera = {"axis": "0,0,1", "up_axis": [0, 1, 0], "shots": [shot], "focus": "scene"}
    captured = render_shots(run["path"] / "engine", [shot], folder, axis=camera["axis"],
                           up_axis=camera["up_axis"], focus=camera["focus"],
                           size=theme.size, background=theme.render_background())
    if captured[0]["status"] != "captured":
        raise RuntimeError("Native relay view failed: " + str(captured[0].get("error")))
    image_path = folder / "relay.png"
    annotated = folder / "relay.annotated.png"
    present.write_annotated(image_path, annotated, theme, TITLES["relay"][theme.language], "")
    write_json(folder / "redraw.json", {"helper": "present_case.py", "camera": camera,
               "theme": spec["theme"], "saved_run": pr.portable_path(run["path"], folder),
               "origin": "native saved engine relay rendered with the public render_shots API"})
    source = pr.resolve_source(out, "relay", spec["sources"]["relay"])
    provenance = pr.provenance_record({"id": "relay", "kind": "view", **camera}, theme,
                                      [source], [], out, extra={"no_solver_invoked": True})
    write_json(folder / "provenance.json", provenance)
    files = {"image": pr.file_record(image_path, out), "annotated": pr.file_record(annotated, out),
             "redraw": pr.file_record(folder / "redraw.json", out),
             "provenance": pr.file_record(folder / "provenance.json", out)}
    captions = {}
    for lang in ("en", "zh-CN"):
        path = folder / f"caption.{lang}.txt"
        path.write_text(CAPTIONS["relay"][lang] + "\n", encoding="utf-8")
        captions[lang] = pr.portable_path(path, out)
        files[f"caption_{lang}"] = pr.file_record(path, out)
    preview = out / "previews" / "relay.png"
    pr.write_preview(annotated, preview)
    return {"id": "relay", "kind": "view", "status": "written", "title": TITLES["relay"][theme.language],
            "titles": TITLES["relay"], "caption": CAPTIONS["relay"][theme.language], "captions": CAPTIONS["relay"],
            "files": files, "preview": pr.portable_path(preview, out), "size_px": list(theme.size),
            "caption_files": captions, "provenance": pr.portable_path(folder / "provenance.json", out),
            "sources": ["relay"], "depends_on": [], "details": camera,
            "fingerprint": pr.fingerprint(camera, source.identity, theme.summary())}


def redraw_external(config_path, out, theme_config):
    """Redraw one bundled card solely from its selected data snapshot."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema") != "bounded-aberration-correction.redraw":
        raise ValueError("Use an external card's redraw.json.")
    theme_config = {**config["theme"], **theme_config}
    theme = pr.resolve_theme(theme_config)
    with np.load(config_path.parent / config["data"], allow_pickle=False) as data:
        runs = [{"config": meta["config"], "metrics": {"transmitted_power_W": meta["transmitted_power_W"]},
                 "ratios": data["onaxis_intensity_ratio"][index]}
                for index, meta in enumerate(config["run_metadata"])]
        if config["asset"] == "psf":
            runs[0].update(axis_mm=data["focal_x_mm"], intensity=data["focal_intensity_W_per_mm2"],
                           peak=float(data["ideal_peak_W_per_mm2"]))
    out.mkdir(parents=True)
    with plt.rc_context(theme.rc()):
        fig = psf_figure(runs[0], theme) if config["asset"] == "psf" else response_figure(runs, theme)
        pr.check_glyphs([text.get_text() for text in fig.findobj(matplotlib.text.Text)], theme)
        for fmt in ("png", "svg"):
            fig.savefig(out / f"{config['asset']}.{fmt}", format=fmt)
        plt.close(fig)
    config["theme"] = theme_config
    shutil.copyfile(config_path.parent / config["data"], out / "plot-data.npz")
    shutil.copyfile(__file__, out / "present_case.py")
    write_json(out / "redraw.json", config)
    print(json.dumps({"status": "COMPLETED", "asset": config["asset"], "snapshot_only": True, "out": str(out)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="*", type=Path, metavar="RUN")
    parser.add_argument("--redraw-external", type=Path, metavar="CONFIG")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--language", choices=("en", "zh-CN"))
    parser.add_argument("--background", choices=("light", "dark"))
    parser.add_argument("--size")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    paths = [path.resolve() for path in args.runs]
    protected = [*paths]
    if args.redraw_external:
        protected.append(args.redraw_external.resolve().parent)
    destinations = [out, *([args.bundle.resolve()] if args.bundle else [])]
    if out.exists() or any(dest.is_relative_to(path) or path.is_relative_to(dest)
                           for dest in destinations for path in protected):
        parser.error("Use a new output directory outside the saved runs.")
    theme_config = {key: value for key, value in (("language", args.language),
                    ("background", args.background), ("size", args.size)) if value is not None}
    if args.redraw_external:
        if paths or args.bundle:
            parser.error("Snapshot redraw accepts no runs or bundle destination.")
        redraw_external(args.redraw_external.resolve(), out, theme_config)
        return
    if not paths:
        parser.error("Supply RUN [VARIANT...] or --redraw-external CONFIG.")
    theme_config = {"preset": "presentation", "language": "en", "background": "light",
                    "size": "1920x1080", "labels": {
                        "target:relay_radius": {"en": "Relay 1/e² radius", "zh-CN": "扩束光束 1/e² 半径"}},
                    **theme_config}
    runs = [read_run(path) for path in paths]
    reference = {key: value for key, value in runs[0]["config"].items() if key != "spherical_waves"}
    if any({key: value for key, value in run["config"].items() if key != "spherical_waves"} != reference for run in runs):
        parser.error("Comparison runs must differ only in spherical_waves (same bounds, optics and grids).")
    theme = pr.resolve_theme(theme_config)
    out.mkdir(parents=True)
    spec = pr.default_spec()
    spec.update(title=TITLES["psf"], theme=theme_config,
                sources={"relay": {"path": pr.portable_path(paths[0] / "engine", out),
                                   "label": {"en": "Gaussian relay", "zh-CN": "高斯扩束光路"}}},
                assets=[{"id": "relay-radius", "kind": "plot", "type": "targets", "sources": ["relay"],
                         "title": {"en": "Relay beam radius", "zh-CN": "扩束光束半径"},
                         "caption": CAPTIONS["relay"]}])
    pr.save_spec(spec, out / "presentation.json")
    built = present.build(out / "presentation.json", gallery=False, quiet=True)
    if built["status"] != "COMPLETED":
        raise RuntimeError(json.dumps(built, ensure_ascii=False))
    manifest = pr.read_manifest(out)
    manifest["assets"].insert(0, relay_card(out, runs[0], theme, spec))
    with plt.rc_context(theme.rc()):
        for asset_id, figure in (("psf", psf_figure(runs[0], theme)), ("response", response_figure(runs, theme))):
            strings = [text.get_text() for text in figure.findobj(matplotlib.text.Text)]
            pr.check_glyphs(strings, theme)
            manifest["assets"].append(add_card(out, asset_id, figure, runs, theme, theme_config))
    manifest["counts"]["written"] += 3
    pr.write_manifest(out, manifest)
    manifest["gallery"] = gallery.write_gallery(out)
    pr.write_manifest(out, manifest)
    bundle = gallery.write_bundle(out, args.bundle) if args.bundle else None
    for run in runs:
        for name, original in run["hashes"].items():
            if digest(run["path"] / name) != original:
                raise RuntimeError(f"Saved input changed: {run['path'].name}/{name}")
    print(json.dumps({"status": "COMPLETED", "assets": ["relay", "relay-radius", "psf", "response"],
                      "out": str(out), "saved_inputs_unchanged": True, "bundle": bundle}, ensure_ascii=False))


if __name__ == "__main__":
    main()
