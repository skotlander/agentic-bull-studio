"""A small, inspectable, local multi-agent writers' room.

The room deliberately owns *process artifacts*, not series canon.  It uses the
existing ``runner/deleg.mjs`` process runner for every real agent turn, keeping
provider credentials and model invocation details in that runner.  This module
only prepares prompts, orders the phases, copies declared deliverables, and
keeps a resumable manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import SlopError, toolkit_root


ROOM_VERSION = 1
REQUIRED_BIBLE = ("series.md", "canon.md", "character.md", "world.md", "visual.md", "story.md")
WRITERS = ("character-dialogue", "situational-comedy", "satire-observation")
EDITORS = ("comedy-editor", "character-editor")
ROLES = WRITERS + EDITORS + ("showrunner",)

# Optional, opt-in execution plans. A session with no plan runs the standard
# five-phase room exactly as before. A plan is fixed data, not a workflow engine.
PILOT_PLAN = "comedy-lab-pilot"
PILOT_WRITING, PILOT_ASSESSMENT, PILOT_SELECTION = "pilot-1-writing", "pilot-2-assessment", "pilot-3-selection"
PLANS = {
    PILOT_PLAN: {
        # Exactly five role invocations, in this order; nothing loops or branches.
        "sequence": tuple((PILOT_WRITING, role) for role in WRITERS)
                    + ((PILOT_ASSESSMENT, "comedy-editor"), (PILOT_SELECTION, "showrunner")),
        "bible": REQUIRED_BIBLE + ("voice.md",),
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result:
        raise SlopError("session id must contain at least one letter or number")
    return result


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SlopError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SlopError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _template_root() -> Path:
    return toolkit_root() / "templates" / "writers-room"


def _bible_manifest(bible_dir: Path, names: tuple[str, ...] = REQUIRED_BIBLE) -> dict:
    bible_dir = bible_dir.resolve()
    if not bible_dir.is_dir():
        raise SlopError(f"Media Bible directory does not exist: {bible_dir}")
    files = []
    for name in names:
        path = bible_dir / name
        if not path.is_file():
            raise SlopError(f"Media Bible is incomplete; required file is missing: {path}")
        files.append({"name": name, "path": str(path), "sha256": _sha256(path)})
    return {"directory": str(bible_dir), "files": files}


def _load_model_dict(roles: object, required: tuple[str, ...], where: str = "model configuration") -> None:
    if not isinstance(roles, dict):
        raise SlopError(f"{where} needs a roles object")
    missing = [role for role in required if not isinstance(roles.get(role), dict)]
    if missing:
        raise SlopError(f"{where} is missing roles: {', '.join(missing)}")
    for role in required:
        item = roles[role]
        if item.get("engine") not in {"claude", "codex", "muse", "gemini"}:
            raise SlopError(f"model configuration for {role} needs a supported runner engine")
        if not isinstance(item.get("model"), str) or not item["model"].strip():
            raise SlopError(f"model configuration for {role} needs a non-empty model selector")
        if item.get("reasoning") not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
            raise SlopError(f"model configuration for {role} has unsupported reasoning level")


def _load_model_config(config_path: Path | None, required: tuple[str, ...] = ROLES,
                       default: Path | None = None) -> tuple[dict, Path]:
    path = (config_path or default or (_template_root() / "models.json")).resolve()
    data = _read_json(path)
    _load_model_dict(data.get("roles"), required, f"model configuration {path}")
    return data, path


def _load_role(role: str) -> str:
    path = _template_root() / "roles" / f"{role}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SlopError(f"role definition is unavailable: {path}") from exc


def _assignment_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    bundled = _template_root() / "assignments" / f"{value}.md"
    if bundled.is_file():
        return bundled.resolve()
    raise SlopError(f"assignment {value!r} was not found (use a Markdown path or a bundled assignment name)")


def _load_session(session: Path) -> tuple[Path, dict]:
    root = session.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SlopError(f"not a writers' room session: {root}")
    return root, _read_json(manifest_path)


def _save_session(root: Path, manifest: dict) -> None:
    manifest["updated_at"] = _now()
    _write_json(root / "manifest.json", manifest)


def _verify_bible(manifest: dict) -> None:
    authority = manifest.get("authority") or {}
    for entry in authority.get("files") or []:
        path = Path(entry["path"])
        if not path.is_file():
            raise SlopError(f"authoritative Bible file is no longer available: {path}")
        actual = _sha256(path)
        if actual != entry["sha256"]:
            raise SlopError(
                f"authoritative Bible changed since this session was prepared: {path}. "
                "Prepare a new session so every agent receives the current authority."
            )


def _artifact(root: Path, phase: str, name: str, extension: str = "md") -> Path:
    return root / "artifacts" / phase / f"{name}.{extension}"


def _artifact_rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _result_output(root: Path, phase: str, role: str) -> Path:
    return _artifact(root, phase, role)


def _authority_text(manifest: dict) -> str:
    lines = ["## Authoritative Media Bible", "", "The following read-only text is supplied directly from the authoritative files for this turn. It is not a new canon source; the paths and SHA-256 fingerprints below identify the authority. This room does not alter those files."]
    for entry in manifest["authority"]["files"]:
        lines.append(f"- `{entry['path']}` — SHA-256 `{entry['sha256']}`")
    for entry in manifest["authority"]["files"]:
        source = Path(entry["path"])
        if not source.is_file() or _sha256(source) != entry["sha256"]:
            raise SlopError(f"authoritative Bible changed during this session: {source}; stop and prepare a new session")
        lines += ["", f"### {entry['name']} (authoritative source: `{source}`)", "", source.read_text(encoding="utf-8").strip()]
    lines += ["", "All proposed names, biographies, relationships, settings, scripts, and running jokes are provisional until the owner explicitly approves them. Do not edit any Bible file."]
    return "\n".join(lines)


def _prompt(manifest: dict, role: str, phase: str, task: str, output_kind: str = "markdown") -> str:
    output_name = "result.json" if output_kind == "json" else "result.md"
    return "\n\n".join((
        "# Business as Unusual — Writers' Room Turn",
        f"**Phase:** {phase}\n\n**Role:** {role}",
        _load_role(role),
        _authority_text(manifest),
        "## Assignment\n\n" + manifest["assignment"]["text"],
        task,
        "## Deliverable\n\n"
        f"Write your complete deliverable to `outbox/{output_name}` in this job folder. "
        "Do not merely describe it in chat. Use specific reasoning, preserve genuine alternatives, and do not claim that a proposal is canon.",
    )) + "\n"


@dataclass
class RunnerExecutor:
    """Run one independent local CLI job through the existing delegated-job runner."""
    runner: Path

    def __call__(self, job_dir: Path, job: dict) -> None:
        _write_json(job_dir / "job.json", job)
        command = ["node", str(self.runner), "run", str(job_dir)]
        completed = subprocess.run(command, cwd=str(job_dir), text=True, capture_output=True)
        (job_dir / "runner.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (job_dir / "runner.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-1200:]
            raise SlopError(f"runner failed for {job.get('title', job_dir.name)} (exit {completed.returncode}): {detail}")


Executor = Callable[[Path, dict], None]


def _run_role(root: Path, manifest: dict, executor: Executor, phase: str, role: str, prompt: str,
              output_kind: str = "markdown") -> Path:
    destination = _result_output(root, phase, role)
    extension = "json" if output_kind == "json" else "md"
    destination = destination.with_suffix("." + extension)
    if destination.is_file():
        return destination
    config = manifest["models"]["roles"][role]
    job_dir = root / "jobs" / phase / role
    outbox = job_dir / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (job_dir / "BRIEF.md").write_text(prompt, encoding="utf-8")
    job = {
        "id": f"writers-room-{phase}-{role}", "kind": "writers-room", "title": f"{phase}: {role}",
        "engine": config["engine"], "model": config["model"], "effort": config["reasoning"],
        "brief": prompt, "cwd": str(job_dir), "timeout_s": int(config.get("timeout_s", 900)),
        # The Bible is embedded verbatim in the brief from its fingerprinted source.
        # Do not grant a worker write access to the authoritative media repository.
        "sandbox": "workspace-write",
        "notes": "Writers' room artifact. No canon or publication action is authorized.",
    }
    if config["engine"] == "claude":
        # Current Claude Code no longer accepts the runner's legacy "default"
        # permission mode. Allow artifact edits while retaining permission checks.
        job["permission_mode"] = "acceptEdits"
    try:
        executor(job_dir, job)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"phase": phase, "role": role, "at": _now(), "message": str(exc)}
        _save_session(root, manifest)
        raise
    source = outbox / ("result.json" if output_kind == "json" else "result.md")
    if not source.is_file() or not source.read_text(encoding="utf-8").strip():
        manifest["status"] = "failed"
        manifest["failure"] = {"phase": phase, "role": role, "at": _now(),
                               "message": f"required deliverable missing or empty: {source}"}
        _save_session(root, manifest)
        raise SlopError(f"{role} did not create the required inspectable deliverable: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    manifest.setdefault("artifacts", {})[f"{phase}/{role}"] = _artifact_rel(root, destination)
    _save_session(root, manifest)
    return destination


def _read_artifacts(root: Path, paths: list[Path]) -> str:
    blocks = []
    for path in paths:
        blocks.append(f"## {path.stem}\n\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n---\n\n".join(blocks)


def _phase_a(root: Path, manifest: dict, executor: Executor) -> None:
    for role in WRITERS:
        task = "## Your independent brief\n\nDevelop a complete proposal independently. You have not seen, and must not attempt to infer, other writers' proposals. Explore a strong, specific point of view rather than a safe average."
        _run_role(root, manifest, executor, "phase-a", role, _prompt(manifest, role, "A — Independent ideation", task))


def _phase_b(root: Path, manifest: dict, executor: Executor) -> None:
    proposals = [_artifact(root, "phase-a", role) for role in WRITERS]
    packet = _read_artifacts(root, proposals)
    for role in WRITERS:
        task = "## Collaborative packet\n\n" + packet + "\n\n## Your collaborative brief\n\nIdentify precise strengths in the other proposals; challenge weak or derivative choices; defend any valuable minority idea; propose surprising combinations only when they make the material sharper. Do not force consensus. Produce a developed alternative or alternatives, with the disagreements still visible."
        _run_role(root, manifest, executor, "phase-b", role, _prompt(manifest, role, "B — Collaborative development", task))
    development = [_artifact(root, "phase-b", role) for role in WRITERS]
    task = "## Developed writers' room material\n\n" + _read_artifacts(root, development) + "\n\n## Showrunner task\n\nCoordinate without averaging. Identify the strongest alternatives, protect worthwhile minority ideas, name unresolved disagreements, and prepare a concise development packet for independent editors. Do not decide canon."
    _run_role(root, manifest, executor, "phase-b", "showrunner", _prompt(manifest, "showrunner", "B — Showrunner coordination", task))


def _phase_c(root: Path, manifest: dict, executor: Executor) -> None:
    packet = _read_artifacts(root, [_artifact(root, "phase-b", role) for role in WRITERS + ("showrunner",)])
    for role in EDITORS:
        task = "## Material under review\n\n" + packet + "\n\n## Independent editorial task\n\nWrite a substantive independent assessment. Cite concrete weaknesses, identify what should be preserved, and recommend accept-for-owner-review, targeted revision, or rejection. Generic praise and numerical scoring are not useful."
        _run_role(root, manifest, executor, "phase-c", role, _prompt(manifest, role, "C — Independent editorial review", task))


def _phase_d(root: Path, manifest: dict, executor: Executor) -> None:
    developed = [_artifact(root, "phase-b", role) for role in WRITERS + ("showrunner",)]
    critiques = [_artifact(root, "phase-c", role) for role in EDITORS]
    task = "## Developed material\n\n" + _read_artifacts(root, developed) + "\n\n## Editorial reviews\n\n" + _read_artifacts(root, critiques) + "\n\n## Revision-plan task\n\nSelect only the issues that materially affect quality. Choose zero to three writers for one bounded revision; rejection is valid. Write JSON only to `outbox/result.json` with this exact shape: {\"revise_roles\":[\"character-dialogue\"],\"brief\":\"targeted instructions\",\"reasoning\":\"why these changes matter\"}. Valid role names are character-dialogue, situational-comedy, satire-observation."
    plan_path = _run_role(root, manifest, executor, "phase-d", "showrunner", _prompt(manifest, "showrunner", "D — Bounded revision planning", task, "json"), "json")
    try:
        plan = _read_json(plan_path)
    except SlopError as exc:
        raise SlopError(f"showrunner revision plan is invalid: {exc}") from exc
    roles = plan.get("revise_roles")
    if not isinstance(roles, list) or any(role not in WRITERS for role in roles) or len(set(roles)) != len(roles):
        raise SlopError("showrunner revision plan must list zero to three distinct writer roles")
    if not isinstance(plan.get("brief"), str) or not plan["brief"].strip():
        raise SlopError("showrunner revision plan needs a non-empty targeted brief")
    for role in roles:
        source = _artifact(root, "phase-b", role).read_text(encoding="utf-8")
        revision_task = "## Your prior developed material\n\n" + source + "\n\n## Targeted revision brief\n\n" + plan["brief"] + "\n\nRevise only the material within scope. Preserve distinctive strengths; do not manufacture agreement or canon."
        _run_role(root, manifest, executor, "phase-d", role, _prompt(manifest, role, "D — One bounded revision", revision_task))
    manifest["revision"] = {"roles": roles, "plan": _artifact_rel(root, plan_path)}
    _save_session(root, manifest)


def _phase_e(root: Path, manifest: dict, executor: Executor) -> None:
    developed = [_artifact(root, "phase-b", role) for role in WRITERS + ("showrunner",)]
    critiques = [_artifact(root, "phase-c", role) for role in EDITORS]
    revised = [_artifact(root, "phase-d", role) for role in manifest.get("revision", {}).get("roles", [])]
    task = "## Developed alternatives\n\n" + _read_artifacts(root, developed) + "\n\n## Editorial concerns\n\n" + _read_artifacts(root, critiques)
    if revised:
        task += "\n\n## Bounded revisions\n\n" + _read_artifacts(root, revised)
    task += "\n\n## Executive-producer report\n\nPrepare a concise report with: distinct strongest alternatives; why each has comedic potential; significant editorial concerns; unresolved disagreements; your recommendation; and specific owner decisions required. State plainly if material remains weak. End with: `Status: PROVISIONAL — owner approval required; no canon has changed.`"
    _run_role(root, manifest, executor, "phase-e", "showrunner", _prompt(manifest, "showrunner", "E — Executive-producer presentation", task))
    manifest["status"] = "awaiting_owner_approval"
    manifest["owner_approval"] = {"required": True, "recorded": False,
                                  "note": "This session cannot modify the Media Bible or establish canon."}
    _save_session(root, manifest)


# ---------------------------------------------------------------- comedy-lab-pilot plan
# Everything below this line applies only to sessions prepared with --plan. The standard
# room above is not routed through any of it.
JOB_STATES = ("never_started", "running", "completed", "failed", "uncertain")
LOCK_NAME = ".orchestrator.lock"


def _plan_root(plan: str) -> Path:
    return _template_root() / "plans" / plan


def _plan_roles(plan: str) -> tuple[str, ...]:
    return tuple(role for _, role in PLANS[plan]["sequence"])


def _plan_keys(plan: str) -> list[str]:
    return [f"{phase}/{role}" for phase, role in PLANS[plan]["sequence"]]


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot_roles(root: Path, plan: str) -> dict:
    """Copy the plan's role instructions into the session and fingerprint them.

    Execution reads only this snapshot, never the mutable shared templates.
    """
    snapshot = {}
    for role in _plan_roles(plan):
        source = _plan_root(plan) / "roles" / f"{role}.md"
        try:
            text = source.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SlopError(f"plan role definition is unavailable: {source}") from exc
        if not text:
            raise SlopError(f"plan role definition is empty: {source}")
        copy = root / "roles" / f"{role}.md"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(text + "\n", encoding="utf-8")
        snapshot[role] = {"source": str(source), "snapshot": _artifact_rel(root, copy),
                          "sha256": _text_sha256(text), "text": text}
    return snapshot


def _toolkit_sessions_dir() -> Path:
    return toolkit_root() / "writers-room" / "sessions"


def _visible_prior_sessions(root: Path) -> list[str]:
    """Other session folders present beside this one or in this checkout."""
    found = []
    for sessions in sorted({_toolkit_sessions_dir().resolve(), root.parent.resolve()}):
        if sessions.is_dir():
            found += [str(item) for item in sorted(sessions.iterdir()) if item.is_dir() and item.resolve() != root]
    return found


# ---- filesystem boundary -------------------------------------------------------------
def _expand(value: str) -> str:
    return os.path.abspath(os.path.expanduser(value))


def _sandbox_argv(config: dict, job_dir: Path, runner_dir: Path) -> list[str]:
    """bubblewrap arguments that give one role job its own filesystem view.

    The job sees: system directories and the listed runtime paths read-only, the
    runner directory read-only, an empty private home, the listed CLI sign-in paths,
    and its own job folder read-write. The session manifest, other jobs, artifacts,
    the rest of the checkout, the Bible directory and every other path are simply
    not mounted, so they can be neither read nor written. A fresh PID namespace and
    --die-with-parent mean no job process can outlive its orchestrator.
    """
    tool = shutil.which(str(config.get("tool") or "bwrap"))
    if not tool:
        raise SlopError("the pilot's filesystem boundary needs bubblewrap (`bwrap`) on PATH; install it "
                        "(Ubuntu/WSL: sudo apt install bubblewrap). The pilot will not run without it.")
    home = _expand("~")
    argv = [tool, "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session"]
    for directory in ("/usr", "/etc"):
        argv += ["--ro-bind", directory, directory]
    for directory in ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32"):
        if os.path.islink(directory):
            argv += ["--symlink", os.readlink(directory), directory]
        elif os.path.isdir(directory):
            argv += ["--ro-bind", directory, directory]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", home]
    node = shutil.which("node")
    runtime = [str(item) for item in config.get("read_only", [])]
    if node:
        runtime.append(str(Path(os.path.realpath(node)).parent.parent))
    for item in runtime:
        path = _expand(item)
        if path not in ("/", home) and os.path.exists(path):
            argv += ["--ro-bind", path, path]
    for item in config.get("read_write", []):
        path = _expand(str(item))
        if path not in ("/", home) and os.path.exists(path):
            argv += ["--bind", path, path]
    for item in config.get("masked", []):
        path = _expand(str(item))
        if os.path.isdir(path):
            argv += ["--tmpfs", path]
        elif os.path.isfile(path):
            argv += ["--ro-bind", "/dev/null", path]
    argv += ["--ro-bind", str(runner_dir), str(runner_dir), "--bind", str(job_dir), str(job_dir),
             "--chdir", str(job_dir)]
    return argv


@dataclass
class SandboxedRunnerExecutor:
    """Run one role job through the existing runner, inside the pilot's filesystem boundary."""
    runner: Path
    sandbox: dict

    def __call__(self, job_dir: Path, job: dict) -> None:
        _write_json(job_dir / "job.json", job)
        command = _sandbox_argv(self.sandbox, job_dir, self.runner.parent) + ["node", str(self.runner), "run", str(job_dir)]
        completed = subprocess.run(command, cwd=str(job_dir), text=True, capture_output=True)
        (job_dir / "runner.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (job_dir / "runner.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-1200:]
            raise SlopError(f"runner failed for {job.get('title', job_dir.name)} (exit {completed.returncode}): {detail}")


