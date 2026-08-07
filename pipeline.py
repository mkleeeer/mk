import io
import os
from datetime import datetime
from pathlib import Path

from PIL import Image

try:
    import pillow_avif  # noqa: F401  (registers AVIF support with Pillow)
except ImportError:
    pass

import db
import net

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
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
    return str(path.relative_to(BASE_DIR)).replace("\\", "/")


def download_and_process(url: str, source_page: str = "", title: str = "", folder: str = "") -> dict:
    if not url:
        raise DownloadError("url이 필요합니다.")

    job_id = db.get_or_create_job(folder)

    try:
        resp = net.fetch_image(url, source_page or url)
        resp.raise_for_status()
    except Exception as e:
        raise DownloadError(f"다운로드 실패: {e}") from e

    raw = resp.content
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:
        content_type = resp.headers.get("Content-Type", "unknown")
        raise DownloadError(f"이미지로 인식할 수 없습니다 (Content-Type: {content_type}): {e}") from e

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
