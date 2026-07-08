import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_recorder  # noqa: E402


def _assistant_line(session_id):
    return json.dumps({
        "type": "assistant",
        "uuid": "u1",
        "sessionId": session_id,
        "timestamp": "2026-07-04T17:25:21.356Z",
        "gitBranch": "main",
        "version": "1.0.0",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "cache_read_input_tokens": 5},
        },
    })


class HandleSyncUsesHookTranscriptPathTests(unittest.TestCase):
    """The Stop hook payload carries the authoritative transcript_path.

    Reconstructing it from cwd (cwd.replace('/', '-')) misses the file when the
    user cd'd mid-session or the path contains characters Claude Code sanitizes
    (e.g. '_'), leaving only metadata-less placeholder prompts. handle_sync must
    prefer the path Claude Code hands it.
    """

    def setUp(self):
        # Temp DB file so handle_sync's own db.close() doesn't drop the data.
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        seed = sqlite3.connect(self.db_path)
        seed.executescript(session_recorder.SCHEMA)
        seed.close()

        def _get_db():
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            db.executescript(session_recorder.SCHEMA)
            return db

        self._orig_get_db = session_recorder.get_db
        session_recorder.get_db = _get_db

        # Real transcript at a location cwd-reconstruction would never produce.
        fd, self.transcript = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        with open(self.transcript, "w") as f:
            f.write(_assistant_line("s1") + "\n")

    def tearDown(self):
        session_recorder.get_db = self._orig_get_db
        os.unlink(self.db_path)
        os.unlink(self.transcript)

    def test_assistant_turn_synced_when_cwd_would_mislocate_transcript(self):
        session_recorder.handle_sync({
            "session_id": "s1",
            # cwd that reconstructs to a bogus, nonexistent transcript dir
            "cwd": "/home/someone/workspace/nsl_tests",
            "transcript_path": self.transcript,
        })

        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT type, sequence, timestamp, usage_json, model "
            "FROM messages WHERE session_id = 's1'").fetchone()
        db.close()

        self.assertIsNotNone(row, "assistant turn was not imported")
        self.assertEqual(row["type"], "assistant")
        self.assertEqual(row["sequence"], 0)
        self.assertEqual(row["timestamp"], "2026-07-04T17:25:21.356Z")
        self.assertEqual(row["model"], "claude-opus-4-8")
        self.assertEqual(json.loads(row["usage_json"])["input_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
