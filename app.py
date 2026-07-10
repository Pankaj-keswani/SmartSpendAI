import os
import re
import glob
import uuid
import pickle
import tempfile
from io import BytesIO

import numpy as np
import pandas as pd
from flask import (
    Flask, render_template, request, jsonify, send_file, session
)

from parsers import (
    parse_statement, PasswordRequired, WrongPassword,
    UnsupportedFormat, ParseError,
)

# ──────────────────────────────────────────────────────────
# App Setup
# ──────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "smartspend-secret-key-change-me")

os.makedirs("uploads", exist_ok=True)

# Module-level store for last analyzed DataFrame (used by /export-csv)
_last_df = None

# ──────────────────────────────────────────────────────────
# ML Model – loaded once at module level
# ──────────────────────────────────────────────────────────
_ml_model = None
try:
    with open("model/expense_model.pkl", "rb") as _f:
        _ml_model = pickle.load(_f)
except Exception:
    _ml_model = None

# ──────────────────────────────────────────────────────────
# Bank Noise Words (used in cleaning)
# ──────────────────────────────────────────────────────────
BANK_NOISE = [
    "upi", "neft", "rtgs", "imps", "transfer", "payment", "paid", "via",
    "from", "to", "ref", "utr", "upiint", "upiintnet",
    "hdfc", "hdfcbank", "sbin", "icici", "icicibank", "idfc", "idfcbank",
    "axis", "axisbank", "yesb", "yesbank", "kotak", "kotakbank",
    "bob", "bankofbaroda", "pnb", "punjabnationalbank", "canara",
    "unionbank", "indianbank", "bankof", "bank", "ltd", "limited",
    "pvtltd", "pvt", "private",
]

