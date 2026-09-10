import io
import os
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image

try:
    import pillow_avif  # noqa: F401  (registers AVIF support with Pillow)
except ImportError:
    pass

import db
import net
from scrape import find_pdf_links

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = Path(r"G:\내 드라이브\[작업공간]\웹이미지 수집")
KEEP_ORIGINAL = os.environ.get("IMAGE_KEEP_ORIGINAL", "1") != "0"

# Formats Pillow can decode. Anything outside this set (or that fails to
# decode) is treated as "not actually an image" — e.g. a site returned an
# HTML error/login page instead of the requested picture.
FORMAT_EXT = {
    "JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif",
    "BMP": "bmp", "TIFF": "tiff", "AVIF": "avif",
}
# JPEG/PNG are saved as-is (byte-for-byte) to avoid a lossy re-encode.
# Everything else gets converted to JPEG, or PNG if it carries transparency.
NO_CONVERT = {"JPEG", "PNG"}


class DownloadError(Exception):
    pass


def _relpath(path: Path) -> str:
    return str(path.relative_to(DOWNLOADS_DIR)).replace("\\", "/")


def _save_pdf(raw: bytes, url: str, source_page: str, title: str, job_id: str) -> dict:
    job_dir = DOWNLOADS_DIR / job_id
    converted_dir = job_dir / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)

    job_seq = db.next_job_seq(job_id)
    daily_seq = db.next_daily_seq()
    out_path = converted_dir / f"{job_seq:02d}.pdf"
    out_path.write_bytes(raw)

    record = {
        "id": f"pdf_{datetime.now():%Y%m%d}_{daily_seq:03d}",
        "job_id": job_id,
        "seq": job_seq,
        "filename": out_path.name,
        "local_path": _relpath(out_path),
        "original_path": None,
        "source_url": url,
        "source_page": source_page or None,
        "title": title or None,
        "caption": None,
        "mime_type": "application/pdf",
        "width": None,
        "height": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "drive_file_id": None,
        "drive_url": None,
    }
    db.insert_image(record)
    return record


def _fetch_url(url: str):
    """Fetch with error types kept distinguishable (URL/DNS, blocked
    internal target, timeout, HTTP status, connection failure) instead of
    collapsing everything into one generic "download failed" — matters for
    the failure-breakdown view, which buckets by these exact messages."""
    try:
        resp = net.fetch_image(url, url)
        resp.raise_for_status()
        return resp
    except net.BlockedURLError as e:
        raise DownloadError(str(e)) from e
    except requests.exceptions.Timeout as e:
        raise DownloadError(f"연결 시간 초과: {e}") from e
    except requests.exceptions.HTTPError as e:
        raise DownloadError(f"다운로드 실패: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise DownloadError(f"연결 실패: {e}") from e
    except requests.exceptions.RequestException as e:
        raise DownloadError(f"다운로드 실패: {e}") from e


def _resolve_pdf_from_html(raw: bytes, page_url: str):
    """A URL can turn out to be an HTML landing/redirect page instead of the
    file itself (a "click here to download" page) — same idea as a download
    manager resolving a link before fetching it. Look for a direct .pdf link
    on that page and follow it, trying candidates in order until one
    actually verifies as a PDF by magic bytes (not just by extension).
    Returns (raw_bytes, resolved_url) or (None, None) if nothing panned out."""
    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception:
        return None, None
    for candidate in find_pdf_links(soup, page_url):
        try:
            resp = _fetch_url(candidate)
        except DownloadError:
            continue
        if resp.content[:5] == b"%PDF-":
            return resp.content, candidate
    return None, None


def download_and_process(url: str, source_page: str = "", title: str = "", folder: str = "") -> dict:
    if not url:
        raise DownloadError("url이 필요합니다.")

    job_id = db.get_or_create_job(folder)
    resp = _fetch_url(url)
    raw = resp.content

    # PDF check comes first and by magic bytes, not Content-Type header — same
    # "never trust the header" reasoning as the image path below (a blocked
    # request can come back as an HTML page with an image/pdf Content-Type).
    if raw[:5] == b"%PDF-":
        return _save_pdf(raw, url, source_page, title, job_id)

    content_type = resp.headers.get("Content-Type", "")
    looks_like_html = content_type.startswith("text/html") or raw.lstrip()[:15].lower().startswith(b"<!doctype html") or raw.lstrip()[:5].lower() == b"<html"
    if looks_like_html:
        resolved_raw, resolved_url = _resolve_pdf_from_html(raw, resp.url)
        if resolved_raw is not None:
            return _save_pdf(resolved_raw, resolved_url, source_page or url, title, job_id)

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:
        content_type = resp.headers.get("Content-Type", "unknown")
        raise DownloadError(f"이미지나 PDF로 인식할 수 없습니다 (Content-Type: {content_type}): {e}") from e

    fmt = im.format or "JPEG"
    orig_ext = FORMAT_EXT.get(fmt, "bin")

    job_dir = DOWNLOADS_DIR / job_id
    converted_dir = job_dir / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)

    job_seq = db.next_job_seq(job_id)
    daily_seq = db.next_daily_seq()
    seq_name = f"{job_seq:02d}"
    file_id = f"img_{datetime.now():%Y%m%d}_{daily_seq:03d}"

    if fmt in NO_CONVERT:
        target_fmt, target_ext = fmt, orig_ext
        out_path = converted_dir / f"{seq_name}.{target_ext}"
        out_path.write_bytes(raw)
    else:
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        if has_alpha:
            target_fmt, target_ext = "PNG", "png"
            out_im = im.convert("RGBA")
        else:
            target_fmt, target_ext = "JPEG", "jpg"
            out_im = im.convert("RGB")
        out_path = converted_dir / f"{seq_name}.{target_ext}"
        save_kwargs = {"quality": 90, "optimize": True} if target_fmt == "JPEG" else {}
        out_im.save(out_path, format=target_fmt, **save_kwargs)

    original_path = None
    if KEEP_ORIGINAL:
        original_dir = job_dir / "original"
        original_dir.mkdir(parents=True, exist_ok=True)
        original_path = original_dir / f"{seq_name}.{orig_ext}"
        original_path.write_bytes(raw)

    width, height = im.size
    record = {
        "id": file_id,
        "job_id": job_id,
        "seq": job_seq,
        "filename": out_path.name,
        "local_path": _relpath(out_path),
        "original_path": _relpath(original_path) if original_path else None,
        "source_url": url,
        "source_page": source_page or None,
        "title": title or None,
        "caption": None,
        "mime_type": f"image/{target_fmt.lower()}",
        "width": width,
        "height": height,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "drive_file_id": None,
        "drive_url": None,
    }
    db.insert_image(record)
    return record
