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


if __name__ == "__main__":
    unittest.main()
