#!/usr/bin/env python3
"""Tests for the reward check-in + reward log.

Contract pinned here:

- The reward log is append-only JSONL at ``journal/reward-log.jsonl`` with a
  stable, train-ready schema that joins with ``review-decisions.jsonl`` by id
  style (session_id / claude_session_id).
- The session-close check-in asks **one number (energy 1-5) + one line (juice)**
  and is always skippable: Enter = skip, non-tty = auto-defer, never blocks.
- Non-interactive sessions record a *pending* check-in; the Brief surfaces the
  count; ``exocortex-checkin`` answers them after the fact.
"""

from __future__ import annotations

import io
import json
import os
import select
import time
import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workers import reward_log, build_brief


class RewardLogSchemaTests(unittest.TestCase):
    def test_path_is_reward_log(self) -> None:
        self.assertEqual(str(reward_log.REWARD_LOG_PATH), "journal/reward-log.jsonl")
        self.assertEqual(
            str(reward_log.PENDING_CHECKINS_PATH),
            "journal/inbox/pending-checkins.jsonl",
        )

    def test_append_record_is_jsonl_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            reward_log.append_reward(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                energy=4,
                juice="shipped the reward log",
                source="wrapper",
            )
            reward_log.append_reward(
                root,
                session_id="local-2",
                claude_session_id=None,
                agent="research",
                scope="root",
                energy=None,
                juice=None,
                source="checkin-deferred",
            )
            path = root / reward_log.REWARD_LOG_PATH
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            for key in (
                "timestamp",
                "session_id",
                "claude_session_id",
                "agent",
                "scope",
                "energy",
                "juice",
                "source",
            ):
                self.assertIn(key, first)
            self.assertEqual(first["session_id"], "local-1")
            self.assertEqual(first["energy"], 4)
            self.assertEqual(first["juice"], "shipped the reward log")
            self.assertEqual(first["source"], "wrapper")
            second = json.loads(lines[1])
            self.assertIsNone(second["energy"])
            self.assertIsNone(second["juice"])

    def test_energy_validation_clamps_to_1_5_or_none(self) -> None:
        self.assertEqual(reward_log.parse_energy("4"), 4)
        self.assertEqual(reward_log.parse_energy(" 1 "), 1)
        self.assertEqual(reward_log.parse_energy("5"), 5)
        self.assertIsNone(reward_log.parse_energy(""))
        self.assertIsNone(reward_log.parse_energy("skip"))
        self.assertIsNone(reward_log.parse_energy("9"))
        self.assertIsNone(reward_log.parse_energy("0"))
        self.assertIsNone(reward_log.parse_energy("abc"))


