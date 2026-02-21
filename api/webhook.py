from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import requests
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USERS", "").split(",") if x.strip().isdigit()]

user_states = {}

# ================= UTIL =================

def now_wib():
    return datetime.now(timezone(timedelta(hours=7)))

def format_currency(amount):
    return f"€{amount:,.0f}"

def parse_amount(text):
    text = text.replace(",", "").replace("€", "").strip()
    try:
        value = float(text)
        if value <= 0:
            return None
        return int(value)
    except:
        return None

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

# ================= ACCOUNT =================

def get_accounts():
    rows = get_sheet("Accounts!A:A")
    if not rows:
        return []
    return [r[0].strip() for r in rows[1:] if r and r[0].strip()]

def account_exists(name):
    return name in get_accounts()

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
    header = acc_rows[0]
    remaining = [header]
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

# ================= TRANSACTION =================

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
    balances = {}
    total = 0

    for row in rows[1:]:
        if len(row) < 5:
            continue
        try:
            amount = int(float(row[2]))
        except:
            continue

        type_tx = row[1].strip().lower()
        account = row[4].strip()
        balances.setdefault(account, 0)

        if type_tx in ["income", "transfer-in"]:
            balances[account] += amount
            total += amount
        elif type_tx in ["expense", "transfer-out"]:
            balances[account] -= amount
            total -= amount

    for acc in get_accounts():
        balances.setdefault(acc, 0)

    return balances, total

# ================= ANALYTICS =================

def get_all_expense_data():
    rows = get_sheet("Sheet1!A:F")
    data = {}
    total = 0

    for row in rows[1:]:
        if len(row) < 5:
            continue
        if row[1].strip().lower() != "expense":
            continue

        try:
            amount = int(float(row[2]))
        except:
            continue

        category = row[3].strip()
        total += amount
        data[category] = data.get(category, 0) + amount

    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)
    return sorted_data, total

# ================= CLEAN =================

def quick_clean():
    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:Z"
    ).execute()

