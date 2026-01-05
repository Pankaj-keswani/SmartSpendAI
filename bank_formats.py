import re

BANK_FORMATS = [

    {
        "name":"SBI",
        "match":["state bank","sbi"],
        "date":["date","txn date","transaction date"],
        "desc":["narration","description","particular","details"],
        "debit":["debit","withdrawal","dr"],
        "credit":["credit","deposit","cr"]
    },

    {
        "name":"HDFC",
        "match":["hdfc"],
        "date":["date"],
        "desc":["narration","description","details"],
        "debit":["debit amt","withdrawal","debit"],
        "credit":["credit amt","deposit","credit"]
    },

    {
        "name":"ICICI",
        "match":["icici"],
        "date":["date"],
        "desc":["transaction details","narration","description"],
        "debit":["debit"],
        "credit":["credit"]
    },

    {
        "name":"AXIS",
        "match":["axis"],
        "date":["date"],
        "desc":["particulars","details","narration"],
        "debit":["withdrawal","debit"],
        "credit":["deposit","credit"]
    },

    {
        "name":"KOTAK",
        "match":["kotak"],
        "date":["date"],
        "desc":["narration","details"],
        "debit":["debit"],
        "credit":["credit"]
    },

    {
        "name":"INDUSIND",
        "match":["indusind"],
        "date":["date"],
        "desc":["description","particulars","details"],
        "debit":["debit"],
        "credit":["credit"]
    },

    {
        "name":"PNB",
        "match":["punjab national","pnb"],
        "date":["date"],
        "desc":["particulars","narration","description"],
        "debit":["debit"],
        "credit":["credit"]
    },

    {
        "name":"AU SMALL FINANCE BANK",
        "match":["au bank","au small"],
        "date":["date"],
        "desc":["description","narration","details"],
        "debit":["debit"],
        "credit":["credit"]
    },

    {
        "name":"PAYTM",
        "match":["paytm","upi"],
        "date":["date"],
        "desc":["transaction details","details"],
        "debit":["paid","debit","- rs"],
        "credit":["received","+ rs"]
    },

    {
        "name":"GENERIC",
        "match":[""],
        "date":["date"],
        "desc":["description","narration","details","particular"],
        "debit":["debit","withdraw","dr"],
        "credit":["credit","deposit","cr"]
    }

]


def detect_bank(text):
    text = text.lower()
    for b in BANK_FORMATS:
        for key in b["match"]:
            if key in text:
                return b
    return BANK_FORMATS[-1]


def detect_column(columns, names):
    columns = [str(c).lower() for c in columns]
    for n in names:
        for c in columns:
            if n in c:
                return c
    return None