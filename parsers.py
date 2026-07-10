import os
import re
import tempfile
import pandas as pd
import pdfplumber
import pikepdf
import docx

class PasswordRequired(Exception):
    pass

class WrongPassword(Exception):
    pass

class UnsupportedFormat(Exception):
    pass

class ParseError(Exception):
    pass

# Column header lists for fuzzy matching
DATE_HEADERS = ["date", "txn date", "transaction date", "value date", "posting date", "tran date"]
DESC_HEADERS = ["narration", "description", "particulars", "details", "remarks", "transaction details", "particular"]
DEBIT_HEADERS = ["debit", "withdrawal", "dr", "debit amount", "withdrawals", "payment"]
CREDIT_HEADERS = ["credit", "deposit", "cr", "credit amount", "deposits", "receipt"]
BALANCE_HEADERS = ["balance", "bal"]
AMOUNT_HEADERS = ["amount", "amt", "value", "transaction amount"]
TYPE_HEADERS = ["type", "dr/cr", "cr/dr", "d/c"]

def clean_val(v):
    """
    Clean raw string amount into numeric float value.
    Returns: (float_value, is_dr, is_cr)
    """
    if v is None:
        return 0.0, False, False
    
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "None", "nan", "NaN"):
        return 0.0, False, False
    
    # Remove currency symbols
    s = re.sub(r"[₹$€£]", "", s).strip()
    
    # Check parenthesized negative amounts
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
        
    # Check Dr/Cr suffix
    is_dr = False
    is_cr = False
    if re.search(r"\bDr\.?\s*$", s, re.IGNORECASE):
        is_dr = True
        s = re.sub(r"\bDr\.?\s*$", "", s, flags=re.IGNORECASE).strip()
    elif re.search(r"\bCr\.?\s*$", s, re.IGNORECASE):
        is_cr = True
        s = re.sub(r"\bCr\.?\s*$", "", s, flags=re.IGNORECASE).strip()

    # Extract numeric part
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        val = float(s)
        if negative:
            val = -abs(val)
        return val, is_dr, is_cr
    except ValueError:
        return 0.0, False, False

def find_columns(headers):
    """Fuzzy match list of headers to indices."""
    headers_lower = [str(h).strip().lower() for h in headers]
    
    def find_match(candidates, headers_list):
        for cand in candidates:
            for i, h in enumerate(headers_list):
                if cand == h or cand in h:
                    return i
        return None

    idx_date = find_match(DATE_HEADERS, headers_lower)
    idx_desc = find_match(DESC_HEADERS, headers_lower)
    idx_debit = find_match(DEBIT_HEADERS, headers_lower)
    idx_credit = find_match(CREDIT_HEADERS, headers_lower)
    idx_amount = find_match(AMOUNT_HEADERS, headers_lower)
    idx_type = find_match(TYPE_HEADERS, headers_lower)
    idx_balance = find_match(BALANCE_HEADERS, headers_lower)
    
    return idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance

def try_open_pdf(path, password=None):
    """
    Test and open PDF using pikepdf for decryption and pdfplumber for layout extraction.
    Only prompts for password if it is actually user password protected (cannot open without password).
    """
    try:
        # Try to open without a password first.
        # This succeeds for unencrypted PDFs or PDFs that are only owner-restricted (no user password required to open).
        pdf = pikepdf.open(path)
        
        # Save a decrypted version to remove restrictions, making it easily readable by pdfplumber
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        pdf.save(temp_file.name)
        pdf.close()
        pdf_obj = pdfplumber.open(temp_file.name)
        return pdf_obj, temp_file.name
    except pikepdf.PasswordError:
        # A user password is explicitly required to open this file!
        if not password:
            raise PasswordRequired()
        try:
            pdf = pikepdf.open(path, password=password)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            pdf.save(temp_file.name)
            pdf.close()
            pdf_obj = pdfplumber.open(temp_file.name)
            return pdf_obj, temp_file.name
        except pikepdf.PasswordError:
            raise WrongPassword()
        except Exception as e:
            raise ParseError(f"Error decrypting PDF: {str(e)}")
    except Exception as e:
        # Fallback to opening directly with pdfplumber (corrupted files or non-standard PDFs)
        try:
            pdf_obj = pdfplumber.open(path)
            return pdf_obj, None
        except Exception:
            raise ParseError(f"Unable to read PDF file: {str(e)}")

