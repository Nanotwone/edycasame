from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ================= ENV =================
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

def get_sheet(range_name):
    service = get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=range_name
    ).execute()
    return result.get("values", [])

# ================= CATEGORY =================
def get_categories():
    rows = get_sheet("Categories!A:B")
    data = {"Income": [], "Expense": []}

    for row in rows[1:]:
        if len(row) >= 2:
            data[row[0].strip()].append(row[1].strip())

    return data

def add_category(type_tx, category):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Categories!A:B",
        valueInputOption="RAW",
        body={"values": [[type_tx, category]]}
    ).execute()

def delete_category(category_name):
    service = get_service()
    rows = get_sheet("Categories!A:B")
    if not rows:
        return False

    header = rows[0]
    remaining = [header]
    found = False

    for row in rows[1:]:
        if row[1].strip().lower() == category_name.lower():
            found = True
            continue
        remaining.append(row)

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Categories!A:B",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

    return found

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
    cat_income = defaultdict(int)
    cat_expense = defaultdict(int)

    for row in rows[1:]:
        if len(row) < 4:
            continue

        date = row[0]
        type_tx = row[1]
        amount = int(row[2])
        category = row[3]

        match = (
            (period == "today" and date.startswith(today)) or
            (period == "month" and date.startswith(month)) or
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

# ================= FLUSH =================
def delete_by_type_and_period(type_tx, period):
    rows = get_transactions()
    service = get_service()
    now = now_wib()

    today = now.strftime("%Y-%m-%d")

    remaining = [rows[0]]

    for row in rows[1:]:
        date = row[0]
        row_type = row[1]

        if period == "today" and row_type == type_tx and date.startswith(today):
            continue

        remaining.append(row)

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

def delete_month():
    rows = get_transactions()
    service = get_service()
    now = now_wib()
    month = now.strftime("%Y-%m")

    remaining = [rows[0]]

    for row in rows[1:]:
        if row[0].startswith(month):
            continue
        remaining.append(row)

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

def flush_all():
    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:D"
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
                         ["Top Income", "Top Expense"],
                         ["Manage Category"],
                         ["Flush Menu"],
                         ["Back"]],
            "resize_keyboard": True}

def category_kb(type_tx):
    categories = get_categories()
    return {"keyboard": [[c] for c in categories[type_tx]] +
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

            # DELETE CATEGORY
            if text.lower().startswith("delete "):
                name = text[7:].strip()
                if delete_category(name):
                    send(chat_id, f"Category '{name}' deleted.", main_kb())
                else:
                    send(chat_id, f"Category '{name}' not found.", main_kb())
                self.send_response(200); self.end_headers(); return

            state = user_states.get(chat_id)

            # FLUSH
            if text == "Flush Menu":
                send(chat_id, "Choose:", flush_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Income Today":
                delete_by_type_and_period("Income", "today")
                send(chat_id, "Income today deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Expense Today":
                delete_by_type_and_period("Expense", "today")
                send(chat_id, "Expense today deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Month":
                delete_month()
                send(chat_id, "This month transactions deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush All":
                flush_all()
                send(chat_id, "All transactions deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            # MENU
            if text == "/start":
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Other":
                send(chat_id, "Choose:", other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Back":
                send(chat_id, "Main Menu:", main_kb())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # RECAP
            if text == "Today":
                income, expense, _, _ = calculate_summary("today")
                send(chat_id,
                     f"Today\nIncome: {format_yen(income)}\nExpense: {format_yen(expense)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "This Month":
                income, expense, _, _ = calculate_summary("month")
                send(chat_id,
                     f"This Month\nIncome: {format_yen(income)}\nExpense: {format_yen(expense)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "All":
                income, expense, _, _ = calculate_summary("all")
                send(chat_id,
                     f"All Time\nIncome: {format_yen(income)}\nExpense: {format_yen(expense)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            # TOP
            if text == "Top Income":
                _, _, cat_income, _ = calculate_summary("all")
                top = sorted(cat_income.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "\n".join([f"{c} - {format_yen(a)}" for c, a in top])
                send(chat_id, msg, other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Top Expense":
                _, _, _, cat_expense = calculate_summary("all")
                top = sorted(cat_expense.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "\n".join([f"{c} - {format_yen(a)}" for c, a in top])
                send(chat_id, msg, other_kb())
                self.send_response(200); self.end_headers(); return

            # QUICK ENTRY
            quick = parse_quick(text)
            if quick:
                type_tx, amount, category = quick
                add_transaction(type_tx, amount, category)
                send(chat_id, "Saved.", main_kb())
                self.send_response(200); self.end_headers(); return

            # WIZARD
            if text in ["Income", "Expense"]:
                user_states[chat_id] = {"step": "category", "type": text}
                send(chat_id, "Select category:", category_kb(text))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "category":
                if text == "+ Add Category":
                    user_states[chat_id]["step"] = "new_category"
                    send(chat_id, "Type new category name:")
                else:
                    user_states[chat_id]["category"] = text
                    user_states[chat_id]["step"] = "amount"
                    send(chat_id, "Enter amount:")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "new_category":
                add_category(state["type"], text)
                user_states[chat_id]["step"] = "category"
                send(chat_id, "Category added. Select:", category_kb(state["type"]))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "amount":
                if not text.isdigit():
                    send(chat_id, "Numbers only.")
                else:
                    add_transaction(state["type"], int(text), state["category"])
                    send(chat_id, "Saved.", main_kb())
                    user_states.pop(chat_id, None)
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
        self.wfile.write(b"Bot is running")
