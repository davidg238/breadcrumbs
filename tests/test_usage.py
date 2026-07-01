import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import session_recorder  # noqa: E402


class LoadUsageConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        cfg = server.load_usage_config(path="/nonexistent/breadcrumbs_usage.json")
        self.assertEqual(cfg["session_budget"], 0)
        self.assertEqual(cfg["weekly_budget"], 0)
        self.assertEqual(cfg["billable"], "output_plus_input")
        self.assertEqual(cfg["model_weights"], {"default": 1.0})

    def test_file_shallow_overrides_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"session_budget": 1000, "model_weights": {"claude-opus-4-8": 5.0}}, f)
            path = f.name
        try:
            cfg = server.load_usage_config(path=path)
            self.assertEqual(cfg["session_budget"], 1000)
            self.assertEqual(cfg["weekly_budget"], 0)           # untouched default
            self.assertEqual(cfg["model_weights"]["claude-opus-4-8"], 5.0)
            self.assertEqual(cfg["model_weights"]["default"], 1.0)  # default preserved
        finally:
            os.unlink(path)

    def test_malformed_file_returns_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            cfg = server.load_usage_config(path=path)
            self.assertEqual(cfg["session_budget"], 0)
        finally:
            os.unlink(path)


def _seed_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(session_recorder.SCHEMA)
    return db


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _insert_msg(db, uuid, ts, model, usage):
    db.execute(
        "INSERT INTO messages (uuid, session_id, type, model, timestamp, usage_json) "
        "VALUES (?, 's1', 'assistant', ?, ?, ?)",
        (uuid, model, ts, json.dumps(usage) if usage is not None else None),
    )
    db.commit()


class GetUsageTests(unittest.TestCase):
    def setUp(self):
        self.db = _seed_db()
        self.now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def test_only_in_window_rows_counted(self):
        # 2h ago -> inside 5h session window; 6h ago -> outside session, inside weekly
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=2)),
                    "claude-sonnet-5", {"input_tokens": 100, "output_tokens": 10})
        _insert_msg(self.db, "b", _iso(self.now - timedelta(hours=6)),
                    "claude-sonnet-5", {"input_tokens": 200, "output_tokens": 20})
        u = server.get_usage(self.db, now=self.now)
        self.assertEqual(u["windows"]["session"]["tokens"]["input"], 100)
        self.assertEqual(u["windows"]["session"]["tokens"]["output"], 10)
        self.assertEqual(u["windows"]["weekly"]["tokens"]["input"], 300)
        self.assertEqual(u["windows"]["weekly"]["tokens"]["output"], 30)

    def test_window_start_and_reset(self):
        first = self.now - timedelta(hours=3)
        _insert_msg(self.db, "a", _iso(first), "claude-sonnet-5",
                    {"input_tokens": 5, "output_tokens": 5})
        _insert_msg(self.db, "b", _iso(self.now - timedelta(hours=1)),
                    "claude-sonnet-5", {"input_tokens": 5, "output_tokens": 5})
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertEqual(w["window_start"], _iso(first))
        expected_reset = _iso(first + timedelta(seconds=5 * 3600))
        self.assertEqual(w["reset_at"], expected_reset)

    def test_empty_window_has_null_start_and_zero_tokens(self):
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertIsNone(w["window_start"])
        self.assertIsNone(w["reset_at"])
        self.assertEqual(w["tokens"]["input"], 0)
        self.assertIsNone(w["percent"])

    def test_weighting_and_percent(self):
        cfg = {"session_budget": 100, "weekly_budget": 0,
               "model_weights": {"default": 1.0, "claude-opus-4-8": 5.0},
               "billable": "output_only"}
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=1)),
                    "claude-opus-4-8", {"input_tokens": 999, "output_tokens": 10})
        w = server.get_usage(self.db, now=self.now, config=cfg)["windows"]["session"]
        # billable=output_only -> 10 output * weight 5 = 50 weighted
        self.assertEqual(w["weighted_tokens"], 50.0)
        self.assertEqual(w["percent"], 50.0)   # 50 / 100 * 100
        # weekly budget 0 -> percent hidden
        self.assertIsNone(server.get_usage(self.db, now=self.now, config=cfg)["windows"]["weekly"]["percent"])

    def test_malformed_usage_json_skipped(self):
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=1)), "claude-sonnet-5", None)
        self.db.execute("UPDATE messages SET usage_json='{bad' WHERE uuid='a'")
        self.db.commit()
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertEqual(w["tokens"]["input"], 0)


if __name__ == "__main__":
    unittest.main()
