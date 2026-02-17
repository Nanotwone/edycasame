from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from collections import defaultdict

# ================= ENV =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
ALLOWED_USERS = list(map(int, os.environ.get("ALLOWED_USERS").split(",")))

# ================= STATE =================
user_states = {}

# ================= FORMAT =================
def format_yen(amount):
    return f"¥{amount:,.0f}"

# ================= GOOGLE SERVICE =================
def get_sheets_service():
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=credentials)

# ================= TRANSACTION =================
def get_all_rows():
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D"
    ).execute()
    return result.get("values", [])

def append_transaction(type_tx, amount, category):
    service = get_sheets_service()
    values = [[
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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

def flush_all():
    service = get_sheets_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A2:D"
    ).execute()

def delete_by_period(period):
    service = get_sheets_service()
    rows = get_all_rows()
    now = datetime.now()

    if not rows:
        return

    remaining = [rows[0]]

    for row in rows[1:]:
        if len(row) < 4:
            continue

        date_obj = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        keep = True

        if period == "today":
            if date_obj.date() == now.date():
                keep = False
        elif period == "month":
            if date_obj.year == now.year and date_obj.month == now.month:
                keep = False

        if keep:
            remaining.append(row)

    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": remaining}
    ).execute()

# ================= CATEGORY =================
def get_categories():
    service = get_sheets_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Categories!A:B"
    ).execute()

    rows = result.get("values", [])
    data = {"Pemasukan": [], "Pengeluaran": []}

    for row in rows[1:]:
        if len(row) >= 2:
            data[row[0]].append(row[1])

    return data

def add_category(type_tx, category):
    service = get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Categories!A:B",
        valueInputOption="RAW",
        body={"values": [[type_tx, category]]}
    ).execute()

# ================= SUMMARY =================
def calculate_summary(period="today"):
    rows = get_all_rows()
    now = datetime.now()

    income = 0
    expense = 0
    cat_income = defaultdict(int)
    cat_expense = defaultdict(int)

    for row in rows[1:]:
        if len(row) < 4:
            continue

        date_obj = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        type_tx = row[1]
        amount = int(row[2])
        category = row[3]

        match = False
        if period == "today":
            match = date_obj.date() == now.date()
        elif period == "month":
            match = date_obj.year == now.year and date_obj.month == now.month
        elif period == "year":
            match = date_obj.year == now.year
        elif period == "all":
            match = True

        if not match:
            continue

        if type_tx == "Pemasukan":
            income += amount
            cat_income[category] += amount
        else:
            expense += amount
            cat_expense[category] += amount

    return income, expense, cat_income, cat_expense

# ================= TELEGRAM =================
def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard

    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json=payload
    )

def main_keyboard():
    return {
        "keyboard": [
            ["Pemasukan", "Pengeluaran"],
            ["Lain-lain"]
        ],
        "resize_keyboard": True
    }

def other_keyboard():
    return {
        "keyboard": [
            ["Today", "Month", "Year"],
            ["Top Expense", "Top Income"],
            ["Flush Menu"],
            ["Kembali"]
        ],
        "resize_keyboard": True
    }

def flush_keyboard():
    return {
        "keyboard": [
            ["Flush Today"],
            ["Flush Month"],
            ["Flush All"],
            ["Kembali"]
        ],
        "resize_keyboard": True
    }

def category_keyboard(type_tx):
    categories = get_categories()
    return {
        "keyboard": [[cat] for cat in categories.get(type_tx, [])] +
                    [["+ Tambah Kategori"], ["Kembali"]],
        "resize_keyboard": True
    }

