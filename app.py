import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = ["upi","transfer","hdfc","sbin","icici","idfc","utr","payment","paid","via","yesb","axis","from","to","ref","upiint","upiintnet"]

# ---------------- CATEGORY ENGINE (Fixed & Working) -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","flpkrtpayment","flipkrt", "meesho", "myntra", "ajio", "nykaa", "amazon"],  
        "Food": ["swiggy","swiggylimited", "zomato","eternal","blinkit", "eatclub", "mcdonalds", "kfc"],  
        "Grocery": ["bigbasket","dealshare", "kirana","mart","store", "jiomart", "blinkit", "zepto"],  
        "Healthcare": ["medical","pharmacy","chemist", "hospital", "apollo"],  
        "Travel": ["uber","ola", "rapido", "irctc", "fuel", "petrol"],  
        "Bills": ["recharge","billdesk", "bill", "electricity", "jio", "airtel"]  
    }

    for category, keywords in replace_map.items():
        for word in keywords:
            if word in raw:
                return category

    if "upi" in str(text).lower(): return "Money Transfer"
    return "Others"

# ---------------- AMOUNT CLEANER (Scientific Notation Fix) -------------------
def clean_debit_amt(val):
    if not val or str(val).strip() in ["", "-", "None", "0"]: return 0.0
    # Sirf digits aur decimal point rakho
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # Filter: 10 digit se lamba number UPI ID hota hai, Amount nahi
        if len(v.replace(".", "")) >= 10: return 0.0
        return num
    except: return 0.0

# ---------------- UNIVERSAL PDF PARSER -------------------
def universal_pdf_reader(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or len(table) < 2: 
                # Fallback: Agar table nahi hai toh text se line-by-line scan karo
                text = page.extract_text()
                if text:
                    for line in text.split('\n'):
                        amt_match = re.findall(r'(\d{1,7}\.\d{2})', line)
                        if amt_match:
                            all_data.append({"Date": "N/A", "Description": line[:100], "Amount": clean_debit_amt(amt_match[-1])})
                continue
            
            df = pd.DataFrame(table)
            # Find Headers
            headers = [str(x).lower().strip() if x else "" for x in df.iloc[0]]
            
            # Keywords to find right columns
            idx_date = next((i for i, h in enumerate(headers) if any(x in h for x in ["date", "txn", "val"])), 0)
            idx_desc = next((i for i, h in enumerate(headers) if any(x in h for x in ["description", "narration", "particulars", "details"])), 1)
            idx_debit = next((i for i, h in enumerate(headers) if any(x in h for x in ["debit", "withdraw", "out", "amt", "paid"])), -1)
            
            # If Debit column not found, try finding any column with numeric values
            if idx_debit == -1:
                idx_debit = len(df.columns) - 1 # Fallback to last column

            current_row = None
            
            for i in range(1, len(df)):
                row = df.iloc[i]
                date_val = str(row[idx_date]).strip()
                desc_val = str(row[idx_desc]).replace("\n", " ") if row[idx_desc] else ""
                debit_val = clean_debit_amt(row[idx_debit])

                # Check if this is a new transaction (Starts with a date like 01 Dec or 01-12)
                if re.search(r'\d{1,2}[\s\-\/]([A-Za-z]{3}|\d{1,2})', date_val):
                    if current_row: all_data.append(current_row)
                    
                    current_row = {
                        "Date": date_val,
                        "Description": desc_val,
                        "Amount": debit_val
                    }
                else:
                    # Append description if row continues (Handling AU and other banks multiline)
                    if current_row and desc_val:
                        current_row["Description"] += " " + desc_val
                        if not current_row["Amount"]: current_row["Amount"] = debit_val
            
            if current_row: all_data.append(current_row)

    final_df = pd.DataFrame(all_data)
    if final_df.empty: return final_df
    
    # Cleaning: Only Debit > 0 and Remove Totals
    final_df = final_df[final_df["Amount"] > 0]
    final_df = final_df[~final_df["Description"].str.upper().str.contains("TOTAL|INTEREST|BALANCE|SUMMARY|LIMIT|OPENING|CLOSING")]
    
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

        if df.empty:
            return "❌ No valid transactions detected. Please ensure the PDF is a clear bank statement."

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
