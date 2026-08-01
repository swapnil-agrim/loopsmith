# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.serve (issue #124, E4.S1) -- the loopback-only --serve helper.

No duckdb needed anywhere in this file: serve.py never touches the store."""
import threading
import urllib.request

from insight.dash.serve import build_server, serve_forever_until_interrupted


def test_build_server_binds_loopback_only(tmp_path):
    server = build_server(tmp_path)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_serve_forever_round_trip_via_background_thread(tmp_path):
    """The exact §J pattern: port=0 lets the OS assign a free ephemeral port (avoids a
    hardcoded-port flakiness in CI), serve_forever runs in a background thread, a real HTTP GET
    is issued, and the byte count is checked to equal the file on disk exactly -- the check that
    would have caught the truncated-response artifact the plan's own prototyping hit once
    (.sdlc/plans/124.md section J)."""
    body = b"<html>hello</html>"
    (tmp_path / "index.html").write_bytes(body)

    httpd = build_server(tmp_path, port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3)
        assert resp.status == 200
        assert resp.read() == body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_serve_forever_until_interrupted_prints_the_url_and_returns_on_keyboardinterrupt(
    tmp_path, monkeypatch
):
    """serve_forever_until_interrupted's own job is to print + block + clean up the socket --
    NOT to swallow KeyboardInterrupt itself (that catch lives in main(), Task 3). A fake server
    object whose serve_forever() raises KeyboardInterrupt immediately proves both: the interrupt
    propagates, and server_close() is still called (the finally block's job)."""
    printed = []
    closed = {"called": False}

    class FakeServer:
        server_address = ("127.0.0.1", 12345)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            closed["called"] = True

    def fake_build_server(directory, host="127.0.0.1", port=8787):
        return FakeServer()

    monkeypatch.setattr("insight.dash.serve.build_server", fake_build_server)

    try:
        serve_forever_until_interrupted(tmp_path, print_fn=printed.append)
        raised = False
    except KeyboardInterrupt:
        raised = True

    assert raised
    assert closed["called"]
    assert any("127.0.0.1:12345" in line for line in printed)
