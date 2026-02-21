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
    total = 0

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

        category = row[3].strip()
        try:
            amount = int(float(row[2]))
        except:
            continue

        total += amount
        data[category] = data.get(category, 0) + amount

    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)
    return sorted_data, total

def format_expense_page(sorted_data, total, page=0, per_page=5):
    start = page * per_page
    end = start + per_page
    slice_data = sorted_data[start:end]

    if not slice_data:
        return None

    msg = f"Total Expense: €{total}\n\n"
    msg += f"By Category (Page {page+1})\n\n"

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

            # ACCOUNT BALANCE WITH TOTAL
            if text == "Account Balance":
                balances, total = calculate_account_balance()
                msg = ""
                for acc, bal in balances.items():
                    msg += f"{acc}: {format_currency(bal)}\n"
                msg += "\nTOTAL: " + format_currency(total)
                send(chat_id, msg, main_menu())
                self.send_response(200); self.end_headers(); return

            # TRANSFER FLOW FIXED
            if text == "Transfer":
                user_states[chat_id] = {"step": "transfer_from"}
                send(chat_id, "Transfer from account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_from":
                user_states[chat_id]["from"] = text
                user_states[chat_id]["step"] = "transfer_to"
                send(chat_id, "Transfer to account?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_to":
                user_states[chat_id]["to"] = text
                user_states[chat_id]["step"] = "transfer_amount"
                send(chat_id, "Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "transfer_amount":
                if text.isdigit():
                    amount = int(text)
                    balances, _ = calculate_account_balance()
                    if balances.get(state["from"], 0) >= amount:
                        add_transaction("Transfer-Out", amount, "Transfer", state["from"], f"To {state['to']}")
                        add_transaction("Transfer-In", amount, "Transfer", state["to"], f"From {state['from']}")
                        send(chat_id, "Transfer completed.", main_menu())
                    else:
                        send(chat_id, "Insufficient balance.", main_menu())
                else:
                    send(chat_id, "Numbers only.", main_menu())
                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            # ALL EXPENSE WITH TOTAL
            if text == "/all_expense":
                data, total = get_all_expense_data()
                if not data:
                    send(chat_id, "No expense recorded.", main_menu())
                else:
                    user_states[chat_id] = {"step": "expense_page", "data": data, "total": total, "page": 0}
                    send(chat_id, format_expense_page(data, total, 0), [["Next"]] if len(data) > 5 else main_menu())
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "expense_page" and text == "Next":
                page = state["page"] + 1
                msg = format_expense_page(state["data"], state["total"], page)
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
