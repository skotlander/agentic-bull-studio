"""Focused, no-model-call verification for the opt-in comedy-lab-pilot plan.

Three kinds of test live here:
  * orchestration tests with an in-process stand-in for the runner (ordering, prompts, ledger, recovery);
  * real-process tests of the session lock (a second Python process tries to run the same session);
  * real OS-level tests of the filesystem boundary (bubblewrap + the real ``runner/deleg.mjs`` + a fake CLI
    that tries prohibited reads and writes). These skip, loudly, where bubblewrap cannot run.
No test invokes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from slop import writers_room as room
from slop.config import SlopError, toolkit_root

PLAN = room.PILOT_PLAN
ORDER = [
    "pilot-1-writing: character-dialogue", "pilot-1-writing: situational-comedy",
    "pilot-1-writing: satire-observation", "pilot-2-assessment: comedy-editor", "pilot-3-selection: showrunner",
]
KEYS = [title.replace(": ", "/") for title in ORDER]
EDITOR, SHOWRUNNER = KEYS[3], KEYS[4]


class Crash(BaseException):
    """Stands in for the orchestrator process dying: nothing may catch and tidy up after it."""


@pytest.fixture(autouse=True)
def clean_toolkit(tmp_path: Path, monkeypatch):
    """The real checkout may hold private sessions; tests look only at the fixture room."""
    monkeypatch.setattr(room, "_toolkit_sessions_dir", lambda: tmp_path / "no-sessions-here")


def bible(tmp_path: Path, names=room.PLANS[PLAN]["bible"]) -> Path:
    root = tmp_path / "bible"
    root.mkdir(exist_ok=True)
    for name in names:
        (root / name).write_text(f"# {name}\nAuthoritative fixture for {name}.\n", encoding="utf-8")
    return root


def prepare(tmp_path: Path, plan: str | None = PLAN, session_id: str = "pilot-test") -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Private fixture brief\nWrite one provisional scene.\n", encoding="utf-8")
    result = room.prepare(argparse.Namespace(
        room_dir=str(tmp_path / "writers-room"), bible_dir=str(bible(tmp_path)), assignment=str(brief),
        models=None, session_id=session_id, plan=plan,
    ))
    assert result["model_calls_started"] is False
    assert result.get("preflight_problems", []) == []
    return Path(result["session"])


def load(session: Path) -> dict:
    return json.loads((session / "manifest.json").read_text(encoding="utf-8"))


def save(session: Path, manifest: dict) -> None:
    (session / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def jobs(session: Path) -> dict:
    return load(session)["plan"]["jobs"]


def finish(job_dir: Path, job: dict, state: str = "done", result: str | None = "") -> None:
    """What a completed delegated job leaves behind: the runner's status record and the deliverable."""
    role = job["title"].split(": ")[1]
    if state is not None:
        (job_dir / "status.json").write_text(json.dumps({"state": state}), encoding="utf-8")
    if result is not None:
        (job_dir / "outbox" / "result.md").write_text(result or f"OUTPUT-OF-{role}", encoding="utf-8")


def good_executor(seen: list[dict]):
    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        finish(job_dir, job)
    return executor


def failing_at(seen: list[dict], title: str, behaviour):
    """Run normally, except that the named invocation behaves as ``behaviour(job_dir, job)``."""
    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        if job["title"] == title:
            behaviour(job_dir, job)
        else:
            finish(job_dir, job)
    return executor


def titles(seen: list[dict]) -> list[str]:
    return [job["title"] for job in seen]


def tree(path: Path) -> dict:
    return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(path.rglob("*")) if item.is_file()}


