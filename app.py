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

# ---------------- SMART CATEGORY ENGINE -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")

    raw = re.sub(r"[^a-zA-Z ]", "", raw)
    raw = raw.replace(" ", "")

    # User's Specific Mapping
    replace_map = {  
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt", "meesho", "me eesho", "m essho", "m e e s h o"],  
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
            if w in raw: return key

    if any(x in raw for x in ["swiggy", "zomato"]): return "Food"
    if any(x in raw for x in ["flipkart", "myntra", "jiomart", "ajio", "meesho"]): return "Shopping"
    if any(x in raw for x in ["kirana", "mart", "store", "bigbasket"]): return "Grocery"
    if "upi" in str(text).lower(): return "Money Transfer"

    return "Others"

# ⭐ FIXED: Amount Cleaner (Ignores IDs & Limits size) ⭐
def clean_amt(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # 12-digit Ref No check + Large amount filter
        if num > 999999 or len(v) >= 10: return 0.0 
        return num
    except: return 0.0

# ---------------- ROBUST PDF PARSER -------------------
def extract_data_from_pdf(path):
    all_rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                cleaned_table = [[str(cell) if cell else "" for cell in row] for row in table]
                all_rows.extend(cleaned_table)
            else:
                text = page.extract_text()
                if text:
                    for line in text.split('\n'):
                        if re.search(r'\d+\.\d{2}', line): all_rows.append([line])

    if not all_rows: return None
    return process_raw_data(all_rows)

def process_raw_data(rows):
    df = pd.DataFrame(rows)
    if len(df) > 1:
        df.columns = [str(c).lower() for c in df.iloc[0]]
        df = df[1:].reset_index(drop=True)

    keywords = {
        'date': ['date', 'txn', 'time'],
        'desc': ['description', 'narration', 'particulars', 'details', 'remarks'],
        'debit': ['debit', 'withdraw', 'outflow', 'dr', 'paid'],
        'credit': ['credit', 'deposit', 'inflow', 'cr', 'received']
    }

    def find_best_col(key_list):
        for col in df.columns:
            if any(k in str(col).lower() for k in key_list): return col
        return None

    date_col = find_best_col(keywords['date'])
    desc_col = find_best_col(keywords['desc'])
    debit_col = find_best_col(keywords['debit'])
    credit_col = find_best_col(keywords['credit'])

    final_data = []
    for _, row in df.iterrows():
        try:
            description = str(row[desc_col]) if desc_col else str(row.values[0])
            
            # ⛔ FILTER: Ignore Summary/Total rows ⛔
            if any(x in description.upper() for x in ["TOTAL", "SUMMARY", "BALANCE", "OPENING", "CLOSING", "LIMIT"]):
                continue

            # 💰 LOGIC: Only pick DEBIT (Spend) 💰
            val = 0.0
            if debit_col:
                val = clean_amt(row[debit_col])
            
            # Agar Credit column mein value hai aur Debit 0 hai, toh skip (Credit = Spend nahi hai)
            if val == 0: continue

            date = str(row[date_col]) if date_col else "N/A"

            final_data.append({
                "Transaction Date": date,
                "Description/Narration": description[:100],
                "Amount": val,
                "AI Category": detect_category(description)
            })
        except: continue

    return pd.DataFrame(final_data)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        file = request.files.get("file")
        if not file: return "No file uploaded"

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file.save(tmp.name)
            path = tmp.name

        df = extract_data_from_pdf(path)
        os.unlink(path)

        if df is None or df.empty:
            return "❌ No valid spend (Debit) transactions detected."

        # Final calculations
        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)
        cat_group = df.groupby("AI Category")["Amount"].sum()
        cat_summary = cat_group.reset_index().values.tolist()
        top_cat = cat_group.idxmax() if not cat_group.empty else "N/A"

        return render_template(
            "dashboard.html",
            rows=df.to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=top_cat,
            category_summary=cat_summary
        )
    except Exception as e:
        return f"❌ Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
