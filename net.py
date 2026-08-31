import threading
from collections import defaultdict
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def _new_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10, pool_connections=10)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# requests.Session/urllib3's connection pool is documented as thread-safe,
# but sharing one Session (and its pooled sockets) across Flask's worker
# threads plus the background extractor/download threads was producing
# random "[SSL: WRONG_VERSION_NUMBER]"/"bad record mac" errors on completely
# unrelated domains (whitehouse.gov, 동아일보, 가디언, ...) — the same class
# of cross-thread socket corruption already seen and fixed in sheets.py.
# Giving each thread its own Session removes the shared-socket race.
_local = threading.local()


def _session() -> requests.Session:
    if getattr(_local, "session", None) is None:
        _local.session = _new_session()
    return _local.session


# Cap how many upstream fetches run at the same time so a burst of requests
# (e.g. a batch image download) doesn't itself look like abuse to the CDN,
# and doesn't itself open enough concurrent sockets to start corrupting them.
fetch_limiter = threading.Semaphore(2)

# Some hosts (Wikimedia's thumbnail CDN in particular) rate-limit far more
# aggressively than the general limiter above allows, and keep 429-ing even
# under fetch_limiter's cap — so give known-strict hosts their own tighter,
# per-host limiter on top of the general one.
_STRICT_HOSTS = {"upload.wikimedia.org", "commons.wikimedia.org"}
_host_limiters = defaultdict(lambda: threading.Semaphore(1))


def _limiter_for(url: str):
    host = urlparse(url).netloc.lower()
    return _host_limiters[host] if host in _STRICT_HOSTS else None


def image_headers(image_url: str, page_url: str) -> dict:
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}" if page_url else ""
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = page_url or origin or image_url
    return headers


def fetch_image(image_url: str, page_url: str = "", stream: bool = False):
    host_limiter = _limiter_for(image_url)
    with fetch_limiter:
        if host_limiter is not None:
            with host_limiter:
                return _session().get(image_url, headers=image_headers(image_url, page_url), timeout=15, stream=stream)
        return _session().get(image_url, headers=image_headers(image_url, page_url), timeout=15, stream=stream)


def fetch_page(url: str):
    with fetch_limiter:
        return _session().get(url, headers=BROWSER_HEADERS, timeout=15)
