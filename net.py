import threading
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

# A shared, connection-pooled session with retries: fetching a grid of images
# fires many near-simultaneous requests at the same host, and some CDNs will
# transiently reject a slice of a sudden burst (429/502/503).
session = requests.Session()
retry = Retry(total=3, backoff_factor=0.4, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20, pool_connections=20)
session.mount("http://", adapter)
session.mount("https://", adapter)

# Cap how many upstream fetches run at the same time so a burst of requests
# (e.g. a batch image download) doesn't itself look like abuse to the CDN.
fetch_limiter = threading.Semaphore(4)


def image_headers(image_url: str, page_url: str) -> dict:
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}" if page_url else ""
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = page_url or origin or image_url
    return headers


def fetch_image(image_url: str, page_url: str = "", stream: bool = False):
    with fetch_limiter:
        return session.get(image_url, headers=image_headers(image_url, page_url), timeout=15, stream=stream)


def fetch_page(url: str):
    return session.get(url, headers=BROWSER_HEADERS, timeout=15)
