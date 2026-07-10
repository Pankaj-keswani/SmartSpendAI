import os
import json
import csv
import random
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import pickle

# Ensure directories exist
os.makedirs("data", exist_ok=True)
os.makedirs("model", exist_ok=True)

# Labeled templates to generate rich, realistic bank statement rows
CANDIDATE_DATA = {
    "Shopping": [
        "Flipkart", "Amazon", "Myntra", "Ajio", "Meesho", "Nykaa", "Tata Cliq", 
        "Zara", "H&M", "Decathlon", "Croma", "Reliance Digital", "Shoppers Stop", 
        "Westside", "DMart", "Tanishq", "Titan", "Crossword", "Vijay Sales", "Uniqlo",
        "Bewakoof", "Urbanic", "Snapdeal", "Shopclues", "Firstcry", "Pepperfry",
        "Ikea", "Kalyan Jewellers", "Fossil Store", "Boat Lifestyle", "Noise Electronics"
    ],
    "Food": [
        "Swiggy", "Zomato", "Dominos", "KFC", "McDonalds", "Burger King", 
        "Starbucks", "CCD", "Chaayos", "Haldirams", "Bikanervala", "Sagar Ratna", 
        "Pizza Hut", "Subway", "Eatfit", "Barista", "Chai Point", "Saravana Bhavan",
        "Faasos", "Ovenstory", "Behrouz Biryani", "Wow Momo", "Rebel Foods", "Box8"
    ],
    "Grocery": [
        "BigBasket", "BBNow", "JioMart", "Zepto", "Blinkit", "Dunzo", "Grofers", 
        "Swiggy Instamart", "Reliance Fresh", "More Supermarket", "Spar", "Star Bazaar", 
        "Nature Basket", "Licious", "Country Delight", "Milkbasket", "Amul Parlour", "Mother Dairy",
        "Kirana Store", "General Provision Store", "Vegetable Vendor Market"
    ],
    "Healthcare": [
        "Apollo Pharmacy", "Practo", "1mg", "Netmeds", "Medplus", "Pharmeasy", 
        "Fortis Hospital", "Max Hospital", "AIIMS Clinic", "Dr Lal Pathlabs", 
        "Pathology Lab Test", "Dental Care Clinic", "Chemist Shop", "Ayurvedic Store",
        "Homeopathy Clinic", "Medanta Medicity", "Apollo Hospitals"
    ],
    "Travel": [
        "Uber Rides", "Ola Cabs", "Rapido Bike Auto", "IRCTC Ticket", "MakeMyTrip", "GoIbibo", 
        "Yatra Travel", "Cleartrip Booking", "Air India", "Indigo Airlines", "Spicejet", "Vistara", 
        "Metro Smart Card", "State Bus Ticket", "Toll Plaza Fastag", "Fastag Recharge", "Parking Fee"
    ],
    "Fuel": [
        "HPCL Petrol Pump", "BPCL Fuel Station", "IOCL Indian Oil", "Shell Petrol Pump", 
        "Hindustan Petroleum", "Bharat Petroleum", "CNG Gas Station", "EV charging point"
    ],
    "Bills": [
        "Airtel Postpaid Bill", "Jio Prepaid Recharge", "Vodafone Vi Bill Pay", "BSNL Fiber Broadband", 
        "Electricity Bill Payment Bescom", "Municipal Water Bill", "Gas Bill Indane Gas", 
        "Tata Play DTH Recharge", "Dish TV Bill", "Act Fibernet Broadband", "Hathway Cable Internet",
        "Billdesk Utility Payment", "Razorpay Utilities Bill"
    ],
    "Entertainment": [
        "Netflix subscription", "Spotify Premium Music", "Amazon Prime Video", 
        "Disney Plus Hotstar", "Zee5 Subscription", "SonyLIV Premium", "JioCinema Pro", 
        "YouTube Premium Member", "BookMyShow Movie tickets", "PVR Cinemas", "INOX Movies", 
        "Steam Games Purchase", "Playstation Network Wallet", "Dream11 Contest", "MPL Gaming"
    ],
    "Education": [
        "Byjus online classes", "Unacademy subscription fee", "Udemy software course", "Coursera certification", 
        "School Fee payment", "College tuition fee semester", "Coaching institute fees", 
        "Stationery Shop notebooks", "Crossword books store", "Exam registration fee gate"
    ],
    "Finance": [
        "HDFC Loan EMI Auto Debit", "SBI Home Loan Payment", "LIC Life Insurance Premium", 
        "Policybazaar health insurance", "Bajaj Finserv EMI", "HDFC Ergo General Insurance", 
        "ICICI Lombard auto insurance", "NACH Auto Debit EMI", "ECS loan mandate payout"
    ],
    "Rent": [
        "Flat Rent payment transfer", "House Rent monthly payout", "PG Accommodation Rent", 
        "Hostel fee and rent", "Landlord rent transfer account", "Monthly apartment rent", "PG Rent"
    ],
    "Salary": [
        "Salary Credited payroll", "Monthly Salary Payout", "Wages payment", "Stipend credit intern", 
        "Freelance payroll project", "Salary credit company", "Consulting fee payout"
    ],
    "Investment": [
        "Zerodha fund transfer demat", "Groww Mutual Fund SIP", "Upstox trading account", 
        "Angel One stock purchase", "Mutual Fund SIP placement", "PPF deposit account", 
        "Fixed Deposit FD placement SBI", "NPS national pension contribution", "Demat maintenance charges"
    ],
    "ATM": [
        "ATM Cash Withdrawal", "ATM-CW SBI Branch", "HDFC ATM withdrawal cash", 
        "Cash withdrawal self teller", "ATM txn cash dispenser", "ATM withdrawal CW money"
    ],
    "Transfer": [
        "UPI Transfer to Person", "IMPS fund transfer", "NEFT transfer out friend", 
        "RTGS payment vendor transfer", "Money transfer to family member", "Sent money to account", 
        "UPI payment to personal account", "Fund transfer self mobile bank"
    ]
}

