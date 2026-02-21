from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

allowed_raw = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS = [int(x) for x in allowed_raw.split(",") if x.strip().isdigit()]

user_states = {}

# ================= TIME =================

def now_wib():
    return datetime.now(timezone(timedelta(hours=7)))

def format_currency(amount):
    return f"€{amount:,.0f}"

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

# ================= ACCOUNTS =================

def get_accounts():
    rows = get_sheet("Accounts!A:A")
    return [r[0].strip() for r in rows[1:] if r]

def add_account(name):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Accounts!A:A",
        valueInputOption="RAW",
        body={"values": [[name]]}
    ).execute()

def delete_account(name):
    rows = get_sheet("Sheet1!A:F")
    for row in rows[1:]:
        if len(row) >= 5 and row[4].strip() == name:
            return False

    acc_rows = get_sheet("Accounts!A:A")
    if not acc_rows:
        return False

    header = acc_rows[0]
    remaining = [header]
    found = False

    for row in acc_rows[1:]:
        if row[0].strip() == name:
            found = True
            continue
        remaining.append(row)

    if not found:
        return False

    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Accounts!A2:A"
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Accounts!A1",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

    return True

# ================= TRANSACTION CORE =================

def add_transaction(type_tx, amount, category, account, note=""):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:F",
        valueInputOption="RAW",
        body={"values": [[
            now_wib().strftime("%Y-%m-%d %H:%M:%S"),
            type_tx,
            amount,
            category,
            account,
            note
        ]]}
    ).execute()

def calculate_account_balance():
    rows = get_sheet("Sheet1!A:F")
    account_map = {}

    for row in rows[1:]:
        if len(row) < 5:
            continue

        type_tx = row[1]
        try:
            amount = int(float(row[2]))
        except:
            continue

        account = row[4]

        if account not in account_map:
            account_map[account] = 0

        if type_tx in ["Income", "Transfer-In"]:
            account_map[account] += amount
        elif type_tx in ["Expense", "Transfer-Out"]:
            account_map[account] -= amount

    for acc in get_accounts():
        if acc not in account_map:
            account_map[acc] = 0

    return account_map

# ================= TRANSFER =================

def transfer_funds(amount, from_acc, to_acc):
    timestamp = now_wib().strftime("%Y-%m-%d %H:%M:%S")
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:F",
        valueInputOption="RAW",
        body={"values": [
            [timestamp, "Transfer-Out", amount, "Transfer", from_acc, f"To {to_acc}"],
            [timestamp, "Transfer-In", amount, "Transfer", to_acc, f"From {from_acc}"]
        ]}
    ).execute()

# ================= SUMMARY & ANALYTICS =================

def daily_summary():
    today = now_wib().strftime("%Y-%m-%d")
    rows = get_sheet("Sheet1!A:F")

    income = 0
    expense = 0

    for row in rows[1:]:
        if len(row) < 3:
            continue

        if row[0].startswith(today):
            if row[1] == "Income":
                income += int(float(row[2]))
            elif row[1] == "Expense":
                expense += int(float(row[2]))

    balances = calculate_account_balance()

    msg = f"Daily Summary\nIncome: {format_currency(income)}\nExpense: {format_currency(expense)}\n\n"
    for acc, bal in balances.items():
        msg += f"{acc}: {format_currency(bal)}\n"

    return msg

def today_expense_detail():
    today = now_wib().strftime("%Y-%m-%d")
    rows = get_sheet("Sheet1!A:F")

    details = []
    total = 0

    for row in rows[1:]:
        if len(row) < 6:
            continue

        if row[0].startswith(today) and row[1] == "Expense":
            time_part = row[0].split(" ")[1]
            category = row[3]
            account = row[4]
            amount = int(float(row[2]))

            total += amount
            details.append(f"{time_part} | {category} | {account} | €{amount}")

    if not details:
        return "No expense today."

    msg = "Today's Expense Detail:\n\n"
    msg += "\n".join(details)
    msg += f"\n\nTotal: €{total}"
    return msg

def top_expense(period="today", top_n=3):
    rows = get_sheet("Sheet1!A:F")
    now = now_wib()
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")

    category_map = {}

    for row in rows[1:]:
        if len(row) < 6:
            continue

        date = row[0]
        type_tx = row[1]
        category = row[3]

        try:
            amount = int(float(row[2]))
        except:
            continue

        if type_tx != "Expense":
            continue

        match = (
            (period == "today" and date.startswith(today)) or
            (period == "month" and date.startswith(month))
        )

        if not match:
            continue

        if category not in category_map:
            category_map[category] = 0

        category_map[category] += amount

    if not category_map:
        return "No expense data."

    sorted_data = sorted(category_map.items(),
                         key=lambda x: x[1],
                         reverse=True)

    msg = f"Top {top_n} Expense ({period}):\n\n"

    for i, (cat, amt) in enumerate(sorted_data[:top_n], start=1):
        msg += f"{i}. {cat} — €{amt}\n"

    return msg

