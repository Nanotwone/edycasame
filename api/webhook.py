from http.server import BaseHTTPRequestHandler, HTTPServer
import json, requests, os, re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_ID = os.environ.get("SHEET_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USERS","").split(",") if x.strip().isdigit()]
user_states = {}

# ================= UTIL =================

def now():
    return datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S")

def service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDENTIALS),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets","v4",credentials=creds)

def get_sheet(r):
    return service().spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=r).execute().get("values",[])

def append_row(row):
    service().spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:F",
        valueInputOption="RAW",
        body={"values":[row]}
    ).execute()

# ================= ACCOUNT =================

def accounts():
    rows = get_sheet("Accounts!A:A")
    return [r[0] for r in rows[1:] if r]

def balance():
    rows = get_sheet("Sheet1!A:F")
    bal,total={},0
    for r in rows[1:]:
        if len(r)<5: continue
        t=r[1].lower()
        a=int(float(r[2]))
        acc=r[4]
        bal.setdefault(acc,0)
        if t in ["income","transfer-in"]:
            bal[acc]+=a; total+=a
        elif t in ["expense","transfer-out"]:
            bal[acc]-=a; total-=a
    return bal,total

# ================= TELEGRAM =================

def send(cid,text,kb=None):
    data={"chat_id":cid,"text":text}
    if kb:
        data["reply_markup"]={"keyboard":kb,"resize_keyboard":True}
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json=data)

def menu():
    return [["Income","Expense"],
            ["Transfer"],
            ["Account Balance"],
            ["Manage Account"],
            ["QuickClean"],
            ["/all_expense"]]

# ================= HANDLER =================

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        l=int(self.headers.get('Content-Length',0))
        body=self.rfile.read(l)

        try:
            data=json.loads(body)
            msg=data.get("message",{})
            cid=msg.get("chat",{}).get("id")
            text=msg.get("text","").strip()
            uid=msg.get("from",{}).get("id")

            if uid not in ALLOWED_USERS:
                self.send_response(200); self.end_headers(); return

            state=user_states.get(cid)

            if text=="/start":
                send(cid,"Ready.",menu())
                self.send_response(200); self.end_headers(); return

            # QUICK CLEAN
            if text=="QuickClean":
                service().spreadsheets().values().clear(
                    spreadsheetId=SHEET_ID,
                    range="Sheet1!A2:Z").execute()
                send(cid,"All cleared.",menu())
                self.send_response(200); self.end_headers(); return

            # ACCOUNT BALANCE
            if text=="Account Balance":
                bal,total=balance()
                msg=""
                for k,v in bal.items():
                    msg+=f"{k}: €{v}\n"
                msg+=f"\nTOTAL: €{total}"
                send(cid,msg,menu())
                self.send_response(200); self.end_headers(); return

            # MANAGE ACCOUNT
            if text=="Manage Account":
                user_states[cid]={"step":"manage"}
                send(cid,"1 Add\n2 Delete\n3 List")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="manage":
                if text=="1":
                    user_states[cid]={"step":"add_acc"}
                    send(cid,"Account name?")
                elif text=="2":
                    user_states[cid]={"step":"del_acc"}
                    send(cid,"Account name?")
                elif text=="3":
                    send(cid,"Accounts:\n"+"\n".join(accounts()),menu())
                    user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="add_acc":
                service().spreadsheets().values().append(
                    spreadsheetId=SHEET_ID,
                    range="Accounts!A:A",
                    valueInputOption="RAW",
                    body={"values":[[text]]}).execute()
                send(cid,"Added.",menu())
                user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="del_acc":
                send(cid,"Delete manually from sheet if unused.",menu())
                user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            # INCOME
            if text=="Income":
                user_states[cid]={"step":"inc_acc"}
                send(cid,"Account?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="inc_acc":
                user_states[cid]["acc"]=text
                user_states[cid]["step"]="inc_amt"
                send(cid,"Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="inc_amt":
                if text.isdigit():
                    append_row([now(),"Income",int(text),"",state["acc"],""])
                    send(cid,"Income saved.",menu())
                else:
                    send(cid,"Numbers only.",menu())
                user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            # EXPENSE
            if text=="Expense":
                user_states[cid]={"step":"exp_cat"}
                send(cid,"Category?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="exp_cat":
                user_states[cid]["cat"]=text
                user_states[cid]["step"]="exp_acc"
                send(cid,"Account?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="exp_acc":
                user_states[cid]["acc"]=text
                user_states[cid]["step"]="exp_amt"
                send(cid,"Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="exp_amt":
                if text.isdigit():
                    append_row([now(),"Expense",int(text),state["cat"],state["acc"],""])
                    send(cid,"Expense saved.",menu())
                else:
                    send(cid,"Numbers only.",menu())
                user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            # TRANSFER
            if text=="Transfer":
                user_states[cid]={"step":"t_from"}
                send(cid,"From account?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="t_from":
                user_states[cid]["from"]=text
                user_states[cid]["step"]="t_to"
                send(cid,"To account?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="t_to":
                user_states[cid]["to"]=text
                user_states[cid]["step"]="t_amt"
                send(cid,"Amount?")
                self.send_response(200); self.end_headers(); return

            if state and state["step"]=="t_amt":
                if text.isdigit():
                    a=int(text)
                    append_row([now(),"Transfer-Out",a,"Transfer",state["from"],""])
                    append_row([now(),"Transfer-In",a,"Transfer",state["to"],""])
                    send(cid,"Transfer saved.",menu())
                else:
                    send(cid,"Numbers only.",menu())
                user_states.pop(cid,None)
                self.send_response(200); self.end_headers(); return

            # ALL EXPENSE
            if text=="/all_expense":
                rows=get_sheet("Sheet1!A:F")
                total=0
                data={}
                for r in rows[1:]:
                    if len(r)>=4 and r[1].lower()=="expense":
                        a=int(float(r[2]))
                        total+=a
                        data[r[3]]=data.get(r[3],0)+a
                msg=f"Total Expense: €{total}\n\n"
                for k,v in sorted(data.items(),key=lambda x:x[1],reverse=True):
                    msg+=f"{k}: €{v}\n"
                send(cid,msg,menu())
                self.send_response(200); self.end_headers(); return

            send(cid,"Use menu.",menu())
            self.send_response(200); self.end_headers()

        except Exception as e:
            print("ERROR:",e)
            self.send_response(200)
            self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")

if __name__=="__main__":
    PORT=int(os.environ.get("PORT",8080))
    server=HTTPServer(("",PORT),handler)
    print("Server running...")
    server.serve_forever()
