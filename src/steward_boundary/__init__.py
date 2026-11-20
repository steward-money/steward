"""Code both sides of the privacy boundary share (spec 3.4): the outgoing text rule, the alias grammar, the tool
schemas, fingerprints and the WebSocket frames. It imports nothing from steward or steward_gateway
(tests/test_packaging.py)."""
PROTOCOL = 3        # 3: a tool result's `ticked` (public issue #53); an older gateway refuses it
