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

# ---------------- CATEGORY ENGINE ----------------
def detect_category(text):
    original = str(text).lower()
    raw = original

    for b in BANK_NOISE:
        raw = raw.replace(b," ")

    raw = re.sub(r"[^a-zA-Z ]","",raw).replace(" ","")

    # 🔥 1️⃣ PERSONAL UPI DETECTOR (MOST IMPORTANT)
    # name + bank + no merchant → Money Transfer
    if (
        "upi" in original and
        re.search(r"\b[a-z]{3,}\b", original) and
        any(x in original for x in ["sbin","hdfc","icici","axis","yesb","indb","aub","pnb"])
    ):
        # if NO merchant keyword present
        merchant_words = [
            "swiggy","zomato","flipkart","amazon","myntra","ajio",
            "bigbasket","recharge","bill","electricity","mobile",
            "netflix","prime","hotstar"
        ]
        if not any(m in original for m in merchant_words):
            return "Money Transfer"

    mp = {

        # 🛍 SHOPPING
        "Shopping":[
            "flipkart","flpkart","flpkrt","flipkrt","pkart",
            "amazon","amzn","myntra","ajio","meesho","jiomart",
            "tatacliq","cliq","nykaa","snapdeal","lenskart"
        ],

        # 🍔 FOOD
        "Food":[
            "swiggy","swiggylimited","zomato","eternal",
            "blinkit","instamart",
            "dominos","pizzahut","kfc",
            "restaurant","hotel","dhaba","cafe"
        ],

        # 🧺 GROCERY
        "Grocery":[
            "bigbasket","dmart","reliancefresh",
            "kirana","generalstore","mart","store"
        ],

        # 💊 HEALTHCARE
        "Healthcare":[
            "medical","pharmacy","chemist",
            "apollo","netmeds","1mg","hospital","clinic"
        ],

        # 🚖 TRAVEL
        "Travel":[
            "uber","ola","rapido","irctc","redbus"
        ],

        # 💡 BILLS (STRICT)
        "Bills":[
            "recharge","billdesk","electricity","water","gas",
            "mobile","broadband","wifi",
            "airtel","jio","vi","bsnl"
        ],

        # 🎬 SUBSCRIPTIONS
        "Subscriptions":[
            "netflix","primevideo","amazonprime",
            "hotstar","spotify","youtube"
        ]
    }

    # 2️⃣ Merchant based detection
    for category, keywords in mp.items():
        for k in keywords:
            if k in raw:
                return category

    # 3️⃣ Wallet should NEVER override personal UPI
    if "upi" in original:
        return "Money Transfer"

    return "Others"


# ---------------- AMOUNT CLEANER ----------------
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


# ⭐ MODE 1 → TABLE PARSER
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

    final=[]
    current=None

    for i in range(len(df)):
        row=df.iloc[i]

        date = str(row[idx_date]) if idx_date is not None else ""
        desc = str(row[idx_desc]) if idx_desc is not None else ""
        debit = clean_amt(row[idx_debit]) if idx_debit is not None else 0

        if re.search(r"\d",date):
            if current:
                final.append(current)

            current={
                "Date":date,
                "Description":desc.replace("\n"," ").strip(),
                "Amount":debit
            }
        else:
            if current and desc.strip():
                current["Description"] += " " + desc.replace("\n"," ").strip()
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


# ⭐ MODE 2 → TEXT PARSER
def parse_text(pdf):
    data=[]
    current=None

    IGNORE = [
        "auto generated","does not require",
        "customer care","call us","website",
        "email","address","branch","page"
    ]

    for page in pdf.pages:
        txt=page.extract_text()
        if not txt:
            continue

        for line in txt.split("\n"):
            l=line.lower().strip()
            if any(x in l for x in IGNORE):
                continue

            amt_match=re.search(r"\d+\.\d{2}", line)
            amt = clean_amt(amt_match.group()) if amt_match else 0

            if amt>0 and ("upi" in l or "imps" in l or "neft" in l):
                if current:
                    data.append(current)

                current={
                    "Date":"N/A",
                    "Description":line.strip(),
                    "Amount":amt
                }
            else:
                if current and line.strip():
                    current["Description"] += " " + line.strip()

        if current:
            data.append(current)
            current=None

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
            rows=df.rename(columns={
                "Date":"Transaction Date",
                "Description":"Description/Narration"
            }).to_dict("records"),
            total_spend=round(total,2),
            total_transactions=tx,
            top_category=top,
            category_summary=cat.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"


if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))