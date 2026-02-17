from http.server import BaseHTTPRequestHandler
import json
import requests
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

# ===== ENV VARIABLES =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
ALLOWED_USERS = list(map(int, os.environ.get("ALLOWED_USERS").split(",")))

# ===== IN-MEMORY STATE =====
user_states = {}

# ===== FORMATTER =====
def format_yen(amount):
    return f"¥{amount:,.0f}"

# ===== GOOGLE SHEETS HELPERS =====
def get_sheets_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials)

def append_to_sheet(type_tx, amount, category):
    service = get_sheets_service()

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

def get_today_summary():
    service = get_sheets_service()

    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D"
    ).execute()

    rows = result.get("values", [])
    today = datetime.now().strftime("%Y-%m-%d")

    income = 0
    expense = 0

    for row in rows[1:]:
        if len(row) >= 3 and row[0].startswith(today):
            if row[1] == "Pemasukan":
                income += int(row[2])
            elif row[1] == "Pengeluaran":
                expense += int(row[2])

    return income, expense

# ===== TELEGRAM HELPER =====
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

def main_keyboard():
    return {
        "keyboard": [["Pengeluaran", "Pemasukan"]],
        "resize_keyboard": True
    }

# ===== HANDLER =====
class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            message = data.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text")
            user_id = message.get("from", {}).get("id")

            # ===== SECURITY CHECK =====
            if user_id not in ALLOWED_USERS:
                self.send_response(200)
                self.end_headers()
                return

            if not chat_id or not text:
                self.send_response(200)
                self.end_headers()
                return

            # ===== COMMAND: TODAY SUMMARY =====
            if text == "/today":
                income, expense = get_today_summary()
                balance = income - expense

                msg = (
                    "📊 Ringkasan Hari Ini\n\n"
                    f"Pemasukan: {format_yen(income)}\n"
                    f"Pengeluaran: {format_yen(expense)}\n"
                    f"Saldo: {format_yen(balance)}"
                )

                send_message(chat_id, msg)
                self.send_response(200)
                self.end_headers()
                return

            state = user_states.get(chat_id)

            # ===== START / RESTART FLOW =====
            if text == "/start":
                send_message(chat_id, "Mau lapor apa?", main_keyboard())
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
                    self.send_response(200)
                    self.end_headers()
                    return

                amount = int(text)
                type_tx = state["type"]
                category = state["category"]

                append_to_sheet(type_tx, amount, category)

                formatted_amount = format_yen(amount)

                send_message(
                    chat_id,
                    f"✔️ {type_tx} {formatted_amount} untuk {category} disimpan."
                )

                # Auto restart flow
                send_message(chat_id, "Mau lapor lagi?", main_keyboard())
                user_states[chat_id] = {"step": "type"}

            else:
                send_message(chat_id, "Pilih jenis transaksi:", main_keyboard())
                user_states[chat_id] = {"step": "type"}

        except Exception as e:
            print(e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
