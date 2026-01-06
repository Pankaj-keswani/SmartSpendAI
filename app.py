import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request
from bank_formats import detect_bank, BANK_FORMATS

app = Flask(__name__)

# ---------------- CATEGORY ENGINE -------------------
def detect_category(text):
    raw = str(text).lower()
    
    # Mapping for smarter detection
    categories = {
        "Food": ["swiggy", "zomato", "eatclub", "restaurant", "hotel", "starbucks", "dominos"],
        "Shopping": ["flipkart", "amazon", "myntra", "ajio", "jiomart", "meesho", "nykaa"],
        "Grocery": ["blinkit", "bigbasket", "zepto", "instamart", "kirana", "mart", "reliance retail"],
        "Travel": ["uber", "ola", "rapido", "irctc", "makemytrip", "indigo", "fuel", "petrol"],
        "Bills": ["recharge", "airtel", "jio", "vi", "electricity", "water bill", "insurance", "rent"],
        "Healthcare": ["apollo", "pharmacy", "medical", "hospital", "pharmeasy"],
        "Money Transfer": ["upi", "transfer", "neft", "rtgs", "sent to", "paid to"]
    }

    for cat, keywords in categories.items():
        if any(kw in raw for kw in keywords):
            return cat
    return "Others"

# ---------------- UNIVERSAL PARSER -------------------
def extract_data_from_pdf(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # 1. Try Extracting Tables first
            tables = page.extract_tables()
            for table in tables:
                if len(table) > 1:
                    df_tmp = pd.DataFrame(table)
                    all_data.append(df_tmp)
            
            # 2. If no tables, fallback to Text Line processing
            if not tables:
                text = page.extract_text()
                if text:
                    lines = text.split('\n')
                    for line in lines:
                        # Pattern to find amounts like 500.00 or 1,200.50
                        amt_match = re.findall(r'(\d{1,3}(?:,\d{2,3})*(?:\.\d{2})?)', line)
                        if amt_match:
                            all_data.append(pd.DataFrame([[line, amt_match[-1]]]))

    if not all_data:
        return None
    
    df = pd.concat(all_data, ignore_index=True)
    return clean_and_format_df(df)

def clean_and_format_df(df):
    # Cleaning column names
    df.columns = [str(c).lower().strip() for c in df.iloc[0]]
    df = df[1:].copy()

    # Identify essential columns using Fuzzy Matching
    def find_col(keywords):
        for col in df.columns:
            if any(kw in str(col) for kw in keywords):
                return col
        return None

    desc_col = find_col(['description', 'narration', 'particulars', 'details', 'remarks'])
    date_col = find_col(['date', 'txn date', 'transaction date'])
    
    # Amount logic: Check for Debit/Credit or a single Amount column
    debit = find_col(['debit', 'withdrawal', 'outflow', 'dr'])
    credit = find_col(['credit', 'deposit', 'inflow', 'cr'])
    amount = find_col(['amount', 'balance', 'value'])

    # Final DF Structure
    new_data = []
    for index, row in df.iterrows():
        try:
            val = 0
            # Logic: If Debit has value, use it. Else use Credit.
            if debit and pd.notnull(row[debit]) and str(row[debit]).strip():
                val = str(row[debit])
            elif credit and pd.notnull(row[credit]) and str(row[credit]).strip():
                val = str(row[credit])
            elif amount:
                val = str(row[amount])
            
            # Clean non-numeric characters from amount
            clean_val = re.sub(r'[^\d.]', '', str(val))
            if not clean_val: continue
            
            amt_float = float(clean_val)
            if amt_float == 0: continue

            narr = str(row[desc_col]) if desc_col else "Unknown Transaction"
            date = str(row[date_col]) if date_col else "N/A"

            new_data.append({
                "Transaction Date": date,
                "Description/Narration": narr,
                "Amount": amt_float,
                "AI Category": detect_category(narr)
            })
        except:
            continue

    return pd.DataFrame(new_data)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        if 'file' not in request.files:
            return "No file uploaded"
        
        file = request.files["file"]
        if file.filename == '':
            return "No file selected"

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file.save(tmp.name)
            path = tmp.name

        df = extract_data_from_pdf(path)
        os.unlink(path) # Delete temp file

        if df is None or df.empty:
            return "❌ Could not read transactions. Please ensure the PDF is not password protected and has a clear list of transactions."

        # Analysis
        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)
        cat_group = df.groupby("AI Category")["Amount"].sum().reset_index()
        top_category = cat_group.loc[cat_group['Amount'].idxmax()]['AI Category'] if not cat_group.empty else "N/A"

        return render_template(
            "dashboard.html",
            rows=df.to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=top_category,
            category_summary=cat_group.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
