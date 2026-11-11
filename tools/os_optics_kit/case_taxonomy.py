"""The case library vocabulary: classes, domains, provenance, publication, tags, scale bands and the image budget.

Every value has an English (``en``) and a Simplified Chinese (``zh-CN``) label. ``CASE.md`` front matter takes the
values; ``cases.json``, ``os-optics-kit cases list`` filters, ``os-optics-kit cases vocabulary`` and the README case
tables read the labels from here. ``scale`` is never written by hand: ``scale_of`` derives it from ``project.json``.
Standard library only.
"""

from __future__ import annotations

from typing import Any, TypedDict

VOCABULARY_SCHEMA = "os-optics-kit.case-vocabulary"
VOCABULARY_SCHEMA_VERSION = 1


class Term(TypedDict):
    value: str
    en: str
    zh: str


class Described(TypedDict):
    value: str
    en: str
    zh: str
    description_en: str
    description_zh: str


class Tag(TypedDict):
    value: str
    en: str
    zh: str
    group: str


class Band(TypedDict):
    value: str
    en: str
    zh: str
    max_elements: int | None
    max_branches: int | None


def _described(value: str, en: str, zh: str, description_en: str, description_zh: str) -> Described:
    return {"value": value, "en": en, "zh": zh, "description_en": description_en, "description_zh": description_zh}


CLASSES: tuple[Described, ...] = (
    _described("lens", "Lens", "镜头",
               "A coaxial lens or lens group (prescription, ideal or tabulated lens model) whose conclusion is the "
               "lens's own behaviour: focusing, prescription, image quality.",
               "同轴透镜或透镜组（处方、理想或表格化透镜模型），结论是透镜自身的行为：聚焦、处方与成像质量。"),
    _described("free-space", "Free-space platform", "自由空间平台",
               "A beam path laid out element by element in open space (expanders, relays, folds, splitters, "
               "polarization trains, stops) whose conclusion is that the path as assembled meets a concrete optical "
               "requirement and, with catalog parts, that it can be bought and built.",
               "在开放空间中逐个元件搭建的光路（扩束、中继、折转、分束、偏振链、光阑），结论是装配后的光路满足具体的"
               "光学需求；使用目录元件时，还包括它可以采购并搭建。"),
    _described("integrated-path", "Integrated path", "集成光路",
               "A path held by custom mechanics (spectrometer, monochromator, packaged laser) whose conclusion is the "
               "joint validity of the internal optical path and its mechanical constraints.",
               "由定制机械固定的光路（光谱仪、单色仪、封装激光器），结论是内部光路与其机械约束共同成立。"),
)

DOMAINS: tuple[Described, ...] = (
    _described("imaging", "Imaging", "成像", "Image formation and image quality: spot, MTF, Strehl, PSF.",
               "成像与成像质量：点列、MTF、Strehl 比、PSF。"),
    _described("gaussian-beam", "Gaussian beams", "高斯光束", "Propagation, focusing, expansion, waists.",
               "传播、聚焦、扩束与束腰。"),
    _described("diffraction", "Diffraction", "衍射", "Wave optics, stops, Fourier optics, PSF and MTF from diffraction.",
               "波动光学、光阑、傅里叶光学，以及由衍射得到的 PSF 和 MTF。"),
    _described("dispersion", "Dispersion", "色散", "Wavelength-dependent behaviour, chromatic focus.",
               "随波长变化的行为、色焦移。"),
    _described("polarization", "Polarization", "偏振", "Polarization states and polarizing elements.",
               "偏振态与偏振元件。"),
    _described("interference", "Interference", "干涉", "Superposition of coherent fields.", "相干场的叠加。"),
    _described("quantum-optics", "Quantum optics", "量子光学", "Photon statistics and linear optical networks.",
               "光子统计与线性光学网络。"),
    _described("nanophotonics", "Nanophotonics", "纳米光子学", "Metasurfaces, RCWA.", "超表面、RCWA。"),
    _described("beam-shaping", "Beam shaping", "光束整形", "Phase masks, flat tops, light sheets.",
               "相位掩模、平顶光束、光片。"),
    _described("wavefront-control", "Wavefront control", "波前控制", "Aberration correction with modes.",
               "用模式校正像差。"),
    _described("beam-steering", "Beam steering", "光束指向", "Reflection geometry, beam position and pointing.",
               "反射几何、光束位置与指向。"),
    _described("radiometry", "Radiometry", "辐射度量", "Power on detectors, detection, measurement data.",
               "探测器上的功率、探测与测量数据。"),
)

