"""Released-package check of the local live viewer (``os-optics-kit view``) in a real browser.

python scripts/released_viewer_check.py OUT_DIR [--engine-version 0.14.0] [--kit-version 0.5.0]
                                        [--kit-source DIR] [--input case:NAME | example:KIND] [--browser PATH]
                                        [--work-dir DIR] [--known-defect ID ...] [--no-known-defects]

Builds two virtual environments outside the checkout (in --work-dir, default a new temporary directory):

  product  only the published packages, as a user installs them: the os-optics and os-optics-kit wheels are
           downloaded from PyPI (pip download --no-deps), their SHA-256 compared with the digests of the PyPI JSON
           API, then installed with their dependencies. --kit-source DIR builds the kit wheel from a source tree
           instead (a branch under test); the engine still comes from PyPI.
  harness  Playwright only (no os-optics, no kit), driving the local Chrome/Chromium (no browser download).

Then, with the product commands only, on a public input (default: the detector-measurements case shipped in the kit
wheel; example:KIND takes 'os-optics project example --kind KIND'):

  rev 1  'os-optics project run project.json --out-dir run', then 'os-optics-kit view project.json --run run
         --no-open' and one page load: rays, power ledger and status of the saved run;
  rev 2  a Project edit (the source power x3): the run is historical, rays and ledger withheld;
  rev 3  the edited Project re-run into the same directory: current again.

At each revision the page's DOM (and its 3D frame), /api/snapshot and the engine's own 'results describe',
'results power-budget' and 'results freshness' are read and compared. Every check is reported with its outcome:
pass, fail, known_defect (a failure of a check that covers a defect listed with --known-defect; os-optics-kit 0.5.0
from PyPI has kit#45-V1 and kit#45-V2 by default) or not_applicable. OUT_DIR receives released-viewer-check.json,
one full-page PNG per revision, the engine outputs and the viewer's log.

Exit status: 0 when no check failed (known defects do not fail the record), 1 when one did, 4 for a setup error.
The script needs only the standard library; it runs itself again inside the harness environment for the browser part.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

SCHEMA = "os-optics-kit.released_viewer_check"
SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
PLAYWRIGHT_SPEC = "playwright>=1.50,<2"
SETUP_ERROR = 4
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
KNOWN_DEFECTS = {
    "kit#45-V1": "the live viewer reframes its camera when a view goes from traced rays to geometry only and back",
    "kit#45-V2": "hidden badges (#badge-conserved, #badge-geom-only, ...) keep stale text in the DOM; no agent-readable"
                 " state attributes on #app",
}
# Defects of a published kit release (the PyPI wheel, not a --kit-source build).
RELEASE_KNOWN_DEFECTS = {"0.5.0": ("kit#45-V1", "kit#45-V2")}
# Text of the English page (viewer.js STRINGS.en) the checks look for.
EN = {
    "computed": "Computed Result",
    "stale": "Stale Run · Project Updated",
    "current": "Current",
    "historical": "Historical",
    "conserved": "Power conserved",
    "not_conserved": "Residual outside tolerance",
    "geometry_only": "Geometry Only",
}
POWER_VALUE = re.compile(r"\d\s?(?:W|mW|µW|nW)(?![A-Za-z])")
STATE_ATTRIBUTES = ("data-result-mode", "data-computed", "data-freshness", "data-project-hash",
                    "data-execution-outcome", "data-power-available", "data-power-conserved", "data-branch-count",
                    "data-geometry-only", "data-drawable-ray-count")


class SetupError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def venv_bin(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / (name + (".exe" if not name.endswith(".exe") else ""))
    return venv / "bin" / name


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, timeout: float = 1800,
        check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if check and done.returncode != 0:
        raise SetupError(f"command failed ({done.returncode}): {' '.join(map(str, cmd))}\n{done.stderr[-4000:]}")
    return done


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else shutil.which(explicit)
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def pypi_digests(name: str, version: str) -> dict[str, str]:
    """filename -> sha256 of every file of NAME==VERSION on PyPI (its JSON API)."""
    request = urllib.request.Request(PYPI_JSON.format(name=name, version=version),
                                     headers={"User-Agent": "os-optics-kit-released-viewer-check"})
    with urllib.request.urlopen(request, timeout=60) as response:
        document = json.load(response)
    return {item["filename"]: item["digests"]["sha256"] for item in document.get("urls") or []}


# ---------------------------------------------------------------------------------------------------- setup phase


def setup_product(work: Path, args, record: dict) -> dict:
    base_python = args.python or sys.executable
    product = work / "product"
    wheels = work / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    run([base_python, "-m", "venv", product])
    python = venv_bin(product, "python")
    run([python, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "-d", wheels,
         f"os-optics=={args.engine_version}"], cwd=work)
    kit_from = "pypi"
    source_info = None
    if args.kit_source:
        source = Path(args.kit_source).resolve()
        build = work / "kit-build"
        run([python, "-m", "pip", "wheel", "-q", "--no-deps", "-w", build, source], cwd=work)
        built = sorted(build.glob("os_optics_kit-*.whl"))
        if len(built) != 1:
            raise SetupError(f"expected one kit wheel built from {source}, found {built}")
        shutil.copy2(built[0], wheels / built[0].name)
        kit_from = "source"
        source_info = {"path": source.as_posix()}
        git = shutil.which("git")
        if git and (source / ".git").exists():
            head = run([git, "-C", source, "rev-parse", "HEAD"], check=False).stdout.strip()
            dirty = run([git, "-C", source, "status", "--porcelain"], check=False).stdout.strip()
            source_info.update({"commit": head, "dirty": bool(dirty)})
    else:
        run([python, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "-d", wheels,
             f"os-optics-kit=={args.kit_version}"], cwd=work)

    files = []
    for wheel in sorted(wheels.glob("*.whl")):
        name = "os-optics-kit" if wheel.name.startswith("os_optics_kit-") else "os-optics"
        version = args.kit_version if name == "os-optics-kit" else args.engine_version
        entry = {"distribution": name, "file": wheel.name, "sha256": sha256_file(wheel), "source": "pypi"}
        if name == "os-optics-kit" and kit_from == "source":
            entry.update({"source": "local-build", "pypi_sha256": None, "matches_pypi": None, "built_from": source_info})
        else:
            published = pypi_digests(name, version)
            entry["pypi_sha256"] = published.get(wheel.name)
            entry["matches_pypi"] = entry["pypi_sha256"] == entry["sha256"]
        files.append(entry)
    run([python, "-m", "pip", "install", "-q", *sorted(wheels.glob("*.whl"))], cwd=work)

    freeze = run([python, "-m", "pip", "freeze", "--all"], cwd=work).stdout.splitlines()
    engine_version = run([venv_bin(product, "os-optics"), "--version"], cwd=work, check=False)
    kit_version = run([venv_bin(product, "os-optics-kit"), "--version"], cwd=work, check=False)
    where = run([python, "-I", "-c",
                 "import json, os_optics, os_optics_kit; print(json.dumps({'os_optics': os_optics.__file__, "
                 "'os_optics_kit': os_optics_kit.__file__, 'engine_version': getattr(os_optics, '__version__', None), "
                 "'kit_version': getattr(os_optics_kit, '__version__', None)}))"], cwd=work)
    imported = json.loads(where.stdout)
    record["packages"] = {
        "kit_from": kit_from,
        "wheels": files,
        "freeze": freeze,
        "os_optics_version_output": (engine_version.stdout or engine_version.stderr).strip(),
        "os_optics_kit_version_output": (kit_version.stdout or kit_version.stderr).strip(),
        "imported": imported,
        "imported_from_product_venv": all(inside(Path(p), product) for p in (imported["os_optics"],
                                                                             imported["os_optics_kit"])),
    }
    return {"venv": product.as_posix(), "python": python.as_posix(),
            "os_optics": venv_bin(product, "os-optics").as_posix(),
            "os_optics_kit": venv_bin(product, "os-optics-kit").as_posix()}


def setup_harness(work: Path, args, record: dict) -> dict:
    base_python = args.python or sys.executable
    harness = work / "harness"
    run([base_python, "-m", "venv", harness])
    python = venv_bin(harness, "python")
    run([python, "-m", "pip", "install", "-q", args.playwright_spec], cwd=work)
    probe = run([python, "-I", "-c",
                 "import importlib.util as u, json, playwright; from importlib.metadata import version; "
                 "print(json.dumps({'os_optics': u.find_spec('os_optics') is not None, "
                 "'os_optics_kit': u.find_spec('os_optics_kit') is not None, 'playwright': version('playwright')}))"],
                cwd=work)
    facts = json.loads(probe.stdout)
    record["harness"] = {"venv": harness.as_posix(), "playwright": facts["playwright"],
                         "engine_importable": facts["os_optics"], "kit_importable": facts["os_optics_kit"]}
    return {"venv": harness.as_posix(), "python": python.as_posix()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir", nargs="?", help="evidence directory (created; its previous content is replaced)")
    parser.add_argument("--engine-version", default="0.14.0")
    parser.add_argument("--kit-version", default="0.5.0",
                        help="the os-optics-kit release from PyPI (with --kit-source: the version the build reports)")
    parser.add_argument("--kit-source", help="build the kit wheel from this source tree instead of PyPI")
    parser.add_argument("--input", default="case:detector-measurements",
                        help="case:NAME (a case shipped in the kit wheel) or example:KIND (os-optics project example)")
    parser.add_argument("--browser", help="Chrome/Chromium executable (default: a local Chrome or Chromium)")
    parser.add_argument("--python", help="interpreter the environments are created from (default: this one)")
    parser.add_argument("--work-dir", help="directory for the environments and inputs (outside the checkout)")
    parser.add_argument("--keep-work", action="store_true", help="keep the work directory")
    parser.add_argument("--known-defect", action="append", default=None, choices=sorted(KNOWN_DEFECTS),
                        help="a defect expected in the kit under test (repeatable); default: those of the PyPI release")
    parser.add_argument("--no-known-defects", action="store_true", help="expect no defect at all")
    parser.add_argument("--playwright-spec", default=PLAYWRIGHT_SPEC)
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for each revision")
    parser.add_argument("--drive", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.drive:
        return drive(Path(args.drive))
    if not args.out_dir:
        parser.error("OUT_DIR is required")

    out = Path(args.out_dir).resolve()
    work = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="released-viewer-"))
    if inside(work, REPO_ROOT):
        print(json.dumps({"status": "SETUP_ERROR", "message": f"--work-dir must be outside the checkout {REPO_ROOT}"}))
        return SETUP_ERROR
    if args.no_known_defects:
        known: list[str] = []
    elif args.known_defect is not None:
        known = sorted(set(args.known_defect))
    else:
        known = [] if args.kit_source else list(RELEASE_KNOWN_DEFECTS.get(args.kit_version, ()))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request": {"engine_version": args.engine_version, "kit_version": args.kit_version,
                    "kit_source": bool(args.kit_source), "input": args.input},
        "known_defects": {key: KNOWN_DEFECTS[key] for key in known},
        "environment": {"os": platform.platform(), "system": platform.system(), "machine": platform.machine(),
                        "python": platform.python_version(),
                        "github_run": (f"{os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}"
                                       f"/actions/runs/{os.environ.get('GITHUB_RUN_ID')}")
                        if os.environ.get("GITHUB_RUN_ID") else None},
    }
    status = SETUP_ERROR
    try:
        browser = find_browser(args.browser)
        if not browser:
            raise SetupError("no Chrome/Chromium found (pass --browser)")
        product = setup_product(work, args, record)
        harness = setup_harness(work, args, record)
        # A source build reports the version its tree declares; a PyPI kit must be the requested release.
        kit_version = record["packages"]["imported"].get("kit_version") if args.kit_source else args.kit_version
        plan = {"work": work.as_posix(), "out": out.as_posix(), "product": product, "browser": browser,
                "input": args.input, "engine_version": args.engine_version, "kit_version": kit_version,
                "known_defects": known, "timeout": args.timeout}
        plan_path = work / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
        result_path = work / "drive-result.json"
        done = subprocess.run([harness["python"], "-I", str(Path(__file__).resolve()), "--drive", str(plan_path)],
                              cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=args.timeout * 6 + 600)
        (out / "harness.log").write_text(done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf-8")
        if not result_path.is_file():
            raise SetupError(f"the browser harness wrote no result (exit {done.returncode}); see harness.log")
        record.update(json.loads(result_path.read_text(encoding="utf-8")))
        record["checks"] += setup_checks(record)
        record["verdict"] = verdict(record["checks"])
        status = 0 if record["verdict"]["outcome"] == "pass" else 1
    except (SetupError, subprocess.SubprocessError, OSError, ValueError) as exc:
        record["setup_error"] = str(exc)
        record["verdict"] = {"outcome": "setup_error"}
    finally:
        (out / "released-viewer-check.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                                        encoding="utf-8")
        if not args.keep_work and not args.work_dir:
            shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"verdict": record.get("verdict"), "record": (out / "released-viewer-check.json").as_posix(),
                      "checks": [(c["id"], c["outcome"]) for c in record.get("checks", [])]}, ensure_ascii=False))
    return status


def setup_checks(record: dict) -> list[dict]:
    packages = record["packages"]
    harness = record["harness"]
    request = record["request"]
    checks = []
    pypi = [w for w in packages["wheels"] if w["source"] == "pypi"]
    checks.append(check("S1.wheels_match_pypi", "setup",
                        "every wheel downloaded from PyPI has the SHA-256 the PyPI JSON API publishes",
                        bool(pypi) and all(w["matches_pypi"] for w in pypi),
                        {w["file"]: {"sha256": w["sha256"], "pypi_sha256": w["pypi_sha256"]} for w in packages["wheels"]}))
    imported = packages["imported"]
    checks.append(check("S2.product_versions", "setup",
                        "the product environment runs the requested engine and kit, imported from that environment",
                        packages["imported_from_product_venv"]
                        and imported.get("engine_version") == request["engine_version"]
                        and (request["kit_source"] or imported.get("kit_version") == request["kit_version"]),
                        {"imported": imported, "os-optics --version": packages["os_optics_version_output"],
                         "os-optics-kit --version": packages["os_optics_kit_version_output"]}))
    checks.append(check("S3.harness_isolated", "setup",
                        "the browser harness can import neither os-optics nor the kit",
                        not harness["engine_importable"] and not harness["kit_importable"], harness))
    return checks


def check(check_id: str, step: str, claim: str, ok: bool | None, details, defect: str | None = None) -> dict:
    outcome = "not_applicable" if ok is None else ("pass" if ok else "fail")
    return {"id": check_id, "step": step, "claim": claim, "outcome": outcome, "defect": defect, "details": details}


def apply_known_defects(checks: list[dict], known: list[str]) -> None:
    for item in checks:
        if item.get("defect") in known:
            if item["outcome"] == "fail":
                item["outcome"] = "known_defect"
            elif item["outcome"] == "pass":
                item["note"] = f"listed known defect {item['defect']} not reproduced"


def verdict(checks: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in checks:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return {"outcome": "fail" if counts.get("fail") else "pass", "counts": counts,
            "failed": [c["id"] for c in checks if c["outcome"] == "fail"],
            "known_defects_reproduced": sorted({c["defect"] for c in checks if c["outcome"] == "known_defect"})}


# ---------------------------------------------------------------------------------------------------- drive phase
# Runs inside the harness environment: Playwright plus the product commands as subprocesses.

OBSERVE_JS = """() => {
    const app = document.getElementById('app');
    const pill = (id) => {
        const n = document.getElementById(id);
        if (!n) return null;
        return {hidden: n.classList.contains('hidden'), text: n.textContent, aria_hidden: n.getAttribute('aria-hidden'),
                class_name: n.className};
    };
    const hiddenWithText = [];
    document.querySelectorAll('.pill.hidden, #frame-updating.hidden').forEach((n) => {
        if ((n.textContent || '') !== '') hiddenWithText.push({id: n.id, text: n.textContent});
    });
    const attrs = {};
    for (const a of app.attributes) { if (a.name.startsWith('data-')) attrs[a.name] = a.value; }
    const f = document.querySelector('iframe.engine-frame.active');
    let frame = null;
    try {
        const w = f && f.contentWindow;
        const v = w && w.__opensciViewer;
        const errNode = w && w.document && w.document.getElementById('error');
        frame = {snapshot_id: f ? (f.getAttribute('data-snapshot-id') || '') : '',
                 camera_attr: f ? (f.getAttribute('data-camera') || '') : '',
                 viewer: !!v, error: errNode ? errNode.textContent : null};
        if (v) {
            const c = v.camera;
            Object.assign(frame, {
                project_hash: (v.doc.identity || {}).project_hash || null,
                scene_content_hash: (v.doc.identity || {}).scene_content_hash || null,
                beams: Array.isArray(v.doc.beams) ? v.doc.beams.length : 0,
                rendered_beam_objects: Array.isArray(v.beamObjects) ? v.beamObjects.length : 0,
                parts: Array.isArray(v.doc.parts) ? v.doc.parts.length : 0,
                camera: {orthographic: !!c.isOrthographicCamera, position: c.position.toArray(),
                         target: v.controls.target.toArray(), up: c.up.toArray(), zoom: c.zoom,
                         stage: [v.width, v.height], auto_framed: v.autoFrame !== null && v.autoFrame !== undefined},
            });
        }
    } catch (e) { frame = {error: String(e)}; }
    const text = (id) => ((document.getElementById(id) || {}).textContent || '').trim();
    return {
        attrs: attrs,
        pills: {mode: pill('badge-mode'), freshness: pill('badge-freshness'), rev: pill('badge-rev'),
                conserved: pill('badge-conserved'), geom_only: pill('badge-geom-only'),
                schematic: pill('badge-schematic'), updating: pill('frame-updating')},
        hidden_with_text: hiddenWithText,
        power_text: text('power-body'),
        execution_text: text('execution-kpis'),
        scene_meta: text('scene-meta'),
        branch_chips: text('scene-branch-chips'),
        banner_error: text('banner-error'),
        body_text: document.body.textContent,
        navigations: performance.getEntriesByType('navigation').length,
        marker: window.__releasedViewerCheck || null,
        frame: frame,
    };
}"""


def format_power(watts) -> str:
    """viewer.js formatPower, for the values this check compares."""
    if watts is None:
        return "—"
    magnitude = abs(watts)
    if magnitude == 0:
        return "0 W"
    for floor, scale, unit, digits in ((0.99995, 1.0, "W", 5), (0.99995e-3, 1e3, "mW", 5), (0.99995e-6, 1e6, "µW", 5),
                                       (0.99995e-9, 1e9, "nW", 4)):
        if magnitude >= floor:
            return f"{float(f'{watts * scale:.{digits}g}'):g} {unit}"
    return f"{watts:.3e} W"


def drive(plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    work, out = Path(plan["work"]), Path(plan["out"])
    result: dict = {"checks": [], "revisions": {}}
    try:
        _drive(plan, work, out, result)
    except Exception as exc:  # noqa: BLE001 - recorded: a check that could not finish is a failed check
        result["checks"].append(check("D.sequence_completed", "drive", "the whole sequence ran to its end", False,
                                      {"error": f"{type(exc).__name__}: {exc}"}))
    apply_known_defects(result["checks"], plan["known_defects"])
    (work / "drive-result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def _product_env(work: Path) -> dict:
    env = dict(os.environ)
    env["OPENSCI_CATALOG_DIR"] = (work / "catalog").as_posix()  # an isolated catalog store
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    return env


def _engine_json(plan: dict, case: Path, out: Path, name: str, *args: str) -> dict:
    done = run([plan["product"]["os_optics"], *args], cwd=case, env=_product_env(Path(plan["work"])), check=False,
               timeout=600)
    engine_dir = out / "engine"
    engine_dir.mkdir(exist_ok=True)
    (engine_dir / f"{name}.json").write_text(done.stdout, encoding="utf-8")
    try:
        document = json.loads(done.stdout)
    except json.JSONDecodeError:
        document = None
    return {"exit": done.returncode, "json": document, "stderr": done.stderr[-2000:], "file": f"engine/{name}.json"}


def _prepare_input(plan: dict, case: Path) -> dict:
    kind, _, name = plan["input"].partition(":")
    env = _product_env(Path(plan["work"]))
    if kind == "case":
        located = run([plan["product"]["python"], "-I", "-c",
                       "import os_optics_kit, pathlib, sys; print(pathlib.Path(os_optics_kit.__file__).parent / "
                       "'_data' / 'cases' / sys.argv[1] / 'project.json')", name], cwd=case, env=env)
        source = Path(located.stdout.strip())
        shutil.copy2(source, case / "project.json")
        origin = {"kind": "kit-wheel-case", "case": name, "path_in_wheel": f"os_optics_kit/_data/cases/{name}/project.json"}
    elif kind == "example":
        made = run([plan["product"]["os_optics"], "project", "example", "--kind", name], cwd=case, env=env)
        (case / "project.json").write_text(made.stdout, encoding="utf-8")
        origin = {"kind": "engine-example", "example": name}
    else:
        raise SetupError(f"--input must be case:NAME or example:KIND, got {plan['input']!r}")
    origin["sha256"] = sha256_file(case / "project.json")
    return origin


def _edit_project(project: Path) -> dict:
    """Rev 2: triple the power of every source of the Project (a physics edit: same geometry)."""
    document = json.loads(project.read_text(encoding="utf-8"))
    changed = []
    for component in document.get("components") or []:
        model = component.get("source_model") if isinstance(component, dict) else None
        if isinstance(model, dict) and isinstance(model.get("power_w"), (int, float)):
            model["power_w"] = model["power_w"] * 3
            changed.append({"instance_id": component.get("instance_id"), "power_w": model["power_w"]})
    if not changed:
        raise SetupError("the input Project has no source with power_w to edit")
    staged = project.with_name(project.name + ".edit")
    staged.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    os.replace(staged, project)
    return {"operation": "source power_w x3", "changed": changed, "sha256": sha256_file(project)}


def _run_project(plan: dict, case: Path, out: Path, name: str) -> dict:
    """os-optics project run project.json --out-dir run (retried on a Windows file lock held by the watcher)."""
    attempts = []
    for attempt in range(1, 4):
        done = run([plan["product"]["os_optics"], "project", "run", "project.json", "--out-dir", "run"], cwd=case,
                   env=_product_env(Path(plan["work"])), check=False, timeout=1200)
        attempts.append({"attempt": attempt, "exit": done.returncode, "stderr": done.stderr[-1500:]})
        # exit 0/1/2: the calculation completed (requirements met / unmet / undetermined); 3: it did not
        if done.returncode in (0, 1, 2) and (case / "run" / "manifest.json").is_file():
            (out / "engine").mkdir(exist_ok=True)
            (out / "engine" / f"{name}.json").write_text(done.stdout, encoding="utf-8")
            return {"exit": done.returncode, "attempts": attempts, "file": f"engine/{name}.json"}
        time.sleep(1.0)
    raise SetupError(f"'os-optics project run' failed: {attempts}")


def _dir_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(sha256_file(item).encode("ascii"))
    return digest.hexdigest()


class Viewer:
    """os-optics-kit view project.json --run run --no-open: the READY record, then stopped at the end."""

    def __init__(self, plan: dict, case: Path, log: Path):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.log = log.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [plan["product"]["os_optics_kit"], "view", "project.json", "--run", "run", "--no-open", "--port", "0",
             "--poll-interval", "0.2"],
            cwd=case, env=_product_env(Path(plan["work"])), stdout=subprocess.PIPE, stderr=self.log, text=True,
            encoding="utf-8", creationflags=flags)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)

    def ready(self, timeout: float) -> dict:
        line = self.lines.get(timeout=timeout)
        return json.loads(line)

    def stop(self) -> dict:
        if self.process.poll() is None:
            if os.name == "nt":
                # The viewer stops cleanly on SIGINT/SIGTERM; a process on Windows has no such signal from outside
                # its console, so it is terminated there with its launcher's child (the exit status then says
                # nothing about the viewer).
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True)
                how = "terminate"
            else:
                self.process.send_signal(signal.SIGINT)
                how = "SIGINT"
            try:
                code = self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=20)
                how += "+kill"
        else:
            code, how = self.process.returncode, "already-exited"
        self.log.close()
        return {"exit": code, "stopped_by": how}


def _snapshot(page, url: str) -> dict:
    response = page.request.get(url + "api/snapshot")
    envelope = response.json()
    return envelope


def _api_summary(envelope: dict) -> dict:
    snap = envelope.get("snapshot") or {}
    power = snap.get("power_ledger") or {}
    fresh = snap.get("freshness") or {}
    scene = snap.get("scene") or {}
    execution = snap.get("execution") or {}
    identity = snap.get("identity") or {}
    return {
        "status": envelope.get("status"), "state_seq": envelope.get("state_seq"),
        "revision": snap.get("revision"), "snapshot_id": snap.get("snapshot_id"),
        "result_mode": snap.get("result_mode"), "is_computed_result": snap.get("is_computed_result"),
        "freshness": {"checked": fresh.get("checked"), "state": fresh.get("state"), "reasons": fresh.get("reasons")},
        "identity": {k: identity.get(k) for k in ("project_hash", "result_hash", "scene_content_hash",
                                                  "engine_version", "kit_version")},
        "power_ledger": {k: power.get(k) for k in ("available", "reason", "total_input_W", "residual_W", "conserved")},
        "branches": len(snap.get("branches") or []),
        "execution": {k: execution.get(k) for k in ("outcome", "requirement_outcome", "in_model_scope")}
        if snap.get("execution") else None,
        "scene": {k: scene.get(k) for k in ("geometry_only", "branch_count", "drawable_ray_count",
                                            "scene_content_hash", "engine_view_url")},
    }


def _expected_attrs(api: dict) -> dict:
    power = api["power_ledger"]
    flag = {True: "true", False: "false", None: ""}
    return {
        "data-result-mode": api["result_mode"] or "",
        "data-computed": flag[api["is_computed_result"] is True],
        "data-freshness": (api["freshness"]["state"] or "") if api["freshness"]["checked"] else "",
        "data-project-hash": api["identity"]["project_hash"] or "",
        "data-execution-outcome": (api["execution"] or {}).get("outcome") or "",
        "data-power-available": flag[power["available"] is True],
        "data-power-conserved": flag[power["conserved"]] if power["available"] else "",
        "data-branch-count": str(api["branches"]),
        "data-geometry-only": flag[api["scene"]["geometry_only"] is True],
        "data-drawable-ray-count": "" if api["scene"]["drawable_ray_count"] is None
        else str(api["scene"]["drawable_ray_count"]),
    }


def _wait(page, url: str, predicate, timeout: float) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout
    while True:
        observed = page.evaluate(OBSERVE_JS)
        api = _api_summary(_snapshot(page, url))
        if predicate(observed, api):
            return observed, api
        if time.monotonic() > deadline:
            raise TimeoutError(f"the page did not reach the expected state: {json.dumps(api)[:600]} / "
                               f"{json.dumps({k: observed[k] for k in ('attrs', 'frame')})[:600]}")
        page.wait_for_timeout(250)


def _settled(observed: dict, api: dict) -> bool:
    """The page shows the server's current snapshot, frame and panel together."""
    frame = observed.get("frame") or {}
    return (api["status"] == "ok" and observed["attrs"].get("data-snapshot-id") == api["snapshot_id"]
            and frame.get("snapshot_id") == api["snapshot_id"] and (frame.get("viewer") or frame.get("error")))


