# Wrappers

Harness wrappers should eventually:

- detect the active folder context
- resolve the active agent
- capture session metadata and transcripts
- hand off completed sessions for summarization

Current implementation:

- `bin/codex`
- `bin/claude`
- `bin/gemini`
- `bin/exocortex-init`
- `bin/exocortex-doctor`
- `exocortex_wrapper.py`
- `doctor.py`

Behavior:

- discovers root/domain/project plus local or agent context from the current folder
- derives a default agent and mode
- routes `wiki/` and `raw/` paths to `knowledge-steward` by default
- writes a per-session manifest and context file
- emits an ExoCortex-owned CLI activity log so wrapper startup, phase transitions, and post-session processing stay visible without overwhelming the terminal
- shows a compact startup summary so agent, mode, level, and context surfaces are explicit in the terminal
- shows the full loaded context file list at startup, grouped by scope in plain language instead of hiding it behind wrapper jargon
- keeps detailed preload/bootstrap mechanics out of the default interactive view and reserves richer detail for verbose/debug modes
- writes a startup context manifest derived from the highest-priority ExoCortex context files
- preloads `wiki/00_meta/Operating Contract.md` for managed `wiki/` and `raw/` work
- injects weighted reusable context from prior sessions when available
- injects a bootstrap context prompt for interactive Claude sessions
- points Codex at the session context manifest via minimal developer instructions, so startup stays fast and Codex does not front-load repo reads
- injects ExoCortex bootstrap context into Gemini even when a user prompt is supplied
- captures a streamed user/tool session log and runs the worker on exit

Builder-specific expectations:

- `builder` is the default execution role for codebase-shaped work contexts
- builder runs in `application` mode by default
- builder sees a narrower context surface than planning/research and does not load
  root personal memory files directly
- builder assumes a lean baseline environment: shell, filesystem, and
  project-local tooling discovered at runtime rather than a pre-baked global setup

To use locally, place `tools/wrappers/bin` ahead of the real harness binaries on `PATH`.

There is also a helper installer:

- `tools/wrappers/install.sh` appends a shell block to `~/.zshrc` by default that prepends the wrapper `bin/` directory and defines `codex`, `claude`, and `gemini` wrapper functions
- `exocortex-init` initializes the clean runtime scaffold in a clone and can scaffold new domains and projects from `templates/`
- run `exocortex-doctor` or `python3 tools/wrappers/doctor.py` to verify that your shell resolves those commands through the wrappers, that authoritative preload is active, and that each wrapper can still reach the underlying CLI
- newly installed wrapper shell blocks also run a low-noise startup health check at most once per day and only print when the doctor detects a problem

Environment overrides:

- `EXOCORTEX_AGENT`
- `EXOCORTEX_MODE`
- `EXOCORTEX_CLI_LOG` (`bar`, `lines`, `off`)
- `EXOCORTEX_CLI_LOG_DETAIL` (`lifecycle`, `inferred`, `verbose`, `debug`)
- `EXOCORTEX_REAL_CODEX`
- `EXOCORTEX_REAL_CLAUDE`
- `EXOCORTEX_REAL_GEMINI`
