import threading

# Runtime-toggleable extraction behavior, shared between app.py (the web UI
# toggle) and extractor_worker.py (which actually branches on it) — kept in
# its own tiny module instead of app.py so extractor_worker.py doesn't have
# to import app.py (that circular import is exactly what scrape.py was
# split out to avoid).
_lock = threading.Lock()
_state = {"only_og_image": False}


def get_only_og_image() -> bool:
    with _lock:
        return _state["only_og_image"]


def set_only_og_image(value: bool) -> bool:
    with _lock:
        _state["only_og_image"] = bool(value)
        return _state["only_og_image"]
