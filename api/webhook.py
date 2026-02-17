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
        range="Categories!A2:B"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Categories!A1:B",
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

        date, type_tx, amount = row[0], row[1], int(row[2])

        match = (
            (period == "today" and date.startswith(today)) or
            (period == "month" and date.startswith(month)) or
            (period == "all")
        )

        if not match:
            continue

        if type_tx == "Income":
            income += amount
        elif type_tx == "Expense":
            expense += amount

    balance = income - expense
    return income, expense, balance

# ================= FLUSH =================
def flush_income_today():
    _flush_type_today("Income")

def flush_expense_today():
    _flush_type_today("Expense")

def _flush_type_today(type_tx):
    rows = get_transactions()
    today = now_wib().strftime("%Y-%m-%d")

    remaining = [rows[0]]
    for row in rows[1:]:
        if row[1] == type_tx and row[0].startswith(today):
            continue
        remaining.append(row)

    service = get_service()
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

def flush_month():
    rows = get_transactions()
    month = now_wib().strftime("%Y-%m")

    remaining = [rows[0]]
    for row in rows[1:]:
        if row[0].startswith(month):
            continue
        remaining.append(row)

    service = get_service()
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

            # ===== DELETE CATEGORY FLOW =====
            if state and state.get("step") == "await_delete_category":
                match = re.match(r'del\s+"(.+)"', text, re.IGNORECASE)
                if not match:
                    send(chat_id, 'Format salah.\nGunakan: del "nama_category"')
                else:
                    name = match.group(1)
                    if delete_category(name):
                        send(chat_id, f'Category "{name}" deleted.', main_kb())
                    else:
                        send(chat_id, f'Category "{name}" not found.', main_kb())
                    user_states.pop(chat_id, None)

                self.send_response(200); self.end_headers(); return

            # ===== MENU =====
            if text == "/start":
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Other":
                send(chat_id, "Choose:", other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Back":
                user_states.pop(chat_id, None)
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            # ===== MANAGE CATEGORY =====
            if text == "Manage Category":
                send(chat_id, "Manage categories:", manage_kb())
                self.send_response(200); self.end_headers(); return

            if text == "+ Add Category":
                user_states[chat_id] = {"step": "manage_add_category"}
                send(chat_id,
                     'Type new category in format:\nIncome: Salary\nExpense: Food')
                self.send_response(200); self.end_headers(); return

            if text == "Delete Category":
                user_states[chat_id] = {"step": "await_delete_category"}
                send(chat_id,
                     'Type category to delete using:\ndel "category_name"')
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "manage_add_category":
                if ":" not in text:
                    send(chat_id, 'Format salah. Gunakan: Income: Nama')
                else:
                    type_tx, name = text.split(":", 1)
                    type_tx = type_tx.strip()
                    name = name.strip()
                    if type_tx in ["Income", "Expense"]:
                        add_category(type_tx, name)
                        send(chat_id, "Category added.", main_kb())
                        user_states.pop(chat_id, None)
                    else:
                        send(chat_id, "Type harus Income atau Expense.")
                self.send_response(200); self.end_headers(); return

            # ===== FLUSH =====
            if text == "Flush Menu":
                send(chat_id, "Choose:", flush_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Income Today":
                flush_income_today()
                send(chat_id, "Income today deleted.", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Flush Expense Today":
                flush_expense_today()
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

            # ===== RECAP =====
            if text == "Today":
                income, expense, balance = calculate_summary("today")
                send(chat_id,
                     f"Today\nIncome: {format_yen(income)}\n"
                     f"Expense: {format_yen(expense)}\n"
                     f"Balance: {format_yen(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "This Month":
                income, expense, balance = calculate_summary("month")
                send(chat_id,
                     f"This Month\nIncome: {format_yen(income)}\n"
                     f"Expense: {format_yen(expense)}\n"
                     f"Balance: {format_yen(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            if text == "All":
                income, expense, balance = calculate_summary("all")
                send(chat_id,
                     f"All\nIncome: {format_yen(income)}\n"
                     f"Expense: {format_yen(expense)}\n"
                     f"Balance: {format_yen(balance)}",
                     other_kb())
                self.send_response(200); self.end_headers(); return

            # ===== WIZARD =====
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
                send(chat_id, "Category added:", category_kb(state["type"]))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "amount":
                if text.isdigit():
                    add_transaction(state["type"], int(text), state["category"])
                    send(chat_id, "Saved.", main_kb())
                    user_states.pop(chat_id, None)
                else:
                    send(chat_id, "Numbers only.")
                self.send_response(200); self.end_headers(); return

            # ===== QUICK ENTRY =====
            quick = parse_quick(text)
            if quick:
                type_tx, amount, category = quick
                add_transaction(type_tx, amount, category)
                send(chat_id, "Saved.", main_kb())
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
