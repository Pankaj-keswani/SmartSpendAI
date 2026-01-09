import os, re, tempfile
import pandas as pd
import pdfplumber
from flask import Flask, render_template, request

app = Flask(__name__)

# ---------------- CLEAN DESCRIPTION ----------------
def build_description(raw):
    raw = raw.upper()

    # UPI case
    if "UPI/" in raw:
        parts = raw.split("/")
        for p in parts:
            if "@" in p:
                return f"UPI Payment ({p})"
        return "UPI Payment"

    # IMPS / NEFT
    if "IMPS" in raw:
        return "IMPS Transfer"
    if "NEFT" in raw:
        return "NEFT Transfer"

    # Kirana / Medical
    if "KIRANA" in raw:
        return "Kirana Store Purchase"
    if "MEDICAL" in raw:
        return "Medical Expense"

    return raw[:60]  # fallback safe


# ---------------- CATEGORY ENGINE ----------------
def detect_category(desc):
    d = desc.lower()
    if any(x in d for x in ["swiggy","zomato","blinkit","instamart"]):
        return "Food"
    if any(x in d for x in ["amazon","flipkart","myntra","ajio"]):
        return "Shopping"
    if any(x in d for x in ["kirana","store","mart"]):
        return "Grocery"
    if any(x in d for x in ["medical","pharmacy","hospital"]):
        return "Healthcare"
    if any(x in d for x in ["uber","ola","rapido"]):
        return "Travel"
    if "upi" in d:
        return "Money Transfer"
    return "Others"


def clean_amt(v):
    try:
        return float(re.sub(r"[^\d.]", "", v))
    except:
        return 0.0


# ---------------- PDF PARSER ----------------
def extract_data(path):
    rows = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            for line in text.split("\n"):
                if not re.match(r"\d{2}\s\w+\s\d{4}", line):
                    continue

                amts = re.findall(r"\d+\.\d{2}", line)
                if len(amts) < 2:
                    continue

                debit = clean_amt(amts[-2])
                if debit <= 0:
                    continue

                date = re.match(r"(\d{2}\s\w+\s\d{4})", line).group(1)
                desc = build_description(line)

                rows.append({
                    "Date": date,
                    "Description": desc,
                    "Amount": debit
                })

    return pd.DataFrame(rows)


# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
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
        total_spend=round(total,2),
        total_transactions=tx,
        top_category=top,
        category_summary=cat.values.tolist()
    )


if __name__ == "__main__":
    app.run(port=5000)