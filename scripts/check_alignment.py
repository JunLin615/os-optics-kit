"""Check the kit against a fresh, isolated installation of os-optics and os-optics-kit.

python scripts/check_alignment.py --out run-alignment
python scripts/check_alignment.py --engine latest --out run-latest
python scripts/check_alignment.py --engine-wheel os_optics-X.Y.Z-py3-none-any.whl --catalog-file RELEASE.json \
    --out run-local
python scripts/check_alignment.py --kit-wheel dist/os_optics_kit-0.3.0-py3-none-any.whl --out run-kit-wheel
python scripts/check_alignment.py --optional all --out run-alignment-full

The engine comes from PyPI (the verified version in baseline.json, an exact version, or latest) or from a local wheel;
the kit comes from this checkout or from a built wheel. The catalog snapshot is synchronized by release from the
catalog service, or imported from a file; either way its SHA-256 must match. alignment-checks.json lists the checks.
A check never changes baseline.json, alignment-checks.json or any other tracked file.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = "baseline.json"
CHECKS_FILE = "alignment-checks.json"
PYPI_INDEX = "https://pypi.org/simple"
VERSION = re.compile(r"\d+(?:\.\d+)+(?:a\d+|b\d+|rc\d+|\.post\d+|\.dev\d+)?")
CATALOG_RELEASE = re.compile(r"20\d\d-\d\d-\d\d\.\d+")
SHA256 = re.compile(r"[0-9a-f]{64}")
CHECK_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
PLACEHOLDERS = {"python", "runs", "out"}
# The website's former host; a catalog service URL on it is a configuration error.
RETIRED_HOSTS = ("opensci-" + "optic.com",)

# The catalog service or its transport could not deliver a usable release.
NETWORK_CODES = {
    "CATALOG_SERVICE_UNREACHABLE", "CATALOG_SERVICE_ERROR", "CATALOG_NOT_AVAILABLE",
    "CATALOG_RELEASE_NOT_FOUND", "CATALOG_SYNC_UNAVAILABLE", "CATALOG_ORIGIN_INVALID",
    "CATALOG_STORE_BUSY",
}
# A delivered or stored release, or a product/model in it, cannot be used.
DATA_CODES = {
    "CATALOG_INVALID", "CATALOG_INTEGRITY_FAILED", "CATALOG_RELEASE_CONFLICT", "CATALOG_STORE_ERROR",
    "CATALOG_ACTIVE_UNKNOWN", "ENGINE_UPGRADE_REQUIRED",
}
MODEL_CODES = {
    "OPTICAL_SKU_NOT_PUBLISHED", "SIMULATION_MODEL_AMBIGUOUS", "PRODUCT_ROLE_MISMATCH",
    "SURFACE_COATING_REQUIRED", "EXAMPLE_UNAVAILABLE", "ENGINE_UPGRADE_REQUIRED",
}
# The engine refused a query or a model kind: its API or its supported models changed, not the catalog data.
PACKAGE_CODES = {"CATALOG_QUERY_INVALID", "SIMULATION_MODEL_UNSUPPORTED"}
# Final traceback lines of uncaught engine errors: "CatalogError: CATALOG_...: message".
EXCEPTION_CODE = re.compile(r"^(?:[\w.]+\.)?(?:CatalogError|LocalRunError): ([A-Z][A-Z0-9_]+)\b", re.M)
# Uncaught transport exceptions in a catalog-dependent command.
TRANSPORT_ERROR = re.compile(
    r"^(?:urllib\.error\.(?:URLError|HTTPError)|http\.client\.\w+|ssl\.SSL\w*Error|"
    r"socket\.(?:timeout|gaierror)|(?:builtins\.)?(?:Connection(?:Reset|Refused|Aborted)?Error|TimeoutError))\b",
    re.M,
)


def release_version(value):
    """Accept an exact public release number, never a URL or pip option."""
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError(f"Expected an exact PyPI version, got {value!r}")
    return value


def latest_version():
    request = urllib.request.Request(
        "https://pypi.org/pypi/os-optics/json", headers={"User-Agent": "os-optics-kit-alignment"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return release_version(json.load(response)["info"]["version"])


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_version(path, distribution):
    """Version from a wheel filename (``os_optics-0.13.0-py3-none-any.whl``) of the expected distribution."""
    path = Path(path)
    parts = path.name.split("-")
    if path.suffix != ".whl" or len(parts) < 5 or parts[0] != distribution.replace("-", "_"):
        raise ValueError(f"{path.name}: not a {distribution} wheel")
    return release_version(parts[1])


def host_pattern(host):
    """Match a host name, including shields.io badge escaping ("--" for "-")."""
    escaped = re.escape(host).replace(r"\-", "-+")
    return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.I)


def read_baseline(root):
    """The fields of baseline.json this runner uses, validated."""
    document = json.loads((Path(root) / BASELINE_FILE).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != "os-optics-kit.baseline" \
            or document.get("schema_version") != 1:
        raise ValueError("expected schema os-optics-kit.baseline, schema_version 1")
    engine, catalog = document.get("engine"), document.get("catalog")
    if not isinstance(engine, dict) or not isinstance(catalog, dict):
        raise ValueError("engine and catalog must be objects")
    if not isinstance(catalog.get("version"), str) or not CATALOG_RELEASE.fullmatch(catalog["version"]):
        raise ValueError(f"catalog.version: expected a release such as 2026-10-28.1, got {catalog.get('version')!r}")
    if not isinstance(catalog.get("sha256"), str) or not SHA256.fullmatch(catalog["sha256"]):
        raise ValueError("catalog.sha256: expected 64 lower-case hexadecimal characters")
    return {"engine_version": release_version(engine.get("verified_version")),
            "requirement": engine.get("requirement"), "kit_version": release_version(document.get("kit_version")),
            "catalog_version": catalog["version"], "catalog_sha256": catalog["sha256"],
            "python": document.get("python")}


def source_kit_version(root):
    text = (Path(root) / "tools" / "os_optics_kit" / "_version.py").read_text(encoding="utf-8")
    match = re.search(r"^__version__ = \"([^\"]+)\"", text, re.M)
    if not match:
        raise ValueError("tools/os_optics_kit/_version.py: no __version__")
    return release_version(match.group(1))


# --------------------------------------------------------------------------------------------------
# Failure classification


def _documents(text):
    """JSON objects in a command's output: the whole text, else single lines from the end."""
    try:
        return [json.loads(text)]
    except ValueError:
        pass
    found = []
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                found.append(json.loads(line))
            except ValueError:
                continue
    return found


