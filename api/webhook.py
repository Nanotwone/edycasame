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
ALLOWED_USERS = [int(os.environ.get("ALLOWED_USERS"))]


user_states = {}

def append_to_sheet(type_tx, amount, category):
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    service = build("sheets", "v4", credentials=credentials)

    values = [[
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        type_tx,
        amount,
        category
    ]]

    body = {"values": values}

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body=body
    ).execute()

def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        payload["reply_markup"] = keyboard

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload
    )

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            message = data.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text")

            if not chat_id or not text:
                return

            state = user_states.get(chat_id)

            if text == "/start":
                keyboard = {
                    "keyboard": [["Pengeluaran", "Pemasukan"]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True
                }
                send_message(chat_id, "Mau lapor apa?", keyboard)
                user_states[chat_id] = {"step": "type"}

            elif state and state["step"] == "type":
                user_states[chat_id]["type"] = text
                user_states[chat_id]["step"] = "category"
                send_message(chat_id, "Kategori?")

            elif state and state["step"] == "category":
                user_states[chat_id]["category"] = text
                user_states[chat_id]["step"] = "amount"
                send_message(chat_id, "Nominal?")

            elif state and state["step"] == "amount":
                if not text.isdigit():
                    send_message(chat_id, "Masukkan angka saja.")
                    return

                amount = int(text)
                type_tx = state["type"]
                category = state["category"]

                append_to_sheet(type_tx, amount, category)

                send_message(chat_id, f"✔️ {type_tx} {amount} untuk {category} disimpan.")

                user_states.pop(chat_id, None)

            else:
                send_message(chat_id, "Ketik /start untuk mulai.")

        except Exception as e:
            print(e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
