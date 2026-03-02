"""Simple HTTP server for serving ~/A2Pod/ on the LAN."""

import configparser
import logging
import signal
import sys
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "a2pod" / "config"
SERVE_DIR = Path.home() / "A2Pod"
DEFAULT_PORT = 8008
LOG_PATH = Path.home() / ".config" / "a2pod" / "server.log"


class CORSHandler(SimpleHTTPRequestHandler):
    """HTTP handler with CORS headers for podcast app compatibility."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        logging.info(format, *args)


def _load_port() -> int:
    """Read port from [server] config section."""
    if CONFIG_PATH.exists():
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_PATH)
        return cfg.getint("server", "port", fallback=DEFAULT_PORT)
    return DEFAULT_PORT


def run_server():
    """Start the HTTP server."""
    port = _load_port()
    SERVE_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure feed.xml exists so podcast apps can subscribe immediately
    from publisher import ensure_feed_exists
    ensure_feed_exists()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        format="%(asctime)s %(message)s",
        level=logging.INFO,
    )

    handler = partial(CORSHandler, directory=str(SERVE_DIR))
    server = HTTPServer(("0.0.0.0", port), handler)

    def shutdown(signum, frame):
        logging.info("Shutting down server (signal %d)", signum)
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logging.info("Serving %s on 0.0.0.0:%d", SERVE_DIR, port)
    print(f"Serving {SERVE_DIR} on http://0.0.0.0:{port}/")
    server.serve_forever()
