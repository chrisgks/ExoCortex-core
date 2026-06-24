import json
import tempfile
import unittest
from pathlib import Path

from tools.workers import usage
from tools.wrappers import exocortex_wrapper as wrapper


class UsageTests(unittest.TestCase):
    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def write_rates(self, root: Path) -> None:
        path = root / usage.RATES_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": "test-rates",
                    "effective_date": "2026-04-29",
                    "source": "test",
                    "models": {
                        "gpt-test": {
                            "input_usd_per_1m": 2.0,
                            "cached_input_usd_per_1m": 0.2,
                            "output_usd_per_1m": 10.0,
                        },
                        "claude-test": {
                            "input_usd_per_1m": 3.0,
                            "cache_creation_input_usd_per_1m": 3.75,
                            "cache_read_input_usd_per_1m": 0.3,
                            "output_usd_per_1m": 15.0,
                        },
                        "gemini-test": {
                            "input_usd_per_1m": 0.5,
                            "cached_input_usd_per_1m": 0.05,
                            "output_usd_per_1m": 3.0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_record_codex_session_writes_ledger_and_daily_rollup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_rates(root)
            session_file = root / "rollout.jsonl"
            self.write_jsonl(
                session_file,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "codex-session",
                            "cwd": str(root),
                            "model_provider": "openai",
                            "cli_version": "0.1",
                        },
                    },
                    {
                        "type": "turn_context",
                        "payload": {
                            "model": "gpt-test",
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "model_context_window": 100000,
                                "total_token_usage": {
                                    "input_tokens": 1000000,
                                    "cached_input_tokens": 400000,
                                    "output_tokens": 100000,
                                    "reasoning_output_tokens": 25000,
                                    "total_tokens": 1100000,
                                },
                            },
                        },
                    },
                ],
            )
            manifest = {
                "session_id": "exo-session",
                "tool": "codex",
                "cwd": str(root),
                "started_at": "2026-04-29T10:00:00+00:00",
                "ended_at": "2026-04-29T10:05:00+00:00",
            }

            record = usage.record_codex_session(root, manifest, session_file)

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["cost_usd"], 2.28)
            self.assertEqual(record["billable_uncached_input_tokens"], 600000)
            ledger = root / usage.LEDGER_PATH
            self.assertTrue(ledger.exists())
            lines = ledger.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            daily = json.loads((root / usage.DAILY_DIR / "2026-04-29.json").read_text(encoding="utf-8"))
            self.assertEqual(daily["session_count"], 1)
            self.assertEqual(daily["cost_usd"], 2.28)

    def test_record_claude_session_uses_cache_write_and_read_rates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_rates(root)
            session_file = root / "claude.jsonl"
            self.write_jsonl(
                session_file,
                [
                    {
                        "sessionId": "claude-session",
                        "cwd": str(root),
                        "version": "1.0",
                        "message": {
                            "id": "msg-1",
                            "model": "claude-test",
                            "usage": {
                                "input_tokens": 1000,
                                "cache_creation_input_tokens": 2000,
                                "cache_read_input_tokens": 3000,
                                "output_tokens": 400,
                            },
                        },
                    },
                    {
                        "sessionId": "claude-session",
                        "cwd": str(root),
                        "message": {
                            "id": "msg-1",
                            "model": "claude-test",
                            "usage": {
                                "input_tokens": 1000,
                                "cache_creation_input_tokens": 2000,
                                "cache_read_input_tokens": 3000,
                                "output_tokens": 400,
                            },
                        },
                    },
                ],
            )
            manifest = {
                "session_id": "exo-claude",
                "tool": "claude",
                "cwd": str(root),
                "started_at": "2026-04-29T10:00:00+00:00",
                "ended_at": "2026-04-29T10:05:00+00:00",
            }

            record = usage.record_harness_session(root, manifest, "claude", session_file)

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["cost_usd"], 0.0174)
            self.assertEqual(record["input_tokens"], 1000)
            self.assertEqual(record["total_input_tokens"], 6000)
            self.assertEqual(record["cached_input_tokens"], 5000)
            self.assertEqual(record["billable_uncached_input_tokens"], 1000)

    def test_record_gemini_session_counts_thought_tokens_as_output_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_rates(root)
            session_file = root / "session-1.json"
            session_file.write_text(
                json.dumps(
                    {
                        "sessionId": "gemini-session",
                        "cwd": str(root),
                        "messages": [
                            {
                                "model": "gemini-test",
                                "tokens": {
                                    "input": 1000,
                                    "cached": 400,
                                    "output": 250,
                                    "thoughts": 50,
                                    "tool": 25,
                                    "total": 1325,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "session_id": "exo-gemini",
                "tool": "gemini",
                "cwd": str(root),
                "started_at": "2026-04-29T10:00:00+00:00",
                "ended_at": "2026-04-29T10:05:00+00:00",
            }

            record = usage.record_harness_session(root, manifest, "gemini", session_file)

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["cost_usd"], 0.00122)
            self.assertEqual(record["output_tokens"], 300)
            self.assertEqual(record["reasoning_output_tokens"], 50)
            self.assertEqual(record["tool_tokens"], 25)

    def test_live_codex_cost_line_uses_pricing_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_rates(root)
            event = {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1000000,
                            "cached_input_tokens": 400000,
                            "output_tokens": 100000,
                            "total_tokens": 1100000,
                        },
                    },
                },
            }

            token_usage, cost, _rates = usage.codex_usage_from_event(root, event, "gpt-test")

            self.assertIsNotNone(token_usage)
            self.assertEqual(cost, 2.28)
            self.assertIn(
                "session $2.28",
                usage.usage_summary_text(
                    {
                        "cost_usd": cost,
                        "input_tokens": token_usage.input_tokens,
                        "cached_input_tokens": token_usage.cached_input_tokens,
                        "output_tokens": token_usage.output_tokens,
                        "model": "gpt-test",
                        "cost_basis": "actual_tokens_priced_live",
                    }
                ),
            )

    def test_wrapper_cost_threshold_parser_ignores_bad_values(self) -> None:
        self.assertEqual(wrapper.parse_cost_thresholds("1,bad,0,0.5"), [0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
