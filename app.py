import os
import re
import tempfile
from flask import Flask, render_template, request
import pandas as pd
import pdfplumber   # 👈 camelot ki jagah ye

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]

# ---------------- CATEGORY ENGINE -------------------
def detect_category(text):

    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b, " ")

    raw = re.sub(r"[^a-zA-Z ]", "", raw)
    raw = raw.replace(" ", "")

    replace_map = {
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt"],
        "swiggy":["swiggy","swiggylimited"],
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

    for key, arr in replace_map.items():
        for w in arr:
            if w in raw:
                raw = key

    if "swiggy" in raw or "zomato" in raw:
        return "Food"

    if "flipkart" in raw or "myntra" in raw or "jiomart" in raw or "ajio" in raw:
        return "Shopping"

    if "kirana" in raw or "mart" in raw or "store" in raw or "bigbasket" in raw:
        return "Grocery"

    if "medical" in raw or "pharmacy" in raw:
        return "Healthcare"

    if "uber" in raw or "ola" in raw:
        return "Travel"

    if "recharge" in raw or "bill" in raw:
        return "Bills"

    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():

    file = request.files["file"]

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "stmt.pdf")
    file.save(path)

    # ---------- SAFE PDF PARSE (NO CAMEL0T) ----------
    rows = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                rows.extend(table)

    if not rows:
        return "No tables detected in PDF"

    df = pd.DataFrame(rows)

    # ---------- SAFE HEADER ----------
    df.columns = df.iloc[0]
    df = df.iloc[1:].copy()
    df.reset_index(drop=True, inplace=True)

    # ---------- SAFE COLUMN DETECT ----------
    def find(col, word):
        for c in col:
            if word in str(c).lower():
                return c
        return None

    date_col = find(df.columns, "date")
    narr_col = find(df.columns, "narr")
    debit_col = find(df.columns, "debit")
    credit_col = find(df.columns, "credit")

    if not all([date_col, narr_col, debit_col, credit_col]):
        return "Statement format not supported"

    # ---------- AMOUNT ----------
    df["Amount"] = df[debit_col].fillna(df[credit_col])

    df["Amount"] = (
        df["Amount"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .replace("-", "0")
    )

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    df = df[df["Amount"] > 0]

    df = df[~df[narr_col].str.upper().str.contains("TOTAL|INTEREST", na=False)]

    df["AI Category"] = df[narr_col].apply(detect_category)

    total_spend = round(df["Amount"].sum(), 2)
    total_transactions = len(df)

    cat_summary = (
        df.groupby("AI Category")["Amount"]
        .sum()
        .reset_index()
        .values
    )

    rows = df.rename(columns={
        date_col: "Transaction Date",
        narr_col: "Description/Narration"
    })

    return render_template(
        "dashboard.html",
        rows=rows.to_dict("records"),
        total_spend=total_spend,
        total_transactions=total_transactions,
        top_category=df.groupby("AI Category")["Amount"].sum().idxmax(),
        category_summary=cat_summary
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