# ================= QUICK INPUT =================

def parse_quick(text):
    match = re.match(r"^([+-])(\d+)\s+(.+)$", text.strip())
    if not match:
        return None

    sign, amount, rest = match.groups()
    amount = int(amount)
    parts = rest.split()

    if sign == "+" and len(parts) >= 1:
        return ("Income", amount, "", parts[0])

    if sign == "-" and len(parts) >= 2:
        return ("Expense", amount, parts[0], parts[1])

    return None

# ================= TELEGRAM =================

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {
            "keyboard": keyboard,
            "resize_keyboard": True
        }
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_menu():
    return [
        ["Income", "Expense"],
        ["Transfer"],
        ["Account Balance"],
        ["Manage Account", "Close Month"],
        ["/daily", "/today_detail"],
        ["/top_today", "/top_month"]
    ]

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

            if text == "/start":
                send(chat_id,
                     "Finance Bot Ready.\nQuick:\n+1000 Cash\n-200 Food Cash",
                     main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "/daily":
                send(chat_id, daily_summary(), main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "/today_detail":
                send(chat_id, today_expense_detail(), main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "/top_today":
                send(chat_id, top_expense("today"), main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "/top_month":
                send(chat_id, top_expense("month"), main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "Account Balance":
                balances = calculate_account_balance()
                msg = ""
                for acc, bal in balances.items():
                    msg += f"{acc}: {format_currency(bal)}\n"
                send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            # INCOME FLOW
            if text == "Income":
                user_states[chat_id] = {"step": "income_account"}
                send(chat_id, "Account name?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_account":
                user_states[chat_id]["account"] = text
                user_states[chat_id]["step"] = "income_amount"
                send(chat_id, "Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_amount":
                if not text.isdigit():
                    send(chat_id, "Numbers only.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return
                add_transaction("Income", int(text), "", state["account"])
                send(chat_id, "Income saved.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # EXPENSE FLOW
            if text == "Expense":
                user_states[chat_id] = {"step": "expense_category"}
                send(chat_id, "Category?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_category":
                user_states[chat_id]["category"] = text
                user_states[chat_id]["step"] = "expense_account"
                send(chat_id, "Account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_account":
                user_states[chat_id]["account"] = text
                user_states[chat_id]["step"] = "expense_amount"
                send(chat_id, "Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_amount":
                if not text.isdigit():
                    send(chat_id, "Numbers only.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

                amount = int(text)
                balances = calculate_account_balance()

                if balances.get(state["account"], 0) < amount:
                    send(chat_id, "Insufficient balance.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

                add_transaction("Expense", amount, state["category"], state["account"])
                send(chat_id, "Expense saved.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # TRANSFER FLOW
            if text == "Transfer":
                user_states[chat_id] = {"step": "transfer_from"}
                send(chat_id, "From account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_from":
                user_states[chat_id]["from"] = text
                user_states[chat_id]["step"] = "transfer_to"
                send(chat_id, "To account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_to":
                user_states[chat_id]["to"] = text
                user_states[chat_id]["step"] = "transfer_amount"
                send(chat_id, "Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_amount":
                if not text.isdigit():
                    send(chat_id, "Numbers only.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

                amount = int(text)
                from_acc = state["from"]
                to_acc = state["to"]

                if from_acc == to_acc:
                    send(chat_id, "Cannot transfer to same account.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

                balances = calculate_account_balance()
                if balances.get(from_acc, 0) < amount:
                    send(chat_id, "Insufficient balance.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

                transfer_funds(amount, from_acc, to_acc)
                send(chat_id, "Transfer saved.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            if text == "Close Month":
                if monthly_closing():
                    send(chat_id, "Monthly closing saved.", main_menu())
                else:
                    send(chat_id, "Month already closed.", main_menu())
                self.send_response(200); self.end_headers(); return

            quick = parse_quick(text)
            if quick:
                type_tx, amount, category, account = quick
                balances = calculate_account_balance()

                if type_tx == "Expense" and balances.get(account, 0) < amount:
                    send(chat_id, "Insufficient balance.", main_menu())
                    self.send_response(200); self.end_headers(); return

                add_transaction(type_tx, amount, category, account)
                send(chat_id, "Saved.", main_menu())
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Unknown command.", main_menu())
            self.send_response(200); self.end_headers()

        except Exception as e:
            print("ERROR:", e)
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")
