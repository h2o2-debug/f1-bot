import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsLogger:
    def __init__(
        self,
        spreadsheet_id: str,
        tab_name: str = "log",
        sa_json: str = "",
        sa_file: str = "",
    ):
        self.spreadsheet_id = spreadsheet_id
        self.tab_name = tab_name

        creds = None

        if sa_json:
            info = json.loads(sa_json)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        elif sa_file:
            creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
        else:
            raise ValueError("No Google service account credentials provided")

        self.service = build("sheets", "v4", credentials=creds)

    def log_event(self, event: Dict[str, Any]) -> None:
        row = [
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            event.get("event", ""),
            event.get("case_id", ""),
            event.get("anonymous", ""),
            event.get("category_key", ""),
            event.get("category_label", ""),
            event.get("message_type", ""),
            event.get("text", ""),
            event.get("user_id", ""),
            event.get("username", ""),
            event.get("full_name", ""),
            event.get("status", ""),
            event.get("actor", ""),
        ]

        body = {"values": [row]}

        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ).execute()


# --- compatibility wrapper for bot.py ---

_logger_instance: Optional[SheetsLogger] = None


def _get_logger() -> Optional[SheetsLogger]:
    global _logger_instance

    if _logger_instance is not None:
        return _logger_instance

    spreadsheet_id = os.environ.get("F1_SHEETS_ID", "").strip()
    tab_name = os.environ.get("F1_SHEETS_TAB", "log").strip()
    sa_json = os.environ.get("F1_GOOGLE_SA_JSON", "").strip()
    sa_file = os.environ.get("F1_GOOGLE_SA_FILE", "").strip()

    if not spreadsheet_id:
        return None

    try:
        _logger_instance = SheetsLogger(
            spreadsheet_id=spreadsheet_id,
            tab_name=tab_name,
            sa_json=sa_json,
            sa_file=sa_file,
        )
        return _logger_instance
    except Exception:
        return None


def append_row(row: List[Any]) -> None:
    logger = _get_logger()
    if logger is None:
        return

    event = {
        "event": row[0] if len(row) > 0 else "",
        "case_id": row[1] if len(row) > 1 else "",
        "category_label": row[2] if len(row) > 2 else "",
        "anonymous": row[3] if len(row) > 3 else "",
        "full_name": row[4] if len(row) > 4 else "",
        "username": row[5] if len(row) > 5 else "",
        "user_id": row[6] if len(row) > 6 else "",
        "text": row[7] if len(row) > 7 else "",
        "message_type": row[8] if len(row) > 8 else "",
        "status": row[9] if len(row) > 9 else "",
        "actor": row[10] if len(row) > 10 else "",
    }

    logger.log_event(event)