def _same_camera(pose: dict | None, ref: dict | None) -> tuple[bool, dict]:
    """Same projection, line of sight, aim point and screen up (the eye too, unless an auto-framed camera was framed
    again on a drawing area of another size)."""
    if not pose or not ref:
        return False, {"pose": pose, "reference": ref}

    def direction(p):
        d = [a - b for a, b in zip(p["position"], p["target"], strict=True)]
        n = sum(x * x for x in d) ** 0.5
        return [x / n for x in d]

    def diff(a, b):
        return max(abs(x - y) for x, y in zip(a, b, strict=True))

    facts = {"line_of_sight_change": diff(direction(pose), direction(ref)),
             "target_change_mm": diff(pose["target"], ref["target"]), "up_change": diff(pose["up"], ref["up"]),
             "eye_change_mm": diff(pose["position"], ref["position"]),
             "projection_same": pose["orthographic"] == ref["orthographic"], "pose": pose, "reference": ref}
    ok = (facts["projection_same"] and facts["line_of_sight_change"] <= 1e-6 and facts["target_change_mm"] <= 1e-6
          and facts["up_change"] <= 1e-6)
    if ok and not (pose["auto_framed"] and ref["auto_framed"] and pose["stage"] != ref["stage"]):
        ok = facts["eye_change_mm"] <= 1e-6
    return ok, facts


