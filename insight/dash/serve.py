# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`insight dash --serve`'s server: a plain stdlib http.server bound to 127.0.0.1 ONLY, never
0.0.0.0 -- not a daemon (it's a synchronous foreground process the user starts and Ctrl-C's,
identical in kind to `python3 -m http.server`; see .sdlc/plans/124.md "Other settled points" for
why this doesn't conflict with the done_when's "no daemon" wording, which is about the DEFAULT
`insight dash` build-only path). No `import duckdb` -- this module never touches the store."""
import functools
import http.server
import pathlib

DEFAULT_PORT = 8787  # also hardcoded as dash_parser's own --port default in insight/__main__.py
DEFAULT_HOST = "127.0.0.1"  # loopback ONLY -- not configurable via a flag, see the plan


def build_server(directory, host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Construct (but do not start) a ThreadingHTTPServer rooted at `directory`, bound to `host`.
    Split from serve_forever_until_interrupted() so tests can start it in a background thread and
    call .shutdown() on it -- the stdlib's own supported pattern for this, proven live (.sdlc/plans/124.md
    section J), rather than a special test-only run-once mode grafted onto the real server."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(pathlib.Path(directory)),
    )
    return http.server.ThreadingHTTPServer((host, port), handler)


def serve_forever_until_interrupted(directory, host=DEFAULT_HOST, port=DEFAULT_PORT, print_fn=print):
    """The real `--serve` entry point: build the server, print the URL, block until Ctrl-C, then
    shut down cleanly. Returns nothing -- main()'s `dash` branch catches KeyboardInterrupt around
    this call and returns exit code 0, mirroring `python3 -m http.server`'s own behavior."""
    httpd = build_server(directory, host=host, port=port)
    # Read the REAL bound port back off the socket, not the `port` argument -- port=0 (used by
    # --port 0 / tests to get an OS-assigned ephemeral port) would otherwise print a literal "0",
    # which is not a usable URL. httpd.server_address is the stdlib's own supported way to learn
    # what was actually bound (see .sdlc/plans/124.md section J).
    bound_host, bound_port = httpd.server_address[:2]
    print_fn(f"insight dash: serving {directory} at http://{bound_host}:{bound_port}/ (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