# ================= QUICK ENTRY =================
def parse_quick_entry(text):
    match = re.match(r"^([+-]?)(\d+)\s+(.+)$", text.strip())
    if not match:
        return None

    sign, amount, category = match.groups()
    amount = int(amount)

    if sign == "+":
        type_tx = "Pemasukan"
    else:
        type_tx = "Pengeluaran"

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

            if user_id not in ALLOWED_USERS:
                self.send_response(200)
                self.end_headers()
                return

            if not chat_id or not text:
                self.send_response(200)
                self.end_headers()
                return

            # ===== START =====
            if text == "/start":
                user_states.pop(chat_id, None)
                send_message(chat_id, "Menu utama:", main_keyboard())
                self.send_response(200)
                self.end_headers()
                return

            state = user_states.get(chat_id)

            # ===== QUICK ENTRY (hanya jika tidak wizard) =====
            if state is None:
                quick = parse_quick_entry(text)
                if quick:
                    type_tx, amount, category = quick
                    append_transaction(type_tx, amount, category)
                    send_message(
                        chat_id,
                        f"⚡ {type_tx} {format_yen(amount)} untuk {category} disimpan.",
                        main_keyboard()
                    )
                    self.send_response(200)
                    self.end_headers()
                    return

            # ===== MENU =====
            if text == "Lain-lain":
                send_message(chat_id, "Pilih fitur:", other_keyboard())
                self.send_response(200); self.end_headers(); return

            if text == "Kembali":
                user_states.pop(chat_id, None)
                send_message(chat_id, "Menu utama:", main_keyboard())
                self.send_response(200); self.end_headers(); return

            if text in ["Today", "Month", "Year"]:
                income, expense, _, _ = calculate_summary(text.lower())
                balance = income - expense
                send_message(
                    chat_id,
                    f"📊 Rekap {text}\n\n"
                    f"Pemasukan: {format_yen(income)}\n"
                    f"Pengeluaran: {format_yen(expense)}\n"
                    f"Saldo: {format_yen(balance)}",
                    other_keyboard()
                )
                self.send_response(200); self.end_headers(); return

            if text == "Top Expense":
                _, _, _, cat_expense = calculate_summary("all")
                top = sorted(cat_expense.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "🔥 Top 3 Pengeluaran:\n\n"
                for i, (cat, amt) in enumerate(top, 1):
                    msg += f"{i}. {cat} - {format_yen(amt)}\n"
                send_message(chat_id, msg, other_keyboard())
                self.send_response(200); self.end_headers(); return

            if text == "Top Income":
                _, _, cat_income, _ = calculate_summary("all")
                top = sorted(cat_income.items(), key=lambda x: x[1], reverse=True)[:3]
                msg = "💰 Top 3 Pemasukan:\n\n"
                for i, (cat, amt) in enumerate(top, 1):
                    msg += f"{i}. {cat} - {format_yen(amt)}\n"
                send_message(chat_id, msg, other_keyboard())
                self.send_response(200); self.end_headers(); return

            # ===== WIZARD =====
            if text in ["Pemasukan", "Pengeluaran"]:
                user_states[chat_id] = {"step": "category", "type": text}
                send_message(chat_id, "Pilih kategori:", category_keyboard(text))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "category":
                if text == "+ Tambah Kategori":
                    user_states[chat_id]["step"] = "new_category"
                    send_message(chat_id, "Ketik nama kategori baru:")
                else:
                    user_states[chat_id]["category"] = text
                    user_states[chat_id]["step"] = "amount"
                    send_message(chat_id, "Nominal?")
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "new_category":
                new_cat = text.strip()
                type_tx = state["type"]
                categories = get_categories()
                if new_cat not in categories.get(type_tx, []):
                    add_category(type_tx, new_cat)
                user_states[chat_id]["step"] = "category"
                send_message(chat_id, "Pilih kategori:", category_keyboard(type_tx))
                self.send_response(200); self.end_headers(); return

            if state and state.get("step") == "amount":
                if not text.isdigit():
                    send_message(chat_id, "Masukkan angka saja.")
                    self.send_response(200); self.end_headers(); return

                amount = int(text)
                append_transaction(state["type"], amount, state["category"])

                send_message(
                    chat_id,
                    f"✔️ {state['type']} {format_yen(amount)} untuk {state['category']} disimpan.",
                    main_keyboard()
                )

                user_states.pop(chat_id, None)
                self.send_response(200); self.end_headers(); return

            send_message(chat_id, "Pilih menu:", main_keyboard())

        except Exception as e:
            print("ERROR:", e)

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
