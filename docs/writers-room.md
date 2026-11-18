# Business as Unusual writers' room

## Authority and current disposition

The creative authority is the separate `agentic-bull-media/bible/` directory, with the series definition established in commit `e74d3e938819015d406a57789fda3419c65ff806`. The six files consumed by the room are `series.md`, `canon.md`, `character.md`, `world.md`, `visual.md`, and `story.md`. The other Bible documents remain relevant authority when their subjects apply.

Owner assignment briefs supplement that authority; they do not rewrite the Bible. Writer output, editorial assessments, successful runs, and generated media do not establish canon.

The latest owner decision closes session `original-human-ensemble-002`'s `corrective-pass-001` with creative criteria unmet. The three human profiles remain unapproved development references; all three proof cards are shelved. Name, gathering-ritual, and proposed Bull-trait decisions are deferred. No further A revision or alternative-ensemble development is authorized. Future development requires a separate owner decision.

The original session manifest still says `awaiting_owner_approval`: that historical state was preserved. For current disposition, consult the later follow-up's `owner-outcome.md` and `manifest.json` (`closed`). Neither the parent status nor the existence of a reusable template authorizes another run.

## Source map and execution entry points

| Entry point / file | Purpose |
| --- | --- |
| `./bin/slop` | WSL/Linux checkout launcher. Sets this checkout on PYTHONPATH, then uses Claudia's venv or a Python 3.11+ fallback. |
| `slop/__main__.py` | Supports `python3 -m slop` from a suitable checkout/environment. |
| `pyproject.toml` | Installed `slop` command points to `slop.cli:main`. The inherited package name is still `claudia`. |
| `slop/cli.py` | Registers the `writers-room` command through `slop.writers_room`. |
| `slop/writers_room.py` | Implements `prepare`, `status`, and `run`; creates prompts, sequences roles, saves artifacts and checks Bible fingerprints. |
| `templates/writers-room/roles/*.md` | Six reusable role definitions: three writers, two editors, Showrunner. |
| `templates/writers-room/models.json` | Default engine/model/reasoning preferences, copied into each prepared session. Selectors are passed to the local CLI, not validated against account entitlement here. |
| `templates/writers-room/assignments/original-human-ensemble.md` | Public reusable ensemble-development assignment. |
| `runner/deleg.mjs` | Existing local job runner, invoked once per role turn by `RunnerExecutor`; launches Claude Code or Codex and records job status/logs. |
| `tests/test_writers_room.py` | Mock-driven checks of authority loading, role sequencing, permissions, artifacts, failures and launch authorization. |
| `writers-room/sessions/` | Local runtime records: assignment snapshots, manifests, proposals, reviews, reports, jobs and logs. Excluded from the public source branch. |

The owner-supplied updated Game-host assignment, `original-human-ensemble-game-host.md`, is a local creative brief, preserved in the local archive rather than distributed with public source. Public clones should supply their own authorized Markdown assignment through `--assignment /path/to/brief.md`.

The room uses `runner/deleg.mjs` for separate CLI invocations and fresh job folders. `slop/relay.py` resumes a production conversation for the Grok-facing relay; it is not the writers' room coordinator. No rendering, database, cloud service or new provider SDK is involved.

## Read-only commands for the existing records

Use Ubuntu/WSL for these sessions: their manifests contain `/mnt/c/...` paths. Do not run their manifests directly from native Windows.

```bash
cd /mnt/c/Users/skort/Projects/agentic-bull-studio
./bin/slop writers-room --help
./bin/slop --json writers-room status writers-room/sessions/original-human-ensemble-001
./bin/slop --json writers-room status writers-room/sessions/original-human-ensemble-002
./bin/slop --json agents status
```

Session `001` is the older prepared assignment, not the updated owner brief. Session `002` completed the full room and then received one separately authorized corrective pass. Its final disposition is in:

```text
writers-room/sessions/original-human-ensemble-002/owner-followups/corrective-pass-001/owner-outcome.md
writers-room/sessions/original-human-ensemble-002/owner-followups/corrective-pass-001/manifest.json
```

