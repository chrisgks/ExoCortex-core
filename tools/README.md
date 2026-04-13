# Tools

This folder will hold implementation support for ExoCortex runtime behavior.

- `wrappers/`: harness launch wrappers
- `bootstrap/`: first-run and scaffold helpers
- `workers/`: summarization and promotion workers
- `automations/`: cron-friendly background status helpers
- `prompts/`: prompt assets used by wrappers and workers

Current implementation:

- `tools/wrappers/exocortex_wrapper.py`: generic launcher that discovers local and ancestor context, injects bootstrap context where possible, captures streamed session logs, and dispatches the session worker
- `tools/wrappers/bin/codex`: wrapper entrypoint for Codex
- `tools/wrappers/bin/claude`: wrapper entrypoint for Claude Code
- `tools/wrappers/bin/gemini`: wrapper entrypoint for Gemini CLI
- `tools/wrappers/bin/exocortex-init`: bootstrap entrypoint for initializing a clean clone and scaffolding new contexts
- `tools/workers/process_session.py`: local worker that creates session summaries, structured candidate files, grouped review queues, weekly synthesis pages, and daily journal entries from wrapper-captured session streams
