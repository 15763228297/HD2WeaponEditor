"""Desktop shell for the HD2 weapon stat editor.

Wraps the existing Flask app in a native window instead of leaving the user a
browser tab. The HTML front end is reused unchanged - only the container
changes - so none of the 400-odd lines of UI had to be rewritten.

Run:  python gui/desktop.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

def _resolve_root() -> Path:
    """Project root: the repo when running from source, the bundle when frozen."""
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    if frozen and meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

HOST = "127.0.0.1"
PORT = 8777

# Imported at module level and deliberately BEFORE webview: compiler mode must
# be able to run without ever loading the WebView2 runtime.
import ljcompile  # noqa: E402


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll until the server answers. Serving takes a moment to start."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def serve() -> None:
    """Run the Flask app. Started on a thread so the window can open."""
    try:
        from gui.app import app
    except ImportError as exc:
        print(f"cannot start the server: {exc}")
        print("is Flask installed?  python -m pip install flask")
        return
    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False,
                threaded=True)
    except OSError as exc:
        # Most likely the port is taken by an earlier run.
        print(f"server error: {exc}")


def main() -> int:
    # Compiler mode: the app re-invokes itself here so LuaJIT compiles in a
    # process with a clean address space (see tools/ljcompile.py). Must be
    # handled before anything imports webview, or the whole point is lost.
    if ljcompile.COMPILE_FLAG in sys.argv[1:]:
        return ljcompile._as_compiler()

    if not wait_for_port(HOST, PORT, timeout=0.4):
        # Nothing serving yet - start our own.
        threading.Thread(target=serve, daemon=True).start()
        if not wait_for_port(HOST, PORT):
            print("the server did not start; see the messages above")
            return 1
    else:
        print(f"reusing the server already on {HOST}:{PORT}")

    try:
        import webview
    except ImportError:
        print("pywebview is not installed.")
        print("  python -m pip install pywebview")
        print(f"Or open http://{HOST}:{PORT} in a browser instead.")
        return 1

    window = webview.create_window(
        "HD2 武器数值编辑器",
        f"http://{HOST}:{PORT}",
        width=1180,
        height=860,
        min_size=(900, 620),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
