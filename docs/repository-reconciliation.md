# Repository reconciliation and authority

## Findings

The configured GitHub remote is `https://github.com/skotlander/agentic-bull-studio.git`. It is public. After fetching, local `main` and GitHub `main` both pointed to `6071aed58a8e00d6a4e496cb9ef5012be188a026` with no divergence. That commit is Claudia 0.3.6; it did not contain the new writers' room. No open pull request or other remote branch contained that source at inspection time.

The writers' room existed only as an unstaged change to `slop/cli.py` plus untracked implementation, role/configuration/assignment files, tests, documentation and local sessions. A remote checkout at the original main commit therefore could not run it.

## Reconciled branches

- `codex/writers-room-source`: reusable source and operating documentation, intended for the public GitHub repository and its pull request. Until merged, GitHub's default `main` remains the upstream toolkit baseline. Use the PR/source branch to inspect the room implementation.
- `codex/writers-room-private-records`: local-only archive based on that source commit. It preserves the full owner-supplied assignment and curated Markdown/JSON session records, including the closed corrective pass and the unapproved character references. It has no upstream; do not publish it to the public remote.
- `main`: preserved rather than reset or silently overwritten.

For current revisions, inspect `git branch -vv`, `git log -1 codex/writers-room-source`, `git log -1 codex/writers-room-private-records`, and `git rev-list --left-right --count codex/writers-room-source...origin/codex/writers-room-source`.

## Which files govern what

| Subject | Authority / source | Status |
| --- | --- | --- |
| Room implementation and public CLI | This checkout's `slop/writers_room.py`, registered by `slop/cli.py`; shared `runner/deleg.mjs` | Executable source, versioned on the source branch |
| Reusable roles, model defaults and generic assignment | `templates/writers-room/` excluding the local owner brief | Versioned defaults; prepared manifests snapshot their own inputs |
| Agentic Bull series/character/visual canon | `C:\Users\skort\Projects\agentic-bull-media\bible\` | Separate authoritative repository, tracked at `e74d3e938819015d406a57789fda3419c65ff806` at inspection |
| Updated Game-host creative direction | Local `original-human-ensemble-game-host.md` and session `002/assignment.md` | Owner assignment direction; not a Bible revision |
| Creative proposals and editor reports | Local session artifacts and corrective-pass package | Development records, not canon or audience validation |
| Current owner disposition | `002/owner-followups/corrective-pass-001/owner-outcome.md` and follow-up `manifest.json` | Closed with creative criteria unmet; references unapproved, three cards shelved, decisions deferred, no further development authorized |
| Historical parent-session status | Session `002/manifest.json` says `awaiting_owner_approval` | Preserved run history; later owner outcome governs current disposition |

The media repository has no configured remote. Its Bible is already locally committed; this reconciliation does not invent a GitHub authority for it or change its files. Its pre-existing untracked episode assets and derived references are untouched.

## What is deliberately excluded

Public source excludes `writers-room/sessions/` and the full owner-supplied Game-host brief. The curated local archive includes relevant Markdown/JSON records, not raw job directories, model streams, cached prompts, generated previews or private credentials. Raw jobs and all original runtime files remain on disk. Exclusion from Git does not delete or approve them.

No model invocation, new creative assignment, Bible change, Game change, production run, automatic publication of creative material or canon approval is part of this reconciliation.

## Entry points

See [the operating guide](writers-room.md) for exact WSL commands, runtime requirements, the completed-session no-op behavior, the distinction between room-level retry and runner-level recovery, and the internal-only mechanism used for the now-closed corrective pass.
