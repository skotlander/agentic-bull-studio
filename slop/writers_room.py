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
import re
import shutil
import subprocess
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


def _bible_manifest(bible_dir: Path) -> dict:
    bible_dir = bible_dir.resolve()
    if not bible_dir.is_dir():
        raise SlopError(f"Media Bible directory does not exist: {bible_dir}")
    files = []
    for name in REQUIRED_BIBLE:
        path = bible_dir / name
        if not path.is_file():
            raise SlopError(f"Media Bible is incomplete; required file is missing: {path}")
        files.append({"name": name, "path": str(path), "sha256": _sha256(path)})
    return {"directory": str(bible_dir), "files": files}


def _load_model_config(config_path: Path | None) -> tuple[dict, Path]:
    path = (config_path or (_template_root() / "models.json")).resolve()
    data = _read_json(path)
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise SlopError(f"model configuration {path} needs a roles object")
    missing = [role for role in ROLES if not isinstance(roles.get(role), dict)]
    if missing:
        raise SlopError(f"model configuration {path} is missing roles: {', '.join(missing)}")
    for role in ROLES:
        item = roles[role]
        if item.get("engine") not in {"claude", "codex", "muse", "gemini"}:
            raise SlopError(f"model configuration for {role} needs a supported runner engine")
        if not isinstance(item.get("model"), str) or not item["model"].strip():
            raise SlopError(f"model configuration for {role} needs a non-empty model selector")
        if item.get("reasoning") not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
            raise SlopError(f"model configuration for {role} has unsupported reasoning level")
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


def run_session(session: Path, executor: Executor | None = None, retry_failed: bool = False) -> dict:
    root, manifest = _load_session(session)
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


def prepare(args: argparse.Namespace) -> dict:
    room = Path(args.room_dir).resolve()
    assignment_path = _assignment_path(args.assignment)
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
    return run_session(Path(args.session), retry_failed=args.retry_failed)


def status(args: argparse.Namespace) -> dict:
    root, manifest = _load_session(Path(args.session))
    return {"ok": True, "session": str(root), "status": manifest.get("status"), "failure": manifest.get("failure"),
            "artifacts": manifest.get("artifacts", {}), "owner_approval": manifest.get("owner_approval")}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("writers-room", help="prepare and run a local, artifact-first comedy writers' room")
    commands = parser.add_subparsers(dest="writers_room_command", required=True)
    prep = commands.add_parser("prepare", help="prepare a session without model calls")
    prep.add_argument("--room-dir", required=True, help="directory that will hold local sessions")
    prep.add_argument("--bible-dir", required=True, help="authoritative agentic-bull-media/bible directory")
    prep.add_argument("--assignment", default="original-human-ensemble", help="bundled assignment name or Markdown path")
    prep.add_argument("--models", help="JSON model-role configuration; default is the bundled editable configuration")
    prep.add_argument("--session-id", help="stable, filesystem-safe session id")
    prep.set_defaults(func=prepare)
    launch = commands.add_parser("run", help="run or resume a prepared session through runner/deleg.mjs")
    launch.add_argument("session", help="prepared session directory")
    launch.add_argument("--authorize-model-calls", action="store_true", help="explicitly permit local subscribed-model calls")
    launch.add_argument("--retry-failed", action="store_true", help="resume a failed session after inspecting its preserved logs and artifacts")
    launch.set_defaults(func=run)
    stat = commands.add_parser("status", help="inspect a session without model calls")
    stat.add_argument("session", help="session directory")
    stat.set_defaults(func=status)