# ================= TELEGRAM =================

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def main_menu():
    return [
        ["Income", "Expense"],
        ["Transfer"],
        ["Account Balance"],
        ["Manage Account"],
        ["QuickClean"],
        ["/all_expense"]
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
                send(chat_id, "Finance Bot Ready.", main_menu())
                self.send_response(200); self.end_headers(); return

            # ================= INCOME =================
            if text == "Income":
                accounts = get_accounts()
                if not accounts:
                    send(chat_id, "No accounts found. Add account first.", main_menu())
                    self.send_response(200); self.end_headers(); return

                user_states[chat_id] = {"flow": "income", "step": "account", "data": {}}
                send(chat_id, "Select account:", [[acc] for acc in accounts])
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "income":
                if state["step"] == "account":
                    if not account_exists(text):
                        send(chat_id, "Invalid account.")
                        self.send_response(200); self.end_headers(); return
                    state["data"]["account"] = text
                    state["step"] = "amount"
                    send(chat_id, "Enter amount:")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "amount":
                    amount = parse_amount(text)
                    if not amount:
                        send(chat_id, "Invalid amount.")
                        self.send_response(200); self.end_headers(); return
                    state["data"]["amount"] = amount
                    state["step"] = "note"
                    send(chat_id, "Enter note (or type skip):")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "note":
                    note = "" if text.lower() == "skip" else text
                    d = state["data"]
                    add_transaction("Income", d["amount"], "", d["account"], note)
                    send(chat_id, "Income recorded.", main_menu())
                    user_states.pop(chat_id)
                    self.send_response(200); self.end_headers(); return

            # ================= EXPENSE =================
            if text == "Expense":
                accounts = get_accounts()
                if not accounts:
                    send(chat_id, "No accounts found. Add account first.", main_menu())
                    self.send_response(200); self.end_headers(); return

                user_states[chat_id] = {"flow": "expense", "step": "account", "data": {}}
                send(chat_id, "Select account:", [[acc] for acc in accounts])
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "expense":
                if state["step"] == "account":
                    if not account_exists(text):
                        send(chat_id, "Invalid account.")
                        self.send_response(200); self.end_headers(); return
                    state["data"]["account"] = text
                    state["step"] = "amount"
                    send(chat_id, "Enter amount:")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "amount":
                    amount = parse_amount(text)
                    if not amount:
                        send(chat_id, "Invalid amount.")
                        self.send_response(200); self.end_headers(); return

                    balances, _ = calculate_account_balance()
                    if balances.get(state["data"]["account"], 0) < amount:
                        send(chat_id, "Insufficient balance.", main_menu())
                        user_states.pop(chat_id)
                        self.send_response(200); self.end_headers(); return

                    state["data"]["amount"] = amount
                    state["step"] = "category"
                    send(chat_id, "Enter category:")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "category":
                    state["data"]["category"] = text
                    state["step"] = "note"
                    send(chat_id, "Enter note (or type skip):")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "note":
                    note = "" if text.lower() == "skip" else text
                    d = state["data"]
                    add_transaction("Expense", d["amount"], d["category"], d["account"], note)
                    send(chat_id, "Expense recorded.", main_menu())
                    user_states.pop(chat_id)
                    self.send_response(200); self.end_headers(); return

            # ================= TRANSFER =================
            if text == "Transfer":
                accounts = get_accounts()
                if not accounts:
                    send(chat_id, "No accounts found.", main_menu())
                    self.send_response(200); self.end_headers(); return

                user_states[chat_id] = {"flow": "transfer", "step": "from", "data": {}}
                send(chat_id, "Transfer from:", [[acc] for acc in accounts])
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "transfer":
                if state["step"] == "from":
                    if not account_exists(text):
                        send(chat_id, "Invalid account.")
                        self.send_response(200); self.end_headers(); return
                    state["data"]["from"] = text
                    state["step"] = "to"
                    send(chat_id, "Transfer to:", [[acc] for acc in get_accounts()])
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "to":
                    if not account_exists(text) or text == state["data"]["from"]:
                        send(chat_id, "Invalid destination.")
                        self.send_response(200); self.end_headers(); return
                    state["data"]["to"] = text
                    state["step"] = "amount"
                    send(chat_id, "Enter amount:")
                    self.send_response(200); self.end_headers(); return

                if state["step"] == "amount":
                    amount = parse_amount(text)
                    if not amount:
                        send(chat_id, "Invalid amount.")
                        self.send_response(200); self.end_headers(); return

                    balances, _ = calculate_account_balance()
                    if balances.get(state["data"]["from"], 0) < amount:
                        send(chat_id, "Insufficient balance.", main_menu())
                        user_states.pop(chat_id)
                        self.send_response(200); self.end_headers(); return

                    add_transaction("Transfer-Out", amount, "Transfer", state["data"]["from"], f"To {state['data']['to']}")
                    add_transaction("Transfer-In", amount, "Transfer", state["data"]["to"], f"From {state['data']['from']}")
                    send(chat_id, "Transfer completed.", main_menu())
                    user_states.pop(chat_id)
                    self.send_response(200); self.end_headers(); return

            # ================= ACCOUNT BALANCE =================
            if text == "Account Balance":
                balances, total = calculate_account_balance()
                msg = ""
                for acc, bal in sorted(balances.items(), key=lambda x: x[1], reverse=True):
                    msg += f"{acc}: {format_currency(bal)}\n"
                msg += "\nTOTAL: " + format_currency(total)
                send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            # ================= MANAGE ACCOUNT =================
            if text == "Manage Account":
                send(chat_id, "Manage Account:", [["List"], ["Add"], ["Delete"], ["Back"]])
                self.send_response(200); self.end_headers(); return

            if text == "List":
                balances, _ = calculate_account_balance()
                msg = ""
                for acc in get_accounts():
                    msg += f"{acc}: {format_currency(balances.get(acc,0))}\n"
                send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            if text == "Add":
                user_states[chat_id] = {"flow": "add_account"}
                send(chat_id, "Enter new account name:")
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "add_account":
                if account_exists(text):
                    send(chat_id, "Account already exists.", main_menu())
                else:
                    add_account(text)
                    send(chat_id, "Account added.", main_menu())
                user_states.pop(chat_id)
                self.send_response(200); self.end_headers(); return

            if text == "Delete":
                user_states[chat_id] = {"flow": "delete_account"}
                send(chat_id, "Enter account name to delete:")
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "delete_account":
                if not account_exists(text):
                    send(chat_id, "Account not found.", main_menu())
                elif not delete_account(text):
                    send(chat_id, "Account has transactions. Cannot delete.", main_menu())
                else:
                    send(chat_id, "Account deleted.", main_menu())
                user_states.pop(chat_id)
                self.send_response(200); self.end_headers(); return

            # ================= ALL EXPENSE =================
            if text == "/all_expense":
                data_exp, total = get_all_expense_data()
                if not data_exp:
                    send(chat_id, "No expense recorded.", main_menu())
                else:
                    msg = f"Total Expense: {format_currency(total)}\n\n"
                    for i, (cat, amt) in enumerate(data_exp, start=1):
                        msg += f"{i}. {cat} — {format_currency(amt)}\n"
                    send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            # ================= QUICK CLEAN =================
            if text == "QuickClean":
                user_states[chat_id] = {"flow": "clean_confirm"}
                send(chat_id, "Type YES to confirm deleting all transactions.")
                self.send_response(200); self.end_headers(); return

            if state and state.get("flow") == "clean_confirm":
                if text == "YES":
                    quick_clean()
                    send(chat_id, "All transactions deleted.", main_menu())
                else:
                    send(chat_id, "Cancelled.", main_menu())
                user_states.pop(chat_id)
                self.send_response(200); self.end_headers(); return

            send(chat_id, "Use menu.", main_menu())
            self.send_response(200); self.end_headers()

        except Exception as e:
            print("ERROR:", e)
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("", PORT), handler)
    print("Server running...")
    server.serve_forever()
