import os  
import re  
import tempfile  
from flask import Flask, render_template, request  
import pandas as pd  
import pdfplumber   

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
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flipkrt", "meesho"],  
        "swiggy":["swiggy","swiggylimited"],  
        "myntra":["myntra"],  
        "jiomart":["jiomart"],  
        "ajio":["ajio"],  
        "bigbasket":["bigbasket","dealshare"],  
        "medical":["medical","pharmacy","chemist"],  
        "kirana":["kirana","mart","store"],  
        "uber":["uber"],  
        "ola":["ola"],  
        "zomato":["zomato","blinkit"],  
        "recharge":["recharge","billdesk"]  
    }  

    for key, arr in replace_map.items():  
        for w in arr:  
            if w in raw: return key  

    if "swiggy" in raw or "zomato" in raw: return "Food"  
    if any(x in raw for x in ["flipkart", "myntra", "ajio", "meesho"]): return "Shopping"  
    if any(x in raw for x in ["kirana", "mart", "store", "bigbasket"]): return "Grocery"  
    if "medical" in raw or "pharmacy" in raw: return "Healthcare"  
    if "uber" in raw or "ola" in raw: return "Travel"  
    if "recharge" in raw or "bill" in raw: return "Bills"  
    if "upi" in str(text).lower(): return "Money Transfer"  

    return "Others"  

# ⭐ NEW: Strong Amount Filter to avoid Scientific Notation (ID vs Amount) ⭐
def clean_amt(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    # Sirf digits aur decimal point rakho
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # 12-digit Reference Number Filter: 
        # Agar number 1 crore (8 digit) se bada hai, toh wo ID hai, amount nahi.
        if num > 9999999 or len(v) >= 10: return 0.0 
        return num
    except: return 0.0

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

        rows = []  
        with pdfplumber.open(path) as pdf:  
            for page in pdf.pages:  
                table = page.extract_table()  
                if table: rows.extend(table)  

        # ⭐ 1. PAYTM/GENERAL TEXT PARSER ⭐
        def parse_text_fallback():  
            text = ""  
            with pdfplumber.open(path) as pdf:  
                for p in pdf.pages: text += (p.extract_text() or "") + "\n"  
            
            lines = text.split("\n")  
            data = []  
            for i, L in enumerate(lines):
                # Regex for currency: Matches 1.00 to 99,999.00 but ignores 12-digit strings
                amt_match = re.findall(r"(?:Rs\.?|₹|\s)(\d{1,6}(?:\.\d{2}))(?!\d)", L)
                if amt_match:
                    val = clean_amt(amt_match[-1])
                    if val > 0:
                        desc = lines[i][:60] if len(lines[i]) > 10 else L[:60]
                        data.append([desc, val])  
            return pd.DataFrame(data, columns=["Narration","Amount"]) if data else None  

        if not rows:  
            df = parse_text_fallback()  
            if df is None: return "❌ No transactions detected. PDF structure unknown."  
            narr_col, date_col = "Narration", None  
        else:  
            df = pd.DataFrame(rows)  
            df.columns = [str(c).lower() for c in df.iloc[0]]
            df = df.iloc[1:].copy()  
            
            def find(col_list, keywords):  
                for c in col_list:  
                    if any(w in str(c) for w in keywords): return c  
                return None  

            date_col = find(df.columns, ["date","txn","posting"])  
            narr_col = find(df.columns, ["narr","details","description","particular","remarks"])  
            debit_col = find(df.columns, ["debit","withdraw","dr","outflow"])  
            credit_col = find(df.columns, ["credit","deposit","cr","inflow"])  

            if not narr_col: narr_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]  

            # Calculation Fix: Priority to Debit/Withdrawal column
            if debit_col and credit_col:
                df["Amount"] = df.apply(lambda r: clean_amt(r[debit_col]) if clean_amt(r[debit_col]) > 0 else clean_amt(r[credit_col]), axis=1)
            elif debit_col:
                df["Amount"] = df[debit_col].apply(clean_amt)
            elif credit_col:
                df["Amount"] = df[credit_col].apply(clean_amt)
            else:
                # Last resort: Try cleaning every column and find the first number
                df["Amount"] = df.apply(lambda r: next((clean_amt(v) for v in r if clean_amt(v) > 0), 0.0), axis=1)

        # Final Cleanup
        df = df[df["Amount"] > 0].copy()
        if narr_col not in df.columns: df[narr_col] = "Transaction"
        
        df = df[~df[narr_col].astype(str).str.upper().str.contains("TOTAL|INTEREST|BALANCE|LIMIT|OPENING", na=False)]  

        if df.empty: return "❌ No valid transactions detected in this statement."  

        df["AI Category"] = df[narr_col].apply(detect_category)  
        total_spend = round(df["Amount"].sum(), 2)  
        total_transactions = len(df)  
        
        cat_group = df.groupby("AI Category")["Amount"].sum()  
        top_category = cat_group.idxmax() if not cat_group.empty else "N/A"  
        cat_summary = cat_group.reset_index().values.tolist()  

        rows_dict = df.rename(columns={  
            date_col if date_col else narr_col: "Transaction Date",  
            narr_col: "Description/Narration"  
        }).to_dict("records")  

        return render_template("dashboard.html", rows=rows_dict, total_spend=total_spend, 
                               total_transactions=total_transactions, top_category=top_category, 
                               category_summary=cat_summary)  

    except Exception as e: return f"❌ Error: {str(e)}"  

if __name__ == "__main__":  
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