# ------------------------------------------------------------------------------- preparation
def test_prepare_records_plan_snapshots_fingerprints_and_an_empty_ledger_without_calls(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    assert manifest["status"] == "prepared"
    assert manifest["plan"]["name"] == PLAN and manifest["plan"]["sequence"] == ORDER
    names = [item["name"] for item in manifest["authority"]["files"]]
    assert names == list(room.REQUIRED_BIBLE) + ["voice.md"]
    assert all(len(item["sha256"]) == 64 for item in manifest["authority"]["files"])
    roles = manifest["plan"]["roles"]
    assert set(roles) == {"character-dialogue", "situational-comedy", "satire-observation", "comedy-editor", "showrunner"}
    for role, entry in roles.items():
        source = room._template_root() / "plans" / PLAN / "roles" / f"{role}.md"
        assert entry["text"] == source.read_text(encoding="utf-8").strip()
        assert entry["sha256"] == hashlib.sha256(entry["text"].encode("utf-8")).hexdigest()
        assert (session / "roles" / f"{role}.md").read_text(encoding="utf-8").strip() == entry["text"]
    assert manifest["models"]["roles"]["comedy-editor"]["engine"] != manifest["models"]["roles"]["showrunner"]["engine"]
    labels = manifest["plan"]["scene_labels"]
    assert sorted(labels) == ["Scene 1", "Scene 2", "Scene 3"] and sorted(labels.values()) == sorted(room.WRITERS)
    assert manifest["assignment"]["sha256"] == hashlib.sha256(manifest["assignment"]["text"].encode()).hexdigest()
    assert manifest["plan"]["sandbox"]["tool"] == "bwrap" and len(manifest["plan"]["sandbox_sha256"]) == 64
    assert manifest["plan"]["jobs"] == {key: {"state": "never_started", "attempts": 0} for key in KEYS}
    assert not (session / "jobs").exists() and not (session / room.LOCK_NAME).exists()


def test_pilot_needs_an_explicit_markdown_assignment_file_and_a_known_plan(tmp_path: Path):
    base = dict(room_dir=str(tmp_path / "room"), bible_dir=str(bible(tmp_path)), models=None, session_id="x")
    with pytest.raises(SlopError, match="explicit --assignment"):
        room.prepare(argparse.Namespace(assignment=None, plan=PLAN, **base))
    # the standard room's bundled alias exists, and the pilot still refuses it
    assert room._assignment_path("original-human-ensemble").is_file()
    with pytest.raises(SlopError, match="bundled assignment names are not accepted"):
        room.prepare(argparse.Namespace(assignment="original-human-ensemble", plan=PLAN, **base))
    text = tmp_path / "brief.txt"
    text.write_text("a brief in the wrong format", encoding="utf-8")
    with pytest.raises(SlopError, match=r"Markdown \(\.md\) file"):
        room.prepare(argparse.Namespace(assignment=str(text), plan=PLAN, **base))
    with pytest.raises(SlopError, match="unknown plan"):
        room.prepare(argparse.Namespace(assignment=None, plan="something-else", **base))
    assert not (tmp_path / "room").exists()


def test_missing_voice_file_blocks_pilot_preparation(tmp_path: Path):
    brief = tmp_path / "brief.md"
    brief.write_text("brief", encoding="utf-8")
    with pytest.raises(SlopError, match="voice.md"):
        room.prepare(argparse.Namespace(
            room_dir=str(tmp_path / "room"), bible_dir=str(bible(tmp_path, room.REQUIRED_BIBLE)),
            assignment=str(brief), models=None, session_id="x", plan=PLAN))


# ------------------------------------------------------------------------------- the normal run
def test_run_makes_exactly_five_invocations_in_order_and_awaits_owner(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    result = room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER
    assert result["status"] == "awaiting_owner_approval"
    assert result["report"] == "artifacts/pilot-3-selection/showrunner.md"
    manifest = load(session)
    assert manifest["status"] == "awaiting_owner_approval" and manifest["owner_approval"]["recorded"] is False
    assert sorted(manifest["artifacts"]) == sorted(KEYS)
    for key, entry in manifest["plan"]["jobs"].items():
        assert entry["state"] == "completed" and entry["attempts"] == 1
        published = session / entry["artifact"]["path"]
        assert hashlib.sha256(published.read_bytes()).hexdigest() == entry["artifact"]["sha256"]
    assert not list(session.rglob("*.partial")) and not (session / room.LOCK_NAME).exists()
    assert all(job["sandbox"] == "workspace-write" for job in seen)
    assert all(job.get("permission_mode") == "acceptEdits" for job in seen if job["engine"] == "claude")
    again = room.run_session(session, executor=good_executor(seen))
    assert again["resumed"] is False and len(seen) == 5


def test_every_role_gets_its_snapshot_the_brief_and_all_seven_bible_files(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    seen: list[dict] = []
    room.run_session(session, executor=good_executor(seen))
    for job in seen:
        role = job["title"].split(": ")[1]
        brief = job["brief"]
        assert manifest["plan"]["roles"][role]["text"] in brief
        assert "Private fixture brief" in brief
        for name in room.PLANS[PLAN]["bible"]:
            assert f"Authoritative fixture for {name}." in brief
        assert "preserve genuine alternatives" not in brief
        assert "outbox/result.md" in brief


def test_prompts_keep_writers_apart_and_show_assessors_only_anonymous_scenes(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    room.run_session(session, executor=good_executor(seen))
    labels = load(session)["plan"]["scene_labels"]
    writers, editor, showrunner = seen[:3], seen[3]["brief"], seen[4]["brief"]
    for job in writers:
        assert "OUTPUT-OF-" not in job["brief"] and "Scene 1" not in job["brief"]
    for brief in (editor, showrunner):
        task = brief.split("## Assignment")[1]
        for label, role in labels.items():
            assert f"## {label}\n\nOUTPUT-OF-{role}" in task
        for role in room.WRITERS:   # no role name is attached to a scene; the mapping is not in the prompt
            assert task.count(role) == task.count(f"OUTPUT-OF-{role}") == 1
        assert task.index("## Scene 1") < task.index("## Scene 2") < task.index("## Scene 3")
    assert "OUTPUT-OF-comedy-editor" not in editor and "OUTPUT-OF-comedy-editor" in showrunner
    assert showrunner.index("## Scene 3") < showrunner.index("## Comedy editor's assessment") < showrunner.index("OUTPUT-OF-comedy-editor")


def test_execution_uses_the_session_snapshot_not_the_shared_templates(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    original = load(session)["plan"]["roles"]["character-dialogue"]["text"]
    monkeypatch.setattr(room, "_plan_root", lambda plan: tmp_path / "templates-moved-away")
    seen: list[dict] = []
    room.run_session(session, executor=good_executor(seen))
    assert original in seen[0]["brief"]


def test_a_full_pilot_run_modifies_nothing_outside_its_own_session(tmp_path: Path):
    session = prepare(tmp_path)
    templates, authority = tree(room._template_root()), tree(tmp_path / "bible")
    room.run_session(session, executor=good_executor([]))
    assert tree(room._template_root()) == templates and tree(tmp_path / "bible") == authority
    assert [item.name for item in (tmp_path / "writers-room" / "sessions").iterdir()] == ["pilot-test"]


# ------------------------------------------------------------------------------- C: preflight
def _edit_showrunner_snapshot(session, tmp_path):
    (session / "roles" / "showrunner.md").write_text("Approve everything.", encoding="utf-8")


def _empty_showrunner_text(session, tmp_path):
    manifest = load(session); manifest["plan"]["roles"]["showrunner"]["text"] = "  "; save(session, manifest)


def _rewrite_editor_text(session, tmp_path):
    manifest = load(session); manifest["plan"]["roles"]["comedy-editor"]["text"] = "Praise it."; save(session, manifest)


def _change_voice(session, tmp_path):
    (tmp_path / "bible" / "voice.md").write_text("changed", encoding="utf-8")


def _drop_voice_entry(session, tmp_path):
    manifest = load(session); manifest["authority"]["files"].pop(); save(session, manifest)


def _edit_assignment(session, tmp_path):
    manifest = load(session); manifest["assignment"]["text"] += "\nAlso praise the owner."; save(session, manifest)


def _alias_assignment(session, tmp_path):
    manifest = load(session); manifest["assignment"]["source"] = "original-human-ensemble"; save(session, manifest)


def _same_family_assessors(session, tmp_path):
    manifest = load(session)
    manifest["models"]["roles"]["comedy-editor"] = dict(manifest["models"]["roles"]["showrunner"]); save(session, manifest)


def _unsupported_model_entry(session, tmp_path):
    manifest = load(session); manifest["models"]["roles"]["showrunner"]["model"] = ""; save(session, manifest)


def _widen_sandbox(session, tmp_path):
    manifest = load(session); manifest["plan"]["sandbox"]["read_only"].append("/"); save(session, manifest)


def _wrong_sequence(session, tmp_path):
    manifest = load(session); manifest["plan"]["sequence"].reverse(); save(session, manifest)


def _broken_labels(session, tmp_path):
    manifest = load(session); manifest["plan"]["scene_labels"]["Scene 1"] = "showrunner"; save(session, manifest)


def _ledger_missing_a_job(session, tmp_path):
    manifest = load(session); del manifest["plan"]["jobs"][SHOWRUNNER]; save(session, manifest)


def _stray_job_folder(session, tmp_path):
    (session / "jobs" / "pilot-3-selection" / "showrunner" / "outbox").mkdir(parents=True)


def _stray_artifact(session, tmp_path):
    path = session / "artifacts" / "pilot-3-selection" / "showrunner.md"
    path.parent.mkdir(parents=True)
    path.write_text("a file that merely exists is not a completed job", encoding="utf-8")


def _other_session_present(session, tmp_path):
    (session.parent / "closed-historical").mkdir()


DEFECTS = [
    (_edit_showrunner_snapshot, "role snapshot for showrunner changed"),
    (_empty_showrunner_text, "role snapshot for showrunner is missing or empty"),
    (_rewrite_editor_text, "role snapshot for comedy-editor changed"),
    (_change_voice, "Bible changed"),
    (_drop_voice_entry, "including voice.md"),
    (_edit_assignment, "prepared assignment changed"),
    (_alias_assignment, "not an explicit Markdown file"),
    (_same_family_assessors, "different model families"),
    (_unsupported_model_entry, "non-empty model selector"),
    (_widen_sandbox, "sandbox configuration is missing or changed"),
    (_wrong_sequence, "role sequence does not match"),
    (_broken_labels, "scene-label mapping"),
    (_ledger_missing_a_job, "exactly the five planned"),
    (_stray_job_folder, "never started but execution artifacts exist"),
    (_stray_artifact, "never started but execution artifacts exist"),
    (_other_session_present, "clean execution context"),
]


@pytest.mark.parametrize("defect,message", DEFECTS, ids=[item[0].__name__.strip("_") for item in DEFECTS])
def test_any_defect_in_any_role_input_stops_the_pilot_before_the_first_invocation(tmp_path: Path, defect, message):
    session = prepare(tmp_path)
    defect(session, tmp_path)
    calls: list[dict] = []
    with pytest.raises(SlopError, match="no model was invoked.*" + message):
        room.run_session(session, executor=good_executor(calls))
    assert calls == []                                   # not four writers and an editor first: nothing at all
    assert load(session)["status"] == "prepared"
    assert not (session / "jobs").exists() or defect is _stray_job_folder
    assert not (session / room.LOCK_NAME).exists()
    assert any(message in problem for problem in room.status(argparse.Namespace(session=str(session)))["preflight_problems"])


def test_preflight_checks_the_execution_paths_and_never_runs_without_the_boundary(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    real_which = shutil.which
    monkeypatch.setattr(room.shutil, "which", lambda name, *a, **k: None if name == "bwrap" else real_which(name, *a, **k))
    monkeypatch.setattr(room.subprocess, "run", lambda *a, **k: pytest.fail("a process was started"))
    with pytest.raises(SlopError, match="no model was invoked.*bubblewrap"):
        room.run_session(session)                         # the real, sandboxed executor path
    assert load(session)["status"] == "prepared" and not (session / "jobs").exists()


def test_cli_run_refuses_the_pilot_without_model_call_authorization(tmp_path: Path):
    session = prepare(tmp_path)
    with pytest.raises(SlopError, match="authorize-model-calls"):
        room.run(argparse.Namespace(session=str(session), authorize_model_calls=False, retry_failed=False))
    assert not (session / "jobs").exists() and load(session)["status"] == "prepared"


def test_unknown_plan_in_a_manifest_never_falls_back_to_the_standard_room(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    manifest["plan"]["name"] = "renamed"
    save(session, manifest)
    calls: list[dict] = []
    with pytest.raises(SlopError, match="unsupported plan"):
        room.run_session(session, executor=good_executor(calls))
    assert calls == []


# ------------------------------------------------------------------------------- B: one orchestrator
SECOND_PROCESS = """
import json, sys
from pathlib import Path
from slop import writers_room as room
calls = []
def executor(job_dir, job):
    calls.append(job["title"])
try:
    room.run_session(Path(sys.argv[1]), executor=executor, retry_failed=True)
    outcome = "RAN"
except Exception as exc:
    outcome = str(exc)
print(json.dumps({"outcome": outcome, "calls": calls}))
"""


def second_process(session: Path) -> dict:
    done = subprocess.run([sys.executable, "-c", SECOND_PROCESS, str(session)], cwd=str(toolkit_root()),
                          text=True, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_second_orchestrator_process_cannot_start_anything_while_one_is_running(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    rivals: list[dict] = []

    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        rivals.append(second_process(session))            # a real concurrent launch, mid-invocation
        with pytest.raises(SlopError, match="another orchestrator"):
            room.run_session(session, executor=good_executor(seen))   # and a re-entrant one
        finish(job_dir, job)

    room.run_session(session, executor=executor)
    assert titles(seen) == ORDER                          # five invocations in total, none duplicated
    assert len(rivals) == 5
    for rival in rivals:
        assert "another orchestrator" in rival["outcome"] and rival["calls"] == []
    assert load(session)["status"] == "awaiting_owner_approval"


def dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_a_lock_left_by_a_dead_orchestrator_is_never_cleared_silently(tmp_path: Path):
    session = prepare(tmp_path)
    lock = session / room.LOCK_NAME
    lock.write_text(json.dumps({"pid": dead_pid(), "host": room.socket.gethostname(), "at": "then"}), encoding="utf-8")
    calls: list[dict] = []
    for _ in range(2):
        with pytest.raises(SlopError, match="no longer running.*--clear-stale-lock"):
            room.run_session(session, executor=good_executor(calls))
    assert calls == [] and lock.exists() and load(session)["status"] == "prepared"
    assert room.status(argparse.Namespace(session=str(session)))["locked"] is True
    # a lock from another machine is never cleared by this command at all
    lock.write_text(json.dumps({"pid": 1, "host": "some-other-host", "at": "then"}), encoding="utf-8")
    with pytest.raises(SlopError, match="some-other-host"):
        room.run_session(session, executor=good_executor(calls), clear_stale_lock=True)
    assert calls == []
    lock.write_text(json.dumps({"pid": dead_pid(), "host": room.socket.gethostname(), "at": "then"}), encoding="utf-8")
    room.run_session(session, executor=good_executor(calls), clear_stale_lock=True)
    assert titles(calls) == ORDER and not lock.exists() and not list(session.glob(room.LOCK_NAME + ".stale-*"))


def test_clear_stale_lock_does_not_displace_a_live_orchestrator(tmp_path: Path):
    session = prepare(tmp_path)
    (session / room.LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "host": room.socket.gethostname(), "at": "now"}))
    calls: list[dict] = []
    with pytest.raises(SlopError, match="another orchestrator"):
        room.run_session(session, executor=good_executor(calls), clear_stale_lock=True)
    assert calls == [] and (session / room.LOCK_NAME).exists()


# ------------------------------------------------------------------------------- B: failure and recovery
def assert_no_new_attempt_without_authorization(session: Path, seen: list[dict], key: str, state: str) -> None:
    """Whatever is rerun, however often, a failed or uncertain job starts no model attempt by itself."""
    before = len(seen)
    with pytest.raises(SlopError, match="retry-failed"):
        room.run_session(session, executor=good_executor(seen))
    for _ in range(3):
        with pytest.raises(SlopError, match=f"{key} is {state}.*No new model attempt was started.*--authorize-new-attempt {key}"):
            room.run_session(session, executor=good_executor(seen), retry_failed=True)
    other = next(item for item in KEYS if item != key)
    with pytest.raises(SlopError, match="No new model attempt was started"):
        room.run_session(session, executor=good_executor(seen), retry_failed=True, new_attempt=other)
    assert len(seen) == before
    assert jobs(session)[key]["state"] == state and jobs(session)[key]["attempts"] == 1
    assert second_process(session)["calls"] == []


def test_terminal_failure_stops_the_pilot_and_only_owner_authorization_starts_one_new_attempt(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def fail(job_dir: Path, job: dict) -> None:
        finish(job_dir, job, state="failed", result=None)
        raise SlopError("runner failed for the fixture (exit 1)")

    with pytest.raises(SlopError, match="runner failed"):
        room.run_session(session, executor=failing_at(seen, ORDER[3], fail))
    assert titles(seen) == ORDER[:4]
    manifest = load(session)
    assert manifest["status"] == "failed" and manifest["failure"]["role"] == "comedy-editor"
    assert manifest["plan"]["jobs"][EDITOR]["state"] == "failed"
    assert manifest["plan"]["jobs"][SHOWRUNNER]["state"] == "never_started"
    scenes = tree(session / "artifacts")
    assert_no_new_attempt_without_authorization(session, seen, EDITOR, "failed")

    room.run_session(session, executor=good_executor(seen), retry_failed=True, new_attempt=EDITOR)
    assert titles(seen) == ORDER[:4] + ORDER[3:]           # one new editor attempt, then the showrunner; writers reused
    assert seen[4]["cwd"].endswith("comedy-editor.attempt-2") and seen[4]["brief"] == seen[3]["brief"]
    assert (session / "jobs" / "pilot-2-assessment" / "comedy-editor" / "status.json").is_file()   # failed attempt preserved
    assert {key: value for key, value in tree(session / "artifacts").items() if key in scenes} == scenes
    manifest = load(session)
    entry = manifest["plan"]["jobs"][EDITOR]
    assert manifest["status"] == "awaiting_owner_approval" and "failure" not in manifest
    assert entry["state"] == "completed" and entry["attempts"] == 2 and entry["history"][0]["state"] == "failed"
    assert [item["job"] for item in manifest["plan"]["authorized_new_attempts"]] == [EDITOR]


def test_an_authorization_covers_one_attempt_of_one_job_and_is_not_remembered(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def fail(job_dir: Path, job: dict) -> None:
        finish(job_dir, job, state="failed", result=None)
        raise SlopError("runner failed")

    always_failing_editor = failing_at(seen, ORDER[3], fail)
    with pytest.raises(SlopError):
        room.run_session(session, executor=always_failing_editor)
    with pytest.raises(SlopError):                          # the authorized second attempt also fails
        room.run_session(session, executor=always_failing_editor, retry_failed=True, new_attempt=EDITOR)
    assert titles(seen) == ORDER[:4] + [ORDER[3]] and jobs(session)[EDITOR]["attempts"] == 2
    for _ in range(2):                                      # the earlier authorization does not carry over
        with pytest.raises(SlopError, match="No new model attempt was started"):
            room.run_session(session, executor=always_failing_editor, retry_failed=True)
    assert len(seen) == 5
    with pytest.raises(SlopError, match="must name one of"):
        room.run_session(session, executor=always_failing_editor, retry_failed=True, new_attempt="pilot-2-assessment/anyone")
    assert len(seen) == 5


def test_a_lost_job_is_uncertain_and_is_not_restarted(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def die_mid_job(job_dir: Path, job: dict) -> None:      # the runner had recorded "running"; then everything died
        finish(job_dir, job, state="running", result=None)
        raise Crash()

    with pytest.raises(Crash):
        room.run_session(session, executor=failing_at(seen, ORDER[1], die_mid_job))
    manifest = load(session)
    assert manifest["status"] == "running" and manifest["plan"]["jobs"][KEYS[1]]["state"] == "running"
    with pytest.raises(SlopError, match=f"{KEYS[1]} is uncertain.*'running', not done"):
        room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER[:2]
    assert_no_new_attempt_without_authorization(session, seen, KEYS[1], "uncertain")
    assert jobs(session)[KEYS[2]]["state"] == "never_started"


def test_a_job_that_died_before_its_folder_existed_is_uncertain_not_never_started(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    seen: list[dict] = []
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "outbox" and "situational-comedy" in str(self):
            raise Crash()
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(Crash):
        room.run_session(session, executor=good_executor(seen))
    monkeypatch.undo()
    monkeypatch.setattr(room, "_toolkit_sessions_dir", lambda: tmp_path / "no-sessions-here")
    with pytest.raises(SlopError, match=f"{KEYS[1]} is uncertain"):
        room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER[:1]


AMBIGUOUS = {
    "no-runner-record": (lambda job_dir, job: finish(job_dir, job, state=None), "'unknown', not done"),
    "runner-still-active": (lambda job_dir, job: finish(job_dir, job, state="running"), "'running', not done"),
    "done-without-deliverable": (lambda job_dir, job: finish(job_dir, job, result=None), "missing, empty"),
    "done-with-blank-deliverable": (lambda job_dir, job: finish(job_dir, job, result="  \n"), "missing, empty"),
    "brief-is-not-the-recorded-prompt": (
        lambda job_dir, job: (finish(job_dir, job), (job_dir / "BRIEF.md").write_text("another prompt", encoding="utf-8")),
        "not the prompt this session recorded"),
    "unreadable-runner-record": (
        lambda job_dir, job: (finish(job_dir, job), (job_dir / "status.json").write_text("{", encoding="utf-8")),
        "'unknown', not done"),
}


@pytest.mark.parametrize("case", sorted(AMBIGUOUS))
def test_ambiguous_completion_is_never_treated_as_completed_and_never_retried(tmp_path: Path, case):
    behaviour, reason = AMBIGUOUS[case]
    session = prepare(tmp_path)
    seen: list[dict] = []
    with pytest.raises(SlopError, match=f"{EDITOR} is uncertain.*{reason}"):
        room.run_session(session, executor=failing_at(seen, ORDER[3], behaviour))   # the executor itself reported success
    assert titles(seen) == ORDER[:4]
    assert not (session / "artifacts" / "pilot-2-assessment" / "comedy-editor.md").exists()
    assert EDITOR not in load(session)["artifacts"]
    assert_no_new_attempt_without_authorization(session, seen, EDITOR, "uncertain")


def test_a_role_cannot_make_the_orchestrator_publish_a_file_from_outside_its_folder(tmp_path: Path):
    """The orchestrator runs outside the boundary, so it must not follow links a role leaves in its job folder."""
    session = prepare(tmp_path)
    seen: list[dict] = []

    def link_result_to_the_manifest(job_dir: Path, job: dict) -> None:
        finish(job_dir, job, result=None)
        (job_dir / "outbox" / "result.md").symlink_to(session / "manifest.json")     # holds the scene-label mapping

    with pytest.raises(SlopError, match=f"{KEYS[0]} is uncertain.*not a file inside the job folder"):
        room.run_session(session, executor=failing_at(seen, ORDER[0], link_result_to_the_manifest))
    assert not (session / "artifacts").exists() and len(seen) == 1

    other = tmp_path / "second"
    other.mkdir()
    session2 = prepare(other)

    def link_outbox_elsewhere(job_dir: Path, job: dict) -> None:
        finish(job_dir, job, result=None)
        (job_dir / "outbox").rmdir()
        (job_dir / "outbox").symlink_to(session2 / "roles")
        (session2 / "roles" / "result.md").write_text("another role's instructions", encoding="utf-8")

    with pytest.raises(SlopError, match="uncertain.*not a file inside the job folder"):
        room.run_session(session2, executor=failing_at(seen, ORDER[0], link_outbox_elsewhere))
    assert not (session2 / "artifacts").exists()


def test_interrupted_publication_creates_no_false_completion_and_is_finished_without_a_model_call(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    seen: list[dict] = []
    artifact = session / "artifacts" / "pilot-2-assessment" / "comedy-editor.md"
    real_replace = os.replace

    def replace(source, destination):
        if Path(destination) == artifact:
            raise Crash()                                   # died after writing the partial file, before publishing it
        return real_replace(source, destination)

    monkeypatch.setattr(room.os, "replace", replace)
    with pytest.raises(Crash):
        room.run_session(session, executor=good_executor(seen))
    monkeypatch.setattr(room.os, "replace", real_replace)
    assert titles(seen) == ORDER[:4]
    assert not artifact.exists() and artifact.with_name(artifact.name + ".partial").exists()
    assert jobs(session)[EDITOR]["state"] == "running" and EDITOR not in load(session)["artifacts"]
    result = room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER                            # the editor was not invoked a second time
    assert result["status"] == "awaiting_owner_approval"
    assert artifact.read_text(encoding="utf-8") == "OUTPUT-OF-comedy-editor"
    assert jobs(session)[EDITOR]["attempts"] == 1 and not list(session.rglob("*.partial"))


def test_a_crash_between_publishing_and_recording_is_reconciled_from_the_runner_record(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    seen: list[dict] = []
    real_save = room._save_session

    def save_session(root, manifest):
        if manifest["plan"]["jobs"][EDITOR]["state"] == "completed":
            raise Crash()                                   # the artifact is on disk; the ledger never learned of it
        return real_save(root, manifest)

    monkeypatch.setattr(room, "_save_session", save_session)
    with pytest.raises(Crash):
        room.run_session(session, executor=good_executor(seen))
    monkeypatch.setattr(room, "_save_session", real_save)
    assert (session / "artifacts" / "pilot-2-assessment" / "comedy-editor.md").exists()
    assert jobs(session)[EDITOR]["state"] == "running"       # the file alone did not make it "completed"
    room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER and jobs(session)[EDITOR]["state"] == "completed"


def test_a_published_file_without_valid_provenance_is_not_a_completed_job(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def die_then_someone_drops_a_file(job_dir: Path, job: dict) -> None:
        artifact = session / "artifacts" / "pilot-2-assessment" / "comedy-editor.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("looks finished", encoding="utf-8")
        raise Crash()

    with pytest.raises(Crash):
        room.run_session(session, executor=failing_at(seen, ORDER[3], die_then_someone_drops_a_file))
    with pytest.raises(SlopError, match=f"{EDITOR} is uncertain"):
        room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER[:4] and EDITOR not in load(session)["artifacts"]


def test_a_completed_artifact_that_no_longer_matches_its_fingerprint_stops_the_pilot(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    with pytest.raises(Crash):
        room.run_session(session, executor=failing_at(seen, ORDER[4], lambda job_dir, job: (_ for _ in ()).throw(Crash())))
    (session / "artifacts" / "pilot-1-writing" / "character-dialogue.md").write_text("a funnier scene", encoding="utf-8")
    with pytest.raises(SlopError, match=f"{KEYS[0]} is uncertain.*no longer matches its recorded fingerprint"):
        room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER


def test_verified_completed_work_is_reused_after_an_interruption(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def finished_but_orchestrator_died(job_dir: Path, job: dict) -> None:
        finish(job_dir, job)
        raise Crash()

    with pytest.raises(Crash):
        room.run_session(session, executor=failing_at(seen, ORDER[2], finished_but_orchestrator_died))
    assert jobs(session)[KEYS[2]]["state"] == "running"
    room.run_session(session, executor=good_executor(seen))
    assert titles(seen) == ORDER                            # the third writer was reused, not asked again


# ------------------------------------------------------------------------------- the standard room
def test_standard_session_is_unchanged_when_no_plan_is_given(tmp_path: Path):
    result = room.prepare(argparse.Namespace(
        room_dir=str(tmp_path / "writers-room"), bible_dir=str(bible(tmp_path, room.REQUIRED_BIBLE)),
        assignment="original-human-ensemble", models=None, session_id="standard"))
    session = Path(result["session"])
    manifest = load(session)
    assert "plan" not in manifest and not (session / "roles").exists() and "preflight_problems" not in result
    assert [item["name"] for item in manifest["authority"]["files"]] == list(room.REQUIRED_BIBLE)
    assert set(manifest["models"]["roles"]) == set(room.ROLES)
    seen: list[dict] = []

    def executor(job_dir: Path, job: dict) -> None:           # no status.json: the standard room never needed one
        seen.append(job)
        if job["title"] == "phase-d: showrunner":
            (job_dir / "outbox" / "result.json").write_text('{"revise_roles": [], "brief": "none", "reasoning": "none"}', encoding="utf-8")
        else:
            (job_dir / "outbox" / "result.md").write_text("work", encoding="utf-8")

    with pytest.raises(SlopError, match="apply only to a session prepared with --plan"):
        room.run_session(session, executor=executor, new_attempt=EDITOR)
    result = room.run_session(session, executor=executor)
    assert len(seen) == 11 and seen[0]["title"] == "phase-a: character-dialogue"
    assert result["report"] == "artifacts/phase-e/showrunner.md"
    assert room._load_role("showrunner") in seen[-1]["brief"] and "preserve genuine alternatives" in seen[-1]["brief"]
    assert not (session / room.LOCK_NAME).exists() and "jobs" not in room.status(argparse.Namespace(session=str(session)))
    with pytest.raises(SlopError, match="only to a session prepared with --plan"):
        room.sandbox_check(argparse.Namespace(session=str(session), deny=None))


def test_cli_exposes_the_plan_and_recovery_flags_and_keeps_the_default_assignment(monkeypatch):
    monkeypatch.setenv("SLOP_MODULES", "slop.writers_room")
    from slop import cli
    parser = cli.build_parser()
    pilot = parser.parse_args(["writers-room", "prepare", "--room-dir", "r", "--bible-dir", "b",
                               "--assignment", "brief.md", "--plan", PLAN])
    assert pilot.plan == PLAN and pilot.func is room.prepare
    standard = parser.parse_args(["writers-room", "prepare", "--room-dir", "r", "--bible-dir", "b"])
    assert standard.plan is None and standard.assignment is None
    launch = parser.parse_args(["writers-room", "run", "s", "--authorize-model-calls"])
    assert launch.authorize_new_attempt is None and launch.clear_stale_lock is False and launch.retry_failed is False
    launch = parser.parse_args(["writers-room", "run", "s", "--authorize-model-calls", "--retry-failed",
                                "--authorize-new-attempt", EDITOR, "--clear-stale-lock"])
    assert launch.authorize_new_attempt == EDITOR and launch.clear_stale_lock is True
    probe = parser.parse_args(["writers-room", "sandbox-check", "s", "--deny", "/a", "--deny", "/b"])
    assert probe.func is room.sandbox_check and probe.deny == ["/a", "/b"]


# ------------------------------------------------------------------------------- A: the real boundary
def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if not tool:
        return False
    return subprocess.run([tool, "--ro-bind", "/", "/", "--unshare-pid", "true"], capture_output=True).returncode == 0


needs_boundary = pytest.mark.skipif(
    not (_bwrap_works() and shutil.which("node")),
    reason="OS-LEVEL ISOLATION NOT VERIFIED HERE: bubblewrap (with working namespaces) and node are required")


def test_the_probe_itself_reports_violations_when_nothing_confines_it(tmp_path: Path):
    """Positive control: outside the boundary the same probe must fail, or its passes would mean nothing."""
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "scene.md").write_text("private", encoding="utf-8")
    done = subprocess.run(["/bin/sh", "-c", room._PROBE, "probe", f"read:{secret}", f"read:{secret / 'scene.md'}",
                           f"write:{secret}", f"read:{tmp_path / 'absent'}"], text=True, capture_output=True)
    assert done.returncode == 1
    assert done.stdout.count("VIOLATION readable") == 2 and (secret / ".pilot-probe").exists()
    assert f"ok hidden {tmp_path / 'absent'}" in done.stdout


@needs_boundary
def test_sandbox_check_proves_each_role_cannot_read_or_write_outside_its_folder(tmp_path: Path):
    session = prepare(tmp_path)
    archive = tmp_path / "private-archive"
    archive.mkdir()
    (archive / "old-scene.md").write_text("closed creative record", encoding="utf-8")
    protected = {"bible": tree(tmp_path / "bible"), "archive": tree(archive), "manifest": (session / "manifest.json").read_bytes()}
    report = room.sandbox_check(argparse.Namespace(session=str(session), deny=[str(archive), str(archive / "old-scene.md")]))
    assert report["model_calls_started"] is False
    assert sorted(report["roles"]) == sorted(KEYS)
    for key, role in report["roles"].items():
        assert role["violations"] == [] and role["errors"] == [], (key, role)
        assert role["ok"] is True and role["passed"] == role["checks"] >= 20
        # paths that exist on the host and must be invisible: these are the meaningful checks
        for must_exist in (session / "manifest.json", session / "roles", session / "assignment.md", tmp_path / "bible",
                           archive, toolkit_root() / "slop", toolkit_root() / "templates"):
            assert str(must_exist) not in role["not_present_on_host"]
        assert any(line.startswith("info runtime node v") for line in role["info"])   # and the runtime still works inside
    assert report["ok"] is True
    assert {"bible": tree(tmp_path / "bible"), "archive": tree(archive),
            "manifest": (session / "manifest.json").read_bytes()} == protected
    assert room._pilot_problems(session, load(session), require_runtime=False) == []  # the check leaves the session runnable


@needs_boundary
def test_runtime_preflight_asks_the_runner_inside_the_boundary_whether_each_cli_can_start(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    manifest = load(session)
    runner = (toolkit_root() / "runner" / "deleg.mjs").resolve()
    roles = room._plan_roles(PLAN)
    monkeypatch.setenv("DELEG_CLAUDE_BIN", json.dumps(["/bin/sh", "stand-in"]))
    monkeypatch.setenv("DELEG_CODEX_BIN", json.dumps(["/bin/sh", "stand-in"]))
    assert room._engines_unreachable(manifest["plan"]["sandbox"], runner, manifest["models"]["roles"], roles) == []
    assert room._pilot_problems(session, manifest, require_runtime=True) == []
    unknown = {role: {"engine": "no-such-engine"} for role in roles}
    problems = room._engines_unreachable(manifest["plan"]["sandbox"], runner, unknown, roles)
    assert len(problems) == 1 and "cannot be started inside the filesystem boundary" in problems[0]
    assert not (session / "jobs").exists()


@needs_boundary
def test_the_boundary_fails_the_check_when_it_is_configured_to_expose_a_protected_path(tmp_path: Path):
    """Negative control: sandbox-check must notice a boundary that leaks."""
    session = prepare(tmp_path)
    manifest = load(session)
    manifest["plan"]["sandbox"]["read_only"].append(str(tmp_path / "bible"))
    save(session, manifest)
    report = room.sandbox_check(argparse.Namespace(session=str(session), deny=None))
    assert report["ok"] is False
    assert all(any(str(tmp_path / "bible") in line for line in role["violations"]) for role in report["roles"].values())


FAKE_CLI = r"""#!/bin/sh
# A stand-in for the claude/codex CLI. It makes no model call. From inside whatever boundary the orchestrator
# gave it, it tries what a misbehaving agent might: reading and changing things it was never given.
# Reads are judged here, inside the view. Writes are only attempted here; the test judges them from outside,
# against the real files, because the boundary's empty placeholder directories accept writes that reach nothing.
job="$(pwd)"
log="$job/boundary.log"
: > "$log"
while IFS= read -r target; do
  kind="${target%%:*}"; path="${target#*:}"
  case "$kind" in
    read) if [ -e "$path" ] || ls "$path" >/dev/null 2>&1 || cat "$path" >/dev/null 2>&1; then echo "VIOLATION readable $path" >> "$log"; else echo "ok hidden $path" >> "$log"; fi ;;
    write) ( echo tampered >> "$path" ) 2>/dev/null; echo "tried write $path" >> "$log" ;;
    create) ( : > "$path/planted-by-agent" ) 2>/dev/null; echo "tried create $path" >> "$log" ;;
  esac
done < "$job/boundary-targets"
ls -A "$HOME" > "$job/home-listing" 2>&1
printf 'SCENE-FROM-%s\n' "$(basename "$job")" > "$job/outbox/result.md"
as="$1"; shift
exec node "$FAKE_ENGINE" --as "$as" -- "$@"
"""


@needs_boundary
def test_end_to_end_real_runner_inside_the_boundary_with_an_adversarial_fake_cli(tmp_path: Path, monkeypatch):
    """The whole pilot through the real sandboxed executor and the real runner; only the model CLI is fake."""
    session = prepare(tmp_path)
    archive = tmp_path / "writers-room" / "private-archive"      # beside the sessions folder, like the real one
    archive.mkdir()
    (archive / "old-scene.md").write_text("closed creative record", encoding="utf-8")
    runner = (toolkit_root() / "runner" / "deleg.mjs").resolve()
    real = room.SandboxedRunnerExecutor(runner, load(session)["plan"]["sandbox"])
    protected_files = [tmp_path / "bible" / "canon.md", tmp_path / "bible" / "voice.md", session / "manifest.json",
                       session / "roles" / "showrunner.md", session / "assignment.md", archive / "old-scene.md",
                       toolkit_root() / "slop" / "writers_room.py", runner,
                       toolkit_root() / "templates" / "writers-room" / "plans" / PLAN / "roles" / "showrunner.md"]
    protected_dirs = [tmp_path / "bible", session, session / "roles", archive, toolkit_root(), runner.parent,
                      toolkit_root() / "slop"]
    before = {"bible": tree(tmp_path / "bible"), "archive": tree(archive), "roles": tree(session / "roles"),
              "runner": tree(runner.parent), "source": tree(toolkit_root() / "slop")}
    seen: list[str] = []

    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job["title"])
        others = [path for path in (session / "jobs").glob("*/*") if path != job_dir]      # earlier roles' job folders
        published = list((session / "artifacts").rglob("*.md"))                            # earlier roles' outputs
        # The runner is a necessary read-only dependency, and the session and checkout roots appear inside the
        # view only as empty placeholders, so those are write-tested but not read-tested.
        visible = {runner, runner.parent, session, toolkit_root()}
        targets = [f"read:{path}" for path in protected_files + protected_dirs + others + published if path not in visible]
        targets += [f"write:{path}" for path in protected_files + published]
        targets += [f"create:{path}" for path in protected_dirs + others]
        (job_dir / "boundary-targets").write_text("\n".join(targets) + "\n", encoding="utf-8")
        cli = job_dir / "fake-cli.sh"
        cli.write_text(FAKE_CLI, encoding="utf-8")
        engine = "DELEG_CLAUDE_BIN" if job["engine"] == "claude" else "DELEG_CODEX_BIN"
        monkeypatch.setenv(engine, json.dumps(["/bin/sh", str(cli), job["engine"]]))
        monkeypatch.setenv("FAKE_ENGINE", str(runner.parent / "fake-engine.mjs"))
        real(job_dir, job)

    result = room.run_session(session, executor=executor)
    assert seen == ORDER and result["status"] == "awaiting_owner_approval"
    engines = set()
    for key, entry in jobs(session).items():
        job_dir = session / entry["job_dir"]
        assert json.loads((job_dir / "status.json").read_text())["state"] == "done"       # the real runner's own record
        log = (job_dir / "boundary.log").read_text(encoding="utf-8").splitlines()
        wanted = (job_dir / "boundary-targets").read_text(encoding="utf-8").split()
        assert [line for line in log if line.startswith("VIOLATION")] == [], key
        assert len(log) == len(wanted) and all(line.startswith(("ok hidden ", "tried ")) for line in log)
        assert sum(line.startswith("ok hidden ") for line in log) == sum(item.startswith("read:") for item in wanted)
        assert entry["state"] == "completed"
        assert (session / entry["artifact"]["path"]).read_text() == f"SCENE-FROM-{job_dir.name}\n"
        calls = [json.loads(line) for line in (job_dir / "fake-calls.jsonl").read_text().splitlines()]
        assert len(calls) == 1                                                             # one CLI start per role
        engines.add(calls[0]["as"])
    assert engines == {"claude", "codex"}
    # later roles really had earlier roles' folders and outputs in their target lists
    showrunner_targets = (session / jobs(session)[SHOWRUNNER]["job_dir"] / "boundary-targets").read_text()
    for needle in ("jobs/pilot-1-writing/character-dialogue", "jobs/pilot-2-assessment/comedy-editor",
                   "artifacts/pilot-1-writing/satire-observation.md", "artifacts/pilot-2-assessment/comedy-editor.md",
                   "manifest.json"):
        assert f"read:{session / needle}" in showrunner_targets
    assert {"bible": tree(tmp_path / "bible"), "archive": tree(archive), "roles": tree(session / "roles"),
            "runner": tree(runner.parent), "source": tree(toolkit_root() / "slop")} == before
    assert not list(tmp_path.rglob("planted-by-agent")) and not list(toolkit_root().rglob("planted-by-agent"))
    for path in protected_files + list((session / "artifacts").rglob("*.md")):
        assert b"tampered" not in path.read_bytes(), path
    assert load(session)["plan"]["scene_labels"]                 # the manifest is intact and was never the agent's to edit


@needs_boundary
def test_without_the_boundary_the_same_fake_cli_does_reach_protected_files(tmp_path: Path, monkeypatch):
    """Control for the end-to-end test: the plain runner executor (the standard room's) confines nothing."""
    session = prepare(tmp_path)
    runner = (toolkit_root() / "runner" / "deleg.mjs").resolve()
    victim = tmp_path / "bible-copy.md"
    victim.write_text("authoritative", encoding="utf-8")
    plain = room.RunnerExecutor(runner)

    def executor(job_dir: Path, job: dict) -> None:
        (job_dir / "boundary-targets").write_text(f"read:{session / 'manifest.json'}\nwrite:{victim}\n", encoding="utf-8")
        cli = job_dir / "fake-cli.sh"
        cli.write_text(FAKE_CLI, encoding="utf-8")
        monkeypatch.setenv("DELEG_CLAUDE_BIN", json.dumps(["/bin/sh", str(cli), "claude"]))
        monkeypatch.setenv("FAKE_ENGINE", str(runner.parent / "fake-engine.mjs"))
        plain(job_dir, job)
        raise Crash()                                            # one job is enough

    with pytest.raises(Crash):
        room.run_session(session, executor=executor)
    log = (session / "jobs" / "pilot-1-writing" / "character-dialogue" / "boundary.log").read_text()
    assert "VIOLATION readable" in log and "tampered" in victim.read_text()


@needs_boundary
def test_no_job_process_outlives_a_killed_orchestrator(tmp_path: Path):
    """Recovery treats a job found 'running' after a restart as dead. That is only sound if this holds."""
    session = prepare(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    runner_dir = (toolkit_root() / "runner").resolve()
    argv = room._sandbox_argv(load(session)["plan"]["sandbox"], job_dir, runner_dir)
    # the "orchestrator": starts a detached, long-lived process tree inside the boundary, as the runner does
    nap = f"sleep {40000 + os.getpid()}"                      # a command line no other process will have
    inner = f"setsid sh -c 'echo $$ > pid-inside; exec {nap}' & {nap}"
    orchestrator = subprocess.Popen([sys.executable, "-c",
                                     "import subprocess, sys; subprocess.run(sys.argv[1:])", *argv, "/bin/sh", "-c", inner])
    try:
        deadline = time.time() + 20
        def sleepers():
            out = subprocess.run(["ps", "-eo", "pid,args"], text=True, capture_output=True).stdout
            return [line for line in out.splitlines() if nap in line]
        while not (job_dir / "pid-inside").exists() and time.time() < deadline:
            time.sleep(0.1)
        assert (job_dir / "pid-inside").exists() and len(sleepers()) >= 2
        orchestrator.kill()                                   # SIGKILL: no cleanup code gets to run
        orchestrator.wait()
        while sleepers() and time.time() < deadline:
            time.sleep(0.1)
        assert sleepers() == []
    finally:
        orchestrator.kill()