def _shot(page, out: Path, name: str) -> dict:
    path = out / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return {"file": path.name, "sha256": sha256_file(path)}


def _dom_checks(step: str, observed: dict, api: dict, *, computed: bool, previous_total: float | None) -> list[dict]:
    checks = []
    frame = observed["frame"] or {}
    pills = observed["pills"]
    attrs = observed["attrs"]
    panel_bound = (attrs.get("data-revision") == str(api["revision"]) and attrs.get("data-snapshot-id") ==
                   api["snapshot_id"] and frame.get("snapshot_id") == api["snapshot_id"]
                   and frame.get("project_hash") == api["identity"]["project_hash"])
    if computed:
        rays = frame.get("beams", 0) > 0 and frame.get("rendered_beam_objects", 0) > 0 and not frame.get("error")
        power_shown = format_power(api["power_ledger"]["total_input_W"]) in observed["power_text"]
        conserved_word = {True: EN["conserved"], False: EN["not_conserved"]}.get(api["power_ledger"]["conserved"])
        conserved_ok = (conserved_word is None and pills["conserved"]["hidden"]) or (
            not pills["conserved"]["hidden"] and pills["conserved"]["text"] == conserved_word)
        status_ok = (pills["mode"]["text"] == EN["computed"] and pills["freshness"]["text"] == EN["current"]
                     and not pills["freshness"]["hidden"])
        meta_ok = f"{api['scene']['branch_count']} branches" in observed["scene_meta"]
        checks.append(check(f"{step}.rays_ledger_status_together", step,
                            "one observation shows the saved revision's rays (3D frame), power ledger and status, "
                            "bound to the /api/snapshot snapshot",
                            panel_bound and rays and power_shown and conserved_ok and status_ok and meta_ok,
                            {"panel_bound": panel_bound, "rays_drawn": rays, "power_text_has_total": power_shown,
                             "conserved_badge": pills["conserved"], "mode": pills["mode"]["text"],
                             "freshness": pills["freshness"]["text"], "scene_meta": observed["scene_meta"],
                             "frame": {k: frame.get(k) for k in ("snapshot_id", "project_hash", "beams",
                                                                  "rendered_beam_objects", "error")}}))
    else:
        withheld = (frame.get("beams") == 0 and pills["conserved"]["hidden"] and pills["mode"]["text"] == EN["stale"]
                    and pills["freshness"]["text"] == EN["historical"]
                    and (previous_total is None or format_power(previous_total) not in observed["power_text"])
                    and not POWER_VALUE.search(observed["power_text"]))
        checks.append(check(f"{step}.stale_withheld", step,
                            "after the Project edit the page states the run is historical and withholds its rays and "
                            "ledger (geometry only), bound to the /api/snapshot snapshot",
                            panel_bound and withheld,
                            {"panel_bound": panel_bound, "frame_beams": frame.get("beams"), "mode": pills["mode"]["text"],
                             "freshness": pills["freshness"]["text"], "conserved_hidden": pills["conserved"]["hidden"],
                             "power_text": observed["power_text"][:400]}))
    hidden_clean = observed["hidden_with_text"] == [] and all(
        p is None or not p["hidden"] or p["aria_hidden"] == "true" for p in pills.values())
    stale_words = [w for w in (EN["conserved"], EN["not_conserved"]) if not computed and w in observed["body_text"]]
    checks.append(check(f"{step}.hidden_badges_empty", step,
                        "every hidden badge is empty and aria-hidden: the DOM holds no text the page does not show",
                        hidden_clean and not stale_words,
                        {"hidden_with_text": observed["hidden_with_text"], "stale_words_in_dom": stale_words,
                         "pills": pills}, defect="kit#45-V2"))
    expected = _expected_attrs(api)
    shown = {k: attrs.get(k) for k in STATE_ATTRIBUTES}
    checks.append(check(f"{step}.state_attributes", step,
                        "#app carries the shown snapshot's state as data attributes equal to /api/snapshot",
                        shown == expected, {"shown": shown, "expected": expected}, defect="kit#45-V2"))
    return checks


