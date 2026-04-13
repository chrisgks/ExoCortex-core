# First 5 Minutes With ExoCortex

The goal of the first five minutes is not to admire the architecture. It is to prove that one wrapped session can
load the right context and leave behind useful state.

## 1. Initialize the clean clone

```bash
python3 tools/bootstrap/init.py --install-wrappers
```

This restores the clean-slate runtime scaffold and installs the wrapper commands into your shell config.

## 2. Open the repo at the right level

Start at the repository root for broad conversation and routing:

```bash
cd /path/to/ExoCortex
```

## 3. Check that the wrapper layer is active

```bash
exocortex-doctor
```

What you want to see:

- `wrapper_bin_on_path: ok`
- `authoritative_preload: ok`
- wrapped resolutions for `codex`, `claude`, and `gemini`

## 4. Launch one wrapped session

```bash
codex
```

Or call another wrapped harness.

## 5. Verify that the session left artifacts behind

Typical artifacts include:

```text
journal/sessions/YYYY-MM-DD/<session-id>.json
journal/sessions/YYYY-MM-DD/<session-id>.context.md
journal/sessions/YYYY-MM-DD/<session-id>.transcript.md
journal/sessions/YYYY-MM-DD/<session-id>.summary.md
journal/sessions/YYYY-MM-DD/<session-id>.candidates.json
journal/raw/YYYY-MM-DD.md
journal/summarised/YYYY-MM-DD.md
```

## 6. Scaffold the first real project context

```bash
exocortex-init project work my-project
```
