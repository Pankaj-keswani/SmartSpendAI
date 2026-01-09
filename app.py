import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ---------------- CATEGORY ENGINE ----------------
BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]

def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {
        "Food": ["swiggy","zomato","blinkit","instamart","dominos","pizza","kfc","faasos"],
        "Shopping": ["flipkart","amazon","myntra","ajio","meesho","jiomart"],
        "Grocery": ["bigbasket","dmart","kirana","generalstore","store","mart"],
        "Healthcare": ["medical","pharmacy","chemist","apollo","1mg"],
        "Travel": ["uber","ola","rapido","irctc"],
        "Bills": ["recharge","billdesk","electricity","gas","water","mobile"]
    }

    for cat, keys in replace_map.items():
        for k in keys:
            if k in raw:
                return cat

    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"


# ---------------- AMOUNT CLEANER ----------------
def clean_amt(val):
    val = re.sub(r"[^\d.]", "", str(val))
    try:
        return float(val)
    except:
        return 0.0


# ---------------- PERFECT TEXT PARSER ----------------
def extract_data(path):
    transactions = []

    IGNORE = [
        "auto generated", "does not require signature",
        "customer care", "call us", "website",
        "email", "address", "branch", "page"
    ]

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for line in text.split("\n"):
                l = line.lower().strip()
                if any(x in l for x in IGNORE):
                    continue

                # DATE at start
                date_match = re.match(r"^(\d{2}\s\w+\s\d{4})", line)
                if not date_match:
                    continue

                # Find all amounts in line
                amounts = re.findall(r"\d+\.\d{2}", line)
                if len(amounts) < 2:
                    continue

                # Statement format:
                # Date | Particulars | Withdrawal | Deposit | Balance
                # Debit = FIRST non-zero amount from right (except balance)

                balance = amounts[-1]
                debit = amounts[-2]

                debit_amt = clean_amt(debit)
                if debit_amt <= 0:
                    continue

                # Extract PARTICULARS only
                # Remove date & trailing amounts
                desc = re.sub(r"\d{2}\s\w+\s\d{4}", "", line)
                desc = re.sub(r"\d+\.\d{2}", "", desc)
                desc = re.sub(r"\s{2,}", " ", desc).strip()

                transactions.append({
                    "Date": date_match.group(1),
                    "Description": desc,
                    "Amount": debit_amt
                })

    return pd.DataFrame(transactions)


# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        file = request.files["file"]

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file.save(tmp.name)
            path = tmp.name

        df = extract_data(path)
        os.unlink(path)

        if df.empty:
            return "❌ No valid transactions found"

        df["AI Category"] = df["Description"].apply(detect_category)

        total = df["Amount"].sum()
        tx = len(df)

        cat = df.groupby("AI Category")["Amount"].sum().reset_index()
        top = cat.loc[cat["Amount"].idxmax()]["AI Category"]

        return render_template(
            "dashboard.html",
            rows=df.rename(columns={
                "Date": "Transaction Date",
                "Description": "Description/Narration"
            }).to_dict("records"),
            total_spend=round(total, 2),
            total_transactions=tx,
            top_category=top,
            category_summary=cat.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)