def parse_pdf_table(pdf):
    """Parse table-based PDF pages using pdfplumber."""
    rows = []
    for page in pdf.pages:
        table = page.extract_table()
        if table:
            table = [[str(c) if c is not None else "" for c in r] for r in table]
            rows.extend(table)
            
    if not rows:
        return None
        
    header_row_idx = None
    cols = None
    
    # Scan first 10 rows for columns headers
    for idx in range(min(10, len(rows))):
        header_candidate = [str(x).lower() for x in rows[idx]]
        idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = find_columns(header_candidate)
        if idx_date is not None and idx_desc is not None:
            header_row_idx = idx
            cols = (idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance)
            break
            
    if header_row_idx is None:
        header_candidate = [str(x).lower() for x in rows[0]]
        idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = find_columns(header_candidate)
        header_row_idx = 0
        cols = (idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance)
        
    idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = cols
    final_transactions = []
    current_tx = None
    
    date_pat = re.compile(
        r"\d{1,4}[-/\s.]\d{1,4}[-/\s.]\d{2,4}|\d{1,2}[-/\s.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s.]\d{2,4}", 
        re.IGNORECASE
    )

    for i in range(header_row_idx + 1, len(rows)):
        row = rows[i]
        if len(row) <= max(idx_date or 0, idx_desc or 0):
            continue
            
        raw_date = row[idx_date] if idx_date is not None else ""
        raw_desc = row[idx_desc] if idx_desc is not None else ""
        
        debit_val = 0.0
        credit_val = 0.0
        balance_val = 0.0
        
        if idx_debit is not None and idx_debit < len(row):
            debit_val, _, _ = clean_val(row[idx_debit])
        if idx_credit is not None and idx_credit < len(row):
            credit_val, _, _ = clean_val(row[idx_credit])
        if idx_amount is not None and idx_amount < len(row):
            amt, is_dr, is_cr = clean_val(row[idx_amount])
            tx_type = ""
            if idx_type is not None and idx_type < len(row):
                tx_type = str(row[idx_type]).strip().upper()
                
            if tx_type in ("DR", "DEBIT", "W", "WITHDRAWAL", "PAYMENT") or is_dr:
                debit_val = abs(amt)
            elif tx_type in ("CR", "CREDIT", "D", "DEPOSIT", "RECEIPT") or is_cr:
                credit_val = abs(amt)
            else:
                if amt < 0:
                    debit_val = abs(amt)
                else:
                    debit_val = amt
                    
        if idx_balance is not None and idx_balance < len(row):
            balance_val, _, _ = clean_val(row[idx_balance])
            
        is_new_tx = False
        clean_date_str = raw_date.strip()
        if clean_date_str and date_pat.search(clean_date_str):
            is_new_tx = True
            
        if is_new_tx:
            if current_tx:
                final_transactions.append(current_tx)
            current_tx = {
                "Date": clean_date_str,
                "Description": raw_desc.replace("\n", " ").strip(),
                "Debit": debit_val,
                "Credit": credit_val,
                "Balance": balance_val
            }
        else:
            if current_tx and raw_desc.strip():
                current_tx["Description"] += " " + raw_desc.replace("\n", " ").strip()
                if current_tx["Debit"] == 0.0 and debit_val != 0.0:
                    current_tx["Debit"] = debit_val
                if current_tx["Credit"] == 0.0 and credit_val != 0.0:
                    current_tx["Credit"] = credit_val
                if current_tx["Balance"] == 0.0 and balance_val != 0.0:
                    current_tx["Balance"] = balance_val

    if current_tx:
        final_transactions.append(current_tx)
        
    if not final_transactions:
        return None
        
    return pd.DataFrame(final_transactions)

