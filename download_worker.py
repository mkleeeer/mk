import time
from datetime import datetime

import pipeline
import sheets
from queue_config import POLL_SECONDS, SPREADSHEET_ID


def process_image(row: dict) -> None:
    row_number = row["_row_number"]
    url = (row.get("source_url") or "").strip()
    print(f"[download] processing row {row_number}: {url}")

    sheets.update_row(
        SPREADSHEET_ID, "images", row_number,
        {"status": "downloading"}, sheets.IMAGES_HEADERS,
    )

    try:
        record = pipeline.download_and_process(
            url=url,
            source_page=row.get("source_page") or "",
            title=row.get("title") or "",
            folder=row.get("folder") or "",
        )
        sheets.update_row(
            SPREADSHEET_ID, "images", row_number,
            {
                "status": "downloaded",
                "filename": record["filename"],
                "local_path": record["local_path"],
                "original_path": record["original_path"] or "",
                "mime_type": record["mime_type"],
                "width": record["width"],
                "height": record["height"],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            sheets.IMAGES_HEADERS,
        )
        print(f"[download] row {row_number}: saved -> {record['local_path']}")
    except pipeline.DownloadError as e:
        sheets.update_row(
            SPREADSHEET_ID, "images", row_number,
            {"status": "failed", "error": str(e)[:300], "updated_at": datetime.now().isoformat(timespec="seconds")},
            sheets.IMAGES_HEADERS,
        )
        print(f"[download] row {row_number} failed: {e}")


def run_once() -> int:
    rows = sheets.read_rows(SPREADSHEET_ID, "images", sheets.IMAGES_HEADERS)
    pending = [r for r in rows if (r.get("status") or "").strip() in ("", "pending")]
    for row in pending:
        process_image(row)
    return len(pending)


def main():
    print(f"[download] watching {SPREADSHEET_ID} every {POLL_SECONDS}s")
    while True:
        try:
            n = run_once()
            if n:
                print(f"[download] handled {n} image(s)")
        except Exception as e:
            print(f"[download] loop error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
