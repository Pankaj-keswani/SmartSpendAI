import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = ["upi","transfer","hdfc","sbin","icici","idfc","utr","payment","paid","via","yesb","axis","from","to","ref","upiint","upiintnet"]

# ---------------- CATEGORY ENGINE (Code 2 Logic) -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","flpkartpayment","flipkrt", "meesho", "myntra", "ajio", "amazon"],  
        "Food": ["swiggy","swiggylimited", "zomato","eternal","blinkit"],  
        "Grocery": ["bigbasket","dealshare", "kirana","mart","store", "jiomart"],  
        "Healthcare": ["medical","pharmacy","chemist"],  
        "Travel": ["uber","ola"],  
        "Bills": ["recharge","billdesk", "bill"]  
    }

    for category, keywords in replace_map.items():
        for word in keywords:
            if word in raw: return category

    return "Money Transfer" if "upi" in str(text).lower() else "Others"

# ---------------- ⭐ STRICT AMOUNT CLEANER (Scientific Notation Fix) ⭐ -------------------
def clean_amt(val):
    if not val or str(val).strip() in ["", "-", "None", "0"]: return 0.0
    # Sirf digits aur decimal point rakho
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # ⛔ ERROR FIX: 12-digit UPI ID ko block karne ke liye (Code 1 & 2 logic merge)
        # Agar number ki length decimal ke pehle 9 se zyada hai, toh wo ID hai, Amount nahi.
        if len(v.split('.')[0]) >= 9 or num > 9999999: return 0.0
        return num
    except: return 0.0

# ---------------- HYBRID PDF PARSER (Universal + Multiline Support) -------------------
def universal_hybrid_parser(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table: continue
            
            df = pd.DataFrame(table)
            # Find the header row (usually contains keywords)
            header_idx = 0
            for i, row in df.iterrows():
                row_str = " ".join([str(x).lower() for x in row if x])
                if "date" in row_str and ("desc" in row_str or "particular" in row_str):
                    header_idx = i
                    break
            
            headers = [str(x).lower().strip() if x else f"col_{j}" for j, x in enumerate(df.iloc[header_idx])]
            
            # Identify columns dynamically (Code 2 style)
            idx_date = next((j for j, h in enumerate(headers) if any(x in h for x in ["date", "txn"])), 0)
            idx_desc = next((j for j, h in enumerate(headers) if any(x in h for x in ["description", "narration", "particular", "details"])), 1)
            # Strict Debit detection (Avoid Balance column)
            idx_debit = next((j for j, h in enumerate(headers) if any(x in h for x in ["debit", "withdraw", "dr", "out"]) and "balance" not in h), -1)

            if idx_debit == -1: idx_debit = len(headers) - 1

            current_row = None
            
            for i in range(header_idx + 1, len(df)):
                row = df.iloc[i]
                date_val = str(row[idx_date]).strip() if idx_date < len(row) else ""
                desc_val = str(row[idx_desc]).replace("\n", " ") if idx_desc < len(row) and row[idx_desc] else ""
                
                # Check for new transaction start using Date Pattern (Code 1 multiline logic)
                if re.search(r'\d{1,2}[\s\-\/]([A-Za-z]{3}|\d{1,2})', date_val):
                    if current_row and current_row["Amount"] > 0: all_data.append(current_row)
                    
                    amt = clean_amt(row[idx_debit]) if idx_debit < len(row) else 0.0
                    current_row = {"Date": date_val, "Description": desc_val, "Amount": amt}
                else:
                    # Append description for multiline rows (Fix for AU/SBI banks)
                    if current_row and desc_val:
                        current_row["Description"] += " " + desc_val
                        if current_row["Amount"] == 0 and idx_debit < len(row):
                            current_row["Amount"] = clean_amt(row[idx_debit])
            
            if current_row and current_row["Amount"] > 0: all_data.append(current_row)

    final_df = pd.DataFrame(all_data)
    if final_df.empty: return final_df
    
    # Cleaning: No Summary rows or Interests
    summary_kw = "TOTAL|INTEREST|BALANCE|SUMMARY|LIMIT|OPENING|CLOSING|BROUGHT|CARRIED"
    final_df = final_df[~final_df["Description"].str.upper().str.contains(summary_kw, na=False)]
    
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

        df = universal_hybrid_parser(path)
        os.unlink(path)

        if df.empty: return "❌ No spend transactions detected. Ensure it's a valid Debit statement."

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
    except Exception as e: return f"❌ Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