def parse_pdf_text(pdf):
    """Parse text-based PDF line by line as fallback."""
    data = []
    date_pat = re.compile(
        r"^(\d{1,2}[-/\s.]\d{1,2}[-/\s.]\d{2,4}|\d{1,2}[-/\s.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s.]\d{2,4})",
        re.IGNORECASE
    )
    amt_pat = re.compile(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b")
    current_tx = None
    
    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue
            
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
                
            date_match = date_pat.search(line)
            amounts = amt_pat.findall(line)
            
            if date_match and amounts:
                if current_tx:
                    data.append(current_tx)
                    
                date_str = date_match.group(1)
                amt_details = []
                for a in amounts:
                    v, dr_flag, cr_flag = clean_val(a)
                    # Check if the raw string in line has CR/DR right next to it (e.g. 150.00Cr)
                    idx = line.find(a)
                    if idx != -1:
                        suffix = line[idx + len(a):idx + len(a) + 3].lower()
                        if "cr" in suffix:
                            cr_flag = True
                        elif "dr" in suffix:
                            dr_flag = True
                    amt_details.append((v, dr_flag, cr_flag))
                    
                debit_val = 0.0
                credit_val = 0.0
                balance_val = 0.0
                
                # Separate description from date and amounts
                desc = line
                desc = desc.replace(date_str, "")
                for a in amounts:
                    desc = desc.replace(a, "")
                desc = re.sub(r"\s+", " ", desc).strip()
                desc_lower = desc.lower()
                
                # Check for explicit keywords in description
                is_credit_desc = any(k in desc_lower for k in ("salary", "refund", "interest", "credit", "received", "deposit", "reversed", "dividend", "cashback"))
                
                if len(amt_details) == 1:
                    v, dr_flag, cr_flag = amt_details[0]
                    if cr_flag or (is_credit_desc and not dr_flag):
                        credit_val = v
                    else:
                        debit_val = v
                elif len(amt_details) == 2:
                    tx_val, tx_dr, tx_cr = amt_details[0]
                    bal_val, _, _ = amt_details[1]
                    
                    if tx_cr or (is_credit_desc and not tx_dr):
                        credit_val = tx_val
                    else:
                        debit_val = tx_val
                    balance_val = bal_val
                elif len(amt_details) >= 3:
                    # Usually [Debit, Credit, Balance]
                    tx1_val, tx1_dr, tx1_cr = amt_details[0]
                    tx2_val, tx2_dr, tx2_cr = amt_details[1]
                    bal_val, _, _ = amt_details[2]
                    
                    if tx2_val > 0 and tx1_val == 0:
                        credit_val = tx2_val
                    elif tx1_val > 0 and tx2_val == 0:
                        debit_val = tx1_val
                    else:
                        if tx2_cr or is_credit_desc:
                            credit_val = tx2_val
                            debit_val = tx1_val
                        else:
                            debit_val = tx1_val
                            credit_val = tx2_val
                    balance_val = bal_val
                    
                desc = line
                desc = desc.replace(date_str, "")
                for a in amounts:
                    desc = desc.replace(a, "")
                desc = re.sub(r"\s+", " ", desc).strip()
                
                current_tx = {
                    "Date": date_str,
                    "Description": desc,
                    "Debit": debit_val,
                    "Credit": credit_val,
                    "Balance": balance_val
                }
            else:
                if current_tx:
                    if not any(k in line.lower() for k in ("page", "statement", "date", "balance", "total")):
                        current_tx["Description"] += " " + line
                        
    if current_tx:
        data.append(current_tx)
        
    if not data:
        return None
        
    return pd.DataFrame(data)

def process_dataframe(df):
    """Normalize raw pandas DataFrame parsed from CSV, Excel, or DOCX."""
    rows = df.values.tolist()
    headers = [str(c).lower() for c in df.columns]
    idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = find_columns(headers)
    
    header_row_idx = -1
    cols = None
    
    if idx_date is not None and idx_desc is not None:
        cols = (idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance)
    else:
        # Scan first 15 rows for column header mapping
        for idx in range(min(15, len(rows))):
            row_cand = [str(x).lower() for x in rows[idx]]
            idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = find_columns(row_cand)
            if idx_date is not None and idx_desc is not None:
                header_row_idx = idx
                cols = (idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance)
                break
                
    if cols is None:
        raise ParseError("Could not find Date and Description columns in the statement structure")
        
    idx_date, idx_desc, idx_debit, idx_credit, idx_amount, idx_type, idx_balance = cols
    final_transactions = []
    start_row = header_row_idx + 1 if header_row_idx != -1 else 0
    
    date_pat = re.compile(
        r"\d{1,4}[-/\s.]\d{1,4}[-/\s.]\d{2,4}|\d{1,2}[-/\s.](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s.]\d{2,4}", 
        re.IGNORECASE
    )
    current_tx = None
    
    for i in range(start_row, len(rows)):
        row = rows[i]
        if len(row) <= max(idx_date or 0, idx_desc or 0):
            continue
            
        raw_date = str(row[idx_date]) if row[idx_date] is not None and not pd.isna(row[idx_date]) else ""
        raw_desc = str(row[idx_desc]) if row[idx_desc] is not None and not pd.isna(row[idx_desc]) else ""
        
        debit_val = 0.0
        credit_val = 0.0
        balance_val = 0.0
        
        if idx_debit is not None and idx_debit < len(row) and not pd.isna(row[idx_debit]):
            debit_val, _, _ = clean_val(row[idx_debit])
        if idx_credit is not None and idx_credit < len(row) and not pd.isna(row[idx_credit]):
            credit_val, _, _ = clean_val(row[idx_credit])
        if idx_amount is not None and idx_amount < len(row) and not pd.isna(row[idx_amount]):
            amt, is_dr, is_cr = clean_val(row[idx_amount])
            tx_type = ""
            if idx_type is not None and idx_type < len(row) and not pd.isna(row[idx_type]):
                tx_type = str(row[idx_type]).strip().upper()
                
            if tx_type in ("DR", "DEBIT", "W", "WITHDRAWAL", "PAYMENT") or is_dr:
                debit_val = abs(amt)
            elif tx_type in ("CR", "CREDIT", "D", "DEPOSIT", "RECEIPT") or is_cr:
                credit_val = abs(amt)
            else:
                if amt < 0:
                    debit_val = abs(amt)
                else:
                    debit_val = amt
                    
        if idx_balance is not None and idx_balance < len(row) and not pd.isna(row[idx_balance]):
            balance_val, _, _ = clean_val(row[idx_balance])
            
        clean_date_str = raw_date.strip()
        is_new_tx = False
        if clean_date_str and (date_pat.search(clean_date_str) or isinstance(row[idx_date], pd.Timestamp)):
            is_new_tx = True
            if isinstance(row[idx_date], pd.Timestamp):
                clean_date_str = row[idx_date].strftime("%d/%m/%Y")
                
        if is_new_tx:
            if current_tx:
                final_transactions.append(current_tx)
            current_tx = {
                "Date": clean_date_str,
                "Description": raw_desc.replace("\n", " ").strip(),
                "Debit": debit_val,
                "Credit": credit_val,
                "Balance": balance_val
            }
        else:
            if current_tx and raw_desc.strip():
                current_tx["Description"] += " " + raw_desc.replace("\n", " ").strip()
                if current_tx["Debit"] == 0.0 and debit_val != 0.0:
                    current_tx["Debit"] = debit_val
                if current_tx["Credit"] == 0.0 and credit_val != 0.0:
                    current_tx["Credit"] = credit_val
                if current_tx["Balance"] == 0.0 and balance_val != 0.0:
                    current_tx["Balance"] = balance_val
                    
    if current_tx:
        final_transactions.append(current_tx)
        
    if not final_transactions:
        raise ParseError("No valid transactions found in statement data")
        
    return pd.DataFrame(final_transactions)

def parse_csv(file_path):
    """Parse CSV statements with dynamic separator detection."""
    df = None
    for encoding in ("utf-8", "latin-1", "utf-16"):
        try:
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(file_path, sep=sep, encoding=encoding)
                    if len(df.columns) >= 2:
                        break
                except Exception:
                    continue
            if df is not None and len(df.columns) >= 2:
                break
        except Exception:
            continue
            
    if df is None:
        raise ParseError("Unable to read or decode CSV file")
        
    return process_dataframe(df)

def parse_excel(file_path):
    """Parse Excel sheets (.xlsx / .xls)."""
    try:
        xl = pd.ExcelFile(file_path, engine="openpyxl")
        df = xl.parse(xl.sheet_names[0])
    except Exception as e:
        raise ParseError(f"Unable to read Excel file: {str(e)}")
        
    return process_dataframe(df)

def parse_docx(file_path):
    """Parse Word DOCX table structures."""
    try:
        doc = docx.Document(file_path)
        all_tables = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells]
                table_data.append(row_cells)
            if table_data:
                all_tables.extend(table_data)
        if not all_tables:
            raise ParseError("No table found inside Word document")
            
        df = pd.DataFrame(all_tables)
        return process_dataframe(df)
    except Exception as e:
        raise ParseError(f"Error parsing Word tables: {str(e)}")