UPI_HANDLES = ["@oksbi", "@okicici", "@okaxis", "@okhdfc", "@ybl", "@paytm", "@upi", "@apl", "@waaxis"]
CITIES = ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Kolkata", "Pune", "Noida", "Gurugram"]

def generate_sample(category, name):
    """Generate a highly realistic transaction text given category and brand name."""
    txn_id = f"{random.randint(100000000000, 999999999999)}"
    ref_id = f"UTR{random.randint(1000000000, 9999999999)}"
    upi_id = f"{name.lower().replace(' ', '')}{random.randint(10,999)}{random.choice(UPI_HANDLES)}"
    city = random.choice(CITIES)
    
    templates = []
    
    if category in ("Shopping", "Food", "Grocery", "Healthcare", "Travel", "Fuel", "Bills", "Entertainment", "Education"):
        templates = [
            f"UPI-{name}-{upi_id}-{txn_id}-Payment",
            f"POS TXN {name.upper()} {city.upper()}",
            f"{name} Private Limited {city}",
            f"UPI-PAY-{name.upper()}-{upi_id}",
            f"Razorpay * {name}",
            f"Paytm * {name} Delhi",
            f"IMPS-{txn_id}-{name.upper()}-Payment",
            f"Card Payment at {name} {city}"
        ]
    elif category == "Finance":
        templates = [
            f"ACH Debit-{name.upper()}-{ref_id}",
            f"ECS MANDATE-{name.upper()}-EMI",
            f"NACH DEBIT {name.upper()} LOAN EMI",
            f"AUTO-DEBIT {name.upper()} INSURANCE"
        ]
    elif category == "Rent":
        landlords = ["Kumar", "Sharma", "Singh", "Patel", "Reddy", "Rao", "Joshi", "Sen"]
        name_land = random.choice(landlords)
        templates = [
            f"NEFT-{ref_id}-Rent to {name_land}",
            f"IMPS-{txn_id}-Room rent payout",
            f"UPI-Rent-{upi_id}-House Rent",
            f"Rent transfer to {name_land} Flat"
        ]
    elif category == "Salary":
        companies = ["TCS", "Infosys", "Wipro", "HCL", "Cognizant", "Google", "Microsoft", "Startup Inc"]
        comp = random.choice(companies)
        templates = [
            f"Salary Credited-{comp}-Payroll",
            f"SALARY CR-{txn_id}",
            f"NEFT-{ref_id}-Salary Payout {comp}",
            f"STIPEND CREDITED-{comp}-Internship"
        ]
    elif category == "Investment":
        templates = [
            f"UPI-{name}-{upi_id}-SIP Transfer",
            f"ACH Debit {name.upper()} MUTUAL FUND",
            f"GROWWSTOCK-{name.upper()}-BUY",
            f"PPF Deposit {txn_id}"
        ]
    elif category == "ATM":
        templates = [
            f"ATM-CW-{txn_id}-{name.upper()} ATM {city.upper()}",
            f"ATM CASH WITHDRAWAL {name.upper()} DISPENSER",
            f"CASH WITHDRAWAL self HDFC ATM",
            f"ATM-CW SBI ATM cash"
        ]
    elif category == "Transfer":
        people = ["Rahul", "Amit", "Priya", "Sneha", "Karan", "Siddharth", "Neha", "Rohan"]
        person = random.choice(people)
        templates = [
            f"UPI-{person.lower()}@{random.choice(UPI_HANDLES)[1:]}-{txn_id}",
            f"IMPS-{txn_id}-Transfer to {person}",
            f"NEFT-{ref_id}-Fund transfer to {person}",
            f"Sent to {person} UPI wallet",
            f"UPI-Transfer-{person.upper()}"
        ]
        
    return random.choice(templates)

