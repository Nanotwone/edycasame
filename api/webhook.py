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

# ================= FORMAT =================

def format_currency(amount):
    return f"€{amount:,.0f}"

def balance_message(balance):
    emoji = "💰" if balance >= 0 else "⚠️"
    return f"{format_currency(balance)} {emoji}"

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

# ================= CATEGORIES =================

def get_categories(type_tx=None):
    rows = get_sheet("Categories!A:B")
    data = {"Income": [], "Expense": []}
    for row in rows[1:]:
        if len(row) >= 2:
            data[row[0].strip()].append(row[1].strip())
    return data if not type_tx else data.get(type_tx, [])

# ================= TRANSACTIONS =================

def add_transaction(type_tx, amount, category, account):
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:E",
        valueInputOption="RAW",
        body={"values": [[
            now_wib().strftime("%Y-%m-%d %H:%M:%S"),
            type_tx,
            amount,
            category,
            account
        ]]}
    ).execute()

def calculate_account_balance():
    rows = get_sheet("Sheet1!A:E")
    account_map = {}

    for row in rows[1:]:
        if len(row) < 5:
            continue

        type_tx = row[1].strip().lower()
        try:
            amount = int(float(row[2]))
        except:
            continue

        account = row[4].strip()

        if account not in account_map:
            account_map[account] = 0

        if type_tx == "income":
            account_map[account] += amount
        elif type_tx == "expense":
            account_map[account] -= amount

    return account_map

# ================= QUICK PARSER =================

def parse_quick(text):
    match = re.match(r"^([+-])(\d+)\s+(.+)$", text.strip())
    if not match:
        return None

    sign, amount, rest = match.groups()
    amount = int(amount)
    parts = rest.split()

    if sign == "+":
        account = parts[0]
        return "Income", amount, "", account

    if sign == "-":
        if len(parts) < 2:
            return None
        category = parts[0]
        account = parts[1]
        return "Expense", amount, category, account

# ================= TELEGRAM =================

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_kb():
    return {
        "keyboard": [["Income", "Expense"],
                     ["Account Balance"],
                     ["Other"]],
        "resize_keyboard": True
    }

def account_kb():
    accounts = get_accounts()
    return {
        "keyboard": [[a] for a in accounts] + [["Back"]],
        "resize_keyboard": True
    }

def category_kb(type_tx):
    categories = get_categories(type_tx)
    return {
        "keyboard": [[c] for c in categories] + [["Back"]],
        "resize_keyboard": True
    }

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

            # START
            if text == "/start":
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            # ACCOUNT BALANCE
            if text == "Account Balance":
                data_bal = calculate_account_balance()
                msg = "Account Balance:\n"
                for acc, bal in data_bal.items():
                    msg += f"{acc}: {balance_message(bal)}\n"
                send(chat_id, msg, main_kb())
                self.send_response(200); self.end_headers(); return

            # INCOME
            if text == "Income":
                user_states[chat_id] = {"step": "income_account"}
                send(chat_id, "Select account:", account_kb())
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_account":
                user_states[chat_id]["account"] = text
                user_states[chat_id]["step"] = "income_amount"
                send(chat_id, "Enter amount:")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_amount":
                if text.isdigit():
                    add_transaction("Income",
                                    int(text),
                                    "",
                                    state["account"])
                    send(chat_id, "Income saved.", main_kb())
                    user_states.pop(chat_id, None)
                else:
                    send(chat_id, "Numbers only.")
                self.send_response(200); self.end_headers(); return

            # EXPENSE
            if text == "Expense":
                user_states[chat_id] = {"step": "expense_account"}
                send(chat_id, "Select account:", account_kb())
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_account":
                user_states[chat_id]["account"] = text
                user_states[chat_id]["step"] = "expense_category"
                send(chat_id, "Select category:", category_kb("Expense"))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_category":
                user_states[chat_id]["category"] = text
                user_states[chat_id]["step"] = "expense_amount"
                send(chat_id, "Enter amount:")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_amount":
                if text.isdigit():
                    amount = int(text)
                    balances = calculate_account_balance()
                    account = state["account"]

                    if balances.get(account, 0) < amount:
                        send(chat_id,
                             f"Saldo tidak cukup.\n"
                             f"Saldo: {format_currency(balances.get(account,0))}")
                        self.send_response(200); self.end_headers(); return

                    add_transaction("Expense",
                                    amount,
                                    state["category"],
                                    account)
                    send(chat_id, "Expense saved.", main_kb())
                    user_states.pop(chat_id, None)
                else:
                    send(chat_id, "Numbers only.")
                self.send_response(200); self.end_headers(); return

            # QUICK INPUT
            quick = parse_quick(text)
            if quick:
                type_tx, amount, category, account = quick

                if type_tx == "Expense":
                    balances = calculate_account_balance()
                    if balances.get(account, 0) < amount:
                        send(chat_id,
                             f"Saldo tidak cukup.\n"
                             f"Saldo: {format_currency(balances.get(account,0))}")
                        self.send_response(200); self.end_headers(); return

                add_transaction(type_tx, amount, category, account)
                send(chat_id, "Saved.", main_kb())
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Main Menu:", main_kb())
            self.send_response(200)
            self.end_headers()

        except Exception as e:
            print("ERROR:", e)
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")
