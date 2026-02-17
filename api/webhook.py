from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
ALLOWED_USERS = list(map(int, os.environ.get("ALLOWED_USERS").split(",")))

user_states = {}

# ================= TIME =================
def now_wib():
    return datetime.now(timezone(timedelta(hours=7)))

def format_yen(amount):
    return f"¥{amount:,.0f}"

# ================= GOOGLE =================
def get_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials)

def get_rows():
    service = get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D"
    ).execute()
    return result.get("values", [])

# ================= TRANSACTION =================
def add_transaction(type_tx, amount, category):
    service = get_service()
    values = [[
        now_wib().strftime("%Y-%m-%d %H:%M:%S"),
        type_tx,
        amount,
        category
    ]]
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()

# ================= SUMMARY =================
def calculate_summary(period):
    rows = get_rows()
    now = now_wib()

    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    year = now.strftime("%Y")

    income = 0
    expense = 0
    cat_income = defaultdict(int)
    cat_expense = defaultdict(int)

    for row in rows[1:]:
        if len(row) < 4:
            continue

        date = row[0].strip()
        type_tx = row[1].strip()
        amount = int(row[2])
        category = row[3]

        match = (
            (period == "today" and date.startswith(today)) or
            (period == "month" and date.startswith(month)) or
            (period == "year" and date.startswith(year)) or
            (period == "all")
        )

        if not match:
            continue

        if type_tx == "Income":
            income += amount
            cat_income[category] += amount
        elif type_tx == "Expense":
            expense += amount
            cat_expense[category] += amount

    return income, expense, cat_income, cat_expense

# ================= TELEGRAM =================
def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_kb():
    return {
        "keyboard": [["Income", "Expense"], ["Other"]],
        "resize_keyboard": True
    }

def other_kb():
    return {
        "keyboard": [
            ["Today", "Month", "Year"],
            ["Top Income", "Top Expense"],
            ["Back"]
        ],
        "resize_keyboard": True
    }

# ================= QUICK ENTRY =================
def parse_quick(text):
    match = re.match(r"^([+-]?)(\d+)\s+(.+)$", text.strip())
    if not match:
        return None
    sign, amount, category = match.groups()
    amount = int(amount)
    type_tx = "Income" if sign == "+" else "Expense"
    return type_tx, amount, category

# ================= HANDLER =================
class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            message = data.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "").strip()
            user_id = message.get("from", {}).get("id")

            if user_id not in ALLOWED_USERS or not chat_id or not text:
                self.send_response(200); self.end_headers(); return

            if text == "/start":
                send(chat_id, "Main menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            # QUICK ENTRY
            quick = parse_quick(text)
            if quick:
                type_tx, amount, category = quick
                add_transaction(type_tx, amount, category)
                send(chat_id,
                     f"Saved {type_tx} {format_yen(amount)} for {category}",
                     main_kb())
                self.send_response(200); self.end_headers(); return

            # MENU
            if text == "Other":
                send(chat_id, "Choose:", other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Back":
                send(chat_id, "Main menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            # RECAP
            if text in ["Today", "Month", "Year"]:
                income, expense, _, _ = calculate_summary(text.lower())
                balance = income - expense
                send(chat_id,
                     f"📊 {text}\n\n"
                     f"Income: {format_yen(income)}\n"
                     f"Expense: {format_yen(expense)}\n"
                     f"Balance: {format_yen(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            # TOP
            if text == "Top Income":
                _, _, cat_income, _ = calculate_summary("all")
                top = sorted(cat_income.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "💰 Top Income:\n\n"
                for i, (cat, amt) in enumerate(top, 1):
                    msg += f"{i}. {cat} - {format_yen(amt)}\n"
                send(chat_id, msg, other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Top Expense":
                _, _, _, cat_expense = calculate_summary("all")
                top = sorted(cat_expense.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "🔥 Top Expense:\n\n"
                for i, (cat, amt) in enumerate(top, 1):
                    msg += f"{i}. {cat} - {format_yen(amt)}\n"
                send(chat_id, msg, other_kb())
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Main menu:", main_kb())
            self.send_response(200); self.end_headers(); return

        except Exception as e:
            print("ERROR:", e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
