import io
import re
import threading
import zipfile
from urllib.parse import urlparse

import requests
from flask import Flask, request, jsonify, render_template, send_file, Response, abort

import db
import download_worker
import drive
import extractor_worker
import net
import pipeline
import sheets
from queue_config import POLL_SECONDS, SPREADSHEET_ID, SPREADSHEET_URL
from scrape import extract_images_from_html

app = Flask(__name__)
db.init_db()

# ---------------------------------------------------------------------------
# Background queue workers, controlled from the web UI instead of running as
# always-on console windows. Off by default; a click starts/stops them.
# ---------------------------------------------------------------------------

_workers = {
    "extractor": {"thread": None, "stop": None, "run_once": extractor_worker.run_once, "busy": threading.Lock()},
    "download": {"thread": None, "stop": None, "run_once": download_worker.run_once, "busy": threading.Lock()},
}
_workers_lock = threading.Lock()


def _run_once_exclusive(name: str, **kwargs) -> int:
    """Only one pass (auto-loop or manual button) runs at a time per worker,
    so a manual click can't grab the same sheet row the loop is mid-processing."""
    w = _workers[name]
    if not w["busy"].acquire(blocking=False):
        return 0
    try:
        return w["run_once"](**kwargs)
    finally:
        w["busy"].release()


def _worker_loop(name: str, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            _run_once_exclusive(name, stop_event=stop_event)
        except Exception as e:
            print(f"[{name}] loop error: {e}")
        stop_event.wait(POLL_SECONDS)


@app.route("/api/workers/status")
def api_workers_status():
    with _workers_lock:
        return jsonify({
            name: "running" if w["thread"] and w["thread"].is_alive() else "stopped"
            for name, w in _workers.items()
        })


@app.route("/api/workers/<name>/start", methods=["POST"])
def api_workers_start(name):
    if name not in _workers:
        return jsonify({"success": False, "error": "알 수 없는 워커"}), 404
    with _workers_lock:
        w = _workers[name]
        if w["thread"] and w["thread"].is_alive():
            return jsonify({"success": True, "status": "running"})
        stop_event = threading.Event()
        thread = threading.Thread(target=_worker_loop, args=(name, stop_event), daemon=True)
        w["stop"] = stop_event
        w["thread"] = thread
        thread.start()
    return jsonify({"success": True, "status": "running"})


@app.route("/api/workers/<name>/stop", methods=["POST"])
def api_workers_stop(name):
    if name not in _workers:
        return jsonify({"success": False, "error": "알 수 없는 워커"}), 404
    with _workers_lock:
        w = _workers[name]
        if w["stop"]:
            w["stop"].set()
    return jsonify({"success": True, "status": "stopped"})


@app.route("/api/workers/<name>/run-once", methods=["POST"])
def api_workers_run_once(name):
    """Process whatever's in the queue right now, once, regardless of
    whether the continuous auto-worker is running — for when a URL just
    landed in the sheet and the next 10s poll feels too slow to wait for."""
    if name not in _workers:
        return jsonify({"success": False, "error": "알 수 없는 워커"}), 404
    try:
        handled = _run_once_exclusive(name)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "handled": handled})


@app.route("/api/queue/status")
def api_queue_status():
    def counts(rows, headers):
        c = {}
        for r in rows:
            status = (r.get("status") or "(없음)").strip() or "(없음)"
            c[status] = c.get(status, 0) + 1
        return c

    try:
        submissions = sheets.read_rows(SPREADSHEET_ID, "submissions", sheets.SUBMISSIONS_HEADERS)
        images = sheets.read_rows(SPREADSHEET_ID, "images", sheets.IMAGES_HEADERS)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "spreadsheet_url": SPREADSHEET_URL,
        "submissions": {"total": len(submissions), "counts": counts(submissions, sheets.SUBMISSIONS_HEADERS)},
        "images": {"total": len(images), "counts": counts(images, sheets.IMAGES_HEADERS)},
    })


_QUEUE_SHEETS = {
    "submissions": {
        "headers": sheets.SUBMISSIONS_HEADERS,
        "worker": extractor_worker,
        "worker_name": "extractor",
    },
    "images": {
        "headers": sheets.IMAGES_HEADERS,
        "worker": download_worker,
        "worker_name": "download",
    },
}

# Rows in these statuses are done and not worth listing by default — the UI
# only shows what's still actionable unless asked to include everything.
_TERMINAL_STATUSES = {"done", "downloaded", "discarded"}


