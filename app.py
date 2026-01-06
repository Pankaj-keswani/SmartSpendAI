import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ---------------- SMART CATEGORY ENGINE -------------------
def detect_category(text):
    raw = str(text).lower()
    categories = {
        "Food": ["swiggy", "zomato", "eatclub", "restaurant", "hotel", "starbucks", "dominos", "kfc", "mcdonald"],
        "Shopping": ["flipkart", "amazon", "myntra", "ajio", "jiomart", "meesho", "nykaa", "shopee"],
        "Grocery": ["blinkit", "bigbasket", "zepto", "instamart", "kirana", "mart", "reliance", "dmart"],
        "Travel": ["uber", "ola", "rapido", "irctc", "makemytrip", "indigo", "fuel", "petrol", "shell"],
        "Bills": ["recharge", "airtel", "jio", "vi", "electricity", "bill", "insurance", "rent", "lic"],
        "Healthcare": ["apollo", "pharmacy", "medical", "hospital", "pharmeasy", "pathology"],
        "Money Transfer": ["upi", "transfer", "neft", "rtgs", "sent to", "paid to", "funds"]
    }

    for cat, keywords in categories.items():
        if any(kw in raw for kw in keywords):
            return cat
    return "Others"

# ---------------- ROBUST PDF PARSER -------------------
def extract_data_from_pdf(path):
    all_rows = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # 1. Try to get tables
            table = page.extract_table()
            if table:
                # Remove empty rows and None values
                cleaned_table = [[str(cell) if cell else "" for cell in row] for row in table]
                all_rows.extend(cleaned_table)
            else:
                # 2. Fallback to Text if no table found
                text = page.extract_text()
                if text:
                    for line in text.split('\n'):
                        # Look for lines that have an amount (e.g., 500.00)
                        if re.search(r'\d+\.\d{2}', line):
                            all_rows.append([line])

    if not all_rows:
        return None
    
    return process_raw_data(all_rows)

def process_raw_data(rows):
    df = pd.DataFrame(rows)
    
    # Header dhundne ki koshish (usually 1st or 2nd row)
    if len(df) > 1:
        df.columns = df.iloc[0]
        df = df[1:].reset_index(drop=True)

    # Keywords to find columns
    keywords = {
        'date': ['date', 'txn', 'time', 'val'],
        'desc': ['description', 'narration', 'particulars', 'details', 'remarks', 'info', 'transaction details'],
        'amt': ['amount', 'debit', 'withdrawal', 'outflow', 'dr', 'credit', 'deposit', 'inflow', 'cr', 'value']
    }

    def find_best_col(key_list):
        for col in df.columns:
            if any(k in str(col).lower() for k in key_list):
                return col
        return None

    date_col = find_best_col(keywords['date'])
    desc_col = find_best_col(keywords['desc'])
    
    # Amount ke liye multiple checks (Debit/Credit columns handle karne ke liye)
    amt_cols = [col for col in df.columns if any(k in str(col).lower() for k in keywords['amt'])]

    final_data = []
    for _, row in df.iterrows():
        try:
            # 1. Extract Description
            description = str(row[desc_col]) if desc_col else str(row.values[0])
            if len(description) < 3: continue # Skip junk

            # 2. Extract Amount (Pick the first non-zero number from amount-related columns)
            val = 0
            for col in amt_cols:
                raw_val = re.sub(r'[^\d.]', '', str(row[col]))
                if raw_val and float(raw_val) > 0:
                    val = float(raw_val)
                    break
            
            if val == 0: continue # Skip if no amount found

            # 3. Date
            date = str(row[date_col]) if date_col else "N/A"

            final_data.append({
                "Transaction Date": date,
                "Description/Narration": description[:100], # Limit length
                "Amount": val,
                "AI Category": detect_category(description)
            })
        except:
            continue

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
            return "❌ Could not detect transactions. Format may be unsupported. Try another statement."

        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)
        cat_summary = df.groupby("AI Category")["Amount"].sum().reset_index().values.tolist()
        top_cat = df.groupby("AI Category")["Amount"].sum().idxmax() if not df.empty else "N/A"

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
