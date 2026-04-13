# First 5 Minutes With ExoCortex

This walkthrough assumes the actual starting point for the project:

you built it for yourself first, to help you function better in the world with your brain.

That means the goal of the first five minutes is not to admire the architecture. It is to get one working loop running so the system starts earning its keep.

## 1. Initialize the clean clone

```bash
python3 tools/bootstrap/init.py
```

This restores any missing clean-slate runtime files and review queues.

## 2. Open the repo at the right level

If you want broad conversation, start at the repo root.

```bash
cd /path/to/ExoCortex
```

If you want project-specific work, start in the narrowest project folder that should shape the session.

## 3. Install the wrappers

```bash
./tools/wrappers/install.sh
```

This appends a wrapper block to `.zshrc` so `codex`, `claude`, and `gemini` resolve through ExoCortex first.

If you do not want to edit your shell yet, you can still call the wrappers directly from `tools/wrappers/bin/`.

## 4. Install the low-risk background automation

```bash
./tools/automations/install_cron.sh
```

This installs the reporting-only ExoCortex cron job that refreshes automation status in the background.

If you want a one-command setup path, you can also initialize with:

```bash
python3 tools/bootstrap/init.py --install-wrappers --install-cron
```

## 5. Check that the wrapper layer is active

```bash
exocortex-doctor
```

What you want to see:

- `wrapper_bin_on_path: ok`
- `authoritative_preload: ok`
- wrapped resolutions for `codex`, `claude`, and `gemini`

Those lines tell you that ExoCortex can actually inject context before the real harness starts.

## 6. Launch one wrapped session

From the right folder:

```bash
codex
```

Or:

```bash
claude
```

Or:

```bash
gemini
```

The important shift is this:

- you are not opening a blank chat
- you are starting from a context surface
- the wrapper is loading root, system, and local contract files first

## 7. Verify that the session left artifacts behind

After the run, inspect the journal.

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

This is the point of the system: the work does not disappear when the session ends.

## 8. Scaffold the first real project context

```bash
exocortex-init project work my-project
```

## What To Do Immediately After

If the first run worked, do one of these next:

1. Start using the root as a daily decision surface.
2. Pick one real project folder and let its local context shape a session.
3. Review the journal output and see whether it captured anything you would actually want next time.

If you want to understand where the system can go after that first loop works, read [compositional-examples.md](compositional-examples.md), [technical-architecture.md](technical-architecture.md), and [../agents/README.md](../agents/README.md).

## What Not To Optimize Yet

Do not spend the first session trying to perfect:

- the ideal hierarchy
- the final persona
- the perfect rule set
- a generalized multi-agent architecture

The first job is simpler: prove that one real session can load the right context and leave behind useful state.

## If You Only Remember One Thing

ExoCortex is useful when it reduces re-orientation cost.

If it helps you restart, recover context, and keep momentum with less friction, it is doing the job it was originally built to do.