@app.route("/api/queue/list")
def api_queue_list():
    sheet = request.args.get("sheet", "images")
    if sheet not in _QUEUE_SHEETS:
        return jsonify({"error": "sheet는 submissions 또는 images"}), 400
    show_all = request.args.get("all") == "1"
    cfg = _QUEUE_SHEETS[sheet]
    try:
        rows = sheets.read_rows(SPREADSHEET_ID, sheet, cfg["headers"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not show_all:
        rows = [r for r in rows if (r.get("status") or "pending") not in _TERMINAL_STATUSES]
    rows.sort(key=lambda r: r["_row_number"], reverse=True)

    folders = sorted({(r.get("folder") or "") for r in rows if r.get("folder")})
    return jsonify({"rows": rows[:300], "truncated": len(rows) > 300, "folders": folders})


@app.route("/api/queue/action", methods=["POST"])
def api_queue_action():
    data = request.get_json(force=True) or {}
    sheet = data.get("sheet")
    action = data.get("action")
    row_numbers = set(int(n) for n in (data.get("row_numbers") or []))
    if sheet not in _QUEUE_SHEETS:
        return jsonify({"success": False, "error": "sheet는 submissions 또는 images"}), 400
    if not row_numbers:
        return jsonify({"success": False, "error": "row_numbers가 필요합니다."}), 400

    cfg = _QUEUE_SHEETS[sheet]
    try:
        if action == "discard":
            sheets.update_rows(
                SPREADSHEET_ID, sheet,
                {n: {"status": "discarded"} for n in row_numbers},
                cfg["headers"],
            )
            return jsonify({"success": True, "handled": len(row_numbers)})
        elif action == "process":
            w = _workers[cfg["worker_name"]]
            if not w["busy"].acquire(blocking=False):
                return jsonify({"success": False, "error": "이 워커가 이미 처리 중입니다. 잠시 후 다시 시도하세요."}), 409
            try:
                handled = cfg["worker"].process_rows(row_numbers)
            finally:
                w["busy"].release()
            return jsonify({"success": True, "handled": handled})
        else:
            return jsonify({"success": False, "error": "action은 process 또는 discard"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/")
def index():
    return render_template("index.html", spreadsheet_url=SPREADSHEET_URL)


@app.route("/extract", methods=["POST"])
def extract():
    data = request.get_json(force=True)
    target = (data.get("url") or "").strip()
    if not target:
        return jsonify({"error": "URL을 입력해주세요."}), 400
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    try:
        resp = net.fetch_page(target)
        resp.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": f"페이지를 불러오지 못했습니다: {e}"}), 400

    content_type = resp.headers.get("Content-Type", "")

    if content_type.startswith("image/"):
        return jsonify({"page_url": target, "images": [{"url": target, "alt": "(직접 이미지 링크)"}]})

    images = extract_images_from_html(resp.text, resp.url)
    return jsonify({"page_url": resp.url, "images": images})


@app.route("/proxy")
def proxy():
    image_url = request.args.get("url", "")
    page_url = request.args.get("ref", image_url)
    if not image_url:
        abort(400)
    try:
        r = net.fetch_image(image_url, page_url, stream=True)
        r.raise_for_status()
    except requests.RequestException:
        abort(502)
    content_type = r.headers.get("Content-Type", "image/jpeg")
    return Response(r.content, content_type=content_type)


def filename_from_url(image_url: str, fallback_ext=".jpg") -> str:
    path = urlparse(image_url).path
    name = path.rsplit("/", 1)[-1] or "image"
    name = re.sub(r"[^\w\.\-]", "_", name)
    if "." not in name:
        name += fallback_ext
    return name[:150]


@app.route("/download")
def download():
    image_url = request.args.get("url", "")
    page_url = request.args.get("ref", image_url)
    if not image_url:
        abort(400)
    try:
        r = net.fetch_image(image_url, page_url)
        r.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

    filename = filename_from_url(image_url)
    return send_file(
        io.BytesIO(r.content),
        mimetype=r.headers.get("Content-Type", "application/octet-stream"),
        as_attachment=True,
        download_name=filename,
    )


@app.route("/download_zip", methods=["POST"])
def download_zip():
    data = request.get_json(force=True)
    urls = data.get("urls") or []
    page_url = data.get("page_url", "")
    if not urls:
        return jsonify({"error": "선택된 이미지가 없습니다."}), 400

    buffer = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for image_url in urls:
            try:
                r = net.fetch_image(image_url, page_url)
                r.raise_for_status()
            except requests.RequestException:
                continue
            name = filename_from_url(image_url)
            base, ext = name.rsplit(".", 1) if "." in name else (name, "jpg")
            candidate = name
            n = 1
            while candidate in used_names:
                candidate = f"{base}_{n}.{ext}"
                n += 1
            used_names.add(candidate)
            zf.writestr(candidate, r.content)

    buffer.seek(0)
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name="images.zip")


# ---------------------------------------------------------------------------
# AI-facing registry API: fetch image URLs found elsewhere (e.g. by an AI web
# search) and turn them into real files on disk, tracked in a SQLite registry
# so they can be referenced again later ("저장한 이미지 1, 3, 5번을 ...").
# ---------------------------------------------------------------------------

@app.route("/api/images/download", methods=["POST"])
def api_download_image():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "url이 필요합니다."}), 400

    try:
        record = pipeline.download_and_process(
            url=url,
            source_page=(data.get("source_page") or "").strip(),
            title=(data.get("title") or "").strip(),
            folder=(data.get("folder") or "").strip(),
        )
    except pipeline.DownloadError as e:
        return jsonify({"success": False, "url": url, "error": str(e)}), 400

    return jsonify({"success": True, "file_id": record["id"], **record})


@app.route("/api/images/download-batch", methods=["POST"])
def api_download_batch():
    data = request.get_json(force=True) or {}
    images = data.get("images") or []
    folder = (data.get("folder") or "").strip()
    if not images:
        return jsonify({"success": False, "error": "images 배열이 필요합니다."}), 400

    results = []
    for item in images:
        url = (item.get("url") or "").strip()
        if not url:
            results.append({"success": False, "url": url, "error": "url이 필요합니다."})
            continue
        try:
            record = pipeline.download_and_process(
                url=url,
                source_page=(item.get("source_page") or "").strip(),
                title=(item.get("title") or "").strip(),
                folder=folder,
            )
            results.append({"success": True, "file_id": record["id"], **record})
        except pipeline.DownloadError as e:
            results.append({"success": False, "url": url, "error": str(e)})

    job_id = results[0]["job_id"] if results and results[0].get("success") else db.get_or_create_job(folder)
    succeeded = sum(1 for r in results if r["success"])
    return jsonify({
        "success": True,
        "job_id": job_id,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    })


@app.route("/api/images/<image_id>")
def api_get_image(image_id):
    record = db.get_image(image_id)
    if not record:
        return jsonify({"error": "not found"}), 404
    return jsonify(record)


@app.route("/api/jobs/<job_id>")
def api_get_job(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


def _upload_one(record: dict, parent_id: str) -> dict:
    if record["drive_file_id"]:
        return {"success": True, "file_id": record["id"], **record}
    local_path = pipeline.BASE_DIR / record["local_path"]
    result = drive.upload_file(
        local_path=str(local_path),
        filename=record["filename"],
        mime_type=record["mime_type"] or "application/octet-stream",
        parent_id=parent_id,
    )
    db.update_image_drive(record["id"], result["drive_file_id"], result["drive_url"])
    record = db.get_image(record["id"])
    return {"success": True, "file_id": record["id"], **record}


@app.route("/api/drive/upload", methods=["POST"])
def api_drive_upload():
    data = request.get_json(force=True) or {}
    file_id = (data.get("file_id") or "").strip()
    record = db.get_image(file_id)
    if not record:
        return jsonify({"success": False, "error": "이미지를 찾을 수 없습니다."}), 404

    try:
        parent_id = data.get("folder_id") or drive.get_or_create_folder(record["job_id"])
        result = _upload_one(record, parent_id)
    except drive.DriveNotConfigured as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"업로드 실패: {e}"}), 500

    return jsonify(result)


@app.route("/api/drive/upload-job", methods=["POST"])
def api_drive_upload_job():
    data = request.get_json(force=True) or {}
    job_id = (data.get("job_id") or "").strip()
    job = db.get_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "job을 찾을 수 없습니다."}), 404

    try:
        parent_id = data.get("folder_id") or drive.get_or_create_folder(job_id)
    except drive.DriveNotConfigured as e:
        return jsonify({"success": False, "error": str(e)}), 400

    results = []
    for record in job["images"]:
        try:
            results.append(_upload_one(record, parent_id))
        except Exception as e:
            results.append({"success": False, "file_id": record["id"], "error": f"업로드 실패: {e}"})

    succeeded = sum(1 for r in results if r["success"])
    return jsonify({
        "success": True,
        "job_id": job_id,
        "folder_id": parent_id,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    })


if __name__ == "__main__":
    app.run(debug=False, port=5000, threaded=True)
