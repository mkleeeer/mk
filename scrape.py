import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Filenames/paths that are almost never the real content photo we want —
# site logos, UI icons, social-share badges, tracking pixels. Checked
# against the URL path only (not query string), case-insensitively, so a
# real photo whose CDN happens to append "?ref=logo_page" isn't caught.
# Based on the same kind of heuristics newspaper3k's image scorer and
# common logo-scraper filename rules use.
_JUNK_URL_PATTERNS = re.compile(
    r"(icon|logo|sprite|badge|avatar|favicon|creativecommons|copyleft|public.?domain)",
    re.IGNORECASE,
)


def _looks_like_junk(url: str) -> bool:
    path = urlparse(url).path.lower()
    # MediaWiki-style thumbnail URLs embed the *original* filename mid-path
    # (.../thumb/2/22/Some_Badge.svg/250px-Some_Badge.svg.png) — any segment
    # ending in .svg means the source was never a photo, regardless of what
    # that particular badge/diagram happens to be named.
    if any(seg.endswith(".svg") for seg in path.split("/")):
        return True
    return bool(_JUNK_URL_PATTERNS.search(path))


def largest_from_srcset(srcset: str) -> str:
    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except ValueError:
                width = 0
        candidates.append((width, url))
    if not candidates:
        return ""
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def extract_images_from_html(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    def add(url, alt=""):
        if not url:
            return
        url = url.strip()
        if url.startswith("data:"):
            return
        absolute = urljoin(page_url, url)
        if absolute in seen:
            return
        seen.add(absolute)
        if _looks_like_junk(absolute):
            return
        found.append({"url": absolute, "alt": alt[:120]})

    for img in soup.find_all("img"):
        alt = img.get("alt", "")
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-original")
            or img.get("data-lazy-src")
        )
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            best = largest_from_srcset(srcset)
            if best:
                add(best, alt)
                continue
        add(src, alt)

    for source in soup.find_all("source"):
        srcset = source.get("srcset")
        if srcset:
            add(largest_from_srcset(srcset))

    for tag in soup.find_all(style=True):
        m = re.search(r"background-image\s*:\s*url\((.*?)\)", tag["style"])
        if m:
            add(m.group(1).strip("'\""))

    for meta_name in ("og:image", "twitter:image"):
        tag = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
        if tag and tag.get("content"):
            add(tag["content"])

    return found
