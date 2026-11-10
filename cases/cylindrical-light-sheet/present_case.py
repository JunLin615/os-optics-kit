"""Present saved light-sheet results; never construct or solve an optical model.

Native assets use present.build. The longitudinal map and FWHM curves are an
explicit case-local external plot, registered through the public asset index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import matplotlib
import numpy as np
from os_optics_kit import gallery, present, presentation as pres

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def bilingual(en, zh):
    return {"en": en, "zh-CN": zh}


TITLE = bilingual("Light-sheet thickness and range", "光片厚度与传播范围")
CAPTION = bilingual(
    "External redraw of saved Gaussian profiles. The map is the first run at x = 0; curves use each run's own "
    "waist origin. Dots mark ±zR, the full sqrt(2)-thickness range. FWHM = sqrt(2 ln 2) × radius. No solver runs.",
    "由保存的高斯分布外部重绘。纵截面取首个结果的 x = 0；各曲线以自身束腰为原点。圆点标记 ±zR，"
    "即 sqrt(2) 倍厚度的全范围。FWHM = sqrt(2 ln 2) × 半径。不运行求解器。")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path, root):
    value = pres.portable_path(path, root)
    if value is None:
        raise ValueError("Sources and output must be on the same drive.")
    return value


def load_runs(paths):
    records, arrays = [], {}
    for i, root in enumerate(paths):
        if not (root / "run" / "manifest.json").is_file():
            raise ValueError(f"Missing saved native run in {root}")
        config, metrics = read_json(root / "configuration.json"), read_json(root / "metrics.json")
        with np.load(root / "profiles.npz", allow_pickle=False) as source:
            z = source["z_from_waist_mm"]
            width = source["thin_radius_mm"] * np.sqrt(2 * np.log(2)) * 1000
            if not np.all(np.isfinite(width)) or not np.isclose(width.min(), metrics["thickness_fwhm_um"], rtol=1e-9):
                raise ValueError("Saved profile and waist metric disagree.")
            if i == 0:
                arrays.update(map_z_mm=z, map_y_mm=source["y_mm"],
                              map_intensity_W_per_mm2=source["intensity_x0_W_per_mm2"])
            arrays[f"z_{i}_mm"], arrays[f"fwhm_{i}_um"] = z, width
        records.append({"id": f"run-{i}", "focal_length_mm": config["focal_length_mm"],
                        "thickness_fwhm_um": metrics["thickness_fwhm_um"],
                        "range_sqrt2_mm": metrics["range_sqrt2_mm"],
                        "waist_limit_um": config["max_thickness_fwhm_um"],
                        "input_hashes": {name: digest(root / name) for name in
                                         ("profiles.npz", "metrics.json", "configuration.json")}})
    return {"origin": "External profiles saved by cylindrical-light-sheet/workflow.py from native q and power",
            "units": {"z": "mm relative to each waist", "y": "mm", "intensity_x0": "W/mm^2",
                      "thickness": "um FWHM", "range": "mm full sqrt(2)-thickness range"},
            "map_run": "run-0", "runs": records}, arrays


def native_spec(paths, data, theme, out):
    sources = {record["id"]: {"path": relative(root / "run", out),
                              "label": f"f = {record['focal_length_mm']:g} mm"}
               for root, record in zip(paths, data["runs"])}
    return {"schema": pres.SPEC_SCHEMA, "schema_version": 1, "title": TITLE, "theme": theme,
            "sources": sources, "assets": [
                {"id": "targets", "kind": "plot", "type": "targets", "sources": list(sources),
                 "targets": ["thin-waist", "sheet-range"], "reference": "run-0",
                 "title": bilingual("Native radius and Rayleigh range targets", "原生半径与瑞利长度目标"),
                 "caption": bilingual("Native 1/e² waist radius and zR; these are not FWHM and full range.",
                                      "原生 1/e² 束腰半径与 zR；对应量分别为半径和瑞利长度。")}]}


def system_asset(out, root, theme_settings):
    # present 0.3.0 has no up_axis field; its default Z up is parallel to this
    # model's real Z optical axis. The public renderer accepts the required Y up.
    from os_optics_kit.render_views import camera_direction, left_to_right, render_shots

    theme = pres.resolve_theme(theme_settings)
    folder = out / "assets" / "system"
    folder.mkdir(parents=True)
    direction, up = camera_direction([0, 0, 1], 80, 10, up_axis=[0, 1, 0])
    direction, up = left_to_right(direction, up, [0, 0, 1], world_up=[0, 1, 0])
    shots = render_shots(root / "run", [{"name": "system", "direction": direction, "up": up}], folder,
                         axis="0,0,1", up_axis=[0, 1, 0], size=theme.size,
                         background=theme.render_background(), focus="scene", edges=True)
    if shots[0]["status"] != "captured":
        raise RuntimeError(f"Native view render failed: {shots[0].get('error')}")
    write_json(folder / "camera.json", {"renderer": "os_optics_kit.render_views.render_shots", "source": "run-0",
                                        "axis": [0, 0, 1], "up_axis": [0, 1, 0], "camera": [80, 10], "shot": shots[0]})
    title = bilingual("Cylindrical lens and beam", "柱面透镜与光束")
    caption = bilingual("Saved native reference run; propagation is left to right. Public render uses Z axis and Y up.",
                        "保存的原生参考结果；光沿左至右传播。公开 render 接口使用 Z 光轴与 Y 向上。")
    asset = {"id": "system", "kind": "view", "title": title, "caption": caption}
    provenance = pres.provenance_record(asset, theme, [], [folder / "camera.json"], out,
                                        extra={"origin": "Saved native run through the official public renderer",
                                               "native_identity": read_json(root / "metrics.json")["engine_identity"],
                                               "native_present_build_supported": False,
                                               "reason": "present 0.3.0 view cannot pass nonparallel up_axis for Z models"})
    write_json(folder / "provenance.json", provenance)
    captions = {}
    for language, value in caption.items():
        path = folder / f"caption.{language}.txt"
        path.write_text(value + "\n", encoding="utf-8")
        captions[language] = relative(path, out)
    image = folder / "system.png"
    preview = pres.write_preview(image, out / "previews" / "system.png")
    return {"id": "system", "kind": "view", "status": "written", "title": title[theme.language], "titles": title,
            "caption": caption[theme.language], "captions": caption, "sources": ["run-0"], "depends_on": [],
            "files": {"image": pres.file_record(image, out), "camera": pres.file_record(folder / "camera.json", out)},
            "preview": relative(preview, out), "size_px": pres.image_size(image),
            "provenance": relative(folder / "provenance.json", out), "caption_files": captions,
            "details": {"renderer": "public render_shots", "present_up_axis_gap": True}}


def external_asset(out, data, arrays, theme_settings):
    theme = pres.resolve_theme(theme_settings)
    lang = theme.language
    def text(en, zh):
        return zh if lang == "zh-CN" else en
    labels = [text("Distance from each waist (mm)", "距各自束腰的距离 (mm)"),
              text("Thin-axis coordinate y (um)", "薄轴坐标 y (um)"),
              text("Thickness FWHM (um)", "厚度 FWHM (um)"),
              text("Intensity (W/mm²)", "强度 (W/mm²)"),
              text("Thickness curves · dots at ±zR", "厚度曲线 · 圆点为 ±zR")]
    pres.check_glyphs([*labels, TITLE[lang], CAPTION[lang]], theme)
    folder = out / "assets" / "profiles"
    folder.mkdir(parents=True)
    write_json(folder / "data.json", data)
    np.savez_compressed(folder / "profiles.npz", **arrays)
    redraw = {"schema": "cylindrical-light-sheet.external-redraw", "schema_version": 1,
              "helper": "present_case.py", "data": "data.json", "arrays": "profiles.npz", "theme": theme_settings,
              "command": "python present_case.py --redraw-external case-redraw.json --out NEW_DIR",
              "scope": "External map and FWHM only; native assets use presentation.json and saved native runs."}
    write_json(folder / "case-redraw.json", redraw)
    shutil.copyfile(__file__, folder / "present_case.py")
    figsize, dpi = theme.canvas()
    with plt.rc_context(theme.rc()):
        fig, axes = plt.subplots(1, 2, figsize=figsize, dpi=dpi)
        fig.subplots_adjust(left=.075, right=.97, bottom=.16, top=.80, wspace=.58)
        intensity = arrays["map_intensity_W_per_mm2"]
        image = axes[0].pcolormesh(arrays["map_z_mm"], arrays["map_y_mm"] * 1000,
                                   intensity.T, shading="auto", cmap="magma", rasterized=True)
        fig.colorbar(image, ax=axes[0], fraction=.055, pad=.025).set_label(labels[3])
        axes[0].set(xlabel=labels[0], ylabel=labels[1],
                    title=text("Reference longitudinal map", "参考方案纵截面"))
        axes[0].grid(False)
        for i, row in enumerate(data["runs"]):
            color = theme.series_color(i)
            label = (f"f = {row['focal_length_mm']:g} mm\n"
                     f"{row['thickness_fwhm_um']:.3f} um / {row['range_sqrt2_mm']:.3f} mm")
            axes[1].plot(arrays[f"z_{i}_mm"], arrays[f"fwhm_{i}_um"], color=color, label=label)
            half = row["range_sqrt2_mm"] / 2
            axes[1].plot([-half, half], [row["thickness_fwhm_um"] * np.sqrt(2)] * 2, "o", color=color)
        limits = sorted({row["waist_limit_um"] for row in data["runs"]})
        for limit in limits:
            axes[1].axhline(limit, color=theme.colors["muted"], linestyle="--", linewidth=1,
                            label=text(f"Waist limit {limit:g} um", f"束腰上限 {limit:g} um"))
        axes[1].set(xlabel=labels[0], ylabel=labels[2], title=labels[4])
        axes[1].legend(loc="upper center", fontsize=theme.font_pt("small") * .85)
        fig.suptitle(TITLE[lang], y=.94)
        fig.text(.5, .045, text("Legend: waist FWHM / full sqrt(2)-thickness range",
                               "图例数值：束腰 FWHM / sqrt(2) 倍厚度全范围"), ha="center",
                 fontsize=theme.font_pt("small"))
        for fmt in ("png", "svg"):
            fig.savefig(folder / f"profiles.{fmt}", dpi=dpi)
        plt.close(fig)
    files = {"image": folder / "profiles.png", "svg": folder / "profiles.svg",
             "data": folder / "data.json", "arrays": folder / "profiles.npz", "redraw": folder / "case-redraw.json",
             "helper": folder / "present_case.py"}
    captions = {}
    for language, caption in CAPTION.items():
        path = folder / f"caption.{language}.txt"
        path.write_text(caption + "\n", encoding="utf-8")
        captions[language] = relative(path, out)
    asset = {"id": "profiles", "kind": "plot", "title": TITLE, "caption": CAPTION}
    provenance = pres.provenance_record(asset, theme, [], [files[role] for role in ("data", "arrays", "redraw")], out,
                                        extra={"origin": data["origin"], "renderer": "case-local present_case.py",
                                               "native_present_build_supported": False, "units": data["units"]})
    write_json(folder / "provenance.json", provenance)
    preview = pres.write_preview(files["image"], out / "previews" / "profiles.png")
    return {"id": "profiles", "kind": "plot", "status": "written", "title": TITLE[lang], "titles": TITLE,
            "caption": CAPTION[lang], "captions": CAPTION, "files": {k: pres.file_record(v, out) for k, v in files.items()},
            "preview": relative(preview, out), "size_px": pres.image_size(files["image"]),
            "provenance": relative(folder / "provenance.json", out), "caption_files": captions,
            "sources": [], "depends_on": [], "details": {"external": True, "origin": data["origin"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="*", metavar="RUN")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--language", choices=("en", "zh-CN"))
    parser.add_argument("--background", choices=("light", "dark"))
    parser.add_argument("--size", help="Canvas pixels, e.g. 1920x1080")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--redraw-external", type=Path, metavar="CONFIG",
                        help="Redraw only the bundled external profiles from case-redraw.json")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Use a new, empty --out directory for each style.")
    theme = {"language": "en", "background": "light", "size": "1920x1080", "labels": {
        "target:thin-waist": bilingual("Waist 1/e² radius", "束腰 1/e² 半径"),
        "target:sheet-range": bilingual("Rayleigh range zR", "瑞利长度 zR")}}
    paths = [p.resolve() for p in args.runs]
    if args.redraw_external:
        if paths:
            parser.error("Use either RUN inputs or --redraw-external.")
        config = read_json(args.redraw_external)
        if config.get("schema") != "cylindrical-light-sheet.external-redraw":
            parser.error("Not a light-sheet external redraw configuration.")
        root = args.redraw_external.resolve().parent
        data = read_json(root / config["data"])
        with np.load(root / config["arrays"], allow_pickle=False) as saved:
            arrays = dict(saved)
        theme = config["theme"]
        protected = [root]
    else:
        if not 1 <= len(paths) <= 3 or len(set(paths)) != len(paths):
            parser.error("Provide one to three distinct saved result folders (default, f40, optionally f65).")
        data, arrays = load_runs(paths)
        protected = paths
    if any(out == root or root in out.parents for root in protected):
        parser.error("Output must be outside the saved input directories.")
    if args.bundle:
        bundle = args.bundle.resolve()
        if any(bundle == root or root in bundle.parents for root in protected):
            parser.error("Bundle must be outside the saved input directories.")
    for key in ("language", "background", "size"):
        if getattr(args, key) is not None:
            theme[key] = getattr(args, key)
    pres.resolve_theme(theme)
    out.mkdir(parents=True, exist_ok=True)
    if paths:
        pres.save_spec(native_spec(paths, data, theme, out), out / "presentation.json")
        present.build(out / "presentation.json", quiet=True)
        manifest = pres.read_manifest(out)
        if any(asset["status"] not in ("written", "reused") for asset in manifest["assets"]):
            raise RuntimeError("A native presentation asset failed; inspect assets.json.")
        manifest["assets"].insert(0, system_asset(out, paths[0], theme))
        manifest["counts"]["written"] += 1
    else:
        manifest = {"schema": pres.MANIFEST_SCHEMA, "schema_version": 1, "title": TITLE[theme["language"]],
                    "titles": TITLE, "language": theme["language"], "theme": pres.resolve_theme(theme).summary(),
                    "assets": [], "counts": {"written": 0}}
    manifest["assets"].append(external_asset(out, data, arrays, theme))
    manifest["counts"]["written"] += 1
    manifest["gallery"] = gallery.write_gallery(out, manifest)
    pres.write_manifest(out, manifest)
    if args.bundle:
        gallery.write_bundle(out, args.bundle)
    print(json.dumps({"out": str(out), "assets": len(manifest["assets"]), "solver_calls": 0}))


if __name__ == "__main__":
    main()
