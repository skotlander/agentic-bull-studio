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

## Comedy Lab pilot plan (opt-in)

`prepare --plan comedy-lab-pilot` records an alternative, smaller execution plan in the session manifest. With no `--plan`, a session is the standard five-phase room and nothing about it changes. A manifest that names a plan this code does not know is refused; it never falls back to the standard room.

The pilot is exactly five role invocations and then stops at `awaiting_owner_approval`:

1. `pilot-1-writing`: `character-dialogue`, `situational-comedy`, `satire-observation`. Each writes one scene from the brief and its own role text. No writer's prompt contains another's work.
2. `pilot-2-assessment`: `comedy-editor` receives the three scenes as `Scene 1`, `Scene 2`, `Scene 3`.
3. `pilot-3-selection`: `showrunner` receives the same labelled scenes, then the editor's assessment after them.

There is no collaborative round, character editor, revision plan, revision, or follow-up phase in this plan.

What `prepare` does differently for the plan:

- `--assignment` must be an existing Markdown (`.md`) file. Bundled assignment names are refused for a plan. Keep the brief and any casting sheet in the ignored `writers-room/private/` folder; they are not public source.
- Role instructions come from `templates/writers-room/plans/comedy-lab-pilot/roles/`. Their text and SHA-256 are copied into the manifest and into `roles/` inside the session. `run` uses only that snapshot.
- `voice.md` is required and fingerprinted with the six standard Bible files, embedded in every prompt, and re-verified before every prompt.
- Default models come from `templates/writers-room/plans/comedy-lab-pilot/models.json`; the comedy editor and showrunner use different families. `--models` still replaces it.
- A random scene-label mapping is stored in the manifest as `plan.scene_labels`. It is provenance for the owner and is never placed in a prompt or mounted into a role's filesystem view.
- The filesystem boundary configuration (`plans/comedy-lab-pilot/sandbox.json`) is copied into the manifest and fingerprinted.
- A job ledger (`plan.jobs`) is created with the five invocations, each `never_started`.

```bash
./bin/slop writers-room prepare \
  --plan comedy-lab-pilot \
  --room-dir /path/to/clean-room \
  --bible-dir /mnt/c/Users/skort/Projects/agentic-bull-media/bible \
  --assignment /path/to/private/assignment.md \
  --session-id NEW_SESSION_ID
./bin/slop --json writers-room status /path/to/clean-room/sessions/NEW_SESSION_ID
./bin/slop --json writers-room sandbox-check /path/to/clean-room/sessions/NEW_SESSION_ID \
  --deny /mnt/c/Users/skort/Projects/agentic-bull-studio/writers-room
```

None of those three commands starts a CLI or a model. Only after explicit owner authorization to execute:

```bash
./bin/slop writers-room run /path/to/clean-room/sessions/NEW_SESSION_ID --authorize-model-calls
```

Scenes are written to `artifacts/pilot-1-writing/`. Read them before the assessment and selection.

### Preflight: everything is checked before the first invocation

`run` validates the whole session before any role starts, and `status` reports the same list as `preflight_problems`. A single problem stops the run with no job folder created. Checked: the plan name and its five-step sequence; all five role snapshots (manifest text, fingerprint, and the copy on disk); the seven recorded Bible entries including `voice.md`, and their current fingerprints; the prepared assignment, its fingerprint, its copy on disk, and that its source was a Markdown file; the model entry for every role, and that editor and showrunner differ in family; the scene-label mapping; the sandbox configuration fingerprint; the clean-context rule below; that the ledger lists exactly the five jobs; and that no job folder or artifact already exists for a job the ledger says never started. On the real execution path it also checks that the runner, `node` and `bwrap` are present, and asks the runner, from inside the boundary, whether it can locate each CLI the plan needs (`deleg.mjs _resolve`, which starts nothing).

### Isolation: an enforced filesystem boundary