_PROBE = r"""
status=0
for target in "$@"; do
  case "$target" in
    read:*) path="${target#read:}"
      if [ -e "$path" ] || ls "$path" >/dev/null 2>&1 || cat "$path" >/dev/null 2>&1; then echo "VIOLATION readable $path"; status=1; else echo "ok hidden $path"; fi ;;
    write:*) path="${target#write:}"
      # Try, and leave the evidence. Whether the real directory changed is judged from outside the boundary,
      # because the boundary's own empty placeholder directories accept writes that reach nothing.
      ( echo probe > "$path/.pilot-probe" ) 2>/dev/null; echo "tried write $path" ;;
    own:*) path="${target#own:}"
      if ( echo probe > "$path/own-folder-proof" ) 2>/dev/null; then echo "tried own $path"; else echo "VIOLATION own folder not writable $path"; status=1; fi ;;
  esac
done
for name in node claude codex; do
  if command -v "$name" >/dev/null 2>&1; then echo "info runtime $name $("$name" --version 2>&1 | head -n 1)"; else echo "info runtime $name not-on-PATH"; fi
done
echo "info home $(ls -A "$HOME" 2>/dev/null | tr '\n' ' ')"
exit $status
"""


def sandbox_check(args: argparse.Namespace) -> dict:
    """No-model proof of the boundary: real processes try prohibited reads and writes for every role."""
    root, manifest = _load_session(Path(args.session))
    plan = manifest.get("plan") or {}
    if plan.get("name") not in PLANS:
        raise SlopError("sandbox-check applies only to a session prepared with --plan")
    runner_dir = (toolkit_root() / "runner").resolve()
    bible = Path(manifest["authority"]["directory"])
    hidden = [root / "manifest.json", root / "roles", root / "artifacts", root / "assignment.md", root / "jobs",
              toolkit_root() / "slop", toolkit_root() / "templates", _toolkit_sessions_dir(), bible,
              toolkit_root() / "writers-room"] + [Path(item) for item in (args.deny or [])]
    sequence = PLANS[plan["name"]]["sequence"]
    probes = {(phase, role): root / "sandbox-check" / phase / role for phase, role in sequence}
    for probe_dir in probes.values():
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "canary.txt").write_text("another role's working folder\n", encoding="utf-8")
    report, ok = {}, True
    for (phase, role), probe_dir in probes.items():
        others = [path for path in probes.values() if path != probe_dir]
        reads = hidden + others
        writes = [bible, runner_dir, toolkit_root(), root, Path("/usr")]
        targets = [f"read:{path}" for path in reads] + [f"write:{path}" for path in writes] + [f"own:{probe_dir}"]
        command = _sandbox_argv(plan["sandbox"], probe_dir, runner_dir) + ["/bin/sh", "-c", _PROBE, "probe", *targets]
        for path in writes:
            (path / ".pilot-probe").unlink(missing_ok=True)
        (probe_dir / "own-folder-proof").unlink(missing_ok=True)
        done = subprocess.run(command, text=True, capture_output=True)
        lines = [line for line in (done.stdout + done.stderr).strip().splitlines() if not line.startswith("tried ")]
        for path in writes:                      # judged here, outside the boundary, against the real directories
            marker = path / ".pilot-probe"
            if marker.exists():
                marker.unlink()
                lines.append(f"VIOLATION wrote into {path}")
            else:
                lines.append(f"ok unchanged {path}")
        proof = probe_dir / "own-folder-proof"
        lines.append(f"ok own-folder-writable {probe_dir}" if proof.exists() else f"VIOLATION own folder write did not reach {probe_dir}")
        proof.unlink(missing_ok=True)
        (probe_dir / "probe.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        passed = sum(1 for line in lines if line.startswith("ok "))
        violations = [line for line in lines if line.startswith("VIOLATION")]
        role_ok = done.returncode == 0 and not violations and passed == len(targets)
        report[f"{phase}/{role}"] = {
            "ok": role_ok, "exit": done.returncode, "checks": len(targets), "passed": passed, "violations": violations,
            # A path that does not exist outside the boundary proves nothing by being absent inside it.
            "not_present_on_host": [str(path) for path in reads if not path.exists()],
            "info": [line for line in lines if line.startswith("info")],
            "errors": [line for line in lines if not line.startswith(("ok ", "info", "VIOLATION"))],
        }
        ok = ok and role_ok
    return {"ok": ok, "session": str(root), "model_calls_started": False, "roles": report}


# ---- preflight -----------------------------------------------------------------------
def _pilot_problems(root: Path, manifest: dict, require_runtime: bool) -> list[str]:
    """Every defect that should stop the pilot before the first model invocation."""
    problems: list[str] = []
    plan = manifest.get("plan") or {}
    name = plan.get("name")
    if name not in PLANS:
        return [f"unsupported plan {name!r}"]
    spec = PLANS[name]
    if plan.get("sequence") != [f"{phase}: {role}" for phase, role in spec["sequence"]]:
        problems.append("recorded role sequence does not match the plan")
    roles = plan.get("roles") if isinstance(plan.get("roles"), dict) else {}
    for role in _plan_roles(name):
        entry = roles.get(role)
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
            problems.append(f"role snapshot for {role} is missing or empty")
            continue
        copy = root / str(entry.get("snapshot"))
        on_disk = copy.read_text(encoding="utf-8").strip() if copy.is_file() else None
        if _text_sha256(entry["text"]) != entry.get("sha256") or on_disk != entry["text"]:
            problems.append(f"role snapshot for {role} changed after preparation")
    files = (manifest.get("authority") or {}).get("files") or []
    if [item.get("name") for item in files] != list(spec["bible"]):
        problems.append("recorded Bible entries are not the seven the plan requires (including voice.md)")
    try:
        _verify_bible(manifest)
    except SlopError as exc:
        problems.append(str(exc))
    assignment = manifest.get("assignment") or {}
    text = assignment.get("text")
    copy = root / "assignment.md"
    if not isinstance(text, str) or not text.strip():
        problems.append("prepared assignment is empty")
    elif _text_sha256(text) != assignment.get("sha256") or not copy.is_file() \
            or copy.read_text(encoding="utf-8").strip() != text:
        problems.append("prepared assignment changed after preparation")
    if Path(str(assignment.get("source", ""))).suffix.lower() != ".md":
        problems.append("assignment source is not an explicit Markdown file")
    try:
        _load_model_dict((manifest.get("models") or {}).get("roles"), _plan_roles(name))
        models = manifest["models"]["roles"]
        if models["comedy-editor"]["engine"] == models["showrunner"]["engine"]:
            problems.append("comedy editor and showrunner must use different model families")
    except SlopError as exc:
        problems.append(str(exc))
    labels = plan.get("scene_labels") if isinstance(plan.get("scene_labels"), dict) else {}
    if sorted(labels) != ["Scene 1", "Scene 2", "Scene 3"] or sorted(labels.values()) != sorted(WRITERS):
        problems.append("scene-label mapping is not a one-to-one mapping of the three writers")
    sandbox = plan.get("sandbox")
    if not isinstance(sandbox, dict) or _text_sha256(json.dumps(sandbox, sort_keys=True)) != plan.get("sandbox_sha256"):
        problems.append("sandbox configuration is missing or changed after preparation")
    visible = _visible_prior_sessions(root)
    if visible:
        problems.append(f"not a clean execution context: {len(visible)} other session folder(s) present (first: {visible[0]}); "
                        "use a separate clean checkout of the public source with its own --room-dir")
    jobs = plan.get("jobs") if isinstance(plan.get("jobs"), dict) else {}
    if sorted(jobs) != sorted(_plan_keys(name)):
        problems.append("job ledger does not list exactly the five planned role invocations")
    for phase, role in spec["sequence"]:
        entry = jobs.get(f"{phase}/{role}") or {}
        state = entry.get("state")
        if state not in JOB_STATES:
            problems.append(f"{phase}/{role} has unknown ledger state {state!r}")
        elif state == "never_started":
            strays = [str(path) for path in (root / "jobs" / phase).glob(f"{role}*")] if (root / "jobs" / phase).is_dir() else []
            if _artifact(root, phase, role).exists():
                strays.append(str(_artifact(root, phase, role)))
            if strays:
                problems.append(f"{phase}/{role} has never started but execution artifacts exist: {strays[0]}")
    if not os.access(root, os.W_OK | os.X_OK):
        problems.append(f"session folder is not writable: {root}")
    if require_runtime:
        runner = toolkit_root() / "runner" / "deleg.mjs"
        if not runner.is_file():
            problems.append(f"runner is missing: {runner}")
        if not shutil.which("node"):
            problems.append("node is not on PATH")
        if isinstance(sandbox, dict) and not shutil.which(str(sandbox.get("tool") or "bwrap")):
            problems.append("bubblewrap (`bwrap`) is not on PATH; the pilot will not run without its filesystem boundary")
        elif isinstance(sandbox, dict) and runner.is_file() and shutil.which("node") and not problems:
            problems += _engines_unreachable(sandbox, runner, manifest["models"]["roles"], _plan_roles(name))
    return problems


def _engines_unreachable(sandbox: dict, runner: Path, models: dict, roles: tuple[str, ...]) -> list[str]:
    """Ask the runner, from inside the boundary, to locate each CLI the plan needs. Starts no CLI and no model."""
    problems = []
    scratch = Path(tempfile.mkdtemp(prefix="pilot-preflight-"))
    try:
        for engine in sorted({models[role]["engine"] for role in roles}):
            command = _sandbox_argv(sandbox, scratch, runner.parent) + ["node", str(runner), "_resolve", engine]
            done = subprocess.run(command, text=True, capture_output=True)
            if done.returncode or '"ok":true' not in done.stdout.replace(" ", ""):
                detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
                problems.append(f"the {engine} CLI cannot be started inside the filesystem boundary ({detail[0][:300]}); "
                                "add its install location to the plan's sandbox.json read_only list and prepare a new session")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return problems


def _pilot_preflight(root: Path, manifest: dict, require_runtime: bool) -> None:
    problems = _pilot_problems(root, manifest, require_runtime)
    if problems:
        raise SlopError("pilot preflight failed; no model was invoked: " + "; ".join(problems))


# ---- one orchestrator at a time ------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _acquire_lock(root: Path, clear_stale: bool) -> Path:
    path = root / LOCK_NAME
    payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "at": _now()})
    for attempt in (1, 2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                holder = json.loads(path.read_text(encoding="utf-8"))
                pid, host = int(holder["pid"]), holder["host"]
            except (OSError, ValueError, KeyError, TypeError):
                raise SlopError(f"session lock {path} is unreadable; inspect it and remove it by hand only if no orchestrator is running")
            if host == socket.gethostname() and _pid_alive(pid):
                raise SlopError(f"another orchestrator (pid {pid}) is operating on this session; nothing was started")
            if not clear_stale or host != socket.gethostname() or attempt == 2:
                raise SlopError(f"session lock {path} was left by orchestrator pid {pid} on {host}, which is no longer running. "
                                "Inspect the session, then rerun with --clear-stale-lock to continue; nothing was started")
            # Move the stale lock aside rather than deleting it, and confirm that what was moved is the
            # lock we judged stale, so two simultaneous clears cannot both end up holding the session.
            aside = path.with_name(f"{LOCK_NAME}.stale-{os.getpid()}")
            try:
                os.rename(path, aside)
                moved = json.loads(aside.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise SlopError(f"session lock {path} changed while it was being cleared; nothing was started")
            if moved != holder:
                try:
                    os.link(aside, path)
                except OSError:
                    pass
                aside.unlink()
                raise SlopError(f"session lock {path} was taken by another orchestrator; nothing was started")
            aside.unlink()
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        return path
    raise SlopError(f"could not take the session lock {path}")


# ---- role execution with a verified ledger -------------------------------------------
def _job_file(job_dir: Path, relative: str) -> Path | None:
    """A regular file that really lives in the job folder, or None.

    The job folder is the one place a role can write. A role must not be able to make the orchestrator, which
    runs outside the boundary, read some other file on its behalf by leaving a symbolic link behind.
    """
    path = job_dir / relative
    real_job = os.path.realpath(job_dir)
    if path.is_symlink() or not path.is_file() or os.path.realpath(path) != os.path.join(real_job, *relative.split("/")):
        return None
    return path


def _runner_state(job_dir: Path) -> str:
    """What the delegated runner recorded for a job folder, read without touching it."""
    if not job_dir.is_dir():
        return "absent"
    record = _job_file(job_dir, "status.json")
    try:
        state = json.loads(record.read_text(encoding="utf-8")).get("state") if record else None
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return state if isinstance(state, str) else "unknown"


def _publish(root: Path, manifest: dict, key: str, job_dir: Path, destination: Path) -> None:
    """Atomically publish a verified result and only then record it as completed."""
    data = (job_dir / "outbox" / "result.md").read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    with open(partial, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, destination)
    entry = manifest["plan"]["jobs"][key]
    entry.update({"state": "completed", "completed_at": _now(), "runner_state": "done",
                  "artifact": {"path": _artifact_rel(root, destination), "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()}})
    manifest.setdefault("artifacts", {})[key] = _artifact_rel(root, destination)
    _save_session(root, manifest)


def _stop(root: Path, manifest: dict, key: str, state: str, message: str) -> None:
    phase, role = key.split("/")
    entry = manifest["plan"]["jobs"][key]
    entry.update({"state": state, "detail": message, "stopped_at": _now()})
    manifest["status"] = "failed"
    manifest["failure"] = {"phase": phase, "role": role, "at": _now(), "state": state, "message": message}
    _save_session(root, manifest)
    raise SlopError(f"{key} is {state}: {message}. No new model attempt was started. After inspecting the job folder, "
                    f"the owner may authorize exactly one new attempt with --authorize-new-attempt {key}")


def _verified_result(job_dir: Path, entry: dict) -> str | None:
    """None when the job folder proves a completed run of the recorded prompt; otherwise why not."""
    state = _runner_state(job_dir)
    if state != "done":
        return f"runner state is {state!r}, not done"
    brief = _job_file(job_dir, "BRIEF.md")
    if brief is None:
        return "job folder has no BRIEF.md of its own"
    if _text_sha256(brief.read_text(encoding="utf-8", errors="replace")) != entry.get("prompt_sha256"):
        return "job folder's brief is not the prompt this session recorded"
    result = _job_file(job_dir, "outbox/result.md")
    if result is None or not result.read_text(encoding="utf-8", errors="replace").strip():
        return "runner reported done but the required deliverable is missing, empty, or not a file inside the job folder"
    return None


def _pilot_role(root: Path, manifest: dict, executor: Executor, phase: str, role: str, prompt: str,
                new_attempt: str | None) -> Path:
    key = f"{phase}/{role}"
    entry = manifest["plan"]["jobs"][key]
    destination = _artifact(root, phase, role)
    if entry["state"] == "completed":
        recorded = entry.get("artifact") or {}
        if not destination.is_file() or _sha256(destination) != recorded.get("sha256"):
            _stop(root, manifest, key, "uncertain", "published artifact is missing or no longer matches its recorded fingerprint")
        return destination
    attempt = 1
    if entry["state"] != "never_started":
        # Reconcile with the delegated runner's own record before deciding anything.
        job_dir = root / entry["job_dir"]
        why = _verified_result(job_dir, entry)
        if why is None:
            _publish(root, manifest, key, job_dir, destination)
            return destination
        if new_attempt != key:
            runner = _runner_state(job_dir)
            state = "failed" if runner in ("failed", "stopped") else "uncertain"
            _stop(root, manifest, key, state, f"attempt {entry['attempts']} did not complete ({why})")
        entry.setdefault("history", []).append({k: entry.get(k) for k in
                                                ("attempts", "job_dir", "state", "detail", "started_at", "stopped_at")})
        manifest["plan"].setdefault("authorized_new_attempts", []).append({"job": key, "at": _now()})
        attempt = entry["attempts"] + 1
    job_dir = root / "jobs" / phase / (role if attempt == 1 else f"{role}.attempt-{attempt}")
    if job_dir.exists():
        _stop(root, manifest, key, "uncertain", f"job folder already exists and is not in the ledger: {job_dir}")
    config = manifest["models"]["roles"][role]
    entry.update({"state": "running", "attempts": attempt, "job_dir": _artifact_rel(root, job_dir),
                  "prompt_sha256": _text_sha256(prompt), "started_at": _now(), "detail": None})
    for stale in ("artifact", "completed_at", "stopped_at", "runner_state"):
        entry.pop(stale, None)
    _save_session(root, manifest)
    (job_dir / "outbox").mkdir(parents=True)
    (job_dir / "BRIEF.md").write_text(prompt, encoding="utf-8")
    job = {
        "id": f"writers-room-{phase}-{role}-{attempt}", "kind": "writers-room", "title": f"{phase}: {role}",
        "engine": config["engine"], "model": config["model"], "effort": config["reasoning"],
        "brief": prompt, "cwd": str(job_dir), "timeout_s": int(config.get("timeout_s", 900)),
        "sandbox": "workspace-write",
        "notes": "Comedy Lab pilot artifact. No canon or publication action is authorized.",
    }
    if config["engine"] == "claude":
        job["permission_mode"] = "acceptEdits"
    try:
        executor(job_dir, job)
    except Exception as exc:
        try:
            _stop(root, manifest, key, "failed", f"attempt {attempt} raised: {exc}")
        except SlopError:
            pass
        raise
    why = _verified_result(job_dir, entry)
    if why is not None:
        runner = _runner_state(job_dir)
        _stop(root, manifest, key, "failed" if runner in ("failed", "stopped") else "uncertain",
              f"attempt {attempt} did not complete ({why})")
    _publish(root, manifest, key, job_dir, destination)
    return destination


def _pilot_prompt(manifest: dict, role: str, phase: str, task: str) -> str:
    return "\n\n".join((
        "# Business as Unusual — Comedy Lab Pilot Turn",
        f"**Phase:** {phase}\n\n**Role:** {role}",
        manifest["plan"]["roles"][role]["text"],
        _authority_text(manifest),
        "## Assignment\n\n" + manifest["assignment"]["text"],
        task,
        "## Deliverable\n\n"
        "Write your complete deliverable to `outbox/result.md` in this job folder. Do not merely describe it in chat. "
        "Nothing you write is canon.",
    )) + "\n"


def _blind_scenes(root: Path, manifest: dict) -> str:
    labels = manifest["plan"]["scene_labels"]
    blocks = []
    for label in sorted(labels):
        text = _artifact(root, PILOT_WRITING, labels[label]).read_text(encoding="utf-8").strip()
        blocks.append(f"## {label}\n\n{text}")
    return "\n\n---\n\n".join(blocks)


def _run_pilot(root: Path, manifest: dict, executor: Executor, new_attempt: str | None) -> None:
    for role in WRITERS:
        task = ("## Your task\n\nWrite your one scene now, alone. You have not seen any other writer's work and must "
                "not try to infer it. Deliver the scene, then at most three lines naming who is in it and anything "
                "you invented. Deliver nothing else.")
        _pilot_role(root, manifest, executor, PILOT_WRITING, role,
                    _pilot_prompt(manifest, role, "1 — Independent writing", task), new_attempt)
    scenes = _blind_scenes(root, manifest)
    task = ("## Scenes under assessment\n\nThe three scenes are labelled anonymously. Do not guess who wrote them.\n\n"
            + scenes + "\n\n## Your task\n\nApply your rubric to each scene and across the three. Quote lines. "
            "Do not rewrite the scenes.")
    _pilot_role(root, manifest, executor, PILOT_ASSESSMENT, "comedy-editor",
                _pilot_prompt(manifest, "comedy-editor", "2 — Independent comedy assessment", task), new_attempt)
    assessment = _artifact(root, PILOT_ASSESSMENT, "comedy-editor").read_text(encoding="utf-8").strip()
    task = ("## Scenes\n\nRead all three scenes and note your own funniest moment in each before you read the "
            "assessment that follows them.\n\n" + scenes
            + "\n\n## Comedy editor's assessment (read after the scenes)\n\n" + assessment
            + "\n\n## Your task\n\nWrite your selection as your role instructions describe. End with: "
            "`Status: PROVISIONAL — owner approval required; no canon has changed.`")
    _pilot_role(root, manifest, executor, PILOT_SELECTION, "showrunner",
                _pilot_prompt(manifest, "showrunner", "3 — Showrunner selection", task), new_attempt)
    manifest["status"] = "awaiting_owner_approval"
    manifest.pop("failure", None)
    manifest["owner_approval"] = {"required": True, "recorded": False,
                                  "note": "This session cannot modify the Media Bible or establish canon."}
    _save_session(root, manifest)


def _run_pilot_session(root: Path, manifest: dict, executor: Executor | None, retry_failed: bool,
                       new_attempt: str | None, clear_stale_lock: bool) -> dict:
    name = (manifest.get("plan") or {}).get("name") if isinstance(manifest.get("plan"), dict) else None
    if name not in PLANS:
        raise SlopError(f"session names an unsupported plan: {manifest.get('plan')!r}; refusing to fall back to the standard room")
    if new_attempt is not None and new_attempt not in _plan_keys(name):
        raise SlopError(f"--authorize-new-attempt must name one of: {', '.join(_plan_keys(name))}")
    lock = _acquire_lock(root, clear_stale_lock)
    try:
        root, manifest = _load_session(root)     # the state as it is now that we hold the lock
        if manifest.get("status") == "awaiting_owner_approval":
            return {"ok": True, "session": str(root), "status": manifest["status"],
                    "report": _final_report(manifest), "resumed": False}
        if manifest.get("status") == "failed" and not retry_failed:
            raise SlopError("session is failed; inspect manifest.json and the job folder, then rerun with --retry-failed. "
                            "That alone starts no new model attempt")
        _pilot_preflight(root, manifest, require_runtime=executor is None)
        if manifest.get("status") == "failed":
            manifest.setdefault("failure_history", []).append(manifest.get("failure"))
        if executor is None:
            executor = SandboxedRunnerExecutor((toolkit_root() / "runner" / "deleg.mjs").resolve(), manifest["plan"]["sandbox"])
        manifest["status"] = "running"
        _save_session(root, manifest)
        try:
            _run_pilot(root, manifest, executor, new_attempt)
        except Exception as exc:
            if manifest.get("status") != "failed":
                manifest["status"] = "failed"
                manifest["failure"] = {"phase": "orchestration", "at": _now(), "message": str(exc)}
                _save_session(root, manifest)
            raise
        return {"ok": True, "session": str(root), "status": manifest["status"],
                "report": _final_report(manifest), "resumed": True}
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _final_report(manifest: dict) -> str | None:
    key = f"{PILOT_SELECTION}/showrunner" if manifest.get("plan") else "phase-e/showrunner"
    return manifest.get("artifacts", {}).get(key)


def run_session(session: Path, executor: Executor | None = None, retry_failed: bool = False,
                new_attempt: str | None = None, clear_stale_lock: bool = False) -> dict:
    root, manifest = _load_session(session)
    if manifest.get("plan") is not None:
        return _run_pilot_session(root, manifest, executor, retry_failed, new_attempt, clear_stale_lock)
    if new_attempt is not None or clear_stale_lock:
        raise SlopError("--authorize-new-attempt and --clear-stale-lock apply only to a session prepared with --plan")
    if manifest.get("status") == "awaiting_owner_approval":
        return {"ok": True, "session": str(root), "status": manifest["status"],
                "report": manifest.get("artifacts", {}).get("phase-e/showrunner"), "resumed": False}
    if manifest.get("status") == "failed":
        if not retry_failed:
            raise SlopError("session is failed; inspect manifest.json and artifacts, then retry explicitly with --retry-failed after correcting the cause")
        manifest.setdefault("failure_history", []).append(manifest.get("failure"))
        manifest.pop("failure", None)
    _verify_bible(manifest)
    executor = executor or RunnerExecutor((toolkit_root() / "runner" / "deleg.mjs").resolve())
    manifest["status"] = "running"
    _save_session(root, manifest)
    try:
        _phase_a(root, manifest, executor)
        _phase_b(root, manifest, executor)
        _phase_c(root, manifest, executor)
        _phase_d(root, manifest, executor)
        _phase_e(root, manifest, executor)
    except Exception as exc:
        if manifest.get("status") != "failed":
            manifest["status"] = "failed"
            manifest["failure"] = {"phase": "orchestration", "at": _now(), "message": str(exc)}
            _save_session(root, manifest)
        raise
    return {"ok": True, "session": str(root), "status": manifest["status"],
            "report": manifest["artifacts"]["phase-e/showrunner"], "resumed": True}


def _prepare_pilot(args: argparse.Namespace, plan: str) -> dict:
    if not args.assignment:
        raise SlopError(f"--plan {plan} needs an explicit --assignment Markdown file; no bundled brief is assumed")
    assignment_path = Path(args.assignment)
    if not assignment_path.is_file() or assignment_path.suffix.lower() != ".md":
        raise SlopError(f"--plan {plan} needs --assignment to be an existing Markdown (.md) file; "
                        f"bundled assignment names are not accepted: {args.assignment}")
    assignment_path = assignment_path.resolve()
    assignment_text = assignment_path.read_text(encoding="utf-8").strip()
    if not assignment_text:
        raise SlopError(f"assignment is empty: {assignment_path}")
    models, model_path = _load_model_config(Path(args.models) if args.models else None, _plan_roles(plan),
                                            _plan_root(plan) / "models.json")
    bible = _bible_manifest(Path(args.bible_dir).resolve(), PLANS[plan]["bible"])
    sandbox = _read_json(_plan_root(plan) / "sandbox.json")
    session_id = _slug(args.session_id or f"{assignment_path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    root = Path(args.room_dir).resolve() / "sessions" / session_id
    if root.exists():
        raise SlopError(f"session already exists: {root}; choose a different --session-id")
    root.mkdir(parents=True)
    (root / "assignment.md").write_text(assignment_text + "\n", encoding="utf-8")
    labels = list(WRITERS)
    random.SystemRandom().shuffle(labels)
    manifest = {
        "format": "business-as-unusual-writers-room", "version": ROOM_VERSION, "id": session_id,
        "created_at": _now(), "updated_at": _now(), "status": "prepared", "authority": bible,
        "assignment": {"source": str(assignment_path), "text": assignment_text, "sha256": _text_sha256(assignment_text)},
        "models": models, "model_config_source": str(model_path), "artifacts": {},
        "owner_approval": {"required": True, "recorded": False,
                           "note": "All creative output is provisional; no command changes the Media Bible."},
        "plan": {
            "name": plan,
            "sequence": [f"{phase}: {role}" for phase, role in PLANS[plan]["sequence"]],
            "roles": _snapshot_roles(root, plan),
            # Private provenance: which writer is behind each anonymous label. Never placed in a prompt,
            # and not mounted into any role's filesystem view.
            "scene_labels": {f"Scene {index}": role for index, role in enumerate(labels, 1)},
            "sandbox": sandbox, "sandbox_sha256": _text_sha256(json.dumps(sandbox, sort_keys=True)),
            "jobs": {key: {"state": "never_started", "attempts": 0} for key in _plan_keys(plan)},
        },
    }
    _save_session(root, manifest)
    problems = _pilot_problems(root, manifest, require_runtime=False)
    return {"ok": True, "session": str(root), "status": "prepared", "model_calls_started": False,
            "preflight_problems": problems,
            "next": f'slop writers-room sandbox-check "{root}"   (no model calls), then, only with owner authorization: '
                    f'slop writers-room run "{root}" --authorize-model-calls'}


def prepare(args: argparse.Namespace) -> dict:
    plan = getattr(args, "plan", None)
    if plan is not None:
        if plan not in PLANS:
            raise SlopError(f"unknown plan {plan!r}; available: {', '.join(sorted(PLANS))}")
        return _prepare_pilot(args, plan)
    room = Path(args.room_dir).resolve()
    assignment_path = _assignment_path(args.assignment or "original-human-ensemble")
    assignment_text = assignment_path.read_text(encoding="utf-8").strip()
    if not assignment_text:
        raise SlopError(f"assignment is empty: {assignment_path}")
    models, model_path = _load_model_config(Path(args.models) if args.models else None)
    bible = _bible_manifest(Path(args.bible_dir).resolve())
    session_id = _slug(args.session_id or f"{assignment_path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    root = room / "sessions" / session_id
    if root.exists():
        raise SlopError(f"session already exists: {root}; choose a different --session-id")
    root.mkdir(parents=True)
    (root / "assignment.md").write_text(assignment_text + "\n", encoding="utf-8")
    manifest = {
        "format": "business-as-unusual-writers-room", "version": ROOM_VERSION, "id": session_id,
        "created_at": _now(), "updated_at": _now(), "status": "prepared", "authority": bible,
        "assignment": {"source": str(assignment_path), "text": assignment_text}, "models": models,
        "model_config_source": str(model_path), "artifacts": {},
        "owner_approval": {"required": True, "recorded": False,
                           "note": "All creative output is provisional; no command changes the Media Bible."},
    }
    _save_session(root, manifest)
    return {"ok": True, "session": str(root), "status": "prepared", "model_calls_started": False,
            "next": f'slop writers-room run "{root}" --authorize-model-calls'}


def run(args: argparse.Namespace) -> dict:
    if not args.authorize_model_calls:
        raise SlopError("refusing to start model calls without --authorize-model-calls; prepare is safe and makes no calls")
    return run_session(Path(args.session), retry_failed=getattr(args, "retry_failed", False),
                       new_attempt=getattr(args, "authorize_new_attempt", None),
                       clear_stale_lock=getattr(args, "clear_stale_lock", False))


def status(args: argparse.Namespace) -> dict:
    root, manifest = _load_session(Path(args.session))
    result = {"ok": True, "session": str(root), "status": manifest.get("status"), "failure": manifest.get("failure"),
              "artifacts": manifest.get("artifacts", {}), "owner_approval": manifest.get("owner_approval")}
    plan = manifest.get("plan")
    if isinstance(plan, dict):
        result["plan"] = plan.get("name")
        result["jobs"] = {key: {k: entry.get(k) for k in ("state", "attempts", "detail")}
                          for key, entry in (plan.get("jobs") or {}).items()}
        result["preflight_problems"] = _pilot_problems(root, manifest, require_runtime=False)
        result["locked"] = (root / LOCK_NAME).exists()
    return result


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("writers-room", help="prepare and run a local, artifact-first comedy writers' room")
    commands = parser.add_subparsers(dest="writers_room_command", required=True)
    prep = commands.add_parser("prepare", help="prepare a session without model calls")
    prep.add_argument("--room-dir", required=True, help="directory that will hold local sessions")
    prep.add_argument("--bible-dir", required=True, help="authoritative agentic-bull-media/bible directory")
    prep.add_argument("--assignment", help="bundled assignment name or Markdown path (default: original-human-ensemble); "
                                           "with --plan, an explicit .md file is required")
    prep.add_argument("--plan", choices=sorted(PLANS), help="optional execution plan; omit for the standard five-phase room")
    prep.add_argument("--models", help="JSON model-role configuration; default is the bundled editable configuration")
    prep.add_argument("--session-id", help="stable, filesystem-safe session id")
    prep.set_defaults(func=prepare)
    launch = commands.add_parser("run", help="run or resume a prepared session through runner/deleg.mjs")
    launch.add_argument("session", help="prepared session directory")
    launch.add_argument("--authorize-model-calls", action="store_true", help="explicitly permit local subscribed-model calls")
    launch.add_argument("--retry-failed", action="store_true", help="resume a failed session after inspecting its preserved logs and artifacts")
    launch.add_argument("--authorize-new-attempt", metavar="PHASE/ROLE",
                        help="plan sessions only: owner authorization for exactly one new model attempt of the named failed or uncertain job")
    launch.add_argument("--clear-stale-lock", action="store_true",
                        help="plan sessions only: remove a session lock whose orchestrator is no longer running")
    launch.set_defaults(func=run)
    stat = commands.add_parser("status", help="inspect a session without model calls")
    stat.add_argument("session", help="session directory")
    stat.set_defaults(func=status)
    probe = commands.add_parser("sandbox-check", help="plan sessions only: prove the role filesystem boundary with no model calls")
    probe.add_argument("session", help="prepared plan session directory")
    probe.add_argument("--deny", action="append", metavar="PATH", help="extra path every role must be unable to read (repeatable)")
    probe.set_defaults(func=sandbox_check)
