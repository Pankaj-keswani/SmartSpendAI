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
            if w in raw: raw = key  

    if "swiggy" in raw or "zomato" in raw: return "Food"  
    if "flipkart" in raw or "myntra" in raw or "jiomart" in raw or "ajio" in raw: return "Shopping"  
    if "kirana" in raw or "mart" in raw or "store" in raw or "bigbasket" in raw: return "Grocery"  
    if "medical" in raw or "pharmacy" in raw: return "Healthcare"  
    if "uber" in raw or "ola" in raw: return "Travel"  
    if "recharge" in raw or "bill" in raw: return "Bills"  
    if "upi" in str(text).lower(): return "Money Transfer"  

    return "Others"  

# ⭐ NEW: Strong Amount Cleaner to ignore Long IDs ⭐
def clean_amt(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    # Sirf digits aur decimal point rakho
    v = re.sub(r'[^\d.]', '', str(val))
    try:
        num = float(v)
        # Filter: Agar number 10 digit se bada hai toh wo Transaction ID hai, Amount nahi
        if num > 9999999: return 0.0 
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

        # ⭐ 1. PAYTM PARSER (Fixed Regex) ⭐
        def parse_paytm_pdf():  
            text = ""  
            with pdfplumber.open(path) as pdf:  
                for p in pdf.pages: text += (p.extract_text() or "") + "\n"  
            lines = text.split("\n")  
            data = []  
            current_desc = ""  
            for L in lines:  
                if re.search(r"\d{1,2}\s\w{3}", L): current_desc = L  
                # Regex modified to only pick small numbers (max 7 digits before decimal)
                amt = re.findall(r"(?:Rs\.?|₹)\s?(\d{1,7}(?:\.\d{2})?)", L)  
                if amt:  
                    val = clean_amt(amt[-1])
                    if val > 0: data.append([current_desc[:60], val])  
            return pd.DataFrame(data, columns=["Narration","Amount"]) if data else None  

        # ⭐ 2. SBI / ICICI LINE MODE (Fixed Regex) ⭐
        def try_bank_line_mode():  
            text_rows = []  
            with pdfplumber.open(path) as pdf:  
                for page in pdf.pages: text_rows.extend((page.extract_text() or "").split("\n"))  
            data=[]  
            for L in text_rows:  
                # Sirf wo numbers pakdo jo decimal ke saath hain aur chhote hain
                amt = re.findall(r"(\d{1,7}\.\d\d)", L)  
                if amt:  
                    val = clean_amt(amt[-1])
                    if val > 0: data.append([L[:50], val])  
            return pd.DataFrame(data, columns=["Narration","Amount"]) if data else None  

        # ⭐ 3. OCR READER (Fixed) ⭐
        def ocr_reader():  
            import fitz, pytesseract  
            from PIL import Image  
            doc = fitz.open(path)  
            data = []  
            for page in doc:  
                pix = page.get_pixmap()  
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)  
                text = pytesseract.image_to_string(img)  
                for L in text.split("\n"):  
                    amt = re.findall(r"(?:Rs\.?|₹)\s?(\d{1,7}(?:\.\d{2})?)", L)  
                    if amt:  
                        val = clean_amt(amt[-1])
                        if val > 0: data.append([L[:60], val])  
            return pd.DataFrame(data, columns=["Narration","Amount"]) if data else None  

        if not rows:  
            df = parse_paytm_pdf()  
            if df is None: df = try_bank_line_mode()  
            if df is None: df = ocr_reader()  
            if df is None: return "No transactions detected."  
            narr_col, date_col = "Narration", None  
        else:  
            df = pd.DataFrame(rows)  
            df.columns = df.iloc[0]  
            df = df.iloc[1:].copy()  
            
            def find(col, words):  
                for c in col:  
                    if any(w in str(c).lower() for w in words): return c  
                return None  

            date_col = find(df.columns, ["date","txn","posting"])  
            narr_col = find(df.columns, ["narr","details","description","particular","remarks"])  
            debit_col = find(df.columns, ["debit","withdraw","dr"])  
            credit_col = find(df.columns, ["credit","deposit","cr"])  

            if not narr_col: narr_col = df.columns[1]  

            if debit_col and credit_col:
                df["Amount"] = df.apply(lambda r: clean_amt(r[debit_col]) if clean_amt(r[debit_col]) > 0 else clean_amt(r[credit_col]), axis=1)
            elif debit_col:
                df["Amount"] = df[debit_col].apply(clean_amt)
            else:
                df["Amount"] = df.iloc[:,-1].apply(clean_amt)

        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)  
        df = df[df["Amount"] > 0]  
        df = df[~df[narr_col].astype(str).str.upper().str.contains("TOTAL|INTEREST|BALANCE|LIMIT", na=False)]  

        if df.empty: return "No valid transactions detected."  

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
