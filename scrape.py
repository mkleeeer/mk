import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


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