def _drive(plan: dict, work: Path, out: Path, result: dict) -> None:
    from playwright.sync_api import sync_playwright

    case = work / "case"
    if case.exists():
        shutil.rmtree(case)
    case.mkdir()
    timeout = float(plan["timeout"])
    result["catalog"] = _engine_json(plan, case, out, "catalog-status", "catalog", "status")
    result["catalog"].pop("stderr", None)
    result["input"] = _prepare_input(plan, case)
    checks = result["checks"]
    revs = result["revisions"]

    # rev 1: the saved run
    rev1_run = _run_project(plan, case, out, "rev1-project-run")
    e1 = {"describe": _engine_json(plan, case, out, "rev1-results-describe", "results", "describe", "run"),
          "power": _engine_json(plan, case, out, "rev1-results-power-budget", "results", "power-budget", "run"),
          "freshness": _engine_json(plan, case, out, "rev1-results-freshness", "results", "freshness", "run",
                                    "--project", "project.json")}
    run_digest_1 = _dir_digest(case / "run")
    unchanged: dict = {}
    viewer = Viewer(plan, case, out / "viewer.log")
    stop_info = None
    try:
        ready = viewer.ready(timeout)
        url = ready["url"] if ready["url"].endswith("/") else ready["url"] + "/"
        checks.append(check("R1.ready_record", "R1",
                            "the viewer's READY record states revision 1, a computed current run, read-only",
                            ready.get("status") == "READY" and ready.get("revision") == 1
                            and ready.get("result_mode") == "computed_run" and ready.get("freshness_state") == "CURRENT"
                            and ready.get("read_only") is True, ready))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=plan["browser"], headless=True)
            result["browser"] = {"executable": plan["browser"], "version": browser.version}
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 840})
                console_errors: list[str] = []
                requests: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("request", lambda req: requests.append(req.url))
                page.goto(url + "?lang=en", wait_until="domcontentloaded")
                page.evaluate("() => { window.__releasedViewerCheck = 'one-load'; }")

                o1, a1 = _wait(page, url, lambda o, a: _settled(o, a) and a["revision"] == 1, timeout)
                total1 = a1["power_ledger"]["total_input_W"]
                d1 = e1["describe"]["json"] or {}
                p1 = e1["power"]["json"] or {}
                engine_total1 = sum(line.get("input_power_W") or 0.0 for line in p1.get("lines") or [])
                checks.append(check("R1.api_matches_engine", "R1",
                                    "/api/snapshot states the run the engine describes: project hash, total input power, "
                                    "freshness CURRENT, engine and kit versions",
                                    a1["result_mode"] == "computed_run" and a1["freshness"]["state"] == "CURRENT"
                                    and a1["identity"]["project_hash"] == (d1.get("run") or {}).get("project_hash")
                                    and total1 is not None and abs(total1 - engine_total1) <= 1e-12 * max(1.0, total1)
                                    and (e1["freshness"]["json"] or {}).get("freshness") == "CURRENT"
                                    and a1["identity"]["engine_version"] == plan["engine_version"]
                                    and a1["identity"]["kit_version"] == plan["kit_version"],
                                    {"api": a1, "engine_project_hash": (d1.get("run") or {}).get("project_hash"),
                                     "engine_total_input_W": engine_total1,
                                     "engine_freshness": (e1["freshness"]["json"] or {}).get("freshness")}))
                checks.extend(_dom_checks("R1", o1, a1, computed=True, previous_total=None))
                revs["rev1"] = {"run": rev1_run, "engine": {k: v["file"] for k, v in e1.items()}, "api": a1,
                                "dom": {k: o1[k] for k in ("attrs", "pills", "power_text", "execution_text",
                                                           "scene_meta", "branch_chips", "frame")},
                                "screenshot": _shot(page, out, "rev1-saved-run")}

                # rev 2: a Project edit (the run becomes historical)
                edit = _edit_project(case / "project.json")
                o2, a2 = _wait(page, url, lambda o, a: _settled(o, a) and a["result_mode"] == "stale_run", timeout)
                e2 = {"freshness": _engine_json(plan, case, out, "rev2-results-freshness", "results", "freshness",
                                                "run", "--project", "project.json"),
                      "check": _engine_json(plan, case, out, "rev2-project-check", "project", "check", "project.json")}
                current_hash = (e2["check"]["json"] or {}).get("project_hash")
                checks.append(check("R2.api_stale_matches_engine", "R2",
                                    "/api/snapshot after the edit: stale run, HISTORICAL, ledger and branches withheld, "
                                    "the edited Project's hash; the engine agrees the run is not current",
                                    a2["result_mode"] == "stale_run" and a2["is_computed_result"] is False
                                    and a2["freshness"]["state"] == "HISTORICAL"
                                    and a2["power_ledger"]["available"] is False and a2["power_ledger"]["conserved"] is None
                                    and a2["branches"] == 0 and a2["revision"] > a1["revision"]
                                    and a2["identity"]["project_hash"] == current_hash
                                    and current_hash != a1["identity"]["project_hash"]
                                    and (e2["freshness"]["json"] or {}).get("freshness") not in (None, "CURRENT"),
                                    {"api": a2, "engine_project_hash": current_hash,
                                     "engine_freshness": (e2["freshness"]["json"] or {}).get("freshness")}))
                checks.extend(_dom_checks("R2", o2, a2, computed=False, previous_total=total1))
                cam_ok, cam = _same_camera((o2["frame"] or {}).get("camera"), (o1["frame"] or {}).get("camera"))
                checks.append(check("R2.camera_kept", "R2",
                                    "the camera is kept from the traced view to the geometry-only view",
                                    cam_ok, cam, defect="kit#45-V1"))
                revs["rev2"] = {"edit": edit, "engine": {k: v["file"] for k, v in e2.items()}, "api": a2,
                                "dom": {k: o2[k] for k in ("attrs", "pills", "power_text", "execution_text",
                                                           "scene_meta", "branch_chips", "frame")},
                                "screenshot": _shot(page, out, "rev2-project-edit-stale")}

                # the viewer read the saved run and the edited Project; it changed neither
                unchanged["run_during_rev1_rev2"] = _dir_digest(case / "run") == run_digest_1
                unchanged["project_during_rev2"] = sha256_file(case / "project.json") == edit["sha256"]

                # rev 3: the edited Project re-run into the same directory
                rev3_run = _run_project(plan, case, out, "rev3-project-run")
                run_digest_3 = _dir_digest(case / "run")
                e3 = {"describe": _engine_json(plan, case, out, "rev3-results-describe", "results", "describe", "run"),
                      "power": _engine_json(plan, case, out, "rev3-results-power-budget", "results", "power-budget",
                                            "run"),
                      "freshness": _engine_json(plan, case, out, "rev3-results-freshness", "results", "freshness",
                                                "run", "--project", "project.json")}
                d3 = e3["describe"]["json"] or {}
                hash3 = (d3.get("run") or {}).get("project_hash")
                o3, a3 = _wait(page, url, lambda o, a: _settled(o, a) and a["result_mode"] == "computed_run"
                               and a["identity"]["project_hash"] == hash3, timeout)
                total3 = a3["power_ledger"]["total_input_W"]
                p3 = e3["power"]["json"] or {}
                engine_total3 = sum(line.get("input_power_W") or 0.0 for line in p3.get("lines") or [])
                checks.append(check("R3.api_current_matches_engine", "R3",
                                    "/api/snapshot after the re-run: computed and CURRENT again, the engine's project "
                                    "hash and total input power (3x revision 1)",
                                    a3["freshness"]["state"] == "CURRENT" and a3["revision"] > a2["revision"]
                                    and hash3 == current_hash and total3 is not None
                                    and abs(total3 - engine_total3) <= 1e-12 * max(1.0, total3)
                                    and abs(total3 - 3 * total1) <= 1e-9 * max(1.0, total3)
                                    and (e3["freshness"]["json"] or {}).get("freshness") == "CURRENT",
                                    {"api": a3, "engine_project_hash": hash3, "engine_total_input_W": engine_total3,
                                     "rev1_total_input_W": total1,
                                     "engine_freshness": (e3["freshness"]["json"] or {}).get("freshness")}))
                checks.extend(_dom_checks("R3", o3, a3, computed=True, previous_total=None))
                cam_ok, cam = _same_camera((o3["frame"] or {}).get("camera"), (o2["frame"] or {}).get("camera"))
                checks.append(check("R3.camera_kept", "R3",
                                    "the camera is kept from the geometry-only view (revision 2) back to the traced view",
                                    cam_ok, cam, defect="kit#45-V1"))
                revs["rev3"] = {"run": rev3_run, "engine": {k: v["file"] for k, v in e3.items()}, "api": a3,
                                "dom": {k: o3[k] for k in ("attrs", "pills", "power_text", "execution_text",
                                                           "scene_meta", "branch_chips", "frame")},
                                "screenshot": _shot(page, out, "rev3-rerun-current")}

                final = page.evaluate(OBSERVE_JS)
                port = str(ready.get("port"))
                foreign = sorted({r for r in requests if not (r.startswith(f"http://127.0.0.1:{port}/")
                                                              or r.startswith("data:") or r == "about:blank")})
                checks.append(check("W.one_page_load", "whole",
                                    "the three revisions were followed with one page load (no reload)",
                                    final["navigations"] == 1 and final["marker"] == "one-load",
                                    {"navigations": final["navigations"], "marker": final["marker"]}))
                checks.append(check("W.loopback_only", "whole",
                                    "every request of the page went to the viewer's loopback address",
                                    not foreign, {"requests": len(requests), "foreign": foreign}))
                checks.append(check("W.no_console_errors", "whole", "the page logged no console error",
                                    not console_errors, {"console_errors": console_errors}))
            finally:
                browser.close()
    finally:
        stop_info = viewer.stop()
    unchanged["run_during_rev3"] = _dir_digest(case / "run") == run_digest_3
    unchanged["project_during_rev3"] = sha256_file(case / "project.json") == revs["rev2"]["edit"]["sha256"]
    checks.append(check("W.inputs_unchanged_by_viewer", "whole",
                        "the read-only viewer changed neither project.json nor the run directory",
                        all(unchanged.values()), {**unchanged, "run_digest_rev1": run_digest_1,
                                                  "run_digest_rev3": run_digest_3}))
    clean = None if stop_info["stopped_by"] == "terminate" else stop_info["exit"] == 0
    checks.append(check("W.server_stopped_cleanly", "whole",
                        "the viewer exited with status 0 on SIGINT (not applicable where it is terminated: Windows)",
                        clean, stop_info))
    checks.append(check("D.sequence_completed", "drive", "the whole sequence ran to its end", True, {}))


if __name__ == "__main__":
    sys.exit(main())