def error_codes(text):
    codes = []
    for document in _documents(text):
        if not isinstance(document, dict):
            continue
        errors = [document.get("error", document)]
        problems = document.get("problems")
        if isinstance(problems, list):
            errors += problems
        if isinstance(errors[0], dict) and isinstance(errors[0].get("problems"), list):
            errors += errors[0]["problems"]
        codes += [error.get("code") for error in errors if isinstance(error, dict)]
    codes += EXCEPTION_CODE.findall(text or "")
    return [code for code in codes if isinstance(code, str)]


def catalog_failure_category(text, transport=True):
    """Classify explicit catalog codes; unrecognized failures remain package/API failures."""
    codes = error_codes(text or "")
    if any(code in NETWORK_CODES for code in codes):
        return "catalog_network"
    if any(code in PACKAGE_CODES for code in codes):
        return "package_api"
    if any(code in DATA_CODES or code in MODEL_CODES or code.startswith("CATALOG_") for code in codes):
        return "catalog_data"
    if transport and not codes and TRANSPORT_ERROR.search(text or ""):
        return "catalog_network"
    return "package_api"


# --------------------------------------------------------------------------------------------------
# Declarative checks


CHECK_FIELDS = {
    "name", "description", "argv", "expected_exit", "expected_status", "status_field", "json_error",
    "needs_catalog", "needs", "requirements", "requires_python", "optional", "timeout", "env",
}
RESERVED = re.compile(
    r"(?:baseline-record|check-definitions|check-selection|release-selection|engine-artifact|kit-artifact|"
    r"create-environment|install-engine|install-kit|install-default-requirements|installed-environment|"
    r"installed-version-match|installed-package-location|installed-packages|installed-artifact|"
    r"dependency-consistency|catalog-file|catalog-import|catalog-sync|catalog-identity|alignment-runner)(?:-.*)?"
    r"|(?:create-environment|install|install-kit|installed-environment|installed-packages)-env-.*"
)


def _repository_file(root, value, label):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label}: expected a repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label}: expected a repository-relative path, got {value!r}")
    if not (root / value).is_file():
        raise ValueError(f"{label}: {value} does not exist")
    return value


def _placeholders(value, label):
    for token in PLACEHOLDER.findall(value):
        if token not in PLACEHOLDERS:
            raise ValueError(f"{label}: unknown placeholder {{{token}}}")
    return value


def load_checks(root):
    """Read and validate alignment-checks.json; every check runs in an isolated interpreter."""
    root = Path(root)
    document = json.loads((root / CHECKS_FILE).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"{CHECKS_FILE}: expected an object with schema_version 1")
    unknown = set(document) - {"schema_version", "default_requirements", "checks"}
    if unknown:
        raise ValueError(f"{CHECKS_FILE}: unknown keys {sorted(unknown)}")
    requirements = document.get("default_requirements", [])
    if not isinstance(requirements, list):
        raise ValueError(f"{CHECKS_FILE}: default_requirements must be a list")
    requirements = [_repository_file(root, item, "default_requirements") for item in requirements]
    if not isinstance(document.get("checks"), list) or not document["checks"]:
        raise ValueError(f"{CHECKS_FILE}: checks must be a non-empty list")
    checks, names = [], set()
    for index, raw in enumerate(document["checks"]):
        label = f"{CHECKS_FILE} checks[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{label}: expected an object")
        unknown = set(raw) - CHECK_FIELDS
        if unknown:
            raise ValueError(f"{label}: unknown keys {sorted(unknown)}")
        name = raw.get("name")
        if not isinstance(name, str) or not CHECK_NAME.fullmatch(name) or RESERVED.fullmatch(name):
            raise ValueError(f"{label}: invalid or reserved name {name!r}")
        if name in names:
            raise ValueError(f"{label}: duplicate name {name}")
        label = f"{CHECKS_FILE} check {name}"
        argv = raw.get("argv")
        if (not isinstance(argv, list) or not argv or argv[0] != "{python}"
                or not all(isinstance(arg, str) for arg in argv)):
            raise ValueError(f"{label}: argv must be a list of strings starting with {{python}}")
        for arg in argv:
            _placeholders(arg, label)
        expected_status = raw.get("expected_status")
        if isinstance(expected_status, str):
            expected_status = [expected_status]
        if expected_status is not None and (
                not isinstance(expected_status, list) or not expected_status
                or not all(isinstance(item, str) for item in expected_status)):
            raise ValueError(f"{label}: expected_status must be a string or list of strings")
        needs = raw.get("needs", [])
        if not isinstance(needs, list) or any(item not in names for item in needs):
            raise ValueError(f"{label}: needs must list earlier checks")
        env = raw.get("env", {})
        if not isinstance(env, dict) or not all(
                isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and isinstance(value, str)
                for key, value in env.items()):
            raise ValueError(f"{label}: env must map upper-case names to strings")
        for value in env.values():
            _placeholders(value, label)
        flags = {key: raw.get(key, False) for key in ("json_error", "needs_catalog", "optional")}
        if not all(isinstance(value, bool) for value in flags.values()):
            raise ValueError(f"{label}: json_error, needs_catalog and optional must be booleans")
        expected_exit, timeout = raw.get("expected_exit", 0), raw.get("timeout", 180)
        if type(expected_exit) is not int or type(timeout) is not int or timeout <= 0:
            raise ValueError(f"{label}: expected_exit and timeout must be integers")
        status_field = raw.get("status_field", "status")
        if not isinstance(status_field, str) or not status_field:
            raise ValueError(f"{label}: status_field must be a string")
        requirements_file = raw.get("requirements")
        if requirements_file is not None:
            requirements_file = _repository_file(root, requirements_file, label + " requirements")
        requires_python = raw.get("requires_python")
        if requires_python is not None and (not isinstance(requires_python, str)
                                            or not re.fullmatch(r"3\.\d+", requires_python)):
            raise ValueError(f"{label}: requires_python must be a minimum version such as \"3.12\"")
        names.add(name)
        checks.append({
            "name": name, "description": raw.get("description"), "argv": argv,
            "expected_exit": expected_exit, "expected_status": expected_status, "status_field": status_field,
            "needs": needs, "requirements": requirements_file, "requires_python": requires_python,
            "env": env, "timeout": timeout, **flags,
        })
    return {"default_requirements": requirements, "checks": checks}


def _names(values):
    return [item for value in values or () for item in value.split(",") if item]


def select_checks(checks, optional, only=(), skip=()):
    """Default checks plus requested optional ones and everything they need.

    ``only`` restricts the run to the named checks and what they need; ``skip`` leaves out the named checks and
    every check that needs them.
    """
    optional, only, skip = _names(optional), _names(only), _names(skip)
    by_name = {check["name"]: check for check in checks}
    available = sorted(name for name, check in by_name.items() if check["optional"])
    unknown = [name for name in optional if name != "all" and name not in by_name]
    if unknown:
        raise ValueError(f"Unknown optional checks {unknown}; available: {available}")
    unknown = [name for name in only + skip if name not in by_name]
    if unknown:
        raise ValueError(f"Unknown checks {unknown}; available: {sorted(by_name)}")
    if only:
        selected = set(only)
    else:
        selected = {name for name, check in by_name.items() if not check["optional"]}
        selected |= set(available) if "all" in optional else set(optional)
    pending = list(selected)
    while pending:
        for need in by_name[pending.pop()]["needs"]:
            if need not in selected:
                selected.add(need)
                pending.append(need)
    removed = set(skip)
    for check in checks:  # declaration order: needs always come first
        if check["name"] in removed or removed.intersection(check["needs"]):
            removed.add(check["name"])
    return selected - removed


def environment_slug(requirements):
    stem = PurePosixPath(requirements).stem.lower()
    stem = stem[len("requirements-"):] if stem.startswith("requirements-") and stem != "requirements-" else stem
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "extra"


# --------------------------------------------------------------------------------------------------
# Runner


IDENTITY = (
    "import json,platform,sys,os_optics,os_optics_kit; from importlib.metadata import version; "
    "print(json.dumps({'engine_version':version('os-optics'),'kit_version':version('os-optics-kit'),"
    "'python':sys.version,'python_version':platform.python_version(),'platform':platform.platform(),"
    "'executable':sys.executable,'module_path':os_optics.__file__,'kit_module_path':os_optics_kit.__file__}))"
)