Each pilot job runs the existing runner inside [bubblewrap](https://github.com/containers/bubblewrap) (`sudo apt install bubblewrap` on Ubuntu/WSL). The runner itself is unchanged. The pilot refuses to run without `bwrap`; there is no unsandboxed fallback.

What one role's process tree can see:

- system directories (`/usr`, `/etc`, and the `/bin`, `/lib` family), read-only;
- the `runner/` folder, read-only;
- the runtime paths listed under `read_only` in `sandbox.json` (CLI and Node install locations), read-only, where they exist;
- the CLI sign-in paths listed under `read_write` (`~/.claude`, `~/.claude.json`, `~/.codex`), with the transcript and history locations listed under `masked` replaced by empty ones;
- its own job folder, read-write;
- an empty private home and `/tmp`.

Nothing else is mounted. The session manifest (and so the scene-label mapping), `roles/`, `assignment.md`, `artifacts/`, every other job folder, the rest of the checkout, the Bible directory, the private archive and the Windows drives do not exist inside the view, so they can be neither read nor changed. The Bible and brief reach a role only as text in its prompt. The editor and showrunner receive scenes only as anonymous text in their prompts.

Paths that sit above a mounted folder (the session folder, the checkout root) appear inside the view as empty placeholder directories. A write there goes to throwaway memory and reaches nothing real.

`sandbox-check` proves this for a prepared session without any model: for each of the five roles it starts a real process inside that role's boundary which tries to read the manifest, role snapshots, assignment, artifacts, job folders, toolkit source, templates, session archive, the Bible directory, the other roles' working folders and every `--deny` path, and tries to write into the Bible, the runner, the checkout, the session folder and `/usr`. Reads are judged inside the view; writes are judged from outside against the real directories. It also reports which of those paths did not exist on the host (absence inside proves nothing for those), the runtime versions visible inside, and the contents of the private home. Run it in the environment that will execute the pilot, and pass the real private archive path with `--deny`.

The clean-context rule remains as a second safeguard: a pilot session refuses to run if any other session folder exists in its own room directory or under this checkout's `writers-room/sessions/`. Run it from a separate clean checkout of the public source with its own `--room-dir`.

Limits of the boundary, stated plainly:

- It confines the filesystem, not the network. The CLIs need the network to reach their models.
- `~/.claude` and `~/.codex` must be writable for the CLIs to stay signed in. The `masked` list hides the transcript and history locations known at the time of writing; a CLI version that stores other sessions' text elsewhere under those folders would expose it. Check the `info home` line and those folders before a run that matters.
- Whether each signed-in CLI actually authenticates and runs inside the boundary can only be shown by running it, which is a model call. Preflight confirms the CLI can be located, not that its sign-in works.
- It depends on unprivileged user namespaces being available to `bwrap` in the WSL distribution.

### One orchestrator, a verified ledger, and no automatic re-attempts

- **One orchestrator.** `run` takes `SESSION/.orchestrator.lock` (created exclusively, holding the process id and host) before reading the ledger and releases it on exit. A second `run` reports the live orchestrator and starts nothing. A lock left by a process that no longer exists is never cleared silently: `run` stops and asks for `--clear-stale-lock`. A lock from another host is never cleared by the command.
- **Job states.** Each of the five jobs is `never_started`, `running`, `completed`, `failed` or `uncertain` in `plan.jobs`. The state is written before the job folder is created, so a job can never be mistaken for unstarted after an interruption.
- **Completion needs provenance.** A job is `completed` only when the runner's own `status.json` says `done`, the job folder's `BRIEF.md` is the prompt this session recorded for that job, and `outbox/result.md` is a non-empty regular file inside the job folder. Symbolic links in a job folder are refused, so a role cannot have the orchestrator read some other file on its behalf. The result is then written to a `.partial` file, flushed, renamed into `artifacts/`, and only after that recorded in the ledger with its SHA-256. A file in `artifacts/` without that ledger entry is not a completed job. A completed artifact that no longer matches its fingerprint stops the run.
- **Reconcile first.** On every `run`, a job that is not `never_started` or `completed` is checked against the runner's record before anything else. If the record proves completion, the result is published and the run continues without a new invocation. This covers an interruption between the model finishing and the artifact being published or recorded.
- **Nothing is re-attempted automatically.** A job that failed, stopped, was lost, or whose completion cannot be proven stops the session and stays stopped. `--retry-failed` reconciles and continues but never starts a new attempt of such a job. Because a job's processes share a PID namespace that dies with the orchestrator, a job found `running` after a restart is dead, and is recorded `uncertain`.
- **Owner-authorized new attempt.** `--authorize-new-attempt PHASE/ROLE` (for example `pilot-2-assessment/comedy-editor`) permits exactly one new attempt of that one job in that one `run`. It is recorded in `plan.authorized_new_attempts`, runs in a fresh folder (`jobs/PHASE/ROLE.attempt-2`), and leaves the earlier attempt's folder untouched. The authorization is not remembered; a further failure needs a new one.

```bash
# inspect first
./bin/slop --json writers-room status SESSION
# continue after an interruption; starts no new attempt of a failed or uncertain job
./bin/slop writers-room run SESSION --authorize-model-calls --retry-failed
# only with the owner's explicit authorization for that job
./bin/slop writers-room run SESSION --authorize-model-calls --retry-failed \
  --authorize-new-attempt pilot-2-assessment/comedy-editor
```

The runner's own `resume` is deliberately not used for pilot jobs. Calling it from outside would run the job without the boundary, and it would continue a model session without the owner's authorization. A verified finished job is reused; anything else needs the explicit new attempt above.

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
python -m pytest tests/test_writers_room.py tests/test_writers_room_pilot.py -q -rs
```

No test makes a model call. `test_writers_room.py` uses fixtures and a fake executor. `test_writers_room_pilot.py` adds real second-process tests of the session lock and, where `bwrap` and `node` are available, real OS-level tests: `sandbox-check`, the full pilot through the sandboxed executor and the real runner with a fake CLI that attempts prohibited reads and writes, a control showing the same fake CLI does reach protected files without the boundary, and a check that no job process outlives a killed orchestrator. Those tests are skipped with the reason `OS-LEVEL ISOLATION NOT VERIFIED HERE` where the boundary cannot run; `-rs` prints that. A skip is not a pass. The public source tests do not imply approval of any generated material.