PROVENANCE: tuple[Term, ...] = (
    {"value": "literature", "en": "Literature", "zh": "文献"},
    {"value": "patent", "en": "Patent", "zh": "专利"},
    {"value": "manufacturer-file", "en": "Manufacturer file", "zh": "厂商文件"},
    {"value": "customer-task", "en": "Customer task", "zh": "客户任务"},
    {"value": "self-built", "en": "Self-built", "zh": "自建"},
)
#: the provenance of a design written for the kit; it goes with publication own-work and no source
SELF_BUILT = "self-built"

PUBLICATION: tuple[Described, ...] = (
    _described("own-work", "Own work", "原创", "Written for the kit, published under the repository's MIT license.",
               "为本工具包编写，按仓库的 MIT 许可证发布。"),
    _described("open-license", "Open license", "开放许可",
               "Third-party material under an open license, attributed.", "采用开放许可的第三方材料，注明出处。"),
    _described("permission", "Permission", "授权发布", "Published with the rights holder's written permission.",
               "经权利人书面许可发布。"),
    _described("public-domain", "Public domain", "公有领域", "Material in the public domain.", "处于公有领域的材料。"),
)
OWN_WORK = "own-work"

TAG_GROUPS: tuple[str, ...] = ("technique", "component", "application", "external-tool")


def _tags(group: str, rows: tuple[tuple[str, str, str], ...]) -> tuple[Tag, ...]:
    return tuple({"value": value, "en": en, "zh": zh, "group": group} for value, en, zh in rows)


TAGS: tuple[Tag, ...] = (
    _tags("technique", (
        ("sensitivity", "Sensitivity", "灵敏度"),
        ("tolerance", "Tolerance", "公差"),
        ("parameter-sweep", "Parameter sweep", "参数扫描"),
        ("optimization", "Optimization", "优化"),
        ("edit-by-id", "Edit by ID", "按 ID 编辑"),
        ("lens-swap", "Lens swap", "换镜"),
        ("design-comparison", "Design comparison", "设计对比"),
        ("requirement-check", "Requirement check", "需求核验"),
        ("placement-tolerance", "Placement tolerance", "装调容差"),
        ("alignment", "Alignment", "对准"),
        ("measurements", "Measurements", "测量数据"),
        ("calibration", "Calibration", "标定"),
        ("handoff", "Handoff", "数据交接"),
        ("interop", "Interop", "外部工具互操作"),
        ("model-validation", "Model validation", "模型验证"),
        ("independent-reference", "Independent reference", "独立参照计算"),
        ("mtf", "MTF", "MTF"),
        ("python-api", "Python API", "Python API"),
        ("report", "Report", "报告"),
    ))
    + _tags("component", (
        ("achromat", "Achromat", "消色差透镜"),
        ("aspheric", "Aspheric surfaces", "非球面"),
        ("beam-expander", "Beam expander", "扩束器"),
        ("ideal-lens", "Ideal lens", "理想透镜"),
        ("fold-mirror", "Fold mirror", "折转镜"),
        ("waveplate", "Waveplate", "波片"),
        ("polarizer", "Polarizer", "偏振片"),
        ("beam-splitter", "Beam splitter", "分束器"),
        ("detector", "Detector", "探测器"),
        ("aperture-stop", "Aperture stop", "孔径光阑"),
        ("metasurface", "Metasurface", "超表面"),
        ("tabulated-thin-lens", "Tabulated thin lens", "表格化薄透镜"),
        ("cylindrical-lens", "Cylindrical lens", "柱面透镜"),
        ("phase-mask", "Phase mask", "相位掩模"),
    ))
    + _tags("application", (
        ("focusing", "Focusing", "聚焦"),
        ("wide-field", "Wide field", "广视场"),
        ("mid-infrared", "Mid-infrared", "中波红外"),
        ("manufacturability", "Manufacturability", "可制造性"),
        ("light-sheet", "Light sheet", "光片"),
        ("flat-top", "Flat top", "平顶光束"),
        ("hong-ou-mandel", "Hong-Ou-Mandel", "HOM 干涉"),
        ("linear-optical-network", "Linear optical network", "线性光学网络"),
        ("rcwa", "RCWA", "RCWA"),
        ("full-aperture", "Full aperture", "全孔径"),
    ))
    + _tags("external-tool", (
        ("scipy", "SciPy", "SciPy"),
        ("optuna", "Optuna", "Optuna"),
        ("salib", "SALib", "SALib"),
        ("lightpipes", "LightPipes", "LightPipes"),
        ("prysm", "prysm", "prysm"),
        ("poppy", "POPPY", "POPPY"),
        ("diffractio", "diffractio", "diffractio"),
        ("grcwa", "grcwa", "grcwa"),
        ("perceval", "Perceval", "Perceval"),
        ("piquasso", "Piquasso", "Piquasso"),
    ))
)

