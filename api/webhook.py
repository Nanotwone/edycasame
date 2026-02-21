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
    rows = get_sheet("Sheet1!A:E")
    for row in rows[1:]:
        if len(row) >= 5 and row[4].strip() == name:
            return False
    acc_rows = get_sheet("Accounts!A:A")
    remaining = [acc_rows[0]]
    for row in acc_rows[1:]:
        if row[0].strip() != name:
            remaining.append(row)
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

# ================= TRANSACTIONS =================

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
        type_tx = row[1].strip()
        amount = int(float(row[2]))
        account = row[4].strip()
        if account not in account_map:
            account_map[account] = 0
        if type_tx in ["Income", "Transfer-In"]:
            account_map[account] += amount
        elif type_tx in ["Expense", "Transfer-Out"]:
            account_map[account] -= amount
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

# ================= MONTHLY CLOSING =================

def monthly_closing():
    month = now_wib().strftime("%Y-%m")
    existing = get_sheet("Monthly_Closing!A:C")
    for row in existing[1:]:
        if row[0] == month:
            return False
    balances = calculate_account_balance()
    rows = []
    for acc, bal in balances.items():
        rows.append([month, acc, bal])
    service = get_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Monthly_Closing!A:C",
        valueInputOption="RAW",
        body={"values": rows}
    ).execute()
    return True

# ================= SUMMARY =================

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

# ================= QUICK INPUT =================

def parse_quick(text):
    match = re.match(r"^([+-])(\d+)\s+(.+)$", text.strip())
    if not match:
        return None
    sign, amount, rest = match.groups()
    amount = int(amount)
    parts = rest.split()
    if sign == "+":
        return ("Income", amount, "", parts[0])
    if sign == "-" and len(parts) >= 2:
        return ("Expense", amount, parts[0], parts[1])
    return None

# ================= TELEGRAM =================

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_kb():
    return {
        "keyboard": [
            ["Income", "Expense"],
            ["Transfer"],
            ["Account Balance"],
            ["Manage Account"],
            ["Close Month"]
        ],
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

            if text == "/start":
                send(chat_id, "Main Menu:", main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "/daily":
                send(chat_id, daily_summary(), main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Account Balance":
                balances = calculate_account_balance()
                msg = ""
                for acc, bal in balances.items():
                    msg += f"{acc}: {format_currency(bal)}\n"
                send(chat_id, msg, main_kb())
                self.send_response(200); self.end_headers(); return

            if text == "Close Month":
                if monthly_closing():
                    send(chat_id, "Monthly closing saved.", main_kb())
                else:
                    send(chat_id, "Month already closed.", main_kb())
                self.send_response(200); self.end_headers(); return

            quick = parse_quick(text)
            if quick:
                type_tx, amount, category, account = quick
                balances = calculate_account_balance()
                if type_tx == "Expense" and balances.get(account,0) < amount:
                    send(chat_id, "Saldo tidak cukup.", main_kb())
                    self.send_response(200); self.end_headers(); return
                add_transaction(type_tx, amount, category, account)
                send(chat_id, "Saved.", main_kb())
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Main Menu:", main_kb())
            self.send_response(200); self.end_headers()

        except Exception as e:
            print("ERROR:", e)
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")