class PendingCheckinTests(unittest.TestCase):
    def test_record_pending_appends(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            reward_log.record_pending(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                source="checkin-deferred",
            )
            pending = reward_log.load_pending(root)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["session_id"], "local-1")
            self.assertEqual(pending[0]["agent"], "builder")

    def test_answering_pending_writes_reward_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            reward_log.record_pending(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                source="checkin-deferred",
            )
            reward_log.answer_pending(
                root, "local-1", energy=3, juice="got it done"
            )
            # pending cleared
            self.assertEqual(reward_log.load_pending(root), [])
            # reward written, joinable by session id
            rewards = [
                json.loads(line)
                for line in (root / reward_log.REWARD_LOG_PATH)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rewards), 1)
            self.assertEqual(rewards[0]["session_id"], "local-1")
            self.assertEqual(rewards[0]["energy"], 3)
            self.assertEqual(rewards[0]["source"], "checkin-deferred")

    def test_pending_count(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual(reward_log.pending_count(root), 0)
            reward_log.record_pending(
                root, session_id="a", claude_session_id=None,
                agent="builder", scope="root", source="checkin-deferred",
            )
            reward_log.record_pending(
                root, session_id="b", claude_session_id=None,
                agent="builder", scope="root", source="checkin-deferred",
            )
            self.assertEqual(reward_log.pending_count(root), 2)


class InteractiveCheckinTests(unittest.TestCase):
    """The prompt must be frictionless and never block on a non-tty stream."""

    def test_non_tty_defers_instead_of_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            # A StringIO is not a tty -> must defer, not hang.
            result = reward_log.run_checkin(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                stdin=io.StringIO(""),
                output=io.StringIO(),
                is_tty=False,
            )
            self.assertEqual(result, "deferred")
            self.assertEqual(reward_log.pending_count(root), 1)
            # No reward row written yet.
            self.assertFalse((root / reward_log.REWARD_LOG_PATH).exists())

    def test_blank_answers_skip_but_still_log_a_row(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            # Two blank lines = Enter,Enter = skip both prompts.
            result = reward_log.run_checkin(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                stdin=io.StringIO("\n\n"),
                output=io.StringIO(),
                is_tty=True,
            )
            self.assertEqual(result, "skipped")
            rewards = [
                json.loads(line)
                for line in (root / reward_log.REWARD_LOG_PATH)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rewards), 1)
            self.assertIsNone(rewards[0]["energy"])
            self.assertIsNone(rewards[0]["juice"])
            self.assertEqual(rewards[0]["source"], "wrapper")

    def test_answered_checkin_logs_energy_and_juice(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            result = reward_log.run_checkin(
                root,
                session_id="local-1",
                claude_session_id="cid-1",
                agent="builder",
                scope="work/proj",
                stdin=io.StringIO("5\nbuilt the check-in\n"),
                output=io.StringIO(),
                is_tty=True,
            )
            self.assertEqual(result, "answered")
            rewards = [
                json.loads(line)
                for line in (root / reward_log.REWARD_LOG_PATH)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(rewards[0]["energy"], 5)
            self.assertEqual(rewards[0]["juice"], "built the check-in")


def _drive_checkin_over_pty(root: Path, *, rating: str, juice: str, drain_fd0: bool) -> float:
    """Run ``run_checkin`` in a forked child attached to a pseudo-terminal and
    play the user from the parent. Returns the wall-clock seconds the child
    took (so a regression that instantly skips both prompts is visible as a
    near-zero duration as well as a lost row).

    When ``drain_fd0`` is set the child first points fd 0 at /dev/null — the
    exact end-state after the postprocess worker's summarizer subprocesses have
    consumed the inherited stdin. The check-in must still reach the real user by
    opening /dev/tty.
    """
    import pty

    pid, master_fd = pty.fork()  # child gets the pty slave as its controlling tty
    if pid == 0:  # child: the wrapped session's check-in
        try:
            # pty.fork dup'd the slave onto fds 0/1/2; rebind Python's stream
            # objects to those raw fds so prompts traverse the real terminal even
            # when the test runner (pytest) has swapped sys.stderr for a capture
            # buffer. This mirrors the production path where output is fd 2.
            sys.stdout = os.fdopen(1, "w", buffering=1)
            sys.stderr = os.fdopen(2, "w", buffering=1)
            if drain_fd0:
                devnull = os.open(os.devnull, os.O_RDONLY)
                os.dup2(devnull, 0)
                os.close(devnull)
            # Defaults mirror the real wrapper call: no stdin override, is_tty true.
            reward_log.run_checkin(
                root,
                session_id="pty-1",
                claude_session_id=None,
                agent="builder",
                scope="root",
                is_tty=True,
            )
        finally:
            os._exit(0)

    started = time.monotonic()
    buf = b""
    sent_rating = sent_juice = False
    deadline = time.time() + 12
    while time.time() < deadline:
        r, _, _ = select.select([master_fd], [], [], 0.2)
        if master_fd in r:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data
        if not sent_rating and b"rate this session" in buf:
            os.write(master_fd, rating.encode() + b"\n")
            sent_rating = True
        if not sent_juice and b"what stood out" in buf:
            os.write(master_fd, juice.encode() + b"\n")
            sent_juice = True
        if b"noted" in buf or b"logged" in buf:
            break
    elapsed = time.monotonic() - started
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
    try:
        os.close(master_fd)
    except OSError:
        pass
    return elapsed


@unittest.skipUnless(hasattr(os, "fork"), "requires fork/pty")
class PtyCheckinRegressionTests(unittest.TestCase):
    """The session-close check-in runs *after* the postprocess worker, whose
    summarizer subprocesses inherit and drain fd 0. The prompt must still reach
    the real terminal (via /dev/tty) and capture the user's answer — it must not
    skip instantly. This pins the fix for the "it was a good session" loss.
    """

    def setUp(self) -> None:
        # pty.fork gives the child its own controlling terminal, so the test
        # does not need the *parent* to have a tty. Only skip where the sandbox
        # forbids allocating a pty at all.
        import pty

        try:
            pid, fd = pty.fork()
        except OSError as exc:
            self.skipTest(f"pty unavailable: {exc}")
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        os.close(fd)

    def _last_row(self, root: Path) -> dict:
        path = root / reward_log.REWARD_LOG_PATH
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        self.assertTrue(rows, "check-in wrote no reward row at all")
        return rows[-1]

    def test_captures_answer_even_after_fd0_is_drained(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _drive_checkin_over_pty(
                root, rating="3", juice="it was a good session", drain_fd0=True
            )
            row = self._last_row(root)
            self.assertEqual(row["energy"], 3)
            self.assertEqual(row["juice"], "it was a good session")

    def test_captures_answer_on_a_clean_tty(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            _drive_checkin_over_pty(
                root, rating="5", juice="shipped the fix", drain_fd0=False
            )
            row = self._last_row(root)
            self.assertEqual(row["energy"], 5)
            self.assertEqual(row["juice"], "shipped the fix")


class BriefPendingCheckinTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        (self.root / "STATE.md").write_text(
            "# Root State\n\n**Last updated:** 2026-04-29\n\n## Current Focus\n\n- ship\n",
            encoding="utf-8",
        )
        (self.root / "journal" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.root / "raw" / "inbox").mkdir(parents=True, exist_ok=True)
        (self.root / "wiki").mkdir(parents=True, exist_ok=True)

    def test_brief_omits_pending_line_when_none(self) -> None:
        brief = build_brief.render_brief(self.root)
        self.assertNotIn("not yet rated", brief)

    def test_brief_surfaces_pending_count(self) -> None:
        reward_log.record_pending(
            self.root, session_id="a", claude_session_id=None,
            agent="builder", scope="root", source="checkin-deferred",
        )
        reward_log.record_pending(
            self.root, session_id="b", claude_session_id=None,
            agent="builder", scope="root", source="checkin-deferred",
        )
        brief = build_brief.render_brief(self.root)
        self.assertIn("2", brief)
        self.assertIn("not yet rated", brief)
        self.assertIn("exocortex-checkin", brief)


if __name__ == "__main__":
    unittest.main()
