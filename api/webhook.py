from http.server import BaseHTTPRequestHandler
import json
import requests
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

def append_to_sheet(amount, category, raw_text):
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    service = build("sheets", "v4", credentials=credentials)

    values = [[
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        amount,
        category,
        raw_text
    ]]

    body = {"values": values}

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body=body
    ).execute()

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            message = data.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text")

            if chat_id and text:
                parts = text.split(" ", 1)

                if len(parts) != 2 or not parts[0].isdigit():
                    reply_text = "Format salah. Contoh: 50000 makan"
                else:
                    amount = int(parts[0])
                    category = parts[1]

                    append_to_sheet(amount, category, text)

                    reply_text = f"✔️ {amount} disimpan ke kategori {category}"

                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": reply_text
                    }
                )

        except Exception as e:
            print(e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