def parse_statement(file_path, password=None):
    """
    Universal entry point to parse any bank statement file.
    Normalizes Output format to have Date, Description, Debit, Credit, Balance, and Amount.
    """
    _, ext = os.path.splitext(file_path.lower())
    
    if ext == ".pdf":
        pdf_obj, temp_path = try_open_pdf(file_path, password)
        try:
            df = parse_pdf_table(pdf_obj)
            if df is None or df.empty:
                df = parse_pdf_text(pdf_obj)
        finally:
            pdf_obj.close()
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                    
        if df is None or df.empty:
            raise ParseError("Could not extract tabular or textual transaction lines from the PDF statement")
            
    elif ext == ".csv":
        df = parse_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        df = parse_excel(file_path)
    elif ext == ".docx":
        df = parse_docx(file_path)
    else:
        raise UnsupportedFormat()
        
    # Standardize columns
    if "Debit" not in df.columns:
        df["Debit"] = 0.0
    if "Credit" not in df.columns:
        df["Credit"] = 0.0
    if "Balance" not in df.columns:
        df["Balance"] = 0.0
        
    # Standardize Amount format: Debit is positive, Credit is negative
    df["Amount"] = df.apply(lambda r: r["Debit"] if r["Debit"] > 0 else -r["Credit"], axis=1)
    
    return df[["Date", "Description", "Debit", "Credit", "Balance", "Amount"]]
