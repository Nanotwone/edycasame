from http.server import BaseHTTPRequestHandler, HTTPServer
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

# ================= UTIL =================

def now_wib():
    return datetime.now(timezone(timedelta(hours=7)))

def format_currency(amount):
    return f"€{amount:,.0f}"

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

    for row in rows[1:]:
        if len(row) < 5:
            continue

        type_tx = row[1].strip().lower()
        try:
            amount = int(float(row[2]))
        except:
            continue

        account = row[4].strip()
        balances.setdefault(account, 0)

        if type_tx in ["income", "transfer-in"]:
            balances[account] += amount
        elif type_tx in ["expense", "transfer-out"]:
            balances[account] -= amount

    for acc in get_accounts():
        balances.setdefault(acc, 0)

    return balances

# ================= ANALYTICS =================

def get_all_expense_data():
    rows = get_sheet("Sheet1!A:F")
    data = {}
    for row in rows[1:]:
        if len(row) < 5:
            continue
        if row[1].strip().lower() != "expense":
            continue
        category = row[3].strip()
        try:
            amount = int(float(row[2]))
        except:
            continue
        data[category] = data.get(category, 0) + amount
    return sorted(data.items(), key=lambda x: x[1], reverse=True)

def format_expense_page(sorted_data, page=0, per_page=5):
    start = page * per_page
    end = start + per_page
    slice_data = sorted_data[start:end]
    if not slice_data:
        return None
    msg = f"All Expense by Category (Page {page+1})\n\n"
    for i, (cat, amt) in enumerate(slice_data, start=start+1):
        msg += f"{i}. {cat} — €{amt}\n"
    return msg

# ================= CLEAN =================

def quick_clean():
    service = get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:Z"
    ).execute()

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

            # QUICK CLEAN
            if text == "QuickClean":
                quick_clean()
                user_states.pop(chat_id, None)
                send(chat_id, "All transactions cleared.", main_menu())
                self.send_response(200); self.end_headers(); return

            # QUICK INPUT
            quick = parse_quick(text)
            if quick:
                type_tx, amount, category, account = quick
                balances = calculate_account_balance()
                if type_tx == "Expense" and balances.get(account, 0) < amount:
                    send(chat_id, "Insufficient balance.", main_menu())
                else:
                    add_transaction(type_tx, amount, category, account)
                    send(chat_id, "Saved.", main_menu())
                self.send_response(200); self.end_headers(); return

            # ACCOUNT BALANCE
            if text == "Account Balance":
                balances = calculate_account_balance()
                msg = ""
                for acc, bal in balances.items():
                    msg += f"{acc}: {format_currency(bal)}\n"
                send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            # MANAGE ACCOUNT
            if text == "Manage Account":
                user_states[chat_id] = {"step": "manage_menu"}
                send(chat_id, "1 Add\n2 Delete\n3 List\nType number.")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "manage_menu":
                if text == "1":
                    user_states[chat_id] = {"step": "add_account"}
                    send(chat_id, "Enter account name:")
                elif text == "2":
                    user_states[chat_id] = {"step": "delete_account"}
                    send(chat_id, "Enter account name:")
                elif text == "3":
                    send(chat_id, "Accounts:\n" + "\n".join(get_accounts()), main_menu())
                    user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "add_account":
                add_account(text)
                send(chat_id, "Account added.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "delete_account":
                if delete_account(text):
                    send(chat_id, "Account deleted.", main_menu())
                else:
                    send(chat_id, "Cannot delete. Account used.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # INCOME
            if text == "Income":
                user_states[chat_id] = {"step": "income_account"}
                send(chat_id, "Account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_account":
                user_states[chat_id]["account"] = text
                user_states[chat_id]["step"] = "income_amount"
                send(chat_id, "Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "income_amount":
                if text.isdigit():
                    add_transaction("Income", int(text), "", state["account"])
                    send(chat_id, "Income saved.", main_menu())
                else:
                    send(chat_id, "Numbers only.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # TRANSFER
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
                if text.isdigit():
                    amount = int(text)
                    balances = calculate_account_balance()
                    if balances.get(state["from"], 0) >= amount:
                        add_transaction("Transfer-Out", amount, "Transfer", state["from"], f"To {state['to']}")
                        add_transaction("Transfer-In", amount, "Transfer", state["to"], f"From {state['from']}")
                        send(chat_id, "Transfer saved.", main_menu())
                    else:
                        send(chat_id, "Insufficient balance.", main_menu())
                else:
                    send(chat_id, "Numbers only.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # EXPENSE WITH NOTE
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
                if text.isdigit():
                    amount = int(text)
                    balances = calculate_account_balance()
                    if balances.get(state["account"], 0) >= amount:
                        user_states[chat_id]["amount"] = amount
                        user_states[chat_id]["step"] = "expense_note_ask"
                        send(chat_id, "Add detail note? (Yes/No)", [["Yes", "No"]])
                        self.send_response(200); self.end_headers(); return
                    else:
                        send(chat_id, "Insufficient balance.", main_menu())
                else:
                    send(chat_id, "Numbers only.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_note_ask":
                if text == "Yes":
                    user_states[chat_id]["step"] = "expense_note_input"
                    send(chat_id, "Enter detail:")
                    self.send_response(200); self.end_headers(); return
                elif text == "No":
                    add_transaction("Expense", state["amount"], state["category"], state["account"], "")
                    send(chat_id, "Expense saved.", main_menu())
                    user_states.pop(chat_id, None)
                    self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_note_input":
                add_transaction("Expense", state["amount"], state["category"], state["account"], text)
                send(chat_id, "Expense saved with detail.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # ANALYTICS
            if text == "/all_expense":
                data = get_all_expense_data()
                if not data:
                    send(chat_id, "No expense recorded.", main_menu())
                else:
                    user_states[chat_id] = {"step": "expense_page", "data": data, "page": 0}
                    send(chat_id, format_expense_page(data, 0), [["Next"]] if len(data) > 5 else main_menu())
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_page" and text == "Next":
                page = state["page"] + 1
                msg = format_expense_page(state["data"], page)
                if msg:
                    state["page"] = page
                    send(chat_id, msg, [["Next"]] if len(state["data"]) > (page+1)*5 else main_menu())
                else:
                    send(chat_id, "No more data.", main_menu())
                    user_states.pop(chat_id, None)
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
