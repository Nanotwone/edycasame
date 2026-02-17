from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta

# ================= ENV =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

allowed_raw = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS = [int(x) for x in allowed_raw.split(",") if x.strip().isdigit()]

user_states = {}

# ================= TIME =================
def now_wib():
    return datetime.now(timezone(timedelta(hours=7)))

# ================= FORMAT =================
def format_currency(amount):
    return f"{amount:,.0f} €"

def balance_message(balance):
    if balance >= 0:
        if balance >= 1000000:
            return f"{format_currency(balance)} 🎉 (Excellent!)"
        return f"{format_currency(balance)} 💰 (Good Job!)"
    else:
        if abs(balance) >= 1000000:
            return f"{format_currency(balance)} 🚨 (Debt Alert!)"
        return f"{format_currency(balance)} ⚠️ (Be careful!)"

# ================= GOOGLE =================
def get_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials)

def get_sheet(range_name):
    service = get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=range_name
    ).execute()
    return result.get("values", [])

def normalize_row(row):
    clean = row[:4]
    while len(clean) < 4:
        clean.append("")
    return clean

# ================= CATEGORY =================
def get_categories():
    rows = get_sheet("Categories!A:B")
    data = {"Income": [], "Expense": []}
    for row in rows[1:]:
        if len(row) >= 2:
            data[row[0].strip()].append(row[1].strip())
    return data

def add_category(type_tx, name):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Categories!A:B",
        valueInputOption="RAW",
        body={"values": [[type_tx, name]]}
    ).execute()

def delete_category(name):
    rows = get_sheet("Categories!A:B")
    if not rows:
        return False

    header = rows[0]
    remaining = [header]
    found = False

    for row in rows[1:]:
        if len(row) >= 2 and row[1].strip().lower() == name.lower():
            found = True
            continue
        remaining.append(row)

    if not found:
        return False

    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Categories!A2:Z"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Categories!A1",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

    return True

# ================= TRANSACTION =================
def add_transaction(type_tx, amount, category):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": [[
            now_wib().strftime("%Y-%m-%d %H:%M:%S"),
            type_tx,
            amount,
            category
        ]]}
    ).execute()

def get_transactions():
    return get_sheet("Sheet1!A:D")

# ================= SUMMARY =================
def calculate_summary(period):
    rows = get_transactions()
    now = now_wib()

    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")

    income = 0
    expense = 0

    for row in rows[1:]:
        if len(row) < 4:
            continue

        date = str(row[0]).strip()
        type_tx = str(row[1]).strip().lower()

        try:
            amount = int(float(row[2]))
        except:
            continue

        match = (
            (period == "today" and date.startswith(today)) or
            (period == "month" and date.startswith(month)) or
            (period == "all")
        )

        if not match:
            continue

        if type_tx == "income":
            income += amount
        elif type_tx == "expense":
            expense += amount

    balance = income - expense
    return income, expense, balance

# ================= FLUSH =================
def flush_type_today(type_tx):
    rows = get_transactions()
    if not rows:
        return

    today = now_wib().strftime("%Y-%m-%d")
    type_tx = type_tx.lower()

    remaining = [normalize_row(rows[0])]

    for row in rows[1:]:
        row = normalize_row(row)
        date = str(row[0]).strip()
        row_type = str(row[1]).strip().lower()

        if row_type == type_tx and date.startswith(today):
            continue

        remaining.append(row)

    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:Z"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

def flush_month():
    rows = get_transactions()
    if not rows:
        return

    month = now_wib().strftime("%Y-%m")
    remaining = [normalize_row(rows[0])]

    for row in rows[1:]:
        row = normalize_row(row)
        if row[0].startswith(month):
            continue
        remaining.append(row)

    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:Z"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

def flush_all():
    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:Z"
    ).execute()

# ================= TELEGRAM =================
def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_kb():
    return {"keyboard": [["Income", "Expense"], ["Other"]],
            "resize_keyboard": True}

def other_kb():
    return {"keyboard": [["Today", "This Month", "All"],
                         ["Manage Category"],
                         ["Flush Menu"],
                         ["Back"]],
            "resize_keyboard": True}

def manage_kb():
    return {"keyboard": [["+ Add Category"],
                         ["Delete Category"],
                         ["Back"]],
            "resize_keyboard": True}

def category_kb(type_tx):
    categories = get_categories().get(type_tx, [])
    return {"keyboard": [[c] for c in categories] +
                        [["+ Add Category"], ["Back"]],
            "resize_keyboard": True}

def flush_kb():
    return {"keyboard": [["Flush Income Today"],
                         ["Flush Expense Today"],
                         ["Flush Month"],
                         ["Flush All"],
                         ["Back"]],
            "resize_keyboard": True}

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
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
            message = data.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "").strip()
            user_id = message.get("from", {}).get("id")

            if user_id not in ALLOWED_USERS:
                self.send_response(200); self.end_headers(); return

            state = user_states.get(chat_id)

            # FLUSH
            if text == "Flush Menu":
                send(chat_id, "Choose:", flush_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Income Today":
                flush_type_today("Income")
                send(chat_id, "Income today deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Expense Today":
                flush_type_today("Expense")
                send(chat_id, "Expense today deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Month":
                flush_month()
                send(chat_id, "This month deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush All":
                flush_all()
                send(chat_id, "All transactions deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            # RECAP
            if text == "Today":
                income, expense, balance = calculate_summary("today")
                send(chat_id,
                     f"Today\nIncome: {format_currency(income)}\n"
                     f"Expense: {format_currency(expense)}\n"
                     f"Balance: {balance_message(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "This Month":
                income, expense, balance = calculate_summary("month")
                send(chat_id,
                     f"This Month\nIncome: {format_currency(income)}\n"
                     f"Expense: {format_currency(expense)}\n"
                     f"Balance: {balance_message(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "All":
                income, expense, balance = calculate_summary("all")
                send(chat_id,
                     f"All\nIncome: {format_currency(income)}\n"
                     f"Expense: {format_currency(expense)}\n"
                     f"Balance: {balance_message(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            # START
            if text == "/start":
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Main Menu:", main_kb())
            self.send_response(200); self.end_headers(); return

        except Exception as e:
            print("ERROR:", e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")
