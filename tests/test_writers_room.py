"""Focused, no-model-call verification for the Business as Unusual writers' room."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from slop import writers_room as room
from slop.config import SlopError


def bible(tmp_path: Path) -> Path:
    root = tmp_path / "bible"
    root.mkdir()
    for name in room.REQUIRED_BIBLE:
        (root / name).write_text(f"# {name}\nAuthoritative fixture.\n", encoding="utf-8")
    return root


def prepare(tmp_path: Path) -> Path:
    result = room.prepare(argparse.Namespace(
        room_dir=str(tmp_path / "writers-room"), bible_dir=str(bible(tmp_path)),
        assignment="original-human-ensemble", models=None, session_id="ensemble-test",
    ))
    assert result["model_calls_started"] is False
    return Path(result["session"])


def test_prepare_fingerprints_all_authoritative_bible_files_and_never_copies_them(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "prepared"
    assert {item["name"] for item in manifest["authority"]["files"]} == set(room.REQUIRED_BIBLE)
    assert not list(session.rglob("series.md")), "the room should reference, not duplicate, the Bible"
    assert "provisional" in (session / "assignment.md").read_text(encoding="utf-8").lower()


def test_model_configuration_has_all_roles_and_preserves_requested_family_diversity(tmp_path: Path):
    session = prepare(tmp_path)
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    roles = manifest["models"]["roles"]
    assert set(roles) == set(room.ROLES)
    assert roles["character-dialogue"]["engine"] == "claude"
    assert roles["situational-comedy"]["engine"] == "codex"
    assert all(spec["reasoning"] == "high" for spec in roles.values())


def test_run_orders_isolation_collaboration_editing_bounded_revision_and_preserves_artifacts(tmp_path: Path):
    session = prepare(tmp_path)
    seen: list[dict] = []

    def fake_executor(job_dir: Path, job: dict) -> None:
        seen.append(job)
        title = job["title"]
        outbox = job_dir / "outbox"
        outbox.mkdir(exist_ok=True)
        if title == "phase-d: showrunner":
            (outbox / "result.json").write_text(json.dumps({
                "revise_roles": ["character-dialogue", "satire-observation"],
                "brief": "Sharpen the two strongest contrasts.", "reasoning": "Specific stakes are missing.",
            }), encoding="utf-8")
        else:
            (outbox / "result.md").write_text(f"# {title}\nSpecific creative work.", encoding="utf-8")

    result = room.run_session(session, executor=fake_executor)
    assert result["status"] == "awaiting_owner_approval"
    assert all(job.get("permission_mode") == "acceptEdits" for job in seen if job["engine"] == "claude")
    assert all("permission_mode" not in job for job in seen if job["engine"] == "codex")
    titles = [job["title"] for job in seen]
    assert titles == [
        "phase-a: character-dialogue", "phase-a: situational-comedy", "phase-a: satire-observation",
        "phase-b: character-dialogue", "phase-b: situational-comedy", "phase-b: satire-observation", "phase-b: showrunner",
        "phase-c: comedy-editor", "phase-c: character-editor", "phase-d: showrunner",
        "phase-d: character-dialogue", "phase-d: satire-observation", "phase-e: showrunner",
    ]
    phase_a_briefs = [job["brief"] for job in seen[:3]]
    assert all("## Collaborative packet" not in brief for brief in phase_a_briefs)
    assert "phase-a: character-dialogue" in seen[3]["brief"]
    assert "phase-c: comedy-editor" in seen[9]["brief"]
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["owner_approval"] == {"required": True, "recorded": False,
                                          "note": "This session cannot modify the Media Bible or establish canon."}
    assert (session / "artifacts" / "phase-a" / "character-dialogue.md").is_file()
    assert (session / "artifacts" / "phase-c" / "comedy-editor.md").is_file()
    assert (session / "artifacts" / "phase-e" / "showrunner.md").is_file()


def test_empty_or_missing_deliverable_fails_without_inventing_material(tmp_path: Path):
    session = prepare(tmp_path)

    def silent_executor(job_dir: Path, job: dict) -> None:
        return None

    with pytest.raises(SlopError, match="required inspectable deliverable"):
        room.run_session(session, executor=silent_executor)
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["failure"]["phase"] == "phase-a"
    assert manifest["failure"]["role"] == "character-dialogue"

    def still_broken(job_dir: Path, job: dict) -> None:
        raise RuntimeError("fixture interruption")

    with pytest.raises(RuntimeError, match="fixture interruption"):
        room.run_session(session, executor=still_broken, retry_failed=True)
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["failure_history"]) == 1
    assert manifest["failure"]["role"] == "character-dialogue"


def test_bible_change_blocks_a_prepared_session(tmp_path: Path):
    session = prepare(tmp_path)
    fixture_bible = tmp_path / "bible"
    (fixture_bible / "series.md").write_text("changed", encoding="utf-8")
    with pytest.raises(SlopError, match="Bible changed"):
        room.run_session(session, executor=lambda *_: None)


def test_cli_run_requires_explicit_model_call_authorization(tmp_path: Path):
    session = prepare(tmp_path)
    with pytest.raises(SlopError, match="authorize-model-calls"):
        room.run(argparse.Namespace(session=str(session), authorize_model_calls=False))


def test_cli_registers_the_room_without_loading_unrelated_optional_modules(monkeypatch):
    monkeypatch.setenv("SLOP_MODULES", "slop.writers_room")
    from slop import cli
    parsed = cli.build_parser().parse_args([
        "writers-room", "prepare", "--room-dir", "room", "--bible-dir", "bible",
    ])
    assert parsed.func is room.prepare
