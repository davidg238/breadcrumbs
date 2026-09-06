import contextlib
import io
import os
import re
import socket
import sqlite3
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import session_recorder  # noqa: E402


def _seed_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(session_recorder.SCHEMA)
    db.execute("INSERT INTO sessions (session_id, cwd, started_at) VALUES ('s1', '/p/one', '2026-09-01T00:00:00Z')")
    return db


def _insert_msg(db, uuid, session_id="s1", ts="2026-09-01T00:00:01Z", sequence=0):
    db.execute(
        "INSERT INTO messages (uuid, session_id, type, timestamp, sequence, usage_json) "
        "VALUES (?, ?, 'assistant', ?, ?, ?)",
        (uuid, session_id, ts, sequence, '{"input_tokens": 10, "output_tokens": 5}'),
    )


class SessionAggregateCacheTests(unittest.TestCase):
    def setUp(self):
        server.SESSION_AGG_CACHE.clear()

    def test_aggregate_reflects_messages_inserted_after_first_load(self):
        db = _seed_db()
        _insert_msg(db, "m1")
        self.assertEqual(server.get_sessions(db)[0]["message_count"], 1)
        _insert_msg(db, "m2", ts="2026-09-01T00:00:02Z", sequence=1)
        s = server.get_sessions(db)[0]
        self.assertEqual(s["message_count"], 2)
        self.assertEqual(s["total_input_tokens"], 20)
        self.assertEqual(s["last_msg"], "2026-09-01T00:00:02Z")

    def test_replaced_messages_are_not_double_counted(self):
        db = _seed_db()
        _insert_msg(db, "crash-insurance", sequence=None)
        self.assertEqual(server.get_sessions(db)[0]["message_count"], 1)
        # session_recorder sync: drops NULL-sequence rows, re-inserts the real ones
        db.execute("DELETE FROM messages WHERE session_id = 's1' AND sequence IS NULL")
        _insert_msg(db, "m1", sequence=0)
        self.assertEqual(server.get_sessions(db)[0]["message_count"], 1)

    def test_unchanged_sessions_are_not_rescanned(self):
        db = _seed_db()
        _insert_msg(db, "m1")
        server.get_sessions(db)
        statements = []
        db.set_trace_callback(statements.append)
        server.get_sessions(db)
        db.set_trace_callback(None)
        scans = [s for s in statements if "usage_json" in s]
        self.assertEqual(scans, [], "second load rescanned message rows: %r" % scans)


class _LiveServer:
    def __init__(self):
        self.httpd = server.make_server("127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class LiveServerTests(unittest.TestCase):
    def test_idle_connection_does_not_block_other_clients(self):
        with _LiveServer() as srv:
            idle = socket.create_connection(("127.0.0.1", srv.port))  # connects, never sends
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/", timeout=5) as r:
                    self.assertEqual(r.status, 200)
            finally:
                idle.close()

    def test_requests_are_logged_with_client_status_and_duration(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), _LiveServer() as srv:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/api/usage", timeout=5).read()
        line = [l for l in buf.getvalue().splitlines() if "/api/usage" in l]
        self.assertEqual(len(line), 1, buf.getvalue())
        self.assertRegex(line[0], r"127\.0\.0\.1 GET /api/usage 200 \d+\.\d{3}s")


if __name__ == "__main__":
    unittest.main()
