import io
import re
import zipfile
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template, send_file, Response, abort

import db
import drive
import net
import pipeline

app = Flask(__name__)
db.init_db()


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


@app.route("/")
def index():
    return render_template("index.html")


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
    app.run(debug=True, port=5000, threaded=True)
