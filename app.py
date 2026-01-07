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


def detect_category(text):
    raw = str(text).lower()

    for b in BANK_NOISE:
        raw = raw.replace(b," ")

    raw = re.sub(r"[^a-zA-Z ]","",raw).replace(" ","")

    replace_map = {  
        "flipkart":["flipkart","flpkart","flpkrt","flpkrtpayment","flpkartpayment","flipkrt","meesho","me eesho","m essho","m e e s h o"],  
        "swiggy":["swiggy","swiggylimited","instamart"],  
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

    for cat,keys in replace_map.items():
        for k in keys:
            if k in raw:
                if cat in ["swiggy","zomato","blinkit"]:
                    return "Food"
                if cat in ["flipkart","myntra","ajio","jiomart","meesho"]:
                    return "Shopping"
                if cat in ["kirana","bigbasket","mart","store"]:
                    return "Grocery"
                if cat in ["medical","pharmacy"]:
                    return "Healthcare"
                if cat in ["uber","ola"]:
                    return "Travel"
                if cat=="recharge":
                    return "Bills"

    if "upi" in str(text).lower():
        return "Money Transfer"

    return "Others"



def clean_amt(v):
    v = re.sub(r"[^\d.]", "", str(v))

    try:
        num = float(v)
        if len(v.replace(".",""))>=10: return 0.0
        if num>9999999: return 0.0
        return num
    except:
        return 0.0



# ⭐ TEXT PARSER — FULL DESCRIPTION ⭐
def extract_data(path):

    tx = []

    with pdfplumber.open(path) as pdf:

        for page in pdf.pages:

            lines = page.extract_text().split("\n")

            current = None

            for line in lines:

                # date format like: 04 Jan 2026
                date_match = re.match(r"(\d{2}\s\w+\s\d{4})", line)

                amt_match = re.findall(r"\d+\.\d{2}", line)

                # -------- NEW TRANSACTION --------
                if date_match and amt_match:

                    if current:
                        tx.append(current)

                    current = {
                        "Date": date_match.group(1),
                        "Description": line,
                        "Amount": clean_amt(amt_match[0])
                    }

                # -------- CONTINUATION LINES --------
                else:
                    if current:
                        current["Description"] += " " + line


            if current:
                tx.append(current)


    df = pd.DataFrame(tx)

    df = df[df["Amount"]>0]

    df=df[~df["Description"].str.upper().str.contains("TOTAL|BALANCE|SUMMARY|INTEREST")]

    return df




@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        file=request.files["file"]

        with tempfile.NamedTemporaryFile(delete=False,suffix=".pdf") as tmp:
            file.save(tmp.name)
            path=tmp.name

        df=extract_data(path)

        os.unlink(path)

        df["AI Category"]=df["Description"].apply(detect_category)

        total=df["Amount"].sum()
        tx=len(df)

        cat=df.groupby("AI Category")["Amount"].sum().reset_index()
        top=cat.loc[cat["Amount"].idxmax()]["AI Category"]

        return render_template(
            "dashboard.html",
            rows=df.rename(columns={"Date":"Transaction Date","Description":"Description/Narration"}).to_dict("records"),
            total_spend=round(total,2),
            total_transactions=tx,
            top_category=top,
            category_summary=cat.values.tolist()
        )

    except Exception as e:
        return f"❌ Error: {str(e)}"



if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))