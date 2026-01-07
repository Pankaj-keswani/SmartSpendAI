import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = ["upi","transfer","hdfc","sbin","icici","idfc","utr","payment","paid","via","yesb","axis","from","to","ref","upiint","upiintnet"]

# ---------------- CATEGORY ENGINE (Integrated from Code 1 & 2) -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","flpkartpayment","flipkrt", "meesho", "myntra", "ajio", "amazon", "jiomart"],  
        "Food": ["swiggy","swiggylimited", "zomato","eternal","blinkit"],  
        "Grocery": ["bigbasket","dealshare", "kirana","mart","store"],  
        "Healthcare": ["medical","pharmacy","chemist"],  
        "Travel": ["uber","ola"],  
        "Bills": ["recharge","billdesk", "bill"]  
    }

    for category, keywords in replace_map.items():
        for word in keywords:
            if word in raw:
                return category

    if "upi" in str(text).lower():
        return "Money Transfer"
    return "Others"

# ---------------- ⭐ STRICT AMOUNT CLEANER (Fixed Calculation) ⭐ -------------------
def clean_amt(val):
    if not val or str(val).strip() in ["", "-", "None", "0"]: return 0.0
    # Sirf digits aur decimal point rakho
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # ⛔ ERROR FIX: 12-digit UPI ID (Reference No) ko block karne ke liye
        # Agar number 1 crore se bada hai ya length 10+ hai toh ignore karo
        if len(v.replace(".", "")) >= 10 or num > 9999999: return 0.0
        return num
    except:
        return 0.0

# ---------------- UNIVERSAL HYBRID PARSER -------------------
def universal_parser(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table: continue
            
            df = pd.DataFrame(table)
            # Universal Header Detection
            headers = [str(x).lower().strip() if x else "" for x in df.iloc[0]]
            
            # Keywords to find Date, Description, and Debit columns
            idx_date = next((i for i, h in enumerate(headers) if any(x in h for x in ["date", "txn"])), 0)
            idx_desc = next((i for i, h in enumerate(headers) if any(x in h for x in ["description", "narration", "particulars", "details"])), 1)
            # Strict Debit detection (Ignoring Balance and Credit)
            idx_debit = next((i for i, h in enumerate(headers) if any(x in h for x in ["debit", "withdraw", "dr", "out"]) and "balance" not in h), -1)

            if idx_debit == -1: # Fallback to Code 2 style if no specific debit col
                idx_debit = len(headers) - 1

            current_row = None
            
            for i in range(1, len(df)):
                row = df.iloc[i]
                date_val = str(row[idx_date]).strip()
                desc_val = str(row[idx_desc]).replace("\n", " ") if row[idx_desc] else ""
                amt_val = clean_amt(row[idx_debit])

                # Check if this row starts a new transaction (Date exists)
                if re.search(r'\d{1,2}[\s\-\/]([A-Za-z]{3}|\d{1,2})', date_val):
                    if current_row: all_data.append(current_row)
                    
                    current_row = {
                        "Date": date_val,
                        "Description": desc_val,
                        "Amount": amt_val
                    }
                else:
                    # Multiline description support (From Code 1)
                    if current_row and desc_val:
                        current_row["Description"] += " " + desc_val
                        # Agar upar wali line mein amount nahi tha, toh yahan se uthao
                        if current_row["Amount"] == 0:
                            current_row["Amount"] = amt_val
            
            if current_row: all_data.append(current_row)

    final_df = pd.DataFrame(all_data)
    if final_df.empty: return final_df
    
    # Cleaning: Spends only (>0) and Remove Total/Interest lines
    final_df = final_df[final_df["Amount"] > 0]
    final_df = final_df[~final_df["Description"].str.upper().str.contains("TOTAL|INTEREST|BALANCE|SUMMARY|LIMIT")]
    
    return final_df

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

        df = universal_parser(path)
        os.unlink(path)

        if df.empty:
            return "❌ No valid transactions detected. Please ensure the PDF is not password protected."

        df["AI Category"] = df["Description"].apply(detect_category)
        
        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)
        cat_group = df.groupby("AI Category")["Amount"].sum().reset_index()
        top_cat = cat_group.loc[cat_group['Amount'].idxmax()]['AI Category'] if not cat_group.empty else "N/A"
        
        return render_template(
            "dashboard.html",
            rows=df.rename(columns={"Date": "Transaction Date", "Description": "Description/Narration"}).to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=top_cat,
            category_summary=cat_group.values.tolist()
        )
    except Exception as e:
        return f"❌ System Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