# ──────────────────────────────────────────────────────────
# Layer 1 – Expanded Keyword Dictionary
# ──────────────────────────────────────────────────────────
CATEGORY_KEYWORDS = {
    "Shopping": [
        "flipkart", "amazon", "myntra", "ajio", "meesho", "nykaa",
        "tatacliq", "snapdeal", "shopclues", "firstcry", "limeroad",
        "bewakoof", "urbanic", "shein", "zara", "hm", "uniqlo",
        "decathlon", "croma", "reliance digital", "vijay sales",
        "shoppers stop", "lifestyle", "pantaloons", "westside",
        "central", "max", "fbb", "dmart", "vishal mega mart",
        "reliance trends", "pepperfry", "urbanladder", "ikea",
        "hometown", "fabindia", "sabyasachi", "tanishq", "kalyan",
        "malabar gold", "bluestone", "caratlane", "titan", "fastrack",
        "fossil", "boat", "noise", "crossword", "landmark", "archies",
    ],
    "Food": [
        "swiggy", "zomato", "blinkit", "dominos", "pizzahut", "kfc",
        "mcdonalds", "burgerking", "faasos", "ovenstory", "behrouz",
        "eatfit", "freshmenu", "box8", "rebel foods", "wow momo",
        "subway", "starbucks", "ccd", "barista", "chaayos",
        "chai point", "haldiram", "bikanervala", "sagar ratna",
        "saravana bhavan", "restaurant", "food court", "cafe", "dhaba",
        "tiffin", "canteen", "mess", "bakery", "eat", "dine",
        "kitchen", "biryani", "pizza", "burger", "chicken", "thali",
    ],
    "Grocery": [
        "bigbasket", "bbnow", "jiomart", "zepto", "blinkit", "dunzo",
        "grofers", "swiggy instamart", "dmart", "reliance fresh",
        "more supermarket", "spar", "star bazaar", "nature basket",
        "fresh to home", "licious", "country delight", "milkbasket",
        "amul", "mother dairy", "kirana", "general store", "supermarket",
        "grocery", "vegetable", "fruit", "provision", "ration",
    ],
    "Healthcare": [
        "apollo", "practo", "1mg", "netmeds", "medplus", "pharmeasy",
        "tata health", "manipal", "fortis", "max hospital", "aiims",
        "medanta", "narayana health", "hospital", "clinic", "diagnostic",
        "pathology", "lab", "dental", "doctor", "physician", "chemist",
        "pharmacy", "medical", "health", "ayurvedic", "homeopathic",
    ],
    "Travel": [
        "uber", "ola", "rapido", "irctc", "makemytrip", "goibibo",
        "ixigo", "yatra", "cleartrip", "easemytrip", "air india",
        "indigo", "spicejet", "vistara", "akasa", "emirates", "hotel",
        "oyo", "treebo", "fabhotel", "zostel", "metro", "bus",
        "railway", "flight", "cab", "taxi", "auto", "rickshaw",
        "toll", "parking", "petrol pump",
    ],
    "Fuel": [
        "hpcl", "bpcl", "iocl", "indian oil", "hindustan petroleum",
        "bharat petroleum", "hp petrol", "shell", "fuel station",
        "petrol", "diesel", "cng", "ev charging",
    ],
    "Bills": [
        "airtel", "jio", "vodafone", "vi", "bsnl", "mtnl",
        "tata play", "dish tv", "d2h", "sun direct", "electricity",
        "bescom", "tata power", "adani electricity", "water bill",
        "gas bill", "piped gas", "broadband", "act fibernet", "hathway",
        "you broadband", "recharge", "bill payment", "billdesk",
        "payu", "utility", "postpaid", "prepaid", "dth",
    ],
    "Entertainment": [
        "netflix", "spotify", "amazon prime", "hotstar", "disney plus",
        "zee5", "sonyliv", "jiocinema", "youtube premium", "apple music",
        "wynk", "gaana", "audible", "kindle", "pvr", "inox",
        "cinepolis", "bookmyshow", "event", "concert", "amusement",
        "gaming", "steam", "playstation", "xbox", "dream11", "mpl",
    ],
    "Education": [
        "byjus", "unacademy", "udemy", "coursera", "upgrad", "vedantu",
        "simplilearn", "toppr", "doubtnut", "physics wallah", "allen",
        "aakash", "fiitjee", "school fees", "college fees", "university",
        "tuition", "coaching", "academy", "institute", "training",
        "certification", "exam", "books", "stationery",
    ],
    "Finance": [
        "emi", "loan", "insurance", "lic", "policybazaar",
        "bajaj finserv", "hdfc life", "sbi life", "icici lombard",
        "max life", "tata aia", "premium", "policy", "nach", "ecs",
        "mandate", "auto debit",
    ],
    "Rent": [
        "rent", "house rent", "room rent", "flat rent", "pg rent",
        "hostel", "accommodation", "lease", "landlord", "property",
    ],
    "Salary": [
        "salary credited", "sal cr", "wages", "stipend", "freelance",
        "consulting fee", "payroll",
    ],
    "Investment": [
        "zerodha", "groww", "upstox", "angel one", "motilal oswal",
        "icici direct", "sip", "mutual fund", "mf purchase", "stock",
        "share", "trading", "demat", "nps", "ppf", "fixed deposit",
        "fd", "rd",
    ],
    "ATM": [
        "atm", "cash withdrawal", "atm-cw", "atm withdrawal",
        "self withdrawal", "cash w/d",
    ],
    "Transfer": [
        "neft", "rtgs", "imps", "upi", "fund transfer",
        "money transfer", "transfer to", "transfer from", "sent to",
        "received from", "credited by", "p2p",
    ],
}

# Regex-based keyword patterns for Shopping (POS matches)
_SHOPPING_PATTERNS = [
    re.compile(r"pos.*mall", re.IGNORECASE),
    re.compile(r"pos.*store", re.IGNORECASE),
    re.compile(r"pos.*shop", re.IGNORECASE),
    re.compile(r"pos.*market", re.IGNORECASE),
    re.compile(r"pos.*retail", re.IGNORECASE),
]

