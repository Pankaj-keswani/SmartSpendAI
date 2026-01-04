import os
import re
import tempfile
from flask import Flask, render_template, request
import pandas as pd
import camelot
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

    # ---- Normalise Brands ----
    replace_map = {
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt"],
        "swiggy":["swiggy","swiggylimited"],
        "myntra":["myntra"],
        "jiomart":["jiomart"],
        "ajio":["ajio"],
        "bigbasket":["bigbasket", "DE ALSHARE", "deal share", "dealshare"],
        "medical":["medical","pharmacy","chemist"],
        "kirana":["kirana","mart","store"],
        "uber":["uber"],
        "ola":["ola"],
        "zomato":["zomato", "eternal", "et ernal", "b linkit"],
        "recharge":["recharge","billdesk"]
    }

    for key, arr in replace_map.items():
        for w in arr:
            if w in raw:
                raw = key

    # ---------- Rules ----------
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

    if "upi" in text.lower():
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

    tables = camelot.read_pdf(path, pages="all", flavor="lattice")
    df = pd.concat([t.df for t in tables], ignore_index=True)

    df.columns = df.iloc[0]
    df = df.iloc[1:]
    df.reset_index(drop=True, inplace=True)

    # Detect date + narration + debit + credit columns
    date_col = [c for c in df.columns if "date" in str(c).lower()][0]
    narr_col = [c for c in df.columns if "narr" in str(c).lower()][0]
    debit_col = [c for c in df.columns if "debit" in str(c).lower()][0]
    credit_col = [c for c in df.columns if "credit" in str(c).lower()][0]

    # Build Amount
    df["Amount"] = df[debit_col].fillna(df[credit_col])

    # Handle "-" safely
    df["Amount"] = (
    df["Amount"]
    .astype(str)
    .str.replace(",", "", regex=False)
    .replace("-", "0")
    )
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

    # Remove zero-value rows
    df = df[df["Amount"] > 0]

    # Remove "TOTAL / INTEREST"
    df = df[~df[narr_col].str.upper().str.contains("TOTAL|INTEREST", na=False)]

    # Detect category
    df["AI Category"] = df[narr_col].apply(detect_category)

    # -------- Summary values --------
    total_spend = round(df["Amount"].sum(),2)
    total_transactions = len(df)

    cat_summary = (
        df.groupby("AI Category")["Amount"]
        .sum()
        .reset_index()
        .values
    )

    # send date + desc + amount + category
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
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )


if __name__ == "__main__":
    app.run(debug=True)
