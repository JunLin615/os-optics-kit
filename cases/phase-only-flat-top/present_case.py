"""Create an offline gallery comparing saved flat-top fields and Gaussian illumination."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
import numpy as np
from os_optics_kit import gallery, present, presentation as pres

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def bi(en, zh):
    return {"en": en, "zh-CN": zh}


TITLE = bi("Flat-top illumination sensitivity", "平顶光斑的照明敏感性")
CAPTION = bi(
    "External scalar Fourier fields with the same phase mask and intensity scale. "
    "CV is evaluated in r < 0.75 R; target power uses target > 10% of peak.",
    "外部标量傅里叶场，使用同一相位掩模与强度色标。CV 统计范围为 r < 0.75 R；"
    "目标区功率取目标强度大于峰值 10% 的区域。")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path, root):
    value = pres.portable_path(path, root)
    if value is None:
        raise ValueError("Input and output must be on the same drive.")
    return value


def load_run(root):
    config, results = read_json(root / "configuration.json"), read_json(root / "results.json")
    with np.load(root / "science.npz", allow_pickle=False) as saved:
        arrays = {"x_mm": saved["focal_x_mm"], "target": saved["target_unit_sum"],
                  "nominal_W_per_mm2": np.abs(saved["output_complex"]) ** 2,
                  "variant_W_per_mm2": np.abs(saved["variant_output_complex"]) ** 2}
    x = arrays["x_mm"]
    radius = np.hypot(x[:, None], x[None, :])
    roi = radius < .75 * config["target_radius_mm"]
    target_roi = arrays["target"] > .1 * arrays["target"].max()
    rows = []
    for field, key in (("nominal_W_per_mm2", "default"), ("variant_W_per_mm2", "width_variant")):
        intensity = arrays[field]
        actual = {"core_cv": float(intensity[roi].std() / intensity[roi].mean()),
                  "target_region_power_fraction": float(intensity[target_roi].sum() / intensity.sum()),
                  "power_W": float(intensity.sum() * (x[1] - x[0]) ** 2)}
        if not all(np.isclose(actual[k], results[key][k], rtol=1e-9, atol=1e-12) for k in actual):
            raise ValueError("Saved field and reported observables disagree.")
        rows.append({"id": key, **actual})
    data = {"origin": "External Fourier fields saved by phase-only-flat-top/workflow.py",
            "units": {"coordinates": "mm", "intensity": "W/mm^2", "power": "W", "CV": "dimensionless"},
            "target_radius_mm": config["target_radius_mm"], "input_width_scale": config["input_width_scale"],
            "phase_mask_sha256": config["phase_mask_sha256"], "core_roi": config["core_roi"],
            "efficiency_roi": config["efficiency_roi"], "runs": rows,
            "input_hashes": {name: digest(root / name) for name in
                             ("science.npz", "configuration.json", "results.json")}}
    return data, arrays


def native_spec(root, out, data, theme):
    return {"schema": pres.SPEC_SCHEMA, "schema_version": 1, "title": TITLE, "theme": theme,
            "sources": {
                "nominal": {"path": relative(root / "native-default", out),
                            "label": bi("Designed input", "设计输入")},
                "variant": {"path": relative(root / "native-width-variant", out),
                            "label": bi(f"Waist ×{data['input_width_scale']:g}",
                                        f"束腰 ×{data['input_width_scale']:g}")}},
            "assets": [
                {"id": "illumination", "kind": "plot", "type": "targets",
                 "sources": ["nominal", "variant"], "targets": ["illumination_radius"], "reference": "nominal",
                 "title": bi("Illumination-radius comparison", "照明半径对比"),
                 "caption": bi("Nominal radius target 0.749–0.752 mm; the variant uses its own width-scaled target. "
                               "Physical radii change while the external phase mask stays fixed.",
                               "标称半径目标为 0.749–0.752 mm；变体使用按输入宽度缩放的目标。"
                               "物理半径改变，外部相位掩模保持固定。")}]}


def native_view(root, out, settings):
    from os_optics_kit.render_views import camera_direction, left_to_right, render_shots

    theme = pres.resolve_theme(settings)
    folder = out / "assets" / "system"
    direction, up = camera_direction([0, 0, 1], 80, 10, up_axis=[0, 1, 0])
    direction, up = left_to_right(direction, up, [0, 0, 1], world_up=[0, 1, 0])
    records = render_shots(root / "native-default", [{"name": "system", "direction": direction, "up": up}],
                           folder, axis="0,0,1", up_axis=[0, 1, 0], size=theme.size,
                           background=theme.render_background(), focus="scene", edges=True)
    if records[0]["status"] != "captured":
        raise RuntimeError(f"Native view failed: {records[0].get('error')}")
    title = bi("Gaussian illumination and reference lens", "高斯照明与参考透镜")
    caption = bi("The −40/+60 mm telescope illuminates the 100 mm Fourier lens. The view shows the unmodulated Gaussian reference.",
                 "−40/+60 mm 扩束透镜组为 100 mm 傅里叶透镜提供照明。图中展示未调制的高斯参考光束。")
    write_json(folder / "camera.json", {"axis": [0, 0, 1], "up_axis": [0, 1, 0],
                                        "direction": direction, "up": up, "applied": records[0].get("applied_camera"),
                                        "renderer": "os_optics_kit.render_views.render_shots",
                                        "native_manifest_sha256": digest(root / "native-default" / "manifest.json")})
    files = {"image": folder / "system.png", "camera": folder / "camera.json"}
    identity = read_json(root / "results.json")["engine_default"]["run"]
    provenance = pres.provenance_record({"id": "system", "kind": "view", "title": title, "caption": caption},
                                        theme, [], [files["camera"]], out,
                                        extra={"origin": "Saved native run through the official public renderer",
                                               "native_identity": identity, "native_present_build_supported": False,
                                               "reason": "the present view asset cannot pass Y up for a Z-axis view"})
    write_json(folder / "provenance.json", provenance)
    captions = {}
    for language, value in caption.items():
        path = folder / f"caption.{language}.txt"
        path.write_text(value + "\n", encoding="utf-8")
        captions[language] = relative(path, out)
    preview = pres.write_preview(files["image"], out / "previews" / "system.png")
    return {"id": "system", "kind": "view", "status": "written", "title": title[theme.language], "titles": title,
            "caption": caption[theme.language], "captions": caption,
            "files": {k: pres.file_record(v, out) for k, v in files.items()}, "caption_files": captions,
            "provenance": relative(folder / "provenance.json", out),
            "preview": relative(preview, out), "size_px": pres.image_size(files["image"]),
            "sources": ["nominal"], "depends_on": [], "details": {"native": True, "axis": "+Z", "up": "+Y"}}


def external_asset(out, data, arrays, settings):
    theme = pres.resolve_theme(settings)
    lang = theme.language

    def text(en, zh):
        return zh if lang == "zh-CN" else en

    titles = [text("Designed input", "设计输入"),
              text(f"Source waist ×{data['input_width_scale']:g} · fixed mask",
                   f"光源束腰 ×{data['input_width_scale']:g} · 固定掩模"),
              text("Center profiles (y = 0)", "中心剖面 (y = 0)")]
    pres.check_glyphs([TITLE[lang], CAPTION[lang], *titles, text("Intensity", "强度")], theme)
    folder = out / "assets" / "flat-top"
    folder.mkdir(parents=True)
    write_json(folder / "data.json", data)
    np.savez_compressed(folder / "fields.npz", **arrays)
    write_json(folder / "case-redraw.json", {
        "schema": "phase-only-flat-top.external-redraw", "schema_version": 1,
        "data": "data.json", "arrays": "fields.npz", "theme": settings,
        "command": "python present_case.py --redraw-external case-redraw.json --out NEW_DIR",
        "scope": "External intensity comparison only. Native assets use presentation.json and saved runs."})
    shutil.copyfile(__file__, folder / "present_case.py")
    x = arrays["x_mm"]
    fields = [arrays["nominal_W_per_mm2"], arrays["variant_W_per_mm2"]]
    limit = data["target_radius_mm"] * 1.35
    selected = np.abs(x) <= limit
    max_intensity = max(float(i.max()) for i in fields)
    figsize, dpi = theme.canvas()
    with plt.rc_context(theme.rc()):
        fig = plt.figure(figsize=figsize, dpi=dpi)
        grid = fig.add_gridspec(1, 3, left=.06, right=.96, bottom=.24, top=.79,
                               wspace=.40, width_ratios=[1, 1, 1.15])
        axes = [fig.add_subplot(grid[0, i]) for i in range(3)]
        for i, intensity in enumerate(fields):
            image = axes[i].pcolormesh(x[selected], x[selected], intensity[np.ix_(selected, selected)],
                                       vmin=0, vmax=max_intensity, cmap="magma", shading="auto", rasterized=True)
            axes[i].set(xlabel="x (mm)", ylabel="y (mm)", aspect="equal", title=titles[i])
            axes[i].set_title(titles[i], fontsize=theme.font_pt("small"))
            axes[i].grid(False)
            axes[i].add_patch(plt.Circle((0, 0), .75 * data["target_radius_mm"], fill=False,
                                        edgecolor="cyan", linestyle="--", linewidth=1))
        color_ax = fig.add_axes([.12, .12, .42, .025])
        fig.colorbar(image, cax=color_ax, orientation="horizontal").set_label(
            text("Intensity (W/mm²) · shared scale; dashed circle: CV region",
                 "强度 (W/mm²) · 共用色标；虚线圆：CV 区域"))
        for i, intensity in enumerate(fields):
            axes[2].plot(x, intensity[len(x) // 2], color=theme.series_color(i), label=titles[i].split(" · ")[0])
        target = arrays["target"]
        pitch = x[1] - x[0]
        target_intensity = target / target.sum() * data["runs"][0]["power_W"] / pitch**2
        axes[2].plot(x, target_intensity[len(x) // 2], "--", color=theme.colors["muted"],
                     label=text("Target (equal power)", "目标（等功率）"))
        axes[2].set(xlabel="x (mm)", ylabel=text("Intensity (W/mm²)", "强度 (W/mm²)"),
                    xlim=(-limit, limit), title=titles[2])
        axes[2].legend(fontsize=theme.font_pt("small") * .75)
        fig.suptitle(TITLE[lang], y=.94)
        values = "   |   ".join(f"{titles[i].split(' · ')[0]}: CV {r['core_cv']:.5f}, "
                                f"{text('target power', '目标区功率')} {r['target_region_power_fraction']:.5f}"
                                for i, r in enumerate(data["runs"]))
        fig.text(.5, .855, values, ha="center", fontsize=theme.font_pt("small") * .82)
        for fmt in ("png", "svg"):
            fig.savefig(folder / f"flat-top.{fmt}", dpi=dpi)
        plt.close(fig)
    files = {"image": folder / "flat-top.png", "svg": folder / "flat-top.svg", "data": folder / "data.json",
             "arrays": folder / "fields.npz", "redraw": folder / "case-redraw.json", "helper": folder / "present_case.py"}
    captions = {}
    for language, caption in CAPTION.items():
        path = folder / f"caption.{language}.txt"
        path.write_text(caption + "\n", encoding="utf-8")
        captions[language] = relative(path, out)
    provenance = pres.provenance_record({"id": "flat-top", "kind": "plot", "title": TITLE, "caption": CAPTION},
                                        theme, [], [files[k] for k in ("data", "arrays", "redraw")], out,
                                        extra={"origin": data["origin"], "renderer": "case-local present_case.py",
                                               "native_present_build_supported": False, "units": data["units"]})
    write_json(folder / "provenance.json", provenance)
    preview = pres.write_preview(files["image"], out / "previews" / "flat-top.png")
    return {"id": "flat-top", "kind": "plot", "status": "written", "title": TITLE[lang], "titles": TITLE,
            "caption": CAPTION[lang], "captions": CAPTION, "files": {k: pres.file_record(v, out) for k, v in files.items()},
            "preview": relative(preview, out), "size_px": pres.image_size(files["image"]),
            "provenance": relative(folder / "provenance.json", out), "caption_files": captions,
            "sources": [], "depends_on": [], "details": {"external": True, "origin": data["origin"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--language", choices=("en", "zh-CN"))
    parser.add_argument("--background", choices=("light", "dark"))
    parser.add_argument("--size")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--redraw-external", type=Path, metavar="CONFIG")
    args = parser.parse_args()
    if bool(args.run) == bool(args.redraw_external):
        parser.error("Provide a saved RUN or --redraw-external CONFIG.")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Use a new, empty output directory for each style.")
    settings = {"language": "en", "background": "light", "size": "1920x1080",
                "labels": {"target:illumination_radius": bi("Illumination 1/e² radius", "照明 1/e² 半径")}}
    if args.run:
        root = args.run.resolve()
        data, arrays = load_run(root)
    else:
        root = args.redraw_external.resolve().parent
        config = read_json(args.redraw_external)
        if config.get("schema") != "phase-only-flat-top.external-redraw":
            parser.error("Not a flat-top external redraw configuration.")
        data = read_json(root / config["data"])
        with np.load(root / config["arrays"], allow_pickle=False) as saved:
            arrays = dict(saved)
        settings = config["theme"]
    if out == root or root in out.parents:
        parser.error("Output must be outside the saved input directory.")
    if args.bundle and (args.bundle.resolve() == root or root in args.bundle.resolve().parents):
        parser.error("Bundle must be outside the saved input directory.")
    for key in ("language", "background", "size"):
        if getattr(args, key) is not None:
            settings[key] = getattr(args, key)
    theme = pres.resolve_theme(settings)
    out.mkdir(parents=True, exist_ok=True)
    if args.run:
        pres.save_spec(native_spec(root, out, data, settings), out / "presentation.json")
        present.build(out / "presentation.json", quiet=True)
        manifest = pres.read_manifest(out)
        if any(a["status"] not in ("written", "reused") for a in manifest["assets"]):
            raise RuntimeError("Native presentation failed; inspect assets.json.")
        manifest["assets"].append(native_view(root, out, settings))
        manifest["counts"]["written"] += 1
    else:
        manifest = {"schema": pres.MANIFEST_SCHEMA, "schema_version": 1, "title": TITLE[theme.language],
                    "titles": TITLE, "language": theme.language, "theme": theme.summary(),
                    "assets": [], "counts": {"written": 0}}
    manifest["assets"].append(external_asset(out, data, arrays, settings))
    manifest["counts"]["written"] += 1
    manifest["gallery"] = gallery.write_gallery(out, manifest)
    pres.write_manifest(out, manifest)
    if args.bundle:
        gallery.write_bundle(out, args.bundle)
    print(json.dumps({"assets": len(manifest["assets"]), "solver_calls": 0}))


if __name__ == "__main__":
    main()
