# sheets_logger.py
# Minimal Google Sheets appender for logging bot events.

import os
import json
from typing import Dict, Any, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


class SheetsLogger:
    """
    Append events to a Google Sheet tab.

    You must:
    - enable Google Sheets API
    - create a Service Account
    - share the spreadsheet with service account email as Editor

    Provide credentials either:
    - env F1_GOOGLE_SA_JSON (full JSON content)
    - env F1_GOOGLE_SA_FILE (path to JSON file in runtime)
    """
    def __init__(self, spreadsheet_id: str, tab_name: str, sa_json: str = "", sa_file: str = ""):
        self.spreadsheet_id = spreadsheet_id
        self.tab_name = tab_name or "log"
        self.sa_json = sa_json
        self.sa_file = sa_file
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        if not self.spreadsheet_id:
            return None

        info = None
        if self.sa_json:
            try:
                info = json.loads(self.sa_json)
            except Exception:
                info = None
        if info is None and self.sa_file:
            try:
                with open(self.sa_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
            except Exception:
                info = None
        if info is None:
            # no credentials - silently disable
            return None

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def log_event(self, event: Dict[str, Any]) -> None:
        """
        Append one row. This method must never crash the bot.
        """
        try:
            service = self._get_service()
            if service is None:
                return

            # Define column order (stable)
            cols = [
                "event",
                "timestamp",
                "case_id",
                "anonymous",
                "category_key",
                "category_label",
                "message_type",
                "text",
                "user_id",
                "username",
                "full_name",
                "status",
                "actor",
            ]
            row = [str(event.get(k, "")) for k in cols]

            body = {"values": [row]}
            rng = f"{self.tab_name}!A1"
            service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=rng,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
        except Exception:
            # intentionally swallow errors to avoid bot downtime
            return
