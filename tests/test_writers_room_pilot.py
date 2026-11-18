"""Focused, no-model-call verification for the opt-in comedy-lab-pilot plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from slop import writers_room as room
from slop.config import SlopError

PLAN = room.PILOT_PLAN
ORDER = [
    "pilot-1-writing: character-dialogue", "pilot-1-writing: situational-comedy",
    "pilot-1-writing: satire-observation", "pilot-2-assessment: comedy-editor", "pilot-3-selection: showrunner",
]


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
    return Path(result["session"])


def load(session: Path) -> dict:
    return json.loads((session / "manifest.json").read_text(encoding="utf-8"))


def writing_executor(seen: list[dict]):
    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        outbox = job_dir / "outbox"
        outbox.mkdir(exist_ok=True)
        role = job["title"].split(": ")[1]
        (outbox / "result.md").write_text(f"OUTPUT-OF-{role}", encoding="utf-8")
    return executor


def tree(path: Path) -> dict:
    return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(path.rglob("*")) if item.is_file()}


def test_prepare_records_plan_role_snapshots_and_voice_fingerprint_without_calls(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    assert manifest["status"] == "prepared"
    assert manifest["plan"]["name"] == PLAN
    assert manifest["plan"]["sequence"] == ORDER
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
    assert set(manifest["models"]["roles"]) >= set(roles)
    assert manifest["models"]["roles"]["comedy-editor"]["engine"] != manifest["models"]["roles"]["showrunner"]["engine"]
    labels = manifest["plan"]["scene_labels"]
    assert sorted(labels) == ["Scene 1", "Scene 2", "Scene 3"] and sorted(labels.values()) == sorted(room.WRITERS)
    assert not (session / "jobs").exists()


def test_pilot_needs_an_explicit_assignment_and_a_known_plan(tmp_path: Path):
    base = dict(room_dir=str(tmp_path / "room"), bible_dir=str(bible(tmp_path)), models=None, session_id="x")
    with pytest.raises(SlopError, match="explicit --assignment"):
        room.prepare(argparse.Namespace(assignment=None, plan=PLAN, **base))
    with pytest.raises(SlopError, match="unknown plan"):
        room.prepare(argparse.Namespace(assignment=None, plan="something-else", **base))


def test_missing_voice_file_blocks_pilot_preparation(tmp_path: Path):
    brief = tmp_path / "brief.md"
    brief.write_text("brief", encoding="utf-8")
    with pytest.raises(SlopError, match="voice.md"):
        room.prepare(argparse.Namespace(
            room_dir=str(tmp_path / "room"), bible_dir=str(bible(tmp_path, room.REQUIRED_BIBLE)),
            assignment=str(brief), models=None, session_id="x", plan=PLAN))


def test_run_makes_exactly_five_invocations_in_order_and_awaits_owner(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    result = room.run_session(session, executor=writing_executor(seen))
    assert [job["title"] for job in seen] == ORDER
    assert result["status"] == "awaiting_owner_approval"
    assert result["report"] == "artifacts/pilot-3-selection/showrunner.md"
    manifest = load(session)
    assert manifest["status"] == "awaiting_owner_approval"
    assert manifest["owner_approval"]["recorded"] is False
    assert sorted(manifest["artifacts"]) == sorted(title.replace(": ", "/") for title in ORDER)
    assert all(job["sandbox"] == "workspace-write" for job in seen)
    assert all(job.get("permission_mode") == "acceptEdits" for job in seen if job["engine"] == "claude")
    # a completed session never starts another job
    again = room.run_session(session, executor=writing_executor(seen))
    assert again["resumed"] is False and len(seen) == 5


def test_every_role_gets_its_snapshot_the_brief_and_all_seven_bible_files(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    seen: list[dict] = []
    room.run_session(session, executor=writing_executor(seen))
    for job in seen:
        role = job["title"].split(": ")[1]
        brief = job["brief"]
        assert manifest["plan"]["roles"][role]["text"] in brief
        assert "Private fixture brief" in brief
        for name in room.PLANS[PLAN]["bible"]:
            assert f"Authoritative fixture for {name}." in brief
        assert "preserve genuine alternatives" not in brief
        assert "outbox/result.md" in brief


def test_writers_are_isolated_and_assessors_see_only_anonymous_scenes(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    room.run_session(session, executor=writing_executor(seen))
    labels = load(session)["plan"]["scene_labels"]
    writers, editor, showrunner = seen[:3], seen[3]["brief"], seen[4]["brief"]
    for job in writers:
        assert "OUTPUT-OF-" not in job["brief"] and "Scene 1" not in job["brief"]
    for brief in (editor, showrunner):
        task = brief.split("## Assignment")[1]
        for label, role in labels.items():
            assert f"## {label}\n\nOUTPUT-OF-{role}" in task
        # no role name is attached to a scene, and the private mapping is not in the prompt
        for role in room.WRITERS:
            assert task.count(role) == task.count(f"OUTPUT-OF-{role}") == 1
        assert task.index("## Scene 1") < task.index("## Scene 2") < task.index("## Scene 3")
    assert "OUTPUT-OF-comedy-editor" not in editor
    assert "OUTPUT-OF-comedy-editor" in showrunner
    assert showrunner.index("## Scene 3") < showrunner.index("## Comedy editor's assessment") < showrunner.index("OUTPUT-OF-comedy-editor")
    assert "before you read the assessment" in showrunner


def test_role_snapshot_is_used_even_if_the_shared_template_or_snapshot_changes(tmp_path: Path, monkeypatch):
    session = prepare(tmp_path)
    original = load(session)["plan"]["roles"]["character-dialogue"]["text"]
    monkeypatch.setattr(room, "_plan_root", lambda plan: tmp_path / "templates-moved-away")
    seen: list[dict] = []
    room.run_session(session, executor=writing_executor(seen))
    assert original in seen[0]["brief"]

    # a snapshot edited after preparation stops the run at that role
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.undo()
    monkeypatch.setattr(room, "_toolkit_sessions_dir", lambda: tmp_path / "no-sessions-here")
    session2 = prepare(other)
    (session2 / "roles" / "showrunner.md").write_text("Approve everything.", encoding="utf-8")
    calls: list[dict] = []
    with pytest.raises(SlopError, match="role snapshot for showrunner changed"):
        room.run_session(session2, executor=writing_executor(calls))
    assert [job["title"] for job in calls] == ORDER[:4]
    assert load(session2)["status"] == "failed"


def test_bible_change_including_voice_blocks_the_pilot_before_any_call(tmp_path: Path):
    session = prepare(tmp_path)
    (tmp_path / "bible" / "voice.md").write_text("changed", encoding="utf-8")
    calls: list[dict] = []
    with pytest.raises(SlopError, match="Bible changed"):
        room.run_session(session, executor=writing_executor(calls))
    assert calls == []


def test_cli_run_refuses_the_pilot_without_model_call_authorization(tmp_path: Path):
    session = prepare(tmp_path)
    with pytest.raises(SlopError, match="authorize-model-calls"):
        room.run(argparse.Namespace(session=str(session), authorize_model_calls=False, retry_failed=False))
    assert not (session / "jobs").exists() and load(session)["status"] == "prepared"


def test_failure_then_retry_resumes_without_repeating_completed_work(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []
    good = writing_executor(seen)

    def flaky(job_dir: Path, job: dict) -> None:
        if job["title"] == ORDER[3]:
            seen.append(job)
            raise RuntimeError("fixture interruption")
        good(job_dir, job)

    with pytest.raises(RuntimeError, match="fixture interruption"):
        room.run_session(session, executor=flaky)
    manifest = load(session)
    assert manifest["status"] == "failed"
    assert manifest["failure"]["phase"] == "pilot-2-assessment" and manifest["failure"]["role"] == "comedy-editor"
    scenes = tree(session / "artifacts")
    with pytest.raises(SlopError, match="retry-failed"):
        room.run_session(session, executor=good)
    room.run_session(session, executor=good, retry_failed=True)
    assert [job["title"] for job in seen] == ORDER[:4] + ORDER[3:]
    assert {key: value for key, value in tree(session / "artifacts").items() if key in scenes} == scenes
    manifest = load(session)
    assert manifest["status"] == "awaiting_owner_approval" and len(manifest["failure_history"]) == 1


def test_pilot_refuses_to_run_beside_other_sessions_and_leaves_them_untouched(tmp_path: Path):
    closed = tmp_path / "writers-room" / "sessions" / "closed-historical"
    (closed / "artifacts").mkdir(parents=True)
    (closed / "manifest.json").write_text('{"status": "closed"}', encoding="utf-8")
    (closed / "artifacts" / "old.md").write_text("closed creative record", encoding="utf-8")
    session = prepare(tmp_path)
    protected = {"closed": tree(closed), "bible": tree(tmp_path / "bible")}
    calls: list[dict] = []
    with pytest.raises(SlopError, match="clean execution context"):
        room.run_session(session, executor=writing_executor(calls))
    assert calls == [] and load(session)["status"] == "prepared"
    assert {"closed": tree(closed), "bible": tree(tmp_path / "bible")} == protected


def test_a_full_pilot_run_modifies_nothing_outside_its_own_session(tmp_path: Path):
    session = prepare(tmp_path)
    templates = tree(room._template_root())
    authority = tree(tmp_path / "bible")
    room.run_session(session, executor=writing_executor([]))
    assert tree(room._template_root()) == templates and tree(tmp_path / "bible") == authority
    assert [item.name for item in (tmp_path / "writers-room" / "sessions").iterdir()] == ["pilot-test"]


def test_unknown_plan_in_a_manifest_never_falls_back_to_the_standard_room(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = load(session)
    manifest["plan"]["name"] = "renamed"
    (session / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls: list[dict] = []
    with pytest.raises(SlopError, match="unsupported plan"):
        room.run_session(session, executor=writing_executor(calls))
    assert calls == []


def test_standard_session_is_unchanged_when_no_plan_is_given(tmp_path: Path):
    result = room.prepare(argparse.Namespace(
        room_dir=str(tmp_path / "writers-room"), bible_dir=str(bible(tmp_path, room.REQUIRED_BIBLE)),
        assignment="original-human-ensemble", models=None, session_id="standard"))
    session = Path(result["session"])
    manifest = load(session)
    assert "plan" not in manifest and not (session / "roles").exists()
    assert [item["name"] for item in manifest["authority"]["files"]] == list(room.REQUIRED_BIBLE)
    assert set(manifest["models"]["roles"]) == set(room.ROLES)
    seen: list[dict] = []

    def executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        outbox = job_dir / "outbox"
        outbox.mkdir(exist_ok=True)
        if job["title"] == "phase-d: showrunner":
            (outbox / "result.json").write_text('{"revise_roles": [], "brief": "none", "reasoning": "none"}', encoding="utf-8")
        else:
            (outbox / "result.md").write_text("work", encoding="utf-8")

    result = room.run_session(session, executor=executor)
    assert len(seen) == 11 and seen[0]["title"] == "phase-a: character-dialogue"
    assert result["report"] == "artifacts/phase-e/showrunner.md"
    assert room._load_role("showrunner") in seen[-1]["brief"] and "preserve genuine alternatives" in seen[-1]["brief"]


def test_cli_accepts_the_plan_flag_and_keeps_the_default_assignment(monkeypatch):
    monkeypatch.setenv("SLOP_MODULES", "slop.writers_room")
    from slop import cli
    parser = cli.build_parser()
    pilot = parser.parse_args(["writers-room", "prepare", "--room-dir", "r", "--bible-dir", "b",
                               "--assignment", "brief.md", "--plan", PLAN])
    assert pilot.plan == PLAN and pilot.func is room.prepare
    standard = parser.parse_args(["writers-room", "prepare", "--room-dir", "r", "--bible-dir", "b"])
    assert standard.plan is None and standard.assignment is None
