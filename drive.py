import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(__file__).parent
CLIENT_SECRET_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "token.json"
# drive.file: the app can only see/manage files *it* creates, not the user's whole Drive.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveNotConfigured(Exception):
    pass


def _load_client_config() -> dict:
    env_value = (os.environ.get("GOOGLE_CLIENT_SECRET_JSON") or "").strip()
    if env_value:
        try:
            return json.loads(env_value)
        except json.JSONDecodeError as e:
            raise DriveNotConfigured(
                f"GOOGLE_CLIENT_SECRET_JSON 환경변수가 올바른 JSON이 아닙니다 ({e}). "
                f"client_secret.json 파일 내용 전체를 다시 복사해서 넣어보세요."
            ) from e
    if CLIENT_SECRET_FILE.exists():
        return json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
    raise DriveNotConfigured(
        f"Google OAuth 클라이언트 설정이 없습니다. GOOGLE_CLIENT_SECRET_JSON 환경변수를 설정하거나 "
        f"{CLIENT_SECRET_FILE.name}을(를) 로컬에 두세요."
    )


def _load_saved_token() -> Credentials | None:
    env_value = (os.environ.get("GOOGLE_TOKEN_JSON") or "").strip()
    if env_value:
        try:
            return Credentials.from_authorized_user_info(json.loads(env_value), SCOPES)
        except json.JSONDecodeError as e:
            raise DriveNotConfigured(
                f"GOOGLE_TOKEN_JSON 환경변수가 올바른 JSON이 아닙니다 ({e}). "
                f"token.json 파일 내용 전체를 다시 복사해서 넣어보세요."
            ) from e
    if TOKEN_FILE.exists():
        return Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    return None


def _save_token(creds: Credentials) -> None:
    # Cloud deploys get their token from an env var and typically have no
    # durable disk to write back to, so only persist to disk locally.
    if not os.environ.get("GOOGLE_TOKEN_JSON"):
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


def get_credentials() -> Credentials:
    creds = _load_saved_token()

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)

    if creds and creds.valid:
        return creds

    # No usable token yet: only viable interactively (opens a local browser).
    # On a headless server this will fail fast rather than hang.
    client_config = _load_client_config()
    try:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        raise DriveNotConfigured(
            "저장된 Google 인증 토큰이 없고, 이 서버에서는 새로 브라우저 인증을 띄울 수 없습니다. "
            "로컬에서 먼저 인증한 뒤 token.json 내용을 GOOGLE_TOKEN_JSON 환경변수로 설정하세요."
        ) from e
    _save_token(creds)
    return creds


def get_service():
    return build("drive", "v3", credentials=get_credentials())


def get_or_create_folder(name: str, parent_id: str | None = None) -> str:
    service = get_service()
    query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)", spaces="drive").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def upload_file(local_path: str, filename: str, mime_type: str, parent_id: str | None = None) -> dict:
    service = get_service()
    metadata = {"name": filename}
    if parent_id:
        metadata["parents"] = [parent_id]
    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=False)
    file = service.files().create(
        body=metadata, media_body=media, fields="id, webViewLink"
    ).execute()
    return {"drive_file_id": file["id"], "drive_url": file["webViewLink"]}
