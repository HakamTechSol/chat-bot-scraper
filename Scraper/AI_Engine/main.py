from fastapi import FastAPI
import json
import os
import torch
import re
from sentence_transformers import SentenceTransformer, util
import nltk

# NLTK requirements
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

app = FastAPI()

# 1. Model & Data Load (Sirf SentenceTransformer use karein)
print("🚀 Loading AI Model...")
model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

JSON_PATH = "../FullWikiData.json"
corpus_data = []
corpus_embeddings = None
def load_knowledge_base():
    global corpus_data, corpus_embeddings
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, 'r', encoding='utf-8') as f:
                # File empty check
                content = f.read().strip()
                if not content:
                    print("❌ Error: JSON file khali (empty) hai!")
                    return
                
                data = json.loads(content)
                corpus_data = [page for page in data if 'Content' in page]
                corpus_text = [page['Content'] for page in corpus_data]
                
                print("🧠 Encoding Knowledge Base... Please wait.")
                corpus_embeddings = model.encode(corpus_text, convert_to_tensor=True)
                print(f"✅ Ready! {len(corpus_data)} pages indexed.")
        except json.JSONDecodeError as e:
            print(f"❌ JSON Format Error: Aapki file '{JSON_PATH}' sahi format mein nahi hai.")
            print(f"Detail: {str(e)}")
        except Exception as e:
            print(f"❌ Error loading knowledge base: {str(e)}")

load_knowledge_base()

def clean_text(text):
    if not text: return ""
    text = text.replace("Copied!", "").replace("1 min read", "").replace("4 min read", "")
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def generate_better_answer(content, question):
    """Summarize using sentence extraction (Fast & Error-free)"""
    try:
        sentences = nltk.sent_tokenize(content)
        # Pehli 4 sentences uthayein as a summary
        summary = ' '.join(sentences[:4]) if len(sentences) > 4 else content
        return f"Σύμφωνα με το SoftOne Wiki: {summary}"
    except Exception:
        return content[:500]  # Fallback: Just return characters if NLTK fails

@app.get("/ask")
def ask_ai(question: str):
    try:
        # Greetings logic
        user_query = question.lower().strip()
        greetings = ["hi", "hello", "hey", "γειά", "γεια"]
        if any(greet in user_query for greet in greetings):
            return {"answer": "Γεια σας! Πώς μπορώ να σας βοηθήσω με το SoftOne σήμερα;", "url": None}

        # Search
        question_embedding = model.encode(question, convert_to_tensor=True)
        hits = util.semantic_search(question_embedding, corpus_embeddings, top_k=1)
        
        score = hits[0][0]['score']
        if score < 0.25:
            return {"answer": "Λυπούμαστε, δεν βρήκαμε κάτι σχετικό.", "url": None}

        best_match = corpus_data[hits[0][0]['corpus_id']]
        cleaned_content = clean_text(best_match['Content'])
        
        # Jawab generate karein (NLTK summarization)
        better_answer = generate_better_answer(cleaned_content, question)

        return {
            "answer": better_answer,
            "url": best_match.get('Url'),
            "score": float(score)
        }
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return {"answer": "System error occurred.", "url": None}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)