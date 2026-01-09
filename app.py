import os
import re
import tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

BANK_NOISE = [
    "upi","transfer","hdfc","sbin","icici","idfc",
    "utr","payment","paid","via","yesb","axis",
    "from","to","ref","upiint","upiintnet"
]

# ---------------- CATEGORY ENGINE ----------------
def detect_category(text):
    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b," ")

    raw = re.sub(r"[^a-zA-Z ]","",raw).replace(" ","")

    replace_map = {  
        "swiggy":["swiggy","swiggylimited","instamart"],
        "zomato":["zomato","zomatoltd"],
        "blinkit":["blinkit"],
        "dominos":["dominos","dominospizza"],
        "kfc":["kfc"],
        "pizza":["pizzahut"],
        "faasos":["faasos"],
        "behrouz":["behrouz"],
        "ovenstory":["ovenstory"],
        "freshmenu":["freshmenu"],
        "eatfit":["eatfit"],

        "flipkart":["flipkart","flpkart","flpkrt","flipkrt","meesho"],
        "amazon":["amazon","amzn"],
        "myntra":["myntra"],
        "ajio":["ajio"],
        "jiomart":["jiomart"],

        "bigbasket":["bigbasket"],
        "dmart":["dmart"],
        "reliancefresh":["reliancefresh"],
        "store":["mart","store"],

        "medical":["medical","pharmacy","chemist"],
        "uber":["uber"],
        "ola":["ola"],

        "recharge":["recharge","billdesk","bill"],
        "paytm":["paytm"],
        "phonepe":["phonepe"],
        "gpay":["gpay","googlepay"],
    }

    for cat,keys in replace_map.items():
        for k in keys:
            if k in raw:
                if cat in ["swiggy","zomato","blinkit","dominos","kfc","pizza","faasos","behrouz","ovenstory","freshmenu","eatfit"]:
                    return "Food"
                if cat in ["flipkart","amazon","myntra","ajio","jiomart"]:
                    return "Shopping"
                if cat in ["bigbasket","dmart","reliancefresh","store"]:
                    return "Grocery"
                if cat in ["medical"]:
                    return "Healthcare"
                if cat in ["uber","ola"]:
                    return "Travel"
                if cat in ["recharge","paytm","phonepe","gpay"]:
                    return "Bills"

    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"


def clean_amt(v):
    v = re.sub(r"[^\d.]", "", str(v))
    try:
        num = float(v)
        if len(v.replace(".","")) >= 10: return 0.0
        if num > 9999999: return 0.0
        return num
    except:
        return 0.0


# ---------------- SMART TEXT PARSER ----------------
def extract_data(path):

    transactions = []
    current = None

    IGNORE_LINES = [
        "auto generated",
        "does not require signature",
        "please inform",
        "call us",
        "customer care",
        "website",
        "email",
        "address",
        "page",
        "indusind",
        "branch",
        "rajasthan",
        "india",
        "toll free"
    ]

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for line in text.split("\n"):
                l = line.lower().strip()

                # ❌ ignore noise lines
                if any(x in l for x in IGNORE_LINES):
                    continue

                # detect new transaction line
                date_match = re.match(r"(\d{2}\s\w+\s\d{4})", line)
                amt_match = re.findall(r"\d+\.\d{2}", line)

                if date_match and amt_match:
                    if current:
                        transactions.append(current)

                    current = {
                        "Date": date_match.group(1),
                        "Description": line.strip(),
                        "Amount": clean_amt(amt_match[0])
                    }

                else:
                    # continuation only if it looks like txn text
                    if current and (
                        "upi/" in l or
                        "imps" in l or
                        "neft" in l or
                        "rtgs" in l or
                        "/" in l
                    ):
                        current["Description"] += " " + line.strip()

            if current:
                transactions.append(current)
                current = None

    df = pd.DataFrame(transactions)
    df = df[df["Amount"] > 0]
    return df


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

        df = extract_data(path)
        os.unlink(path)

        df["AI Category"] = df["Description"].apply(detect_category)

        total = df["Amount"].sum()
        tx = len(df)

        cat = df.groupby("AI Category")["Amount"].sum().reset_index()
        top = cat.loc[cat["Amount"].idxmax()]["AI Category"]

        return render_template(
            "dashboard.html",
            rows=df.rename(columns={
                "Date": "Transaction Date",
                "Description": "Description/Narration"
            }).to_dict("records"),
            total_spend=round(total, 2),
            total_transactions=tx,
            top_category=top,
            category_summary=cat.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))