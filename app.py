import os
import re
import tempfile
from flask import Flask, render_template, request
import pandas as pd
import pdfplumber

app = Flask(__name__)

BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]


def detect_category(text):

    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b, " ")

    raw = re.sub(r"[^a-zA-Z ]", "", raw)
    raw = raw.replace(" ", "")

    replace_map = {
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt","meesho"],
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
            if w in raw:
                raw = key

    if "swiggy" in raw or "zomato" in raw:
        return "Food"
    if "flipkart" in raw or "myntra" in raw or "jiomart" in raw or "ajio" in raw:
        return "Shopping"
    if "kirana" in raw or "mart" in raw or "store" in raw or "bigbasket" in raw:
        return "Grocery"
    if "medical" in raw or "pharmacy" in raw:
        return "Healthcare"
    if "uber" in raw or "ola" in raw:
        return "Travel"
    if "recharge" in raw or "bill" in raw:
        return "Bills"
    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"



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
                if table:
                    rows.extend(table)

        # -------- PAYTM SUPER PARSER ----------
        def parse_paytm():
            text = ""
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    text += (p.extract_text() or "") + "\n"

            lines = [l.strip() for l in text.split("\n") if l.strip()]

            data = []
            pending_date = None
            pending_desc = None

            for L in lines:

                # detect date
                if re.match(r"^\d{1,2}\s\w{3}", L):
                    pending_date = L
                    pending_desc = None
                    continue

                # detect description
                if pending_date and not pending_desc:
                    pending_desc = L
                    continue

                # detect amount third line
                amt = re.findall(r"-?\s?(?:Rs\.?|₹)\s?[\d,]+", L)

                if amt and pending_date and pending_desc:
                    v = amt[-1]
                    v = v.replace("Rs.","").replace("₹","").replace(",","").replace(" ","")

                    neg = "-" in v
                    v = v.replace("-","")

                    try:
                        v = float(v)
                        if neg:
                            v = abs(v)
                    except:
                        continue

                    full = f"{pending_date} — {pending_desc}"

                    data.append([full[:80], v])

                    pending_date = None
                    pending_desc = None

            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        # -------- SBI / HDFC fallback ----------
        def try_bank_line_mode():
            text_rows = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text_rows.extend((page.extract_text() or "").split("\n"))

            data=[]
            for L in text_rows:
                amt = re.findall(r"[\d,]+\.\d\d", L)
                if not amt:
                    continue

                amt = amt[-1].replace(",","")

                try:
                    amt = float(amt)
                except:
                    continue

                data.append([L[:60], amt])

            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        # -------- OCR fallback ----------
        def ocr_reader():
            import fitz
            import pytesseract
            from PIL import Image

            doc = fitz.open(path)
            data = []

            for page in doc:
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)

                for L in text.split("\n"):
                    amt = re.findall(r"-?\s?(?:Rs\.?|₹)\s?[\d,]+", L)
                    if not amt:
                        continue

                    value = amt[-1].replace("Rs.","").replace("₹","").replace(",","").strip()

                    try:
                        value = abs(float(value))
                    except:
                        continue

                    data.append([L[:60], value])

            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        # --- IF NO TABLE ---
        if not rows:

            df = parse_paytm()

            if df is None:
                df = try_bank_line_mode()

            if df is None:
                df = ocr_reader()

            if df is None:
                return "No transactions detected — please upload full detailed statement."

            narr_col = "Narration"
            date_col = None

        else:

            df = pd.DataFrame(rows)

            df.columns = df.iloc[0]
            df = df.iloc[1:].copy()
            df.reset_index(drop=True, inplace=True)

            def find(col, words):
                for c in col:
                    text = str(c).lower()
                    for w in words:
                        if w in text:
                            return c
                return None

            date_col = find(df.columns, ["date","txn","posting","transaction"])
            narr_col = find(df.columns, ["narr","details","description","particular","remarks","info"])
            debit_col = find(df.columns, ["debit","withdraw","dr","debit amt","outflow"])
            credit_col = find(df.columns, ["credit","deposit","cr","credit amt","inflow"])

            if not narr_col:
                narr_col = df.columns[1]

            if debit_col and credit_col:
                df["Amount"] = df[debit_col].fillna(df[credit_col])
            elif debit_col:
                df["Amount"] = df[debit_col]
            elif credit_col:
                df["Amount"] = df[credit_col]
            else:
                df["Amount"] = df.iloc[:,-1]


        df["Amount"] = (
            df["Amount"]
            .astype(str)
            .str.replace(",", "", regex=False)
        )

        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

        df = df[df["Amount"] != 0]

        df["AI Category"] = df["Narration"].apply(detect_category)

        total_spend = round(df["Amount"].sum(), 2)
        total_transactions = len(df)

        cat_group = df.groupby("AI Category")["Amount"].sum()

        if len(cat_group)==0:
            top_category = "Not Available"
            cat_summary = []
        else:
            top_category = cat_group.idxmax()
            cat_summary = cat_group.reset_index().values

        rows = df.rename(columns={
            date_col if date_col else "Narration": "Transaction Date",
            "Narration": "Description/Narration"
        })

        return render_template(
            "dashboard.html",
            rows=rows.to_dict("records"),
            total_spend=total_spend,
            total_transactions=total_transactions,
            top_category=top_category,
            category_summary=cat_summary
        )

    except Exception as e:
        return f"❌ Error processing PDF:<br><br>{str(e)}"



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )