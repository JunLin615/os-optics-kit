"""Shared pieces of ``os-optics-kit contrib``: errors, front matter, templates, indexes, git, bundle and PR steps.

Everything here runs on this device. The network is used only by :func:`open_pull_request`, and only when the
contributor asked for ``--open-pr`` without ``--dry-run``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

UPSTREAM = "JunLin615/os-optics-kit"
UPSTREAM_BASE_BRANCH = "main"
BUNDLE_SCHEMA = "os-optics-kit.contrib-bundle"
CASES_SCHEMA = "os-optics-kit.cases"
EXPECTED_SCHEMA = "os-optics-kit.case-expected"
TOKEN_ESTIMATE = "ceil(characters/4)"
BUDGETS = {"SKILL.md": 900, "CASE.md": 700}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODULE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

EXIT_OK = 0
EXIT_VERIFICATION = 1
EXIT_USAGE = 4


class ContribError(Exception):
    """A refusal with a stable code; printed as JSON ``{"status": "ERROR", "error": {...}}``."""

    def __init__(self, code: str, message: str, *, problems: Iterable[Any] = (), hint: str | None = None,
                 exit_code: int = EXIT_USAGE, details: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.problems = list(problems)
        self.hint = hint
        self.exit_code = exit_code
        self.details = details or {}

    def as_dict(self) -> dict:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.problems:
            error["problems"] = self.problems
        if self.hint:
            error["hint"] = self.hint
        error.update(self.details)
        return {"status": "ERROR", "error": error}


def emit(document: dict) -> None:
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))


def dump_json(document: Any) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def token_estimate(text: str) -> int:
    """Deterministic token estimate used by the kit's budgets: ceil(characters / 4)."""
    return math.ceil(len(text) / 4)


def check_name(value: str, *, what: str = "name", max_length: int = 40) -> str:
    if not NAME_RE.match(value or "") or len(value) > max_length:
        raise ContribError("NAME_INVALID", f"{what} {value!r} must be kebab-case (a-z, 0-9, single hyphens), "
                           f"at most {max_length} characters")
    return value


def split_list(value: str | None) -> list[str]:
    if value is None:
        return []
    items = [item.strip() for item in value.split(",")]
    items = [item for item in items if item]
    return [] if items == ["none"] else items


# ---------------------------------------------------------------------------------------------- front matter
# Internal implementation of the kit's flat front-matter format (KIT_SPEC "Skills - SKILL.md format"); the shared
# parser of the skills lane replaces parse_front_matter / render_front_matter / body_sections at integration.

def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """``(keys, body)`` of a document that starts with flat ``key: value`` lines between two ``---`` lines."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("front matter missing: the file must start with a '---' line")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("front matter not closed: no second '---' line") from None
    keys: dict[str, str] = {}
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not re.match(r"^[a-z_]+$", key):
            raise ValueError(f"front matter line {number} is not 'key: value'")
        if key in keys:
            raise ValueError(f"front matter key {key!r} repeated")
        keys[key] = value.strip()
    return keys, "\n".join(lines[end + 1:])


def render_front_matter(keys: dict[str, str]) -> str:
    return "---\n" + "".join(f"{key}: {value}\n" for key, value in keys.items()) + "---\n"


def body_sections(body: str) -> list[str]:
    """``## `` headings of a Markdown body, in order (fenced code blocks skipped)."""
    sections, fenced = [], False
    for line in body.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            sections.append(line[3:].strip())
    return sections


def check_sections(found: list[str], required: list[str]) -> list[str]:
    """Problems when ``required`` sections are missing or out of order (extra sections are allowed)."""
    problems = [f"section '## {name}' missing" for name in required if name not in found]
    present = [name for name in found if name in required]
    if not problems and present != required:
        problems.append("sections out of order: expected " + ", ".join(required))
    return problems


# ------------------------------------------------------------------------------------------------- templates

def fill_template(template: str, values: dict[str, str]) -> str:
    unknown = sorted({m.group(1) for m in PLACEHOLDER_RE.finditer(template)} - set(values))
    if unknown:
        raise KeyError(", ".join(unknown))
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def choose_template(repo: Path | None, relative: str, builtin: str, values: dict[str, str],
                    warnings: list[str]) -> str:
    """Fill ``repo/relative`` when it is a ``{{key}}`` template this version can fill, else the built-in one."""
    if repo is not None:
        path = repo / relative
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if PLACEHOLDER_RE.search(text):
                try:
                    return fill_template(text, values)
                except KeyError as exc:
                    warnings.append(f"TEMPLATE_NOT_USED: {relative} has placeholders this command does not fill "
                                    f"({exc.args[0]}); the built-in template was used")
    return fill_template(builtin, values)


# ------------------------------------------------------------------------------------------------ kit checkout

def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, check: bool = False,
        input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=cwd, env=env, input=input_bytes, capture_output=True)
    if check and result.returncode != 0:
        raise ContribError("COMMAND_FAILED", f"{_show(cmd)} exited with {result.returncode}",
                           problems=[_tail(result.stderr or result.stdout)])
    return result


def _show(cmd: list[str]) -> str:
    return " ".join(str(part) for part in cmd)


def _tail(data: bytes, limit: int = 1500) -> str:
    text = data.decode("utf-8", "replace").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def git_cmd(*args: str) -> list[str]:
    return ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args]


def find_kit_checkout(start: Path) -> Path | None:
    """The os-optics-kit Git checkout containing ``start``, if any."""
    result = run(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    root = Path(result.stdout.decode().strip())
    return root if is_kit_checkout(root) else None


def is_kit_checkout(path: Path) -> bool:
    return (path / "tools" / "os_optics_kit" / "cli.py").is_file() and (path / "pyproject.toml").is_file()


def resolve_repo(option: str | None) -> Path | None:
    if option:
        repo = Path(option).resolve()
        if not is_kit_checkout(repo) or run(["git", "-C", str(repo), "rev-parse", "--git-dir"]).returncode != 0:
            raise ContribError("REPO_INVALID", f"--repo {option} is not an os-optics-kit Git checkout",
                               hint="git clone https://github.com/JunLin615/os-optics-kit")
        return repo
    return find_kit_checkout(Path.cwd())


def git_identity(repo: Path) -> tuple[str, str]:
    name = run(["git", "-C", str(repo), "config", "user.name"]).stdout.decode().strip()
    email = run(["git", "-C", str(repo), "config", "user.email"]).stdout.decode().strip()
    if not name or not email:
        raise ContribError("GIT_IDENTITY_MISSING", "the commit needs your Git identity for its DCO sign-off",
                           hint='git config --global user.name "Your Name"; '
                                'git config --global user.email you@example.com')
    return name, email


def resolve_base(repo: Path, base: str | None) -> str:
    ref = base or "HEAD"
    result = run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise ContribError("BASE_INVALID", f"--base {ref!r} is not a commit of the kit checkout")
    return result.stdout.decode().strip()


def kit_version_of(root: Path) -> str:
    text = (root / "tools" / "os_optics_kit" / "_version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def read_baseline(root: Path | None) -> dict | None:
    candidates = [root / "baseline.json"] if root else []
    try:
        from .._resources import data_root
        candidates.append(data_root() / "baseline.json")
    except (FileNotFoundError, ImportError):
        pass
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                return None
    return None


def kit_commands(root: Path | None) -> set[str]:
    """Subcommand names of the kit's ``cli.COMMANDS`` (from ``root`` when given, else the installed package)."""
    if root is not None:
        tree = ast.parse((root / "tools" / "os_optics_kit" / "cli.py").read_text(encoding="utf-8"))
        node = _commands_node(tree)
        if node is not None:
            return {key.value for key in node.keys if isinstance(key, ast.Constant)}
    from ..cli import COMMANDS
    return set(COMMANDS)


def _commands_node(tree: ast.Module) -> ast.Dict | None:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in targets) and isinstance(node.value, ast.Dict):
            return node.value
    return None


def register_command(cli_text: str, command: str, module: str, summary: str) -> str:
    """``cli.py`` text with ``command`` added to ``COMMANDS``, before the ``cases`` entry (tools come first)."""
    tree = ast.parse(cli_text)
    node = _commands_node(tree)
    if node is None:
        raise ContribError("CLI_LAYOUT_UNKNOWN", "COMMANDS table not found in tools/os_optics_kit/cli.py")
    keys = {key.value: key for key in node.keys if isinstance(key, ast.Constant)}
    if command in keys:
        raise ContribError("COMMAND_EXISTS", f"os-optics-kit already has a {command!r} command",
                           hint="choose another --command")
    lines = cli_text.split("\n")
    anchor = keys.get("cases")
    index = (anchor.lineno - 1) if anchor is not None else (node.end_lineno - 1)
    entry = f"    {json.dumps(command)}: ({json.dumps(module)}, {json.dumps(summary, ensure_ascii=False)}),"
    if len(entry) > 120:
        entry = (f"    {json.dumps(command)}: (\n        {json.dumps(module)},\n"
                 f"        {json.dumps(summary, ensure_ascii=False)},\n    ),")
    lines.insert(index, entry)
    updated = "\n".join(lines)
    check = _commands_node(ast.parse(updated))
    if check is None or command not in {k.value for k in check.keys if isinstance(k, ast.Constant)}:
        raise ContribError("CLI_LAYOUT_UNKNOWN", "could not register the command in COMMANDS")
    return updated


# ------------------------------------------------------------------------------------------- cases.json (shim)
# Internal implementation of the cases index (KIT_SPEC "Cases - format"). At integration the cases lane's indexer
# (``os-optics-kit cases index --write``) is used whenever the checkout has it (see ``update_indexes``); these two
# functions are the fallback for a checkout without it and should then be replaced by the shared ones.

def case_index_entry(case_dir: Path, front: dict[str, str]) -> dict:
    files = sorted(p.relative_to(case_dir).as_posix() for p in case_dir.rglob("*") if p.is_file())
    catalog = front.get("catalog", "none")
    return {
        "name": front["name"],
        "title": front["title"],
        "summary": front["summary"],
        "path": f"cases/{front['name']}",
        "engine": front["engine"],
        "catalog": None if catalog == "none" else catalog,
        "tools": split_list(front.get("tools")),
        "tags": split_list(front.get("tags")),
        "files": files,
        "project_sha256": sha256_file(case_dir / "project.json"),
    }


def update_cases_index(root: Path, entry: dict) -> None:
    path = root / "cases.json"
    if path.is_file():
        index = json.loads(path.read_text(encoding="utf-8"))
    else:
        index = {"schema": CASES_SCHEMA, "schema_version": 1, "kit_version": kit_version_of(root), "cases": []}
    cases = [case for case in index.get("cases", []) if case.get("name") != entry["name"]]
    cases.append(entry)
    index["cases"] = sorted(cases, key=lambda case: case["name"])
    write_text(path, dump_json(index))


# ------------------------------------------------------------------------------------------------ scratch clone

@dataclass
class Clone:
    path: Path
    base: str
    branch: str
    identity: tuple[str, str]

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return run(git_cmd("-C", str(self.path), *args), check=check)

    def env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.path / "tools"), env.get("PYTHONPATH")]))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def kit(self, *args: str) -> subprocess.CompletedProcess:
        """Run this checkout's own ``os-optics-kit``."""
        return run([sys.executable, "-m", "os_optics_kit", *args], cwd=self.path, env=self.env())


