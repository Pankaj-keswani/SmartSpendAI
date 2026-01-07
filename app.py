import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

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

    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","meesho","myntra","ajio","jiomart"],  
        "Food": ["swiggy","zomato","blinkit"],  
        "Grocery": ["bigbasket","dealshare","mart","store"],  
        "Healthcare": ["medical","pharmacy","chemist"],  
        "Travel": ["uber","ola"],  
        "Bills": ["recharge","billdesk","bill"]  
    }

    for category, arr in replace_map.items():
        for w in arr:
            if w in raw:
                return category
    
    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"


# ⭐ Accurate Debit Amount Cleaner ⭐
def clean_amt(val):
    if not val or str(val).strip() == "" or str(val).strip() == "-":
        return 0.0
    
    v = re.sub(r'[^\d.]', '', str(val))

    try:
        num = float(v)

        # 🚨 IDs / UTR length filter
        if len(v.replace(".", "")) >= 10:
            return 0.0
        
        # 🚨 Any absurdly large amount ignore
        if num > 9999999:
            return 0.0
        
        return num
    except:
        return 0.0



# ---------------- UNIVERSAL PDF PARSER ----------------
def extract_data_from_pdf(path):
    rows = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()

            if table:
                table = [[str(c) if c else "" for c in row] for row in table]
                rows.extend(table)

    if not rows:
        return None
    

    df = pd.DataFrame(rows)

    # First row most likely header
    headers = [str(x).lower() for x in df.iloc[0]]
    df = df[1:].reset_index(drop=True)

    # find best columns
    def find(col_list):
        for i, h in enumerate(headers):
            if any(k in h for k in col_list):
                return i
        return None

    idx_date = find(["date"])
    idx_desc = find(["description","narration","particular"])
    idx_debit = find(["debit","withdraw","dr"])
    idx_credit = find(["credit","deposit","cr"])

    final = []
    current = None

    for i in range(len(df)):
        row = df.iloc[i]

        date_val = str(row[idx_date]) if idx_date is not None else ""
        desc_val = str(row[idx_desc]) if idx_desc is not None else ""
        debit_val = clean_amt(row[idx_debit]) if idx_debit is not None else 0
        credit_val = clean_amt(row[idx_credit]) if idx_credit is not None else 0

        # detect date row
        if re.search(r'\d', date_val):
            if current:
                final.append(current)

            current = {
                "Date": date_val,
                "Description": desc_val.replace("\n"," "),
                "Amount": debit_val  # ONLY DEBIT COUNT
            }
        
        else:
            if current:
                current["Description"] += " " + desc_val

                if not current["Amount"]:
                    current["Amount"] = debit_val
    

    if current:
        final.append(current)


    df = pd.DataFrame(final)

    # --- FILTER ONLY VALID DEBITS ---
    df = df[df["Amount"] > 0]

    # remove totals
    df = df[~df["Description"].str.upper().str.contains("TOTAL|INTEREST|BALANCE|SUMMARY")]

    return df



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

        df = extract_data_from_pdf(path)
        os.unlink(path)

        if df is None or df.empty:
            return "❌ No valid transactions found"

        df["AI Category"] = df["Description"].apply(detect_category)

        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)

        cat_group = df.groupby("AI Category")["Amount"].sum().reset_index()

        return render_template(
            "dashboard.html",
            rows=df.rename(columns={"Date":"Transaction Date","Description":"Description/Narration"}).to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=cat_group.loc[cat_group['Amount'].idxmax()]['AI Category'],
            category_summary=cat_group.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))