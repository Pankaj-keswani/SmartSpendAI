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

    if "flipkart" in raw or "meesho" in raw or "ajio" in raw or "myntra" in raw:
        return "Shopping"
    if "swiggy" in raw or "zomato" in raw or "blinkit" in raw:
        return "Food"
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



        # -------- PAYTM PDF PARSER (FINAL) --------
        def parse_paytm():

            text = ""
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    text += (p.extract_text() or "") + "\n"

            lines = [l.strip() for l in text.split("\n") if l.strip()]

            data = []

            current_block = []
            current_date = None

            for L in lines:

                # detect transaction start
                if re.match(r"^\d{1,2}\s\w{3}", L):
                    # flush previous block
                    if current_block and current_date:
                        amt_lines = " ".join(current_block)
                        amt = re.findall(r"(?:Rs\.?|₹)\s?[\d,]+", amt_lines)

                        if amt:
                            v = amt[-1]
                            v = (
                                v.replace("Rs.","")
                                .replace("₹","")
                                .replace(",","")
                                .replace(" ","")
                                .replace("-","")
                            )
                            try:
                                v = float(v)
                                data.append([current_date, v])
                            except:
                                pass

                    # reset new block
                    current_date = L
                    current_block = []
                    continue

                if current_date:
                    current_block.append(L)

            # flush last block
            if current_block and current_date:
                amt_lines = " ".join(current_block)
                amt = re.findall(r"(?:Rs\.?|₹)\s?[\d,]+", amt_lines)

                if amt:
                    v = amt[-1]
                    v = (
                        v.replace("Rs.","")
                        .replace("₹","")
                        .replace(",","")
                        .replace(" ","")
                        .replace("-","")
                    )
                    try:
                        v = float(v)
                        data.append([current_date, v])
                    except:
                        pass


            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        def try_bank_text():
            text = ""
            with pdfplumber.open(path) as pdf:
                for p in pdf.pages:
                    text += (p.extract_text() or "")

            data=[]
            for L in text.split("\n"):

                amt = re.findall(r"[\d,]+\.\d\d", L)
                if not amt:
                    continue

                try:
                    v = float(amt[-1].replace(",",""))
                except:
                    continue

                data.append([L[:60], v])

            if len(data)==0:
                return None

            return pd.DataFrame(data, columns=["Narration","Amount"])



        # ------------- MAIN LOGIC -------------
        if not rows:

            df = parse_paytm()

            if df is None:
                df = try_bank_text()

            if df is None:
                return "No transactions detected — please upload full detailed statement."


        else:
            df = pd.DataFrame(rows)
            df.columns = df.iloc[0]
            df = df.iloc[1:].copy()

            if "Narration" not in df.columns:
                df["Narration"] = df.iloc[:,1]

            if "Amount" not in df.columns:
                df["Amount"] = df.iloc[:,-1]


        df["Amount"] = (
            df["Amount"].astype(str)
            .str.replace(",", "", regex=False)
        )

        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)

        df = df[df["Amount"] != 0]

        if len(df)==0:
            return "No valid transactions detected in this statement."

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
            "Narration":"Description/Narration"
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
    app.run(host="0.0.0.0", port=port, debug=False)