#: scale bands in order; a band holds a project when E <= max_elements and B <= max_branches (None: no bound)
SCALE_BANDS: tuple[Band, ...] = (
    {"value": "small", "en": "Small", "zh": "小", "max_elements": 3, "max_branches": 0},
    {"value": "medium", "en": "Medium", "zh": "中", "max_elements": 12, "max_branches": 2},
    {"value": "large", "en": "Large", "zh": "大", "max_elements": 40, "max_branches": 8},
    {"value": "very-large", "en": "Very large", "zh": "超大", "max_elements": None, "max_branches": None},
)

# Front matter counts.
DOMAIN_MIN, DOMAIN_MAX = 1, 3
TAG_MIN, TAG_MAX = 1, 8
SOURCE_MAX_CHARS = 200

# Image budget per case: system views are engine snapshots or sections, figures are case-script plots.
IMAGE_MAX_COUNT = 5
SYSTEM_VIEW_MIN = 1
SYSTEM_VIEW_MAX = 3
FIGURE_MAX = 3
IMAGE_MAX_BYTES = 400 * 1024
FILE_MAX_BYTES = 1024 * 1024
#: snapshot views of ``os-optics view --no-browser --snapshot DIR --views ...``
SNAPSHOT_VIEWS: tuple[str, ...] = ("iso", "path_horizontal", "top", "front", "side", "beam_axis", "plane_xy",
                                   "plane_zx", "plane_yz")
CAMERA_VIEW = "camera"
SECTION_VIEW = "section"
SYSTEM_VIEW_KIND = "system-view"
FIGURE_KIND = "figure"

#: values a tag may not repeat besides the other fields' vocabularies (``catalog:`` says it)
FIELD_WORDS: tuple[str, ...] = ("catalog", "catalog-free", "none")


def values(terms: tuple[Any, ...]) -> tuple[str, ...]:
    """The values of a vocabulary, in vocabulary order."""
    return tuple(term["value"] for term in terms)


def label(terms: tuple[Any, ...], value: str, language: str = "en") -> str:
    """The ``en`` or ``zh-CN`` label of a value (the value itself when it is unknown)."""
    key = "zh" if language == "zh-CN" else "en"
    for term in terms:
        if term["value"] == value:
            return str(term[key])
    return value


def field_values() -> set[str]:
    """Every value of the class, domain, provenance and publication vocabularies (a tag never repeats a field)."""
    return set(values(CLASSES)) | set(values(DOMAINS)) | set(values(PROVENANCE)) | set(values(PUBLICATION))


# ----------------------------------------------------------------------------------------------- scale

