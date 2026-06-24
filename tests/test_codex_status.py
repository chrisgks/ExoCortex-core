import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.wrappers import codex_status


class CodexStatusTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_parse_snapshot_reads_latest_token_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "rollout.jsonl"
            self.write_jsonl(
                session_file,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "abc123",
                            "cwd": "/tmp/demo",
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "model_context_window": 258400,
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "model_context_window": 258400,
                                "last_token_usage": {
                                    "input_tokens": 176759,
                                    "cached_input_tokens": 176384,
                                    "output_tokens": 44,
                                },
                                "total_token_usage": {
                                    "input_tokens": 2260211,
                                    "output_tokens": 11703,
                                    "total_tokens": 2271914,
                                },
                            },
                        },
                    },
                ],
            )

            snapshot = codex_status.parse_snapshot(session_file)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot.session_id, "abc123")
            self.assertEqual(snapshot.cwd, "/tmp/demo")
            self.assertEqual(snapshot.model_context_window, 258400)
            self.assertEqual(snapshot.last_input_tokens, 176759)
            self.assertEqual(snapshot.total_tokens, 2271914)
            self.assertEqual(
                codex_status.build_summary_text(snapshot),
                "ctx~68% 177k/258k tok 2.27M",
            )

    def test_prompt_hides_stale_or_unrelated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            tty_path = "/dev/ttys001"
            cache_path = cache_dir / "dev_ttys001.json"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "cwd": "/tmp/demo",
                        "summary_text": "ctx~68% 177k/258k tok 2.27M",
                        "updated_at": 100,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(codex_status, "STATUS_CACHE_DIR", cache_dir):
                with mock.patch.object(codex_status.time, "time", return_value=200):
                    self.assertEqual(
                        codex_status.render_prompt(tty_path, "/tmp/demo/subdir", max_age_seconds=500),
                        "ctx~68% 177k/258k tok 2.27M",
                    )
                    self.assertEqual(
                        codex_status.render_prompt(tty_path, "/tmp/elsewhere", max_age_seconds=500),
                        "",
                    )
                    self.assertEqual(
                        codex_status.render_prompt(tty_path, "/tmp/demo", max_age_seconds=50),
                        "",
                    )
