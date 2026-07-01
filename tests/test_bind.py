import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


class ResolveBindHostTests(unittest.TestCase):
    def test_default_returns_host_unchanged(self):
        self.assertEqual(server.resolve_bind_host("127.0.0.1", False), "127.0.0.1")

    def test_explicit_host_passthrough(self):
        self.assertEqual(server.resolve_bind_host("0.0.0.0", False), "0.0.0.0")

    def test_tailscale_uses_lookup(self):
        got = server.resolve_bind_host("127.0.0.1", True, ip_lookup=lambda: "100.101.102.103")
        self.assertEqual(got, "100.101.102.103")

    def test_tailscale_lookup_failure_raises(self):
        with self.assertRaises(ValueError):
            server.resolve_bind_host("127.0.0.1", True, ip_lookup=lambda: None)


if __name__ == "__main__":
    unittest.main()
