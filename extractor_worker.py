import time
import uuid
from datetime import datetime

import net
import sheets
from queue_config import POLL_SECONDS, SPREADSHEET_ID
from scrape import extract_images_from_html

# Real-pixel-size thresholds for dropping obviously-junk candidates before
# they're ever queued for download (icons, tracking pixels, thin banner
# strips) — same shape of heuristic as newspaper3k's image scorer
# (minimal_area / min width / max aspect ratio), tuned looser on the ratio
# than newspaper3k's 16:9 since that would reject plenty of legitimate
# portrait/landscape photos.
MIN_CANDIDATE_AREA = 5000
MIN_CANDIDATE_WIDTH = 80
MAX_CANDIDATE_ASPECT_RATIO = 3.0


def _passes_size_filter(dimensions) -> bool:
    if dimensions is None:
        return True  # couldn't determine size — don't punish it for that
    width, height = dimensions
    if width * height < MIN_CANDIDATE_AREA:
        return False
    if width < MIN_CANDIDATE_WIDTH:
        return False
    if max(width, height) / max(min(width, height), 1) > MAX_CANDIDATE_ASPECT_RATIO:
        return False
    return True


def _filter_candidates_by_size(candidates: list, page_url: str) -> list:
    kept = []
    for cand in candidates:
        dimensions = net.probe_image_dimensions(cand["url"], page_url)
        if _passes_size_filter(dimensions):
            kept.append(cand)
        else:
            print(f"[extractor] dropping small/odd-shaped candidate ({dimensions}): {cand['url']}")
    return kept


def process_submission(row: dict) -> None:
    url = (row.get("url") or "").strip()
    folder = row.get("folder") or ""
    source_page = row.get("source_page") or ""
    title = row.get("title") or ""
    row_number = row["_row_number"]

    print(f"[extractor] processing row {row_number}: {url}")

    try:
        sheets.update_row(
            SPREADSHEET_ID, "submissions", row_number,
            {"status": "processing"}, sheets.SUBMISSIONS_HEADERS,
        )
        resp = net.fetch_page(url)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")

        if content_type.startswith("image/"):
            candidates = [{"url": url, "alt": title}]
        else:
            candidates = extract_images_from_html(resp.text, resp.url)
            if not source_page:
                source_page = resp.url
            candidates = _filter_candidates_by_size(candidates, resp.url)

        if not candidates:
            sheets.update_row(
                SPREADSHEET_ID, "submissions", row_number,
                {"status": "error", "error": "이미지를 찾지 못했습니다."}, sheets.SUBMISSIONS_HEADERS,
            )
            return

        now = datetime.now().isoformat(timespec="seconds")
        image_rows = [
            {
                "id": f"cand_{uuid.uuid4().hex[:12]}",
                "submission_id": row.get("id", ""),
                "folder": folder,
                "seq": i,
                "source_url": cand["url"],
                "source_page": source_page or url,
                "title": title or cand.get("alt", ""),
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            for i, cand in enumerate(candidates, start=1)
        ]
        sheets.append_rows(SPREADSHEET_ID, "images", image_rows, sheets.IMAGES_HEADERS)

        sheets.update_row(
            SPREADSHEET_ID, "submissions", row_number,
            {"status": "done", "error": ""}, sheets.SUBMISSIONS_HEADERS,
        )
        print(f"[extractor] row {row_number}: found {len(candidates)} image(s)")

    except Exception as e:
        sheets.update_row(
            SPREADSHEET_ID, "submissions", row_number,
            {"status": "error", "error": str(e)[:300]}, sheets.SUBMISSIONS_HEADERS,
        )
        print(f"[extractor] row {row_number} failed: {e}")


def process_rows(row_numbers: set) -> int:
    """Process exactly these rows (by sheet row number), regardless of their
    current status — used by the web UI's selective/per-folder processing."""
    rows = sheets.read_rows(SPREADSHEET_ID, "submissions", sheets.SUBMISSIONS_HEADERS)
    targets = [r for r in rows if r["_row_number"] in row_numbers]
    handled = 0
    for row in targets:
        try:
            process_submission(row)
        except Exception as e:
            print(f"[extractor] row {row['_row_number']} unrecoverable: {e}")
        handled += 1
    return handled


def run_once(stop_event=None) -> int:
    rows = sheets.read_rows(SPREADSHEET_ID, "submissions", sheets.SUBMISSIONS_HEADERS)
    pending = [r for r in rows if (r.get("status") or "").strip() in ("", "pending")]
    handled = 0
    for row in pending:
        if stop_event is not None and stop_event.is_set():
            break
        # One row's failure (including a failed error-status write) must not
        # stop the rest of the batch from being attempted.
        try:
            process_submission(row)
        except Exception as e:
            print(f"[extractor] row {row['_row_number']} unrecoverable: {e}")
        handled += 1
    return handled


def main():
    print(f"[extractor] watching {SPREADSHEET_ID} every {POLL_SECONDS}s")
    while True:
        try:
            n = run_once()
            if n:
                print(f"[extractor] handled {n} submission(s)")
        except Exception as e:
            print(f"[extractor] loop error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