class AlignmentCheck:
    def __init__(self, root, out, engine=None, *, engine_wheel=None, engine_sha256=None, kit_wheel=None,
                 catalog_file=None, catalog_release=None, catalog_sha256=None, optional=(), only=(), skip=()):
        self.root = Path(root).resolve()
        self.out = Path(out).resolve()
        # Never overwrite earlier evidence or environments.
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "logs").mkdir()
        self.env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            self.env.pop(key, None)
        # The snapshot is prepared explicitly (pinned sync or import); runs never switch releases on their own.
        self.env.update(PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1",
                        MPLBACKEND="Agg", OPENSCI_CATALOG_DIR=str(self.out / ".catalog"),
                        OPENSCI_CATALOG_CHECK_INTERVAL="0")
        self.engine_wheel = Path(engine_wheel).resolve() if engine_wheel else None
        self.engine_sha256 = engine_sha256
        self.kit_wheel = Path(kit_wheel).resolve() if kit_wheel else None
        self.catalog_file = Path(catalog_file).resolve() if catalog_file else None
        self.catalog_release = catalog_release
        self.catalog_sha256 = catalog_sha256
        self.optional, self.only, self.skip_names = list(optional or ()), list(only or ()), list(skip or ())
        self.report = {
            "schema_version": 1, "baseline_version": None,
            "requested_version": engine or "baseline", "candidate_version": None,
            "installed_version": None, "kit_version": None, "python_version": None, "status": "FAIL", "checks": [],
            "engine_source": {"mode": "local-wheel" if self.engine_wheel else "pypi",
                              "publication_verified": False, "index_url": PYPI_INDEX},
            "kit_source": {"mode": "wheel" if self.kit_wheel else "source",
                           "file": str(self.kit_wheel) if self.kit_wheel else None},
            "catalog_source": {"mode": "file" if self.catalog_file else "sync",
                               "file": str(self.catalog_file) if self.catalog_file else None,
                               "release_requested": catalog_release},
            "optional_checks": {"requested": self.optional, "selected": [], "not_selected": []},
            "selection": {"only": self.only, "skip": self.skip_names, "left_out": []},
            "artifact_directory": str(self.out),
        }
        self.rows = {}
        self.target = None
        self.kit_expected = None
        self.environments = {}
        self.catalog_row = None

    # Evidence -----------------------------------------------------------------------------------

    def record(self, name, category, status, command, *, error=None, **extra):
        entry = {"name": name, "category": category, "status": status,
                 "command": list(command), "exit_code": None,
                 "stdout": None, "stderr": None, "error": error, **extra}
        self.report["checks"].append(entry)
        self.rows[name] = entry
        return entry

    def skip(self, name, blocker, **extra):
        return self.record(name, blocker["category"], "SKIPPED", [], blocked_by=blocker["name"],
                           error=f"Blocked by {blocker['name']} ({blocker['category']})", **extra)

    def command(self, name, category, command, *, expected_exit=0, result_status=None, status_field="status",
                json_error=False, timeout=180, catalog=False, classify=False, env=None):
        stdout = self.out / "logs" / f"{name}.stdout.txt"
        stderr = self.out / "logs" / f"{name}.stderr.txt"
        code, error, output, diagnostic = None, None, "", ""
        timed_out = False
        expected = [result_status] if isinstance(result_status, str) else result_status
        try:
            process = subprocess.run(command, cwd=self.root, env=dict(self.env, **(env or {})),
                                     capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     timeout=timeout)
            code, output, diagnostic = process.returncode, process.stdout or "", process.stderr or ""
            if code != expected_exit:
                error = f"Expected exit {expected_exit}, received {code}"
            elif expected is not None:
                try:
                    document = json.loads(output)
                except ValueError:
                    error = "Expected one JSON document on stdout"
                else:
                    value = document.get(status_field) if isinstance(document, dict) else None
                    if value not in expected:
                        error = f"Expected {status_field} {' or '.join(expected)}, received {value!r}"
            elif json_error:
                try:
                    document = json.loads(diagnostic)
                except ValueError:
                    document = None
                if output.strip() or not isinstance(document, dict) or not document.get("error"):
                    error = "Expected JSON error on stderr and empty stdout"
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            output = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) \
                else (exc.stdout or "")
            diagnostic = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) \
                else (exc.stderr or "")
            error = f"Command exceeded {timeout}s timeout"
        except (OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        stdout.write_text(output, encoding="utf-8")
        stderr.write_text(diagnostic, encoding="utf-8")
        if (catalog or classify) and error and not timed_out:
            # Explicit catalog codes override the preset category; transport tracebacks count
            # only for commands that need the catalog.
            found = catalog_failure_category(output, transport=catalog)
            if found == "package_api":
                found = catalog_failure_category(diagnostic, transport=catalog)
            if found != "package_api" or catalog:
                category = found
        if error:
            excerpt = (diagnostic.strip() or output.strip())[-1500:]
            if excerpt:
                error += f"\n{excerpt}"
        return self.record(name, category, "FAIL" if error else "PASS", command,
                           exit_code=code, expected_exit_code=expected_exit, error=error,
                           stdout=str(stdout), stderr=str(stderr), result_status=result_status)

    def finish(self):
        self.report["status"] = "FAIL" if any(c["status"] == "FAIL" for c in self.report["checks"]) else "PASS"
        (self.out / "report.json").write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        return self.report

    def run(self):
        try:
            self._run()
        except Exception as exc:
            self.record("alignment-runner", "setup", "FAIL", [], error=f"{type(exc).__name__}: {exc}")
        return self.finish()

    # Environments -------------------------------------------------------------------------------

    def _python(self, venv):
        return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    def _pip_install(self, python, *arguments):
        return [python, "-m", "pip", "--isolated", "install", "--no-compile", "--index-url", PYPI_INDEX, *arguments]

    def _engine_requirement(self):
        if self.engine_wheel:
            # Rechecked for every environment, so a replaced file cannot become a different artifact.
            if sha256_file(self.engine_wheel) != self.report["engine_source"]["sha256"]:
                raise ValueError("Engine wheel changed after its identity check")
            return "os-optics[protected] @ " + self.engine_wheel.as_uri()
        return f"os-optics[protected]=={self.target}"

    def _kit_requirement(self):
        if self.kit_wheel:
            if sha256_file(self.kit_wheel) != self.report["kit_source"]["sha256"]:
                raise ValueError("Kit wheel changed after its identity check")
            return "os-optics-kit @ " + self.kit_wheel.as_uri()
        return str(self.root)

    def _install_kit(self, name, python):
        # --no-deps: the engine under test stays installed even when it is outside the kit's declared range;
        # dependency-consistency then reports the mismatch.
        return self.command(name, "package_api", self._pip_install(python, "--no-deps", self._kit_requirement()),
                            timeout=600)

    def _package_artifact(self, install_report):
        """Keep the artifact URL and hash pip actually installed, not a guess from the index page."""
        try:
            document = json.loads(install_report.read_text(encoding="utf-8"))
            if document.get("version") != "1":
                raise ValueError("Unsupported pip installation report version")
            installs = document["install"]
            engine = next(item for item in installs
                          if item["metadata"]["name"].replace("_", "-").lower() == "os-optics")
            download = engine["download_info"]
            digest = download["archive_info"]["hashes"]["sha256"]
            if not SHA256.fullmatch(digest):
                raise ValueError("Invalid installed artifact SHA-256")
            source = self.report["engine_source"]
            if self.engine_wheel and digest != source.get("sha256"):
                raise ValueError("Installed wheel differs from the checked engine wheel")
            source.update(url=download["url"], sha256=digest,
                          filename=Path(urllib.request.url2pathname(download["url"].split("/")[-1])).name)
            if not self.engine_wheel:
                # The default runner uses the public index; an artifact from another origin is not PyPI evidence.
                if urlparse(download["url"]).hostname != "files.pythonhosted.org":
                    raise ValueError("Installed artifact did not come from PyPI files.pythonhosted.org")
                source["publication_verified"] = True
            return self.record("installed-artifact", "package_api", "PASS", [], sha256=digest)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            return self.record("installed-artifact", "package_api", "FAIL", [],
                               error=f"Cannot establish installed artifact identity: {exc}")

    def _identity(self, name, python, venv):
        """Return the installed identity, or None after recording why the environment is unusable."""
        identity = self.command(name, "package_api", [python, "-c", IDENTITY])
        if identity["status"] == "FAIL":
            return None
        try:
            info = json.loads(Path(identity["stdout"]).read_text(encoding="utf-8"))
        except ValueError as exc:
            identity.update(status="FAIL", error=f"Unreadable installed identity: {exc}")
            return None
        mismatch = [f"{label}: expected {expected}, installed {info[key]}"
                    for label, key, expected in (("os-optics", "engine_version", self.target),
                                                 ("os-optics-kit", "kit_version", self.kit_expected))
                    if info[key] != expected]
        if mismatch:
            self.record(name.replace("installed-environment", "installed-version-match"), "package_api", "FAIL", [],
                        error="; ".join(mismatch))
            return None
        outside = [info[key] for key in ("module_path", "kit_module_path")
                   if not Path(info[key]).resolve().is_relative_to(venv.resolve())]
        if outside:
            self.record(name.replace("installed-environment", "installed-package-location"), "package_api", "FAIL",
                        [], error=f"Loaded outside the isolated environment: {', '.join(outside)}")
            return None
        return info

    def environment(self, requirements):
        """Python of a separate environment with the engine, the kit and one requirements file, or a blocker row."""
        if requirements in self.environments:
            return self.environments[requirements]
        slug = environment_slug(requirements)
        venv = self.out / f".venv-{slug}"
        python = self._python(venv)
        result = self.command(f"create-environment-env-{slug}", "setup", [sys.executable, "-m", "venv", str(venv)])
        if result["status"] == "PASS":
            result = self.command(f"install-env-{slug}", "package_api", self._pip_install(
                python, self._engine_requirement(), "-r", str(self.root / requirements)), timeout=1200)
        if result["status"] == "PASS":
            result = self._install_kit(f"install-kit-env-{slug}", python)
        if result["status"] == "PASS":
            info = self._identity(f"installed-environment-env-{slug}", python, venv)
            if info is None:
                result = self.report["checks"][-1]
            else:
                self.command(f"installed-packages-env-{slug}", "package_api", [python, "-m", "pip", "freeze", "--all"])
                result = {"python": python, "info": info}
        self.environments[requirements] = result
        return result

    # Catalog ------------------------------------------------------------------------------------

    def catalog(self, python, baseline):
        """Prepare the catalog once (pinned import or pinned sync); return the row that decides."""
        if self.catalog_row is not None:
            return self.catalog_row
        source = self.report["catalog_source"]
        if self.catalog_file:
            self.catalog_row = self._catalog_file(python, baseline, source)
        else:
            release = self.catalog_release or baseline["catalog_version"]
            expected = self.catalog_sha256 or (baseline["catalog_sha256"]
                                               if release == baseline["catalog_version"] else None)
            source.update(release=release, expected_sha256=expected)
            if expected is None:
                self.catalog_row = self.record("catalog-sync", "catalog_data", "FAIL", [], error=(
                    f"No published SHA-256 for catalog release {release!r}; pass --catalog-sha256 "
                    "with the digest from the catalog manifest"))
                return self.catalog_row
            self.catalog_row = self.command("catalog-sync", "catalog_network",
                                            [python, "-m", "os_optics", "catalog", "sync", "--release", release],
                                            catalog=True, timeout=300)
            if self.catalog_row["status"] == "FAIL":
                # Name the service the failed sync tried, from its error document.
                for document in _documents(Path(self.catalog_row["stdout"]).read_text(encoding="utf-8")):
                    error = document.get("error") if isinstance(document, dict) else None
                    details = error.get("details") if isinstance(error, dict) else None
                    if isinstance(details, dict) and isinstance(details.get("url"), str):
                        source["service_url"] = details["url"]
                        break
        if self.catalog_row["status"] == "PASS":
            self.catalog_row = self._catalog_identity(python, source)
        return self.catalog_row

    def _catalog_file(self, python, baseline, source):
        try:
            actual = sha256_file(self.catalog_file)
            release = json.loads(self.catalog_file.read_text(encoding="utf-8")).get("catalog_version")
        except (OSError, ValueError, AttributeError) as exc:
            return self.record("catalog-file", "catalog_data", "FAIL", [], error=f"{type(exc).__name__}: {exc}")
        expected = self.catalog_sha256
        if expected is None and release == baseline["catalog_version"]:
            expected = baseline["catalog_sha256"]
        source.update(sha256=actual, expected_sha256=expected, release=release)
        if expected is None:
            return self.record("catalog-file", "catalog_data", "FAIL", [], error=(
                f"No published SHA-256 for catalog release {release!r}; pass --catalog-sha256 "
                "with the digest from the catalog manifest"))
        if actual != expected:
            return self.record("catalog-file", "catalog_data", "FAIL", [],
                               error=f"SHA-256 {actual} differs from published {expected}")
        self.record("catalog-file", "catalog_data", "PASS", [], sha256=actual, catalog_version=release)
        return self.command("catalog-import", "catalog_data",
                            [python, "-m", "os_optics", "catalog", "import", str(self.catalog_file)],
                            catalog=True)

    def _catalog_identity(self, python, source):
        row = self.command("catalog-identity", "catalog_data", [python, "-m", "os_optics", "catalog", "status"],
                           catalog=True)
        if row["status"] == "FAIL":
            return row
        try:
            status = json.loads(Path(row["stdout"]).read_text(encoding="utf-8"))
            active = status.get("active") or {}
            service = status.get("service") or {}
        except (ValueError, AttributeError) as exc:
            row.update(status="FAIL", category="package_api", error=f"Unreadable catalog status: {exc}")
            return row
        source.update(catalog_version=active.get("catalog_version"), active_sha256=active.get("sha256"),
                      service_url=service.get("manifest_url"))
        problems = []
        if not active.get("catalog_version"):
            problems.append("No active catalog release")
        elif active.get("catalog_version") != source.get("release"):
            problems.append(f"Active release {active.get('catalog_version')} differs from {source.get('release')}")
        if active.get("sha256") != source.get("expected_sha256"):
            problems.append(f"Active release SHA-256 {active.get('sha256')} differs from the expected "
                            f"{source.get('expected_sha256')}")
        for host in RETIRED_HOSTS:
            if service.get("manifest_url") and host_pattern(host).search(service["manifest_url"]):
                problems.append(f"Catalog service URL {service['manifest_url']} uses retired host {host}")
        if problems:
            row.update(status="FAIL", error="; ".join(problems))
        return row

    # Sequence -----------------------------------------------------------------------------------

    def _artifacts(self, requested):
        """Resolve the engine version and check local wheels; return False after recording a failure."""
        try:
            if self.engine_wheel:
                if not self.engine_wheel.is_file():
                    raise ValueError(f"{self.engine_wheel.name}: engine wheel not found")
                version = wheel_version(self.engine_wheel, "os-optics")
                if requested not in {"baseline", version}:
                    raise ValueError(f"--engine {requested} differs from the wheel version {version}")
                actual = sha256_file(self.engine_wheel)
                if self.engine_sha256 and actual != self.engine_sha256:
                    raise ValueError("Engine wheel SHA-256 differs from --engine-sha256")
                self.report["engine_source"].update(filename=self.engine_wheel.name, sha256=actual)
                self.record("engine-artifact", "setup", "PASS", [], sha256=actual)
                self.target = version
            elif requested == "latest":
                self.target = latest_version()
            else:
                self.target = release_version(self.report["baseline_version"] if requested == "baseline"
                                              else requested)
            self.report["candidate_version"] = self.target
            self.record("release-selection", "setup", "PASS", [], resolved_version=self.target)
        except Exception as exc:
            self.record("engine-artifact" if self.engine_wheel else "release-selection", "setup", "FAIL", [],
                        error=f"{type(exc).__name__}: {exc}")
            return False
        try:
            source = self.report["kit_source"]
            if self.kit_wheel:
                if not self.kit_wheel.is_file():
                    raise ValueError(f"{self.kit_wheel.name}: kit wheel not found")
                self.kit_expected = wheel_version(self.kit_wheel, "os-optics-kit")
                source.update(filename=self.kit_wheel.name, sha256=sha256_file(self.kit_wheel))
            else:
                self.kit_expected = source_kit_version(self.root)
                source.update(path=str(self.root))
            source["version"] = self.kit_expected
            self.record("kit-artifact", "setup", "PASS", [], version=self.kit_expected, sha256=source.get("sha256"))
        except (OSError, ValueError) as exc:
            self.record("kit-artifact", "setup", "FAIL", [], error=f"{type(exc).__name__}: {exc}")
            return False
        return True

    def _run(self):
        try:
            baseline = read_baseline(self.root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.record("baseline-record", "setup", "FAIL", [], error=f"{BASELINE_FILE}: {type(exc).__name__}: {exc}")
            return
        self.report["baseline_version"] = baseline["engine_version"]
        try:
            definitions = load_checks(self.root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.record("check-definitions", "setup", "FAIL", [], error=f"{type(exc).__name__}: {exc}")
            return
        try:
            selected = select_checks(definitions["checks"], self.optional, self.only, self.skip_names)
        except ValueError as exc:
            self.record("check-selection", "setup", "FAIL", [], error=str(exc))
            return
        checks = [check for check in definitions["checks"] if check["name"] in selected]
        self.report["optional_checks"].update(
            selected=[check["name"] for check in checks if check["optional"]],
            not_selected=[check["name"] for check in definitions["checks"]
                          if check["optional"] and check["name"] not in selected])
        if self.only or self.skip_names:
            self.report["selection"]["left_out"] = [check["name"] for check in definitions["checks"]
                                                    if not check["optional"] and check["name"] not in selected]
        if not self._artifacts(self.report["requested_version"]):
            return
        blocker = self._default_environment(definitions["default_requirements"])
        default = None if blocker else self.environments[None]
        runs = self.out / "runs"
        for check in checks:
            needed = [self.rows[name] for name in check["needs"] if self.rows[name]["status"] != "PASS"]
            if needed:
                self.skip(check["name"], needed[0])
                continue
            environment = default
            if blocker:
                self.skip(check["name"], blocker)
                continue
            minimum = check["requires_python"]
            if minimum and sys.version_info[:2] < tuple(int(part) for part in minimum.split(".")):
                # Not a failure of this release: the runner's interpreter is older than the check needs.
                self.record(check["name"], "setup", "SKIPPED", [], requires_python=minimum,
                            error=f"Requires Python >= {minimum}; the runner uses {sys.version.split()[0]}")
                continue
            if check["requirements"]:
                environment = self.environment(check["requirements"])
                if "python" not in environment:
                    self.skip(check["name"], environment)
                    continue
            if check["needs_catalog"]:
                catalog = self.catalog(default["python"], baseline)
                if catalog["status"] != "PASS":
                    self.skip(check["name"], catalog)
                    continue
            values = {"python": environment["python"], "runs": str(runs), "out": str(self.out)}

            def substitute(text, values=values):
                path = re.fullmatch(r"\{(runs|out)\}((?:/[^/]+)*)", text)
                if path:  # native separators for output paths
                    return str(Path(values[path.group(1)], *path.group(2).split("/")[1:]))
                return PLACEHOLDER.sub(lambda match: values[match.group(1)], text)

            self.command(check["name"], "package_api", [substitute(arg) for arg in check["argv"]],
                         expected_exit=check["expected_exit"], result_status=check["expected_status"],
                         status_field=check["status_field"], json_error=check["json_error"],
                         timeout=check["timeout"], catalog=check["needs_catalog"], classify=True,
                         env={key: substitute(value) for key, value in check["env"].items()})

    def _default_environment(self, requirements):
        """Create the main environment; return the failed row that blocks every check, if any."""
        venv = self.out / ".venv"
        python = self._python(venv)
        row = self.command("create-environment", "setup", [sys.executable, "-m", "venv", str(venv)], timeout=180)
        if row["status"] == "FAIL":
            return row
        install_report = self.out / "logs" / "install-engine.json"
        row = self.command("install-engine", "package_api", self._pip_install(
            python, self._engine_requirement(), "--report", str(install_report)), timeout=600)
        if row["status"] == "FAIL":
            return row
        row = self._package_artifact(install_report)
        if row["status"] == "FAIL":
            return row
        row = self._install_kit("install-kit", python)
        if row["status"] == "FAIL":
            return row
        if requirements:
            arguments = [item for name in requirements for item in ("-r", str(self.root / name))]
            row = self.command("install-default-requirements", "package_api",
                               self._pip_install(python, *arguments), timeout=600)
            if row["status"] == "FAIL":
                return row
        info = self._identity("installed-environment", python, venv)
        if info is None:
            return self.report["checks"][-1]
        self.report["installed_version"] = info["engine_version"]
        self.report["kit_version"] = info["kit_version"]
        self.report["python_version"] = info.get("python_version")
        (self.out / "environment.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        self.command("installed-packages", "package_api", [python, "-m", "pip", "freeze", "--all"])
        self.command("dependency-consistency", "package_api", [python, "-m", "pip", "check"])
        self.environments[None] = {"python": python, "info": info}
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", "--candidate", dest="engine", metavar="VERSION|latest",
                        help="os-optics from PyPI: an exact version or latest (default: the verified baseline)")
    parser.add_argument("--engine-wheel", type=Path, help="Install os-optics from this local wheel instead of PyPI")
    parser.add_argument("--engine-sha256", help="Expected SHA-256 of --engine-wheel")
    parser.add_argument("--kit-wheel", type=Path, help="Install os-optics-kit from this wheel (default: this checkout)")
    parser.add_argument("--catalog-file", type=Path,
                        help="Import this catalog release file instead of a sync; its SHA-256 is verified")
    parser.add_argument("--catalog-release", help="Sync this catalog release instead of the baseline's")
    parser.add_argument("--catalog-sha256", help="Published SHA-256 of --catalog-file or --catalog-release "
                                                 "(default: the baseline's, for the baseline release)")
    parser.add_argument("--optional", action="append", default=[], metavar="NAME[,NAME]|all",
                        help="Also run these optional checks from alignment-checks.json (repeatable)")
    parser.add_argument("--only", action="append", default=[], metavar="NAME[,NAME]",
                        help="Run only these checks and the checks they need (repeatable)")
    parser.add_argument("--skip", action="append", default=[], metavar="NAME[,NAME]",
                        help="Leave out these checks and the checks that need them (repeatable)")
    parser.add_argument("--out", type=Path, default=Path("run-alignment"),
                        help="New directory for reports, logs and runs")
    args = parser.parse_args(argv)
    if args.engine is not None and args.engine != "latest" and not VERSION.fullmatch(args.engine):
        parser.error("--engine takes an exact version or latest")
    if args.engine_wheel and args.engine == "latest":
        parser.error("--engine-wheel cannot be combined with --engine latest")
    if args.engine_sha256 is not None and (not args.engine_wheel or not SHA256.fullmatch(args.engine_sha256)):
        parser.error("--engine-sha256 needs --engine-wheel and 64 lower-case hexadecimal characters")
    if args.catalog_file and args.catalog_release:
        parser.error("--catalog-file and --catalog-release are alternatives")
    if args.catalog_release is not None and not CATALOG_RELEASE.fullmatch(args.catalog_release):
        parser.error("--catalog-release takes a release such as 2026-10-28.1")
    if args.catalog_sha256 is not None and (not (args.catalog_file or args.catalog_release)
                                            or not SHA256.fullmatch(args.catalog_sha256)):
        parser.error("--catalog-sha256 needs --catalog-file or --catalog-release and 64 lower-case hexadecimal "
                     "characters")
    try:
        report = AlignmentCheck(ROOT, args.out, args.engine, engine_wheel=args.engine_wheel,
                                engine_sha256=args.engine_sha256, kit_wheel=args.kit_wheel,
                                catalog_file=args.catalog_file, catalog_release=args.catalog_release,
                                catalog_sha256=args.catalog_sha256, optional=args.optional, only=args.only,
                                skip=args.skip).run()
    except OSError as exc:
        parser.exit(1, f"Cannot create a fresh output directory: {exc}\n")
    print(json.dumps({"status": report["status"], "baseline_version": report["baseline_version"],
                      "installed_version": report["installed_version"], "kit_version": report["kit_version"],
                      "catalog_source": report["catalog_source"]["mode"],
                      "report": str(args.out / "report.json")}, separators=(",", ":")))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
