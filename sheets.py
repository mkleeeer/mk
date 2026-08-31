from googleapiclient.discovery import build

import drive

SUBMISSIONS_HEADERS = ["id", "url", "source_page", "title", "folder", "status", "error", "created_at"]
IMAGES_HEADERS = [
    "id", "submission_id", "folder", "seq", "source_url", "source_page", "title", "status",
    "filename", "local_path", "original_path", "mime_type", "width", "height",
    "drive_file_id", "drive_url", "error", "created_at", "updated_at",
]


def get_service():
    return build("sheets", "v4", credentials=drive.get_credentials())


def create_spreadsheet(title: str) -> str:
    service = get_service()
    body = {
        "properties": {"title": title},
        "sheets": [
            {"properties": {"title": "submissions"}},
            {"properties": {"title": "images"}},
        ],
    }
    result = service.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    spreadsheet_id = result["spreadsheetId"]
    _set_row(spreadsheet_id, "submissions", 1, SUBMISSIONS_HEADERS)
    _set_row(spreadsheet_id, "images", 1, IMAGES_HEADERS)
    return spreadsheet_id


def _col_letter(idx: int) -> str:
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _set_row(spreadsheet_id: str, sheet_name: str, row_number: int, values: list) -> None:
    service = get_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A{row_number}",
        valueInputOption="RAW",
        body={"values": [[str(v) for v in values]]},
    ).execute()


def append_row(spreadsheet_id: str, sheet_name: str, row: dict, headers: list) -> None:
    values = [str(row.get(h, "") if row.get(h) is not None else "") for h in headers]
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


def read_rows(spreadsheet_id: str, sheet_name: str, headers: list) -> list:
    service = get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A2:Z"
    ).execute()
    rows = result.get("values", [])
    out = []
    for i, row in enumerate(rows):
        row = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, row))
        record["_row_number"] = i + 2
        out.append(record)
    return out


def update_row(spreadsheet_id: str, sheet_name: str, row_number: int, updates: dict, headers: list) -> None:
    service = get_service()
    data = []
    for key, value in updates.items():
        col_letter = _col_letter(headers.index(key))
        data.append({"range": f"{sheet_name}!{col_letter}{row_number}", "values": [[str(value)]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