def make_clone(repo: Path, base: str, branch: str, workdir: Path) -> Clone:
    """A private clone of ``repo`` at ``base`` (the contributor's checkout is never modified)."""
    identity = git_identity(repo)
    path = workdir / "kit"
    run(git_cmd("clone", "--quiet", "--shared", "--no-checkout", str(repo), str(path)), check=True)
    clone = Clone(path=path, base=base, branch=branch, identity=identity)
    clone.git("checkout", "--quiet", "-b", branch, base)
    return clone


def commit_and_format_patch(clone: Clone, paths: list[str], message: str, bundle: Path) -> Path:
    clone.git("add", "--", *paths)
    if not clone.git("diff", "--cached", "--name-only").stdout.strip():
        raise ContribError("NOTHING_TO_COMMIT", "the contribution does not change the kit checkout")
    name, email = clone.identity
    clone.git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-s", "-m", message)
    for old in bundle.glob("*.patch"):
        old.unlink()
    out = clone.git("format-patch", "-1", "HEAD", "--output-directory", str(bundle)).stdout.decode().strip()
    return Path(out.splitlines()[-1]).resolve()


def committed_files(clone: Clone) -> list[str]:
    out = clone.git("show", "--name-only", "--format=", "HEAD").stdout.decode()
    return [line for line in out.splitlines() if line]


# --------------------------------------------------------------------------------------------------- bundles