def _model_refs(value: Any, found: list[str]) -> list[str]:
    """Every ``model_id`` string inside a model body (COMPOUND members and any nested reference), in order."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "model_id" and isinstance(item, str):
                found.append(item)
            else:
                _model_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _model_refs(item, found)
    return found


def _leaf_count(model_id: str, models: dict[str, Any], seen: frozenset[str]) -> int:
    entry = models.get(model_id)
    if not isinstance(entry, dict) or model_id in seen:
        return 1  # unknown structure (or a reference loop) counts 1
    body = entry.get("model")
    if not isinstance(body, dict):
        return 1
    if body.get("model_kind") == "APERTURE_STOP":
        return 0
    refs = _model_refs(body, [])
    if not refs:
        return 1
    return sum(_leaf_count(ref, models, seen | {model_id}) for ref in refs)


def band_of(elements: int, branches: int) -> str:
    for band in SCALE_BANDS:
        max_e, max_b = band["max_elements"], band["max_branches"]
        if (max_e is None or elements <= max_e) and (max_b is None or branches <= max_b):
            return band["value"]
    return SCALE_BANDS[-1]["value"]


def scale_of(project_document: dict[str, Any]) -> dict[str, Any]:
    """``{"band", "elements", "sources", "branches"}`` of a ProjectDocumentV2.

    elements: every component whose ``optical_role`` is not ``source``: a catalog ``product`` counts 1, an
    ``optical_model_id`` counts the leaf models of that model (COMPOUND members recursively; APERTURE_STOP models
    count 0), anything else 1. sources: components with role ``source``. branches: components with role
    ``splitter``. Raises ValueError when the document is not a JSON object."""
    if not isinstance(project_document, dict):
        raise ValueError("project.json must hold a JSON object")
    components = project_document.get("components")
    components = components if isinstance(components, list) else []
    models_list = project_document.get("optical_models")
    models: dict[str, Any] = {}
    for model in models_list if isinstance(models_list, list) else []:
        if isinstance(model, dict) and isinstance(model.get("model_id"), str):
            models[model["model_id"]] = model
    elements = sources = branches = 0
    for component in components:
        if not isinstance(component, dict):
            elements += 1
            continue
        role = component.get("optical_role")
        if role == "source":
            sources += 1
            continue
        if role == "splitter":
            branches += 1
        if component.get("product"):
            elements += 1
        elif isinstance(component.get("optical_model_id"), str):
            elements += _leaf_count(component["optical_model_id"], models, frozenset())
        else:
            elements += 1
    return {"band": band_of(elements, branches), "elements": elements, "sources": sources, "branches": branches}


# ----------------------------------------------------------------------------------------------- vocabulary

def _term(term: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"value": term["value"], "en": term["en"], "zh-CN": term["zh"]}
    if "description_en" in term:
        row["description"] = {"en": term["description_en"], "zh-CN": term["description_zh"]}
    if "group" in term:
        row["group"] = term["group"]
    if "max_elements" in term:
        row["max_elements"] = term["max_elements"]
        row["max_branches"] = term["max_branches"]
    return row


def image_budget() -> dict[str, Any]:
    return {"max_images": IMAGE_MAX_COUNT, "min_system_views": SYSTEM_VIEW_MIN, "max_system_views": SYSTEM_VIEW_MAX,
            "max_figures": FIGURE_MAX, "max_image_bytes": IMAGE_MAX_BYTES, "max_file_bytes": FILE_MAX_BYTES,
            "format": "png", "snapshot_views": list(SNAPSHOT_VIEWS) + [CAMERA_VIEW],
            "section_view": SECTION_VIEW, "section_required_for_class": ["lens"]}


def vocabulary() -> dict[str, Any]:
    """The whole vocabulary as one JSON document (``os-optics-kit cases vocabulary``)."""
    return {
        "schema": VOCABULARY_SCHEMA,
        "schema_version": VOCABULARY_SCHEMA_VERSION,
        "classes": [_term(t) for t in CLASSES],
        "domains": [_term(t) for t in DOMAINS],
        "domain_count": {"min": DOMAIN_MIN, "max": DOMAIN_MAX},
        "provenance": [_term(t) for t in PROVENANCE],
        "publication": [_term(t) for t in PUBLICATION],
        "source_max_chars": SOURCE_MAX_CHARS,
        "scale": [_term(t) for t in SCALE_BANDS],
        "tag_groups": list(TAG_GROUPS),
        "tags": [_term(t) for t in TAGS],
        "tag_count": {"min": TAG_MIN, "max": TAG_MAX},
        "images": image_budget(),
    }
