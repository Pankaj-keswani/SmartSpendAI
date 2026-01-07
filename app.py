import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]


# ------------- CATEGORY ENGINE ----------------
def detect_category(text):
    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b," ")

    raw = re.sub(r"[^a-zA-Z ]","",raw).replace(" ","")

    replace_map = {  
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt","meesho","me eesho","m essho","m e e s h o"],  
        "swiggy":["swiggy","swiggylimited","instamart"],  
        "myntra":["myntra"],  
        "jiomart":["jiomart"],  
        "ajio":["ajio"],  
        "bigbasket":["bigbasket","dealshare","deal share","de alshare"],  
        "medical":["medical","pharmacy","chemist"],  
        "kirana":["kirana","mart","store"],  
        "uber":["uber"],  
        "ola":["ola"],  
        "zomato":["zomato","eternal","blinkit","b linkit"],  
        "recharge":["recharge","billdesk"]  
    }  

    for cat,keys in replace_map.items():
        for k in keys:
            if k in raw:
                if cat in ["swiggy","zomato","blinkit"]:
                    return "Food"
                if cat in ["flipkart","myntra","ajio","jiomart","meesho"]:
                    return "Shopping"
                if cat in ["kirana","bigbasket","mart","store"]:
                    return "Grocery"
                if cat in ["medical","pharmacy"]:
                    return "Healthcare"
                if cat in ["uber","ola"]:
                    return "Travel"
                if cat=="recharge":
                    return "Bills"

    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"



# ------------- AMOUNT CLEANER ----------------
def clean_amt(v):
    if not v or str(v).strip()=="" or str(v).strip()=="-":
        return 0.0

    v = re.sub(r"[^\d.]", "", str(v))

    try:
        num = float(v)

        if len(v.replace(".",""))>=10:
            return 0.0

        if num>9999999:
            return 0.0

        return num
    except:
        return 0.0



# ------------- TABLE PARSER ----------------
def parse_table(pdf):
    rows=[]
    for page in pdf.pages:
        table = page.extract_table()
        if table:
            table = [[str(c) if c else "" for c in r] for r in table]
            rows.extend(table)

    if not rows:
        return None

    df = pd.DataFrame(rows)

    headers = [str(x).lower() for x in df.iloc[0]]
    df=df[1:].reset_index(drop=True)

    def find(keys):
        for i,h in enumerate(headers):
            if any(k in h for k in keys):
                return i
        return None

    idx_date = find(["date"])
    idx_desc = find(["description","narration","details","particular"])
    idx_debit = find(["debit","withdraw","dr"])
    idx_credit = find(["credit","cr","deposit"])

    final=[]
    current=None

    for i in range(len(df)):
        row=df.iloc[i]

        date = str(row[idx_date]) if idx_date is not None else ""
        desc = str(row[idx_desc]) if idx_desc is not None else ""
        debit = clean_amt(row[idx_debit]) if idx_debit is not None else 0
        credit = clean_amt(row[idx_credit]) if idx_credit is not None else 0


        # -------- NEW TXN --------
        if re.search(r"\d",date):

            if current:
                final.append(current)

            current={
                "Date":date,
                # 🔥 FULL EXACT DESCRIPTION — NO TRIM
                "Description":desc.replace("\n"," ").strip(),
                "Amount":debit
            }

        # -------- CONTINUATION LINE --------
        else:
            if current:
                if desc.strip():
                    current["Description"]+=" "+desc.replace("\n"," ").strip()

                if not current["Amount"]:
                    current["Amount"]=debit


    if current:
        final.append(current)

    df=pd.DataFrame(final)

    if df.empty:
        return None

    df=df[df["Amount"]>0]
    df=df[~df["Description"].str.upper().str.contains("TOTAL|BALANCE|SUMMARY|INTEREST")]

    return df



# -------- TEXT BACKUP PARSER --------
def parse_text(pdf):
    data=[]
    for page in pdf.pages:
        txt=page.extract_text()
        if not txt: continue

        for line in txt.split("\n"):

            amt_match=re.search(r"\d+\.\d{2}", line)
            if not amt_match: 
                continue

            amt=clean_amt(amt_match.group())
            if amt==0:
                continue

            data.append({
                "Date":"N/A",
                # 🔥 NO TRUNCATE HERE ALSO
                "Description":line.strip(),
                "Amount":amt
            })

    if not data:
        return None

    df=pd.DataFrame(data)
    df=df[df["Amount"]>0]
    df=df[~df["Description"].str.upper().str.contains("TOTAL|BALANCE|SUMMARY|INTEREST")]
    return df



def extract_data(path):
    with pdfplumber.open(path) as pdf:

        df=parse_table(pdf)

        if df is None or df.empty:
            df=parse_text(pdf)

        return df



@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        file=request.files["file"]

        with tempfile.NamedTemporaryFile(delete=False,suffix=".pdf") as tmp:
            file.save(tmp.name)
            path=tmp.name

        df=extract_data(path)
        os.unlink(path)

        if df is None or df.empty:
            return "❌ Unsupported / unreadable format"

        df["AI Category"]=df["Description"].apply(detect_category)

        total=df["Amount"].sum()
        tx=len(df)

        cat=df.groupby("AI Category")["Amount"].sum().reset_index()
        top=cat.loc[cat["Amount"].idxmax()]["AI Category"]

        return render_template(
            "dashboard.html",
            rows=df.rename(columns={"Date":"Transaction Date","Description":"Description/Narration"}).to_dict("records"),
            total_spend=round(total,2),
            total_transactions=tx,
            top_category=top,
            category_summary=cat.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"



if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))