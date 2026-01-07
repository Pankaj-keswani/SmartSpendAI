import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = ["upi","transfer","hdfc","sbin","icici","idfc","utr","payment","paid","via","yesb","axis","from","to","ref","upiint","upiintnet"]

# ---------------- CATEGORY ENGINE (Fixed Logic) -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","flpkrtpayment","flipkrt", "meesho", "myntra", "ajio", "amazon"],  
        "Food": ["swiggy","swiggylimited", "zomato","eternal","blinkit", "zomatoonline"],  
        "Grocery": ["bigbasket","dealshare", "kirana","mart","store", "jiomart", "blinkit", "zepto"],  
        "Healthcare": ["medical","pharmacy","chemist", "hospital"],  
        "Travel": ["uber","ola", "rapido", "irctc", "petrol", "fuel"],  
        "Bills": ["recharge","billdesk", "bill", "electricity", "jio", "airtel"]  
    }

    for category, keywords in replace_map.items():
        for word in keywords:
            if word in raw: return category

    if "upi" in str(text).lower(): return "Money Transfer"
    return "Others"

# ---------------- AMOUNT CLEANER (Strict ID Filter) -------------------
def clean_debit_amt(val):
    if not val or str(val).strip() in ["", "-", "None", "0", "0.00"]: return 0.0
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # ⛔ ID Filter: Agar 10 digit se bada hai toh wo Amount nahi, Ref ID hai
        if len(v.replace(".", "")) >= 10 or num > 500000: return 0.0
        return num
    except: return 0.0

# ---------------- ROBUST MULTI-BANK PARSER -------------------
def universal_pdf_reader(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table: continue
            
            df = pd.DataFrame(table)
            # Find the header row (usually contains 'date' and 'description')
            header_idx = -1
            for i, row in df.iterrows():
                row_str = " ".join([str(x).lower() for x in row if x])
                if "date" in row_str and ("desc" in row_str or "particular" in row_str):
                    header_idx = i
                    break
            
            if header_idx == -1: continue # Page skip if no header found
            
            headers = [str(x).lower().strip() if x else f"col_{j}" for j, x in enumerate(df.iloc[header_idx])]
            
            # 🔥 Strict Column Identification
            idx_date = next((j for j, h in enumerate(headers) if any(x in h for x in ["date", "txn"])), 0)
            idx_desc = next((j for j, h in enumerate(headers) if any(x in h for x in ["description", "narration", "particular", "details"])), 1)
            idx_debit = next((j for j, h in enumerate(headers) if any(x in h for x in ["debit", "withdraw", "out", "dr"]) and "balance" not in h), -1)
            
            # Agar Debit specific nahi mila, toh first numeric column lo (excluding Balance)
            if idx_debit == -1:
                idx_debit = next((j for j, h in enumerate(headers) if "amt" in h and "balance" not in h), -1)

            current_row = None
            
            for i in range(header_idx + 1, len(df)):
                row = df.iloc[i]
                date_val = str(row[idx_date]).strip() if idx_date < len(row) else ""
                desc_val = str(row[idx_desc]).replace("\n", " ") if idx_desc < len(row) and row[idx_desc] else ""
                
                # Check for new transaction start (Date pattern)
                if re.search(r'\d{1,2}[\s\-\/]([A-Za-z]{3}|\d{1,2})', date_val):
                    if current_row and current_row["Amount"] > 0: 
                        all_data.append(current_row)
                    
                    amt = clean_debit_amt(row[idx_debit]) if idx_debit != -1 else 0.0
                    current_row = {"Date": date_val, "Description": desc_val, "Amount": amt}
                else:
                    # Multiline description support (AU Bank style)
                    if current_row and desc_val:
                        current_row["Description"] += " " + desc_val
                        if current_row["Amount"] == 0 and idx_debit != -1:
                            current_row["Amount"] = clean_debit_amt(row[idx_debit])
            
            if current_row and current_row["Amount"] > 0: all_data.append(current_row)

    final_df = pd.DataFrame(all_data)
    if final_df.empty: return final_df
    
    # Final Cleaning: No Summary/Total Rows
    summary_kw = "TOTAL|INTEREST|BALANCE|SUMMARY|LIMIT|OPENING|CLOSING|CARRIED|BROUGHT"
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

        df = universal_pdf_reader(path)
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
    except Exception as e: return f"❌ System Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
