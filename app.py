import os
import re
import tempfile
from flask import Flask, render_template, request
import pandas as pd
import pdfplumber

app = Flask(__name__)

BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]


def detect_category(text):

    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b, " ")

    raw = re.sub(r"[^a-zA-Z ]", "", raw)
    raw = raw.replace(" ", "")

    if "flipkart" in raw or "myntra" in raw or "ajio" in raw or "meesho" in raw:
        return "Shopping"
    if "swiggy" in raw or "zomato" in raw or "blinkit" in raw:
        return "Food"
    if "medical" in raw or "pharmacy" in raw or "chemist" in raw:
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

    try:
        file = request.files["file"]

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "stmt.pdf")
        file.save(path)

        # ---- Read tables if available ----
        rows = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if table:
                    rows.extend(table)

        # ---------- UNIVERSAL TEXT PARSER ----------
        def universal_parser():

            text = ""
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    text += (p.extract_text() or "") + "\n"

            lines = [l.strip() for l in text.split("\n") if l.strip()]

            data = []

            for i, line in enumerate(lines):

                amt = re.findall(r"(?:Rs\.?|INR|₹)\s?-?\s?[\d,]+(?:\.\d{1,2})?", line)

                if amt:

                    value = amt[-1]
                    value = (
                        value.replace("INR","")
                        .replace("Rs.","")
                        .replace("₹","")
                        .replace(",","")
                        .replace(" ","")
                        .replace("-","")
                    )

                    try:
                        value = float(value)
                    except:
                        continue

                    narration = lines[i-1] if i>0 else line

                    data.append([narration[:80], value])

            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        # ---- If NO table found ----
        if not rows:

            df = universal_parser()

            if df is None:
                return "No transactions detected — please upload full detailed statement."

            narr_col = "Narration"

        else:
            df = pd.DataFrame(rows)

            df.columns = df.iloc[0]
            df = df.iloc[1:].copy()
            df.reset_index(drop=True, inplace=True)

            if "Narration" not in df.columns:
                df["Narration"] = df.iloc[:,1]

            if "Amount" not in df.columns:
                df["Amount"] = df.iloc[:,-1]


        # ---- CLEAN AMOUNT ----
        df["Amount"] = df["Amount"].astype(str).str.replace(",", "", regex=False)
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

        df = df[df["Amount"] != 0]

        # ---- REMOVE NON EXPENSE ROWS ----
        df = df[~df["Narration"].astype(str).str.upper().str.contains(
            "TOTAL|INTEREST|BALANCE",
            na=False
        )]

        if len(df)==0:
            return "No valid transactions detected in this statement."

        # ---- CATEGORY ----
        df["AI Category"] = df["Narration"].apply(detect_category)

        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)

        cat_group = df.groupby("AI Category")["Amount"].sum()

        if len(cat_group)==0:
            top_category = "Not Available"
            cat_summary = []
        else:
            top_category = cat_group.idxmax()
            cat_summary = cat_group.reset_index().values


        rows = df.rename(columns={
            "Narration":"Description/Narration"
        })

        return render_template(
            "dashboard.html",
            rows=rows.to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=top_category,
            category_summary=cat_summary
        )

    except Exception as e:
        return f"❌ Error processing PDF:<br><br>{str(e)}"



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)