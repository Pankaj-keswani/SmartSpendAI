import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
import pickle

# Load dataset
data = pd.read_csv("data/training_data.csv")

X = data["text"]
y = data["category"]

# Build pipeline
model = Pipeline([
    ('tfidf', TfidfVectorizer(ngram_range=(1,2), lowercase=True)),
    ('clf', LogisticRegression())
])

# Train model
model.fit(X, y)

# Save model
with open("model/expense_model.pkl", "wb") as f:
    pickle.dump(model, f)

print("🎯 Model trained & saved successfully!")