These commands inspect records or authentication status. Do not run `agents test` or runner `preflight` as a read-only check: they invoke models.

## Preparing and running a future, separately authorized assignment

These are reference examples, not authorization to reopen the closed work. Choose a new session ID and an owner-authorized brief.

```bash
./bin/slop writers-room prepare \
  --room-dir ./writers-room \
  --bible-dir /mnt/c/Users/skort/Projects/agentic-bull-media/bible \
  --assignment /absolute/path/to/authorized-brief.md \
  --session-id NEW_SESSION_ID
```

`prepare` makes no model calls. It snapshots the assignment and model configuration and fingerprints the external Bible. Optional `--models /path/to/config.json` supplies a complete replacement role mapping.

Only after explicit owner authorization to execute:

```bash
./bin/slop writers-room run \
  writers-room/sessions/NEW_SESSION_ID \
  --authorize-model-calls
```

The phases are fixed: three independent writer proposals; three collaborative writer developments plus Showrunner packet; two independent editorial reviews; one Showrunner revision plan and zero to three writer revisions; final Showrunner report. All outputs remain provisional.

## Local execution requirements and boundaries

Python 3.11+ and Node 18.17+ are needed for the room/runner. The existing Python 3.12 fallback and Node 20 installation have executed this room. Claudia's full managed venv, FFmpeg and renderer are not requirements for these jobs; the generic runner doctor may still flag missing production tools.

Authenticate the Claude and Codex CLIs inside the same WSL user environment used by the room. The runner searches Claudia's managed bin directory before PATH; this avoids accidentally choosing a Windows app binary from WSL PATH. IDE/app authentication alone is not proof of CLI readiness.

Claude room jobs explicitly select `acceptEdits` because the installed CLI rejects the shared runner's legacy `default` permission mode. Codex jobs select `workspace-write`. The runner strips API-billing environment variables by default and forces ChatGPT login for Codex plan runs. The room does not pass API-billing or external-spend grants.

A full session has 11-14 role invocations, not 11-14 underlying API requests: each CLI may use several model/tool turns. Token usage, rate limits and plan cost vary. No offline selector check proves model entitlement or comedy quality.

## Continuation and failure limitations

- The public CLI has no `continue`, owner-decision, archive, approval or bounded-follow-up subcommand.
- `run` on an `awaiting_owner_approval` session returns its existing report without new work.
- Completed artifacts are reused. A failed session requires `--retry-failed`, but that flag alone does not reset a terminal failed delegated job; the underlying runner has its own `resume` and `start --fresh` controls. Inspect both layers before any authorized recovery.
- The previous corrective pass used existing internal Python helpers (`_prompt`, `_run_role`, `RunnerExecutor`) with separate per-role directories, then two editors and one final Showrunner. It did not restart the five phases or modify application code. Those helpers are implementation interfaces, not a supported continuation CLI.
- That follow-up is closed. Its six role invocations are complete. No command or automation should infer permission for another development round.
- Prompt isolation is provided by separate sessions and supplied packets; the reused runner also grants access to the toolkit checkout. It is not a strong filesystem confidentiality boundary between writers.

## Version control and records

The public GitHub source branch contains implementation, CLI registration, reusable roles/configuration, the generic assignment, tests and these operating notes. The original upstream toolkit and package metadata remain intact; upstream Claudia/gallery authority applies to Claudia, not to Agentic Bull.

Owner briefs, creative outputs, session manifests, raw job streams, local authentication and media are not public source. A local-only Git archive branch preserves selected creative records and the closed owner decision. It has no upstream and must not be pushed to the public remote. See [reconciliation notes](repository-reconciliation.md) for exact branch and authority boundaries. Raw runtime job logs remain local and are not included in that curated archive.

The tracked local archive is a local recovery copy, not an off-device backup. A private remote or other private backup would require a separate owner choice; no private service or repository is created here.

## Focused verification

From the checkout, using a Python environment that already has pytest:

```bash
python -m pytest tests/test_writers_room.py -q
```

The focused suite uses fixtures and a fake executor. It makes no model calls. The public source tests do not imply approval of any generated material.
