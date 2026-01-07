import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ----------------- Bank Noise Remove ----------------
BANK_NOISE = ["upi","transfer","hdfc","sbin","icici","idfc","utr","payment","paid","via","yesb","axis","from","to","ref","upiint","upiintnet"]

# ---------------- CATEGORY ENGINE -------------------
def detect_category(text):
    raw = str(text).lower()
    for b in BANK_NOISE:
        raw = raw.replace(b, " ")
    raw = re.sub(r"[^a-zA-Z ]", "", raw).replace(" ", "")

    replace_map = {  
        "Shopping": ["flipkart","flpkart","flpkrt","flpkartpayment","flpkartpayment","flipkrt", "meesho", "me eesho", "m essho", "m e e s h o", "myntra", "ajio"],  
        "Food": ["swiggy","swiggylimited", "zomato","eternal","blinkit","b linkit"],  
        "Grocery": ["bigbasket","dealshare","deal share","de alshare", "kirana","mart","store", "jiomart"],  
        "Healthcare": ["medical","pharmacy","chemist"],  
        "Travel": ["uber","ola"],  
        "Bills": ["recharge","billdesk", "bill"]  
    }

    # Smart Check: Direct Category return
    for category, keywords in replace_map.items():
        for word in keywords:
            if word in raw:
                return category

    if "upi" in str(text).lower():
        return "Money Transfer"
    
    return "Others"

# ---------------- AMOUNT CLEANER -------------------
def clean_debit_amt(val):
    if not val or str(val).strip() == "" or str(val).strip() == "-":
        return 0.0
    # Scientific notation aur UPI ID se bachne ke liye filter
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # Agar number 10 digit se lamba hai toh wo ID hai, amount nahi
        if len(v.replace(".", "")) >= 10: return 0.0
        return num
    except:
        return 0.0

# ---------------- SMART PDF PARSER -------------------
def extract_au_bank_data(path):
    all_data = []
    
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table: continue
            
            df = pd.DataFrame(table)
            # AU Bank Headers usually: Date, Value Date, Description/Ref, Debit, Credit, Balance
            # [span_7](start_span)[span_8](start_span)We identify columns by keywords[span_7](end_span)[span_8](end_span)
            
            headers = [str(x).lower() for x in df.iloc[0]]
            
            # Find Column Indexes
            idx_date = next((i for i, h in enumerate(headers) if "date" in h), 0)
            idx_desc = next((i for i, h in enumerate(headers) if "description" in h or "narration" in h), 2)
            idx_debit = next((i for i, h in enumerate(headers) if "debit" in h), 3)
            
            current_row = None
            
            for i in range(1, len(df)):
                row = df.iloc[i]
                date_val = str(row[idx_date]).strip()
                
                # [span_9](start_span)[span_10](start_span)Agar naya Date mila toh nayi transaction start[span_9](end_span)[span_10](end_span)
                if re.search(r'\d{1,2}\s\w{3}\s\d{4}', date_val):
                    if current_row:
                        all_data.append(current_row)
                    
                    current_row = {
                        "Date": date_val,
                        "Description": str(row[idx_desc]).replace("\n", " "),
                        "Amount": clean_debit_amt(row[idx_debit])
                    }
                else:
                    # [span_11](start_span)Agar date nahi hai, toh ye purani transaction ka hi description hai[span_11](end_span)
                    if current_row and str(row[idx_desc]).strip():
                        current_row["Description"] += " " + str(row[idx_desc]).replace("\n", " ")
                        # Agar is line mein debit hai toh update karo
                        if not current_row["Amount"]:
                            current_row["Amount"] = clean_debit_amt(row[idx_debit])
            
            if current_row:
                all_data.append(current_row)

    # Filtering only Debits and cleaning
    final_df = pd.DataFrame(all_data)
    final_df = final_df[final_df["Amount"] > 0]
    # [span_12](start_span)Total/Interest line remove[span_12](end_span)
    final_df = final_df[~final_df["Description"].str.upper().str.contains("TOTAL|INTEREST|BALANCE|SUMMARY")]
    
    return final_df

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

        df = extract_au_bank_data(path)
        os.unlink(path)

        if df.empty:
            return "❌ No valid transactions detected."

        df["AI Category"] = df["Description"].apply(detect_category)
        
        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)
        cat_group = df.groupby("AI Category")["Amount"].sum().reset_index()
        
        return render_template(
            "dashboard.html",
            rows=df.rename(columns={"Date": "Transaction Date", "Description": "Description/Narration"}).to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=cat_group.loc[cat_group['Amount'].idxmax()]['AI Category'],
            category_summary=cat_group.values.tolist()
        )
    except Exception as e:
        return f"❌ Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
