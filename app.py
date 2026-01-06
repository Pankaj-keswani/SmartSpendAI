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

# ---------------- CATEGORY ENGINE (Food, Shopping, etc.) -------------------  
def detect_category(text):  
    raw = str(text).lower()  

    for b in BANK_NOISE:  
        raw = raw.replace(b, " ")  

    raw = re.sub(r"[^a-zA-Z ]", "", raw)  
    raw = raw.replace(" ", "")  

    replace_map = {  
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt", "meesho", "meeesho", "messho"],  
        "swiggy":["swiggy","swiggylimited"],  
        "myntra":["myntra"],  
        "jiomart":["jiomart"],  
        "ajio":["ajio"],  
        "bigbasket":["bigbasket","dealshare","dealshare"],  
        "medical":["medical","pharmacy","chemist"],  
        "kirana":["kirana","mart","store"],  
        "uber":["uber"],  
        "ola":["ola"],  
        "zomato":["zomato","eternal","blinkit"],  
        "recharge":["recharge","billdesk"]  
    }  

    # App name mapping to Category
    mapped_val = raw
    for key, arr in replace_map.items():  
        for w in arr:  
            if w in raw:  
                mapped_val = key  
                break

    if mapped_val in ["swiggy", "zomato"]: return "Food"  
    if mapped_val in ["flipkart", "myntra", "jiomart", "ajio", "meesho"]: return "Shopping"  
    if mapped_val in ["kirana", "mart", "store", "bigbasket"]: return "Grocery"  
    if mapped_val in ["medical", "pharmacy"]: return "Healthcare"  
    if mapped_val in ["uber", "ola"]: return "Travel"  
    if mapped_val in ["recharge", "billdesk"]: return "Bills"  
    if "upi" in str(text).lower(): return "Money Transfer"  

    return "Others"  

# ⭐ HELPER: Clean Amount & Ignore IDs (Fixed Scientific Notation) ⭐
def clean_amount_val(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    # Sirf digits aur decimal point rakho, baaki sab hata do
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # 12-digit UPI ID check: Agar digits 10 se zyada hain aur decimal nahi hai, toh ID hai.
        # Bank amounts usually 10 lakh ke niche hote hain (9999999)
        if num > 9999999 or len(v.split('.')[0]) >= 10: return 0.0 
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
                table = page.extract_table({
                    "vertical_strategy": "lines", 
                    "horizontal_strategy": "text",
                    "snap_tolerance": 3,
                })
                if table: rows.extend(table)  

        # --- TERE ORIGINAL FALLBACKS (With Precision Fix) ---
        def universal_parser():
            text = ""
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages: text += (p.extract_text() or "") + "\n"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            data = []
            for i, line in enumerate(lines):
                # Only look for Debit patterns (often marked with Dr or specific spacing)
                amt_match = re.findall(r"(?:Rs\.?|INR|₹|\s)([\d,]+\.\d{2})", line)
                if amt_match:
                    value = clean_amount_val(amt_match[-1])
                    if value > 0:
                        narration = lines[i-1] if i > 0 else line
                        data.append([narration[:80], value])
            return pd.DataFrame(data, columns=["Narration","Amount"]) if data else None

        # --- MAIN LOGIC ---
        if not rows:  
            df = universal_parser()
            if df is None: return "❌ Could not read transactions. PDF structure might be unsupported or encrypted."  
            narr_col, date_col = "Narration", None  
        else:  
            df = pd.DataFrame(rows)
            # Remove empty columns
            df = df.dropna(how='all', axis=1)
            df.columns = [str(c).lower().strip() if c else f"col_{i}" for i, c in enumerate(df.iloc[0])]
            df = df.iloc[1:].copy()  
            
            def find_col(words):  
                for c in df.columns:  
                    if any(w in str(c) for w in words): return c  
                return None  

            date_col = find_col(["date", "txn", "posting", "time"])  
            narr_col = find_col(["narr", "details", "description", "particular", "remarks"])  
            debit_col = find_col(["debit", "withdraw", "dr", "outflow", "amt paid", "payment"])  
            credit_col = find_col(["credit", "deposit", "cr", "inflow", "received"])  

            if not narr_col: narr_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]  

            # 🔥 DEBIT-ONLY LOGIC: Spends only 🔥
            if debit_col:
                df["Amount"] = df[debit_col].apply(clean_amount_val)
            else:
                # Agar Debit col nahi mila, toh last column check karo (usually amount hota hai)
                df["Amount"] = df.iloc[:, -1].apply(clean_amount_val)

        # Cleanup: Remove 0 amounts, Totals, and Credits
        df = df[df["Amount"] > 0].copy()
        # Filter Summary lines (Total, Balance, etc.)
        summary_keywords = "TOTAL|INTEREST|BALANCE|SUMMARY|LIMIT|OPENING|CLOSING|BROUGHT|CARRIED"
        df = df[~df[narr_col].astype(str).str.upper().str.contains(summary_keywords, na=False)]  

        if df.empty: return "❌ No spend transactions (Debit) found in this statement."  

        # Categorization
        df["AI Category"] = df[narr_col].apply(detect_category)  
        total_spend = round(df["Amount"].sum(), 2)  
        total_transactions = len(df)  
        
        cat_group = df.groupby("AI Category")["Amount"].sum()  
        top_category = cat_group.idxmax() if not cat_group.empty else "Others"  
        cat_summary = cat_group.reset_index().values.tolist()  

        rows_dict = df.rename(columns={  
            date_col if date_col else narr_col: "Transaction Date",  
            narr_col: "Description/Narration"  
        }).to_dict("records")  

        return render_template("dashboard.html", rows=rows_dict, total_spend=total_spend, 
                               total_transactions=total_transactions, top_category=top_category, 
                               category_summary=cat_summary)  

    except Exception as e: return f"❌ System Error: {str(e)}"  

if __name__ == "__main__":  
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
