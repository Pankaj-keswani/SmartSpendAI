BANK_FORMATS = [
    {
        "name": "SBI",
        "keywords": ["state bank of india", "sbi"],
        "cols": {"date": "date", "desc": "narration", "amt": ["debit", "credit"]}
    },
    {
        "name": "HDFC",
        "keywords": ["hdfc bank"],
        "cols": {"date": "date", "desc": "narration", "amt": ["withdrawal", "deposit"]}
    },
    {
        "name": "PAYTM",
        "keywords": ["paytm", "one97"],
        "cols": {"date": "date", "desc": "transaction details", "amt": ["amount"]}
    }
    # Add more as needed...
]

def detect_bank(text):
    text = text.lower()
    for bank in BANK_FORMATS:
        if any(kw in text for kw in bank["keywords"]):
            return bank
    return None
