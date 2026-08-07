import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import db
import drive
import pipeline

db.init_db()

# The streamable-http transport rejects requests whose Host/Origin aren't on
# this list (DNS-rebinding protection). Render's own hostname is picked up
# automatically; MCP_ALLOWED_HOSTS lets you add more (comma-separated) if the
# service is reachable under another domain too.
_allowed_hosts = ["127.0.0.1", "localhost"]
_allowed_origins = ["http://127.0.0.1", "http://localhost"]
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host:
    _allowed_hosts.append(_render_host)
    _allowed_origins.append(f"https://{_render_host}")
for _extra in os.environ.get("MCP_ALLOWED_HOSTS", "").split(","):
    _extra = _extra.strip()
    if _extra:
        _allowed_hosts.append(_extra)
        _allowed_origins.append(f"https://{_extra}")

mcp = FastMCP(
    name="image-crawler",
    streamable_http_path="/",  # mounted at /mcp externally in asgi.py; avoid a doubled /mcp/mcp path
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    ),
    instructions=(
        "Downloads images found elsewhere on the web into real files, converts "
        "them to JPG/PNG when needed, tracks them in a registry, and can upload "
        "them to the user's Google Drive. Use download_image/download_images "
        "once you already have image URLs (e.g. from a web search) — this tool "
        "does not search the web itself."
    ),
)


@mcp.tool()
def download_image(url: str, source_page: str = "", title: str = "", folder: str = "") -> dict:
    """Download a single image URL, converting it to JPG/PNG if needed, and record it in the registry.

    Args:
        url: Direct URL to the image file.
        source_page: The page the image was found on (used as Referer to get past hotlink protection).
        title: Short human-readable label for this image.
        folder: Job/collection name to group this image under. Images saved under the same
            folder can be listed later with get_job. Omit to use today's date as the default job.
    """
    try:
        record = pipeline.download_and_process(url=url, source_page=source_page, title=title, folder=folder)
        return {"success": True, "file_id": record["id"], **record}
    except pipeline.DownloadError as e:
        return {"success": False, "url": url, "error": str(e)}


@mcp.tool()
def download_images(images: list[dict], folder: str = "") -> dict:
    """Download several image URLs at once into the same folder/job. Failures don't stop the batch.

    Args:
        images: List of {"url": ..., "source_page": ... (optional), "title": ... (optional)}.
        folder: Job/collection name to group these images under.
    """
    results = []
    for item in images:
        url = (item.get("url") or "").strip()
        if not url:
            results.append({"success": False, "url": url, "error": "url이 필요합니다."})
            continue
        try:
            record = pipeline.download_and_process(
                url=url,
                source_page=item.get("source_page", ""),
                title=item.get("title", ""),
                folder=folder,
            )
            results.append({"success": True, "file_id": record["id"], **record})
        except pipeline.DownloadError as e:
            results.append({"success": False, "url": url, "error": str(e)})

    job_id = results[0]["job_id"] if results and results[0].get("success") else db.get_or_create_job(folder)
    succeeded = sum(1 for r in results if r["success"])
    return {
        "success": True,
        "job_id": job_id,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


@mcp.tool()
def get_image(file_id: str) -> dict:
    """Look up a previously downloaded image's metadata (local path, source, Drive link if uploaded)."""
    record = db.get_image(file_id)
    return record if record else {"error": "not found"}


@mcp.tool()
def get_job(job_id: str) -> dict:
    """List all images saved under a given folder/job name, e.g. to recall 'the yen chart images from earlier'."""
    job = db.get_job(job_id)
    return job if job else {"error": "not found"}


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


@mcp.tool()
def upload_to_drive(file_id: str, folder_id: str = "") -> dict:
    """Upload one previously-downloaded image to the user's Google Drive.

    Args:
        file_id: The file_id returned by download_image/download_images.
        folder_id: Optional existing Drive folder ID to upload into. If omitted, a
            Drive folder named after the image's job is created/reused automatically.
    """
    record = db.get_image(file_id)
    if not record:
        return {"success": False, "error": "이미지를 찾을 수 없습니다."}
    try:
        parent_id = folder_id or drive.get_or_create_folder(record["job_id"])
        return _upload_one(record, parent_id)
    except drive.DriveNotConfigured as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"업로드 실패: {e}"}


@mcp.tool()
def upload_job_to_drive(job_id: str, folder_id: str = "") -> dict:
    """Upload every image in a folder/job to the user's Google Drive at once.

    Args:
        job_id: The folder/job name (as passed to download_image/download_images, or returned as job_id).
        folder_id: Optional existing Drive folder ID to upload into. If omitted, a
            Drive folder named after job_id is created/reused automatically.
    """
    job = db.get_job(job_id)
    if not job:
        return {"success": False, "error": "job을 찾을 수 없습니다."}
    try:
        parent_id = folder_id or drive.get_or_create_folder(job_id)
    except drive.DriveNotConfigured as e:
        return {"success": False, "error": str(e)}

    results = []
    for record in job["images"]:
        try:
            results.append(_upload_one(record, parent_id))
        except Exception as e:
            results.append({"success": False, "file_id": record["id"], "error": f"업로드 실패: {e}"})

    succeeded = sum(1 for r in results if r["success"])
    return {
        "success": True,
        "job_id": job_id,
        "folder_id": parent_id,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