def check_bundle_target(out: Path, kind: str, name: str, *, force: bool) -> None:
    """Refuse (before any work) when ``out`` cannot take this bundle."""
    if not (out.exists() and any(out.iterdir())):
        return
    manifest = out / "bundle.json"
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    except ValueError:
        previous = {}
    if previous.get("kind") != kind or previous.get("name") != name:
        raise ContribError("OUT_NOT_EMPTY", f"{out} exists and is not a {kind} bundle of {name!r}",
                           hint="choose another --out")
    if not force:
        raise ContribError("BUNDLE_EXISTS", f"{out} already holds this {kind} bundle",
                           hint=f"edit its files and run 'os-optics-kit contrib {kind} --refresh {out}', "
                                "or regenerate everything with --force")


def prepare_bundle_dir(out: Path, kind: str, name: str, *, force: bool) -> None:
    check_bundle_target(out, kind, name, force=force)
    if out.exists():
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)


def clear_bundle_outputs(out: Path, staged: str) -> None:
    """Remove what a rebuild of the bundle rewrites: the staged files, patches, PR body and manifest."""
    for path in [out / staged, out / "PR_BODY.md", out / "bundle.json", *out.glob("*.patch")]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def load_bundle(path: Path, kind: str) -> dict:
    manifest = path / "bundle.json"
    if not manifest.is_file():
        raise ContribError("BUNDLE_INVALID", f"{path} has no bundle.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != BUNDLE_SCHEMA or data.get("kind") != kind:
        raise ContribError("BUNDLE_INVALID", f"{path} is not a {kind} contribution bundle")
    return data


def submission_hint(bundle: Path, patch: Path | None) -> str:
    issues = f"https://github.com/{UPSTREAM}/issues"
    if patch is None:
        return (f"no patch: run again with --repo pointing at a clone of https://github.com/{UPSTREAM}, or attach "
                f"the files in {bundle} to an issue on {issues}")
    return (f"add --open-pr to open the pull request with your own gh login, or apply {patch.name} with 'git am' in "
            f"your fork and push; without GitHub credentials attach {patch.name} and PR_BODY.md to an issue on "
            f"{issues}")


# ---------------------------------------------------------------------------------------------- pull request

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class PullRequestPlan:
    branch: str
    title: str
    body_file: Path
    upstream: str = UPSTREAM
    base_branch: str = UPSTREAM_BASE_BRANCH
    steps: list[str] = field(default_factory=list)

    def planned_commands(self) -> list[str]:
        owner, repo = self.upstream.split("/", 1)
        return [
            "gh auth status",
            "gh api user --jq .login",
            f"gh repo fork {self.upstream} --clone=false --remote=false   (skipped when you are {owner})",
            f"git push https://github.com/<login>/{repo}.git HEAD:refs/heads/{self.branch}",
            f"gh pr create --repo {self.upstream} --base {self.base_branch} --head <login>:{self.branch} "
            f"--title \"{self.title}\" --body-file {self.body_file.name}",
        ]


def open_pull_request(plan: PullRequestPlan, clone: Clone, runner: Runner = run) -> dict:
    """Fork, push and open the PR with the contributor's own ``gh`` login. Returns ``{"opened": bool, ...}``."""
    if shutil.which("gh") is None:
        return {"opened": False, "reason": "GH_NOT_INSTALLED"}
    if runner(["gh", "auth", "status"]).returncode != 0:
        return {"opened": False, "reason": "GH_NOT_AUTHENTICATED"}
    login = runner(["gh", "api", "user", "--jq", ".login"], check=True).stdout.decode().strip()
    owner, repo = plan.upstream.split("/", 1)
    if login.lower() == owner.lower():
        push_url, head = f"https://github.com/{plan.upstream}.git", plan.branch
    else:
        runner(["gh", "repo", "fork", plan.upstream, "--clone=false", "--remote=false"], check=True)
        push_url, head = f"https://github.com/{login}/{repo}.git", f"{login}:{plan.branch}"
    runner(git_cmd("-C", str(clone.path), "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                   "push", push_url, f"HEAD:refs/heads/{plan.branch}"), check=True)
    created = runner(["gh", "pr", "create", "--repo", plan.upstream, "--base", plan.base_branch, "--head", head,
                      "--title", plan.title, "--body-file", str(plan.body_file)], check=True)
    url = created.stdout.decode().strip().splitlines()
    return {"opened": True, "url": url[-1] if url else None, "head": head}


def relative_listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