def build_training_dataset():
    """Generates 1200+ structured examples and writes them to data/training_data.csv."""
    samples = []
    
    # Generate around 80-100 samples per category
    for category, brands in CANDIDATE_DATA.items():
        # Generate several samples for each brand
        for brand in brands:
            # Create 3-4 distinct variations of descriptions for each brand
            for _ in range(4):
                text = generate_sample(category, brand)
                samples.append((text, category))
                
        # Fill in extra generic terms
        for _ in range(30):
            text = f"Generic transaction row for {category.lower()} payment {random.randint(100,999)}"
            samples.append((text, category))
            
    # Shuffle and write to CSV
    random.shuffle(samples)
    
    csv_path = "data/training_data.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "category"])
        writer.writerows(samples)
        
    print(f"Dataset generated with {len(samples)} examples at {csv_path}")

def train_model():
    """Load dataset, train model pipeline, and save expense_model.pkl & categories.json."""
    csv_path = "data/training_data.csv"
    if not os.path.exists(csv_path):
        build_training_dataset()
        
    # Read the dataset
    data = pd.read_csv(csv_path)
    X = data["text"]
    y = data["category"]
    
    # Split into train/test
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    
    # Build pipeline: TF-IDF vectorizer + Logistic Regression model
    # char n-grams capture spelling bits (swiggy vs swiggy123); word n-grams capture words
    model = Pipeline([
        ("tfidf", TfidfVectorizer(
            ngram_range=(1, 3), 
            analyzer="word", 
            lowercase=True,
            sublinear_tf=True
        )),
        ("clf", LogisticRegression(
            max_iter=1000, 
            class_weight="balanced", 
            C=5.0
        ))
    ])
    
    print("Training model...")
    model.fit(X_train, y_train)
    
    # Evaluate
    predictions = model.predict(X_test)
    print("\nModel Evaluation Report:")
    print(classification_report(y_test, predictions))
    
    # Save the pickle file
    model_path = "model/expense_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    print(f"Model trained and saved successfully at {model_path}!")
    
    # Save classes
    classes_path = "model/categories.json"
    with open(classes_path, "w", encoding="utf-8") as f:
        json.dump(list(model.classes_), f, indent=4)
    print(f"Saved categories list at {classes_path}")

if __name__ == "__main__":
    train_model()