# Bills regex pattern
_BILLS_RAZORPAY_PATTERN = re.compile(r"razorpay.*bill", re.IGNORECASE)


def _clean_for_keyword_match(desc: str) -> str:
    """Strip bank noise and special chars for keyword matching."""
    raw = desc.lower()
    for noise in BANK_NOISE:
        raw = raw.replace(noise, " ")
    # Collapse non-alpha chars but keep spaces for multi-word matching
    raw = re.sub(r"[^a-z0-9 ]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _layer1_keywords(desc: str, raw_desc: str) -> str:
    """Keyword-based categorisation. Returns category or 'Others'."""
    cleaned = _clean_for_keyword_match(desc)
    # Also try a space-stripped version for single-word keywords
    stripped = cleaned.replace(" ", "")

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if " " in kw:
                # Multi-word keyword: match in the spaced version
                if kw in cleaned:
                    return category
            else:
                if kw in stripped:
                    return category

    # Regex Shopping patterns (POS)
    for pat in _SHOPPING_PATTERNS:
        if pat.search(raw_desc):
            return "Shopping"

    # Razorpay bill pattern
    if _BILLS_RAZORPAY_PATTERN.search(raw_desc):
        return "Bills"

    return "Others"


def _layer2_ml(desc: str) -> tuple:
    """ML model prediction. Returns (category, confidence) or (None, 0)."""
    if _ml_model is None:
        return None, 0.0
    try:
        prediction = _ml_model.predict([desc])[0]
        probabilities = _ml_model.predict_proba([desc])[0]
        confidence = float(np.max(probabilities))
        return prediction, confidence
    except Exception:
        return None, 0.0


# Layer 3 – compiled regex patterns
_PATTERN_RULES = [
    (re.compile(r"\b(ATM|CASH\s*W/?D|CW|CASH\s*WITHDRAWAL)\b", re.IGNORECASE), "ATM"),
    (re.compile(r"\b(SAL\b|SALARY|WAGES|STIPEND|PAYROLL)", re.IGNORECASE), "Salary"),
    (re.compile(r"\b(EMI|LOAN|NACH|ECS|MANDATE|AUTO\s*DEBIT)\b", re.IGNORECASE), "Finance"),
    (re.compile(r"\b(RENT|LEASE)\b", re.IGNORECASE), "Rent"),
]

# Pattern to detect UPI transfer to a person (not a merchant)
# Merchant UPI IDs typically contain brand names; person IDs are phone-based
_UPI_PERSON_PATTERN = re.compile(
    r"UPI[-/].*?(\d{10}|[a-z]+\d*@(ok(sbi|icici|axis|hdfc)|ybl|paytm|upi|apl))",
    re.IGNORECASE,
)


def _layer3_patterns(desc: str) -> str:
    """Regex pattern matching. Returns category or 'Others'."""
    for pattern, category in _PATTERN_RULES:
        if pattern.search(desc):
            return category

    # UPI transfer to a person
    if _UPI_PERSON_PATTERN.search(desc):
        return "Transfer"

    return "Others"


# Layer 4 – subscription amounts commonly used by streaming/bill services
_SUBSCRIPTION_AMOUNTS = {49, 59, 79, 89, 99, 129, 149, 169, 179, 199,
                         249, 299, 349, 399, 449, 499, 599, 699, 799,
                         899, 999, 1199, 1499}


def _layer4_amount_heuristics(desc: str, amount: float, current: str) -> str:
    """Amount-based refinement. Only refines 'Others'."""
    if current != "Others":
        return current

    rounded_amt = round(amount)

    # Round amounts ending in 000 via NEFT → likely Rent or Transfer
    if rounded_amt >= 1000 and rounded_amt % 1000 == 0:
        desc_upper = desc.upper()
        if "NEFT" in desc_upper or "RTGS" in desc_upper:
            return "Transfer"

    # Very small amounts
    if 0 < amount < 20:
        return "Food"

    # Subscription amounts
    if rounded_amt in _SUBSCRIPTION_AMOUNTS:
        return "Entertainment"

    return current


def categorize_transaction(description: str, amount: float = 0.0) -> str:
    """
    4-layer hybrid categorisation engine.
    Layer 1: Expanded keyword matching
    Layer 2: ML model prediction (if layer 1 yields Others/Transfer)
    Layer 3: Regex pattern rules
    Layer 4: Amount heuristics
    """
    desc = str(description).strip()
    if not desc:
        return "Others"

    # Layer 1 – keywords
    category = _layer1_keywords(desc, desc)

    # Layer 2 – ML (only if layer 1 gave Others or Transfer)
    if category in ("Others", "Transfer"):
        ml_cat, ml_conf = _layer2_ml(desc)
        if ml_cat and ml_conf > 0.4:
            category = ml_cat

    # Layer 3 – patterns (only if still Others)
    if category == "Others":
        category = _layer3_patterns(desc)

    # Layer 4 – amount heuristics
    category = _layer4_amount_heuristics(desc, amount, category)

    return category


# ──────────────────────────────────────────────────────────
# Merchant Name Extraction
# ──────────────────────────────────────────────────────────
_MERCHANT_NOISE_PATTERNS = [
    re.compile(r"UPI[-/]", re.IGNORECASE),
    re.compile(r"NEFT[-/]", re.IGNORECASE),
    re.compile(r"IMPS[-/]", re.IGNORECASE),
    re.compile(r"RTGS[-/]", re.IGNORECASE),
    re.compile(r"\b(HDFC|SBI|ICICI|AXIS|KOTAK|IDFC|YES|PNB|BOB|CANARA|UNION)\s*BANK\b", re.IGNORECASE),
    re.compile(r"\b(HDFC|SBIN|ICICI|AXIS|KOTAK|IDFC|YESB|PUNB|BARB)\b", re.IGNORECASE),
    re.compile(r"\b\d{8,}\b"),                       # Long reference numbers
    re.compile(r"\bUTR\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bREF\s*\d+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9]{12,}\b"),                # Transaction IDs
    re.compile(r"@\S+"),                              # UPI handles
    re.compile(r"\b(PVT|LTD|LIMITED|PRIVATE)\b", re.IGNORECASE),
    re.compile(r"\b(VIA|PAYMENT|PAID|FROM|TO)\b", re.IGNORECASE),
]


def extract_merchant(description: str) -> str:
    """Extract clean merchant/payee name from a raw transaction description."""
    text = str(description).strip()
    if not text:
        return "Unknown"

    # Split on common separators (-, /, |)
    parts = re.split(r"[-/|]", text)

    # For UPI transactions, the merchant name is usually the second token
    upper = text.upper()
    if any(tag in upper for tag in ("UPI", "IMPS", "NEFT", "RTGS")):
        # Try to pick the part that looks like a merchant name
        candidates = []
        for part in parts:
            cleaned = part.strip()
            if not cleaned:
                continue
            # Skip parts that are just noise
            is_noise = False
            if re.match(r"^\d+$", cleaned):
                is_noise = True
            if cleaned.upper() in ("UPI", "NEFT", "IMPS", "RTGS", "CR", "DR"):
                is_noise = True
            if re.match(r"^[A-Z0-9]{12,}$", cleaned):
                is_noise = True
            if "@" in cleaned:
                is_noise = True
            # Skip bank names
            if any(b in cleaned.lower() for b in ["hdfc", "sbin", "icici", "axis",
                                                    "kotak", "idfc", "yesb", "bank"]):
                is_noise = True
            if not is_noise and len(cleaned) > 1:
                candidates.append(cleaned)

        if candidates:
            # Return the first meaningful candidate, title-cased
            return candidates[0].strip().title()

    # Fallback: strip all noise patterns from the raw text
    result = text
    for pat in _MERCHANT_NOISE_PATTERNS:
        result = pat.sub(" ", result)

    result = re.sub(r"\s+", " ", result).strip()

    if len(result) < 2:
        return "Unknown"

    return result.title()


# ──────────────────────────────────────────────────────────
# Amount Cleaner
# ──────────────────────────────────────────────────────────
def clean_amt(v) -> float:
    """
    Parse an amount string into a float. Handles:
    - Indian number format (1,23,456.78)
    - Parenthesised amounts as negative (debits)
    - Dr/Cr suffix
    - Empty / dash values
    """
    if v is None:
        return 0.0

    s = str(v).strip()
    if s in ("", "-", "None", "nan", "NaN"):
        return 0.0

    # Detect parenthesised amounts → negative
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # Detect Dr suffix → treat as positive debit
    if re.search(r"\bDr\.?\s*$", s, re.IGNORECASE):
        s = re.sub(r"\bDr\.?\s*$", "", s, flags=re.IGNORECASE).strip()

    # Detect Cr suffix → keep as-is (credit)
    if re.search(r"\bCr\.?\s*$", s, re.IGNORECASE):
        s = re.sub(r"\bCr\.?\s*$", "", s, flags=re.IGNORECASE).strip()

    # Remove currency symbols and whitespace
    s = re.sub(r"[₹$€£]", "", s).strip()

    # Remove commas (handles Indian format: 1,23,456.78)
    s = s.replace(",", "")

    # Remove any remaining non-numeric chars except dot and minus
    s = re.sub(r"[^\d.\-]", "", s)

    try:
        num = float(s)
        if negative:
            num = -abs(num)
        return num
    except (ValueError, TypeError):
        return 0.0


# ──────────────────────────────────────────────────────────
# Smart Insights
# ──────────────────────────────────────────────────────────
def generate_insights(df: pd.DataFrame) -> list:
    """Generate a list of human-readable insight strings from the analysed DataFrame."""
    insights = []

    if df.empty:
        return ["No transactions to analyse."]

    # Work with absolute amounts for spending analysis
    amounts = df["Amount"].abs()
    total = amounts.sum()
    count = len(df)
    avg = amounts.mean()

    # 1. Highest spending category
    if "Category" in df.columns:
        cat_totals = df.groupby("Category")["Amount"].apply(lambda x: x.abs().sum())
        if not cat_totals.empty:
            top_cat = cat_totals.idxmax()
            top_amt = cat_totals.max()
            pct = (top_amt / total * 100) if total > 0 else 0
            insights.append(
                f"Your highest spending category is {top_cat} at "
                f"\u20b9{top_amt:,.0f} ({pct:.1f}% of total)"
            )

    # 2. Largest single transaction
    idx_max = amounts.idxmax()
    largest_amt = amounts.loc[idx_max]
    merchant = df.loc[idx_max, "Merchant"] if "Merchant" in df.columns else "Unknown"
    insights.append(
        f"Your largest single transaction was \u20b9{largest_amt:,.0f} to {merchant}"
    )

    # 3. Transaction count & average
    insights.append(
        f"You made {count} transactions averaging \u20b9{avg:,.0f} each"
    )

    # 4. Most frequent merchant
    if "Merchant" in df.columns:
        merchant_counts = df["Merchant"].value_counts()
        if not merchant_counts.empty:
            top_merchant = merchant_counts.index[0]
            top_count = merchant_counts.iloc[0]
            if top_count > 1:
                insights.append(
                    f"Your most frequent merchant is {top_merchant} with "
                    f"{top_count} transactions"
                )

    # 5. High-value transactions
    high_value = amounts[amounts > 10000]
    if len(high_value) > 0:
        insights.append(
            f"You had {len(high_value)} high-value transactions over \u20b910,000"
        )

    # 6. Busiest spending day
    if "Date" in df.columns:
        try:
            dates = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
            valid = dates.dropna()
            if not valid.empty:
                day_groups = df.loc[valid.index].copy()
                day_groups["_date"] = valid.dt.date
                daily = day_groups.groupby("_date").agg(
                    tx_count=("Amount", "count"),
                    tx_total=("Amount", lambda x: x.abs().sum()),
                )
                busiest = daily["tx_count"].idxmax()
                busiest_count = daily.loc[busiest, "tx_count"]
                busiest_total = daily.loc[busiest, "tx_total"]
                insights.append(
                    f"Your busiest spending day was {busiest} with "
                    f"{busiest_count} transactions totaling "
                    f"\u20b9{busiest_total:,.0f}"
                )
        except Exception:
            pass

    # 7. Unusual spikes (transactions > 3× average)
    if avg > 0:
        spikes = df[amounts > 3 * avg]
        if len(spikes) > 0:
            insights.append(
                f"\u26a0\ufe0f {len(spikes)} transactions were unusually large "
                f"(over 3\u00d7 your average of \u20b9{avg:,.0f})"
            )

    return insights


# ──────────────────────────────────────────────────────────
# Core analysis helper
# ──────────────────────────────────────────────────────────
def _build_dashboard_data(df: pd.DataFrame) -> dict:
    """
    Take a parsed DataFrame (Date, Description, Amount columns expected),
    apply categorisation / merchant extraction, compute summaries,
    and return a dict ready to pass into render_template.
    """
    global _last_df

    # Ensure required columns
    if "Amount" not in df.columns:
        df["Amount"] = 0.0
    df["Amount"] = df["Amount"].apply(lambda x: clean_amt(x) if not isinstance(x, (int, float)) else x)

    # Apply categorisation & merchant extraction
    df["Category"] = df.apply(
        lambda row: categorize_transaction(
            str(row.get("Description", "")),
            abs(float(row.get("Amount", 0))),
        ),
        axis=1,
    )
    df["Merchant"] = df["Description"].apply(extract_merchant)

    # Store for CSV export
    _last_df = df.copy()

    # ── Summary metrics ──
    debits = df[df["Amount"] > 0]["Amount"].sum()
    credits = df[df["Amount"] < 0]["Amount"].abs().sum()
    # If all amounts are positive (common in parsed statements), treat total as debit
    total_debit = round(debits, 2)
    total_credit = round(credits, 2)
    net_flow = round(total_debit - total_credit, 2)
    tx_count = len(df)
    avg_tx = round(df["Amount"].abs().mean(), 2) if tx_count > 0 else 0

    # ── Category summary ──
    cat_summary = (
        df.groupby("Category")["Amount"]
        .apply(lambda x: round(x.abs().sum(), 2))
        .reset_index()
        .sort_values("Amount", ascending=False)
    )
    top_category = cat_summary.iloc[0]["Category"] if not cat_summary.empty else "N/A"

    # ── Top merchants (top 10 by absolute spend) ──
    merchant_spend = (
        df.groupby("Merchant")["Amount"]
        .agg(
            total=lambda x: round(x.abs().sum(), 2),
            count="count"
        )
        .reset_index()
        .sort_values("total", ascending=False)
        .head(10)
    )

    # ── Daily spending ──
    daily_data = []
    if "Date" in df.columns:
        try:
            dates = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
            valid_mask = dates.notna()
            if valid_mask.any():
                tmp = df.loc[valid_mask].copy()
                tmp["_date_str"] = dates[valid_mask].dt.strftime("%Y-%m-%d")
                daily = (
                    tmp.groupby("_date_str")["Amount"]
                    .apply(lambda x: round(x.abs().sum(), 2))
                    .reset_index()
                    .sort_values("_date_str")
                )
                daily_data = daily.values.tolist()
        except Exception:
            pass

    # ── Smart insights ──
    insights = generate_insights(df)

    # Clean up NaNs before converting to records
    df_clean = df.copy()
    df_clean["Debit"] = df_clean["Debit"].fillna(0.0)
    df_clean["Credit"] = df_clean["Credit"].fillna(0.0)
    df_clean["Balance"] = df_clean["Balance"].fillna(0.0)
    df_clean["Amount"] = df_clean["Amount"].fillna(0.0)
    df_clean["Merchant"] = df_clean["Merchant"].fillna("—")
    df_clean["Category"] = df_clean["Category"].fillna("Others")

    rows = df_clean.rename(columns={
        "Date": "Transaction Date",
        "Description": "Description/Narration",
        "Category": "AI Category",
    }).to_dict("records")

    return dict(
        rows=rows,
        total_spend=total_debit,
        total_credit=total_credit,
        net_flow=net_flow,
        total_transactions=tx_count,
        avg_transaction=avg_tx,
        top_category=top_category,
        category_summary=cat_summary.values.tolist(),
        top_merchants=merchant_spend.values.tolist(),
        daily_spending=daily_data,
        insights=insights,
    )


# ──────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return "OK", 200


@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    password = request.form.get("password", None)

    # Preserve original extension
    original_name = file.filename or "upload"
    _, ext = os.path.splitext(original_name)
    ext = ext if ext else ".pdf"

    # Save to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        file.save(tmp.name)
        path = tmp.name

    try:
        df = parse_statement(path, password)
    except PasswordRequired:
        # Cache the file for later retry
        file_id = str(uuid.uuid4())
        cached_path = os.path.join("uploads", f"{file_id}{ext}")
        import shutil
        shutil.move(path, cached_path)
        return jsonify({"needs_password": True, "file_id": file_id})
    except WrongPassword:
        _safe_delete(path)
        return jsonify({"error": "Wrong password, Try Again"})
    except UnsupportedFormat:
        _safe_delete(path)
        return jsonify({
            "error": "Unsupported file format. Please upload PDF, CSV, XLSX, or DOCX."
        })
    except ParseError:
        _safe_delete(path)
        return jsonify({
            "error": "Could not parse the statement. The format may not be recognized."
        })
    except Exception as e:
        _safe_delete(path)
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

    _safe_delete(path)

    if df is None or df.empty:
        return jsonify({
            "error": "Could not parse the statement. The format may not be recognized."
        })

    data = _build_dashboard_data(df)
    return render_template("dashboard.html", **data)


@app.route("/retry-password", methods=["POST"])
def retry_password():
    payload = request.get_json(force=True)
    file_id = payload.get("file_id", "")
    password = payload.get("password", "")

    if not file_id:
        return jsonify({"error": "Missing file_id"}), 400

    # Find the cached file (any extension)
    pattern = os.path.join("uploads", f"{file_id}.*")
    matches = glob.glob(pattern)
    if not matches:
        return jsonify({"error": "File not found. Please upload again."}), 404

    cached_path = matches[0]

    try:
        df = parse_statement(cached_path, password)
    except PasswordRequired:
        return jsonify({"needs_password": True, "file_id": file_id})
    except WrongPassword:
        return jsonify({"error": "Wrong password, Try Again"})
    except UnsupportedFormat:
        _safe_delete(cached_path)
        return jsonify({
            "error": "Unsupported file format. Please upload PDF, CSV, XLSX, or DOCX."
        })
    except ParseError:
        _safe_delete(cached_path)
        return jsonify({
            "error": "Could not parse the statement. The format may not be recognized."
        })
    except Exception as e:
        _safe_delete(cached_path)
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

    # Clean up cached file on success
    _safe_delete(cached_path)

    if df is None or df.empty:
        return jsonify({
            "error": "Could not parse the statement. The format may not be recognized."
        })

    data = _build_dashboard_data(df)
    return render_template("dashboard.html", **data)


@app.route("/export-csv")
def export_csv():
    global _last_df
    if _last_df is None or _last_df.empty:
        return jsonify({"error": "No data to export. Please analyse a statement first."}), 400

    buf = BytesIO()
    _last_df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)

    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name="smartspend_analysis.csv",
    )


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────
def _safe_delete(path: str):
    """Silently delete a file if it exists."""
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass



# ──────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
