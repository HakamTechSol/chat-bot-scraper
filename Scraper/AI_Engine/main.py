import os
import json
import re
import unicodedata
import math
from typing import Optional, List, Tuple
from dotenv import load_dotenv
from functools import lru_cache
from datetime import datetime, timedelta
from collections import Counter

from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import google.generativeai as genai
from fastapi import HTTPException

# ──────────────────────────────────────────────
# 1.  CONFIGURATION
# ──────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing! Set it in a .env file or as an environment variable."
    )

# Override via env var if you want a different Gemini model name.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_COOLDOWN_SECONDS = int(os.getenv("GEMINI_COOLDOWN_SECONDS", "60"))

# Configure Google Generative AI SDK
genai.configure(api_key=GEMINI_API_KEY)

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "FullWikiData.json")

RESPONSE_CACHE: dict = {}
CACHE_EXPIRY_HOURS = 1

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150
MIN_CHUNK_LENGTH = 100
BM25_K1 = 1.5
BM25_B = 0.75
MIN_CONFIDENCE_THRESHOLD = 5.0

# Gemini model configuration
generation_config = genai.GenerationConfig(
    temperature=0.2,
    max_output_tokens=2048,
    response_mime_type="application/json"
)

_gemini_cooldown_until: Optional[datetime] = None

# ──────────────────────────────────────────────
# 2.  DATA STRUCTURES
# ──────────────────────────────────────────────

class Chunk:
    def __init__(self, text: str, page_index: int, page_title: str, page_url: str, page_timestamp: str, chunk_index: int):
        self.text = text
        self.page_index = page_index
        self.page_title = page_title
        self.page_url = page_url
        self.page_timestamp = page_timestamp
        self.chunk_index = chunk_index
        self.tokens = []

    def __repr__(self):
        return f"Chunk(page={self.page_index}, idx={self.chunk_index}, len={len(self.text)})"

# ──────────────────────────────────────────────
# 3.  LOAD AND CHUNK KNOWLEDGE BASE
# ──────────────────────────────────────────────

corpus_data: List[dict] = []
chunks: List[Chunk] = []
chunk_index_by_page: dict = {}
corpus_index: dict = {}

def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace for matching."""
    return re.sub(r"\s+", " ", text.lower().strip())

def _tokenize(text: str) -> List[str]:
    """Tokenize text for search indexing."""
    if not text:
        return []
    return re.findall(r"[a-z0-9\u0370-\u03FF]+", _normalize(text))

def _chunk_text(text: str, page_idx: int, title: str, url: str, timestamp: str) -> List[Chunk]:
    """Split page content into overlapping semantic chunks."""
    page_chunks = []
    
    if not text or len(text) < MIN_CHUNK_LENGTH:
        if text and len(text.strip()) > 20:
            chunk = Chunk(text.strip(), page_idx, title, url, timestamp, 0)
            page_chunks.append(chunk)
        return page_chunks
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current_chunk_text = ""
    chunk_idx = 0
    
    for sentence in sentences:
        if len(current_chunk_text) + len(sentence) <= CHUNK_SIZE:
            current_chunk_text += " " + sentence if current_chunk_text else sentence
        else:
            if current_chunk_text.strip():
                chunk = Chunk(current_chunk_text.strip(), page_idx, title, url, timestamp, chunk_idx)
                page_chunks.append(chunk)
                chunk_idx += 1
            
            if len(sentence) > CHUNK_SIZE:
                for i in range(0, len(sentence), CHUNK_SIZE - CHUNK_OVERLAP):
                    sub_chunk = sentence[i:i + CHUNK_SIZE]
                    if len(sub_chunk) >= MIN_CHUNK_LENGTH:
                        chunk = Chunk(sub_chunk.strip(), page_idx, title, url, timestamp, chunk_idx)
                        page_chunks.append(chunk)
                        chunk_idx += 1
                current_chunk_text = ""
            else:
                current_chunk_text = sentence
    
    if current_chunk_text.strip():
        chunk = Chunk(current_chunk_text.strip(), page_idx, title, url, timestamp, chunk_idx)
        page_chunks.append(chunk)
    
    return page_chunks

def load_knowledge_base() -> None:
    """Load, chunk, and index FullWikiData.json."""
    global corpus_data, chunks, chunk_index_by_page, corpus_index
    abs_path = os.path.abspath(JSON_PATH)

    if not os.path.exists(abs_path):
        print(f"Knowledge file not found: {abs_path}")
        return

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                print("JSON file is empty!")
                return
            data = json.loads(raw)

        corpus_data = [page for page in data if page.get("Content", "").strip()]
        
        chunks = []
        chunk_index_by_page = {}
        
        for idx, page in enumerate(corpus_data):
            title = page.get("Title", "")
            url = page.get("Url", "")
            timestamp = page.get("ScrapedAt", "")
            content = page.get("Content", "")
            
            page_chunks = _chunk_text(content, idx, title, url, timestamp)
            
            if page_chunks:
                chunk_index_by_page[idx] = [len(chunks) + i for i in range(len(page_chunks))]
                chunks.extend(page_chunks)
        
        corpus_index = {}
        for cidx, chunk in enumerate(chunks):
            chunk.tokens = _tokenize(chunk.text)
            chunk_tokens = set(chunk.tokens)
            
            for token in chunk_tokens:
                if len(token) >= 3:
                    corpus_index.setdefault(token, []).append(cidx)
        
        print(f"Knowledge base loaded: {len(corpus_data)} pages, {len(chunks)} chunks indexed.")
    except Exception as exc:
        print(f"Error loading knowledge base: {exc}")

# ──────────────────────────────────────────────
# 4.  BM25 RANKING IMPLEMENTATION
# ──────────────────────────────────────────────

avg_doc_length: float = 0.0
doc_lengths: List[int] = []
document_frequencies: dict = {}
total_docs: int = 0

def _compute_bm25_stats() -> None:
    """Pre-compute BM25 statistics."""
    global avg_doc_length, doc_lengths, document_frequencies, total_docs
    
    if not chunks:
        return
    
    total_docs = len(chunks)
    doc_lengths = [len(chunk.text) for chunk in chunks]
    avg_doc_length = sum(doc_lengths) / total_docs if total_docs > 0 else 1
    
    document_frequencies = Counter()
    for chunk in chunks:
        unique_tokens = set(chunk.tokens)
        for token in unique_tokens:
            document_frequencies[token] += 1

def _bm25_score(chunk: Chunk, query_tokens: List[str]) -> float:
    """Calculate BM25 score for a chunk given query tokens."""
    if not query_tokens or avg_doc_length == 0:
        return 0.0
    
    score = 0.0
    chunk_len = len(chunk.text)
    chunk_token_set = set(chunk.tokens)
    
    for token in query_tokens:
        if token not in chunk_token_set:
            continue
        
        tf = chunk.tokens.count(token)
        df = document_frequencies.get(token, 0)
        
        if df == 0:
            continue
            
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1)
        
        numerator = tf * (BM25_K1 + 1)
        denominator = tf + BM25_K1 * (1 - BM25_B + BM25_B * chunk_len / avg_doc_length)
        
        score += idf * (numerator / denominator)
    
    return score

# ──────────────────────────────────────────────
# 5.  LANGUAGE DETECTION
# ──────────────────────────────────────────────

_GREEK_RANGE = set(range(0x0370, 0x0400)) | set(range(0x1F00, 0x2000))
_TOKEN_RE = re.compile(r"[a-z0-9\u0370-\u03FF]+", re.IGNORECASE)
_EN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does",
    "for", "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "please", "tell", "that", "the", "to", "what", "when", "where", "which",
    "who", "why", "with", "you", "your", "soft1", "softone", "wiki",
}
_GR_STOPWORDS = {
    "και", "να", "σε", "στο", "στη", "τι", "που", "πως", "για", "με", "το", "τη",
    "της", "των", "ένα", "μια", "είναι", "soft1", "softone", "wiki",
}

def detect_language(text: str) -> str:
    """Detect language: 'Greek' | 'English'."""
    greek_count = 0
    latin_count = 0
    
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if cp in _GREEK_RANGE:
            greek_count += 1
        elif unicodedata.category(ch).startswith("L"):
            latin_count += 1
    
    max_count = max(greek_count, latin_count)
    if max_count == 0:
        return "English"
    if greek_count == max_count:
        return "Greek"
    return "English"

def _extract_query_tokens(query: str) -> List[str]:
    """Extract meaningful query tokens by removing stopwords."""
    language = detect_language(query)
    stopwords = _GR_STOPWORDS if language == "Greek" else _EN_STOPWORDS

    tokens = []
    for token in _tokenize(query):
        if len(token) < 2:
            continue
        if token in stopwords:
            continue
        tokens.append(token)

    if not tokens:
        tokens = [t for t in _tokenize(query) if len(t) >= 2][:5]
    return tokens

# ──────────────────────────────────────────────
# 6.  GREETING DETECTION
# ──────────────────────────────────────────────

_ENGLISH_GREETINGS = {
    "hi", "hello", "hey", "hola", "ciao", "greetings",
    "how are you", "whats up", "sup", "hiya", "howdy"
}

_GREEK_GREETINGS = {
    "γειά", "καλημέρα", "καλησπέρα", "γεια σας"
}

def is_greeting(text: str, language: Optional[str] = None) -> bool:
    """Enhanced greeting detection."""
    normalized = text.strip().lower()
    
    if len(normalized) <= 25:
        cleaned = re.sub(r"[^a-zA-Z0-9\s\u0370-\u03FF]", "", normalized)
        tokens = cleaned.split()
        
        if not tokens:
            return False
        
        first_token = tokens[0]
        
        if first_token in _ENGLISH_GREETINGS:
            return True
        
        if any(greeting in normalized for greeting in _GREEK_GREETINGS):
            return True
    
    return False

GREETING_RESPONSES = {
    "English": (
        "Hello! I'm the XIT Cognitive Support Agent, a Senior SoftOne ERP Consultant. "
        "I can analyze your questions and provide intelligent solutions based on the official SoftOne documentation. "
        "How can I assist you today?"
    ),
    "Greek": (
        "Γειά σας! Είμαι ο XIT Cognitive Support Agent, Senior Σύμβουλος SoftOne ERP. "
        "Μπορώ να αναλύσω τις ερωτήσεις σας και να παρέχω έξυπνες λύσεις βασισμένες στην επίσημη τεκμηρίωση του SoftOne. "
        "Πώς μπορώ να σας εξυπηρετήσω σήμερα;"
    ),
}

# ──────────────────────────────────────────────
# 7.  RESPONSE CACHING
# ──────────────────────────────────────────────

def _get_cache_key(question: str, language: str) -> str:
    """Generate cache key from normalized question."""
    normalized = _normalize(question)
    return f"{language}:{normalized}"

def _is_cache_valid(timestamp: datetime) -> bool:
    """Check if cached response is still valid."""
    return datetime.now() - timestamp < timedelta(hours=CACHE_EXPIRY_HOURS)

def _get_from_cache(question: str, language: str) -> Optional[dict]:
    """Retrieve cached response if available and valid."""
    key = _get_cache_key(question, language)
    if key in RESPONSE_CACHE:
        cached = RESPONSE_CACHE[key]
        if _is_cache_valid(cached["timestamp"]):
            return cached["response"]
        else:
            del RESPONSE_CACHE[key]
    return None

def _store_in_cache(question: str, language: str, response: dict) -> None:
    """Store response in cache with timestamp."""
    key = _get_cache_key(question, language)
    RESPONSE_CACHE[key] = {
        "response": response,
        "timestamp": datetime.now()
    }

# ──────────────────────────────────────────────
# 8.  QUERY INTENT DETECTION
# ──────────────────────────────────────────────

QUESTION_INTENTS = {
    "definition": ["what is", "what are", "what does", "what's", "define", "explain what", "tell me about", "τι είναι", "τι είναι το", "ποια είναι"],
    "howto": ["how do i", "how can i", "how to", "how does", "how does", "πώς", "πως να", "πως μπορώ"],
    "list": ["which", "what modules", "what features", "list", "ποια", "ποιες", "λίστα"],
    "comparison": ["difference between", "vs", "versus", "compared to", "διαφορά", "σύγκριση"],
    "feature": ["features", "capabilities", "functions", " functionalities", "λειτουργίες", "δυνατότητες"],
    "setup": ["setup", "configure", "install", "enable", "activate", "ρύθμιση", "ενεργοποίηση", "εγκατάσταση"],
}

def detect_intent(query: str) -> str:
    """Detect the intent of the user's question."""
    query_lower = query.lower()
    
    for intent, patterns in QUESTION_INTENTS.items():
        for pattern in patterns:
            if pattern in query_lower:
                return intent
    
    return "definition"

# ──────────────────────────────────────────────
# 9.  HYBRID SEARCH WITH BM25
# ──────────────────────────────────────────────

def search_chunks_scored(query: str, top_k: int = 12) -> List[Tuple[float, Chunk]]:
    """Hybrid search using BM25 ranking with title boosting."""
    query_tokens = _extract_query_tokens(query)
    
    if not query_tokens:
        return []
    
    candidate_indices = set()
    for token in query_tokens:
        if len(token) >= 2 and token in corpus_index:
            candidate_indices.update(corpus_index[token])
    
    if not candidate_indices:
        candidate_indices = set(range(len(chunks)))
    
    scored = []
    
    for cidx in candidate_indices:
        chunk = chunks[cidx]
        
        bm25_score = _bm25_score(chunk, query_tokens)
        
        title_boost = 0.0
        title_tokens = _tokenize(corpus_data[chunk.page_index].get("Title", ""))
        for token in query_tokens:
            if token in title_tokens:
                title_boost += 5.0
        
        exact_phrase_boost = 0.0
        query_norm = _normalize(query)
        chunk_text_norm = _normalize(chunk.text)
        if query_norm in chunk_text_norm:
            exact_phrase_boost = 10.0
        elif any(qt + " " + qt2 in chunk_text_norm for qt, qt2 in zip(query_tokens[:-1], query_tokens[1:])):
            exact_phrase_boost = 5.0
        
        position_boost = 0.0
        first_token_pos = float('inf')
        for token in query_tokens:
            pos = chunk_text_norm.find(token)
            if pos != -1 and pos < first_token_pos:
                first_token_pos = pos
        
        if first_token_pos < 200:
            position_boost = 3.0
        elif first_token_pos < 500:
            position_boost = 1.5
        
        total_score = bm25_score + title_boost + exact_phrase_boost + position_boost
        
        if total_score > 0:
            scored.append((total_score, chunk))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]

def _get_unique_pages(chunks_with_scores: List[Tuple[float, Chunk]], max_pages: int = 3) -> List[Tuple[float, dict, List[Chunk]]]:
    """Get top unique pages from scored chunks."""
    page_scores = {}
    
    for score, chunk in chunks_with_scores:
        page_idx = chunk.page_index
        if page_idx not in page_scores:
            page_scores[page_idx] = {'score': score, 'chunks': [], 'chunk_indices': set()}
        
        if len(page_scores[page_idx]['chunks']) < 3:
            page_scores[page_idx]['chunks'].append(chunk)
            page_scores[page_idx]['chunk_indices'].add((chunk.page_index, chunk.chunk_index))
        else:
            if score > page_scores[page_idx]['score']:
                page_scores[page_idx]['score'] = score
    
    sorted_pages = sorted(page_scores.items(), key=lambda x: x[1]['score'], reverse=True)
    
    result = []
    for page_idx, data in sorted_pages[:max_pages]:
        page = corpus_data[page_idx]
        result.append((data['score'], page, data['chunks']))
    
    return result

# ──────────────────────────────────────────────
# 9.  XIT COGNITIVE SUPPORT AGENT SYSTEM PROMPT
# ──────────────────────────────────────────────

SYSTEM_INSTRUCTION = """\
You are the XIT Cognitive Agent, a senior SoftOne ERP technical consultant.

Your job is to analyze user questions and provide logical troubleshooting answers, not to copy documentation.

You may receive documentation excerpts from the SoftOne wiki.
Use them only as background knowledge.

CRITICAL RULES

* NEVER copy sentences from the documentation.
* NEVER behave like a search engine.
* ALWAYS explain the solution in your own words.
* Focus on diagnosing the user's issue and providing practical steps.

CLIENT-SPECIFIC BUSINESS LOGIC (MANDATORY)

1. SoftOne Webservices Check

If a user asks how to check whether SoftOne webservices are working:

You MUST instruct them to test the service endpoint in a browser.

Example:
http://Demo.oncloud.gr/s1service

Explain that:

* If the URL returns XML or service data → the webservice is working.
* If there is no response or an error → the webservice may be down or misconfigured.

2. Wrong VAT Error

If the user mentions:

* Wrong VAT
* VAT validation error
* Invoice rejected due to VAT

Explain that:

* The customer's VAT number may be incorrect.
* It should be verified with the Greek tax authority (GSIS).
* SoftOne ERP validates VAT through the GSIS system.

RESPONSE STYLE

Be concise and practical like a real ERP consultant.

Do NOT say:
"Based on the documentation..."

Instead explain the issue directly.

RESPONSE STRUCTURE

Explanation:
Explain what the problem likely is.

Steps to Check:
1. Step one
2. Step two
3. Step three

Reference (optional):
Mention the wiki source if helpful.

OUTPUT FORMAT (STRICT JSON):
{
  "answer": "Explanation: ...\nSteps to Check: 1. ...\nReference: ...",
  "source_link": "The URL of the most relevant page.",
  "data_timestamp": "The ScrapedAt value.",
  "language": "Detect query language (English/Greek).",
  "status": "found/not_found"
}
"""

# ──────────────────────────────────────────────
# 10.  CONTEXT ASSEMBLY
# ──────────────────────────────────────────────

def build_context_for_llm(question: str, ranked_pages: List[Tuple[float, dict, List[Chunk]]]) -> str:
    """Build context block with top chunks organized by relevance."""
    if not ranked_pages:
        return "NO RELEVANT PAGES FOUND."
    
    blocks = []
    
    for rank, (score, page, page_chunks) in enumerate(ranked_pages, 1):
        title = page.get('Title', 'N/A')
        url = page.get('Url', 'N/A')
        timestamp = page.get('ScrapedAt', 'N/A')
        
        chunks_text = "\n".join([f"[Chunk {i+1}] {chunk.text}" for i, chunk in enumerate(page_chunks)])
        
        block = (
            f"Source {rank}:\n"
            f"Title: {title}\n"
            f"Url: {url}\n"
            f"ScrapedAt: {timestamp}\n"
            f"Content:\n{chunks_text}\n"
        )
        
        blocks.append(block)
    
    combined = "\n---\n".join(blocks)
    
    max_context = 4500
    if len(combined) > max_context:
        combined = combined[:max_context] + "\n...[truncated]..."
    
    return combined

# ──────────────────────────────────────────────
# 11.  ENHANCED ANSWER GENERATION
# ──────────────────────────────────────────────

def _extract_json_from_text(text: str) -> Optional[dict]:
    """Safely extract JSON from any text response."""
    # Try direct JSON parse first
    try:
        return json.loads(text)
    except:
        pass
    
    # Try markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) > 1:
            json_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            try:
                return json.loads(json_text)
            except:
                pass
    
    # Try to find JSON object in text
    start = text.find('{')
    if start != -1:
        depth = 0
        end = start
        for i, char in enumerate(text[start:], start):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        json_str = text[start:end]
        try:
            return json.loads(json_str)
        except:
            pass
    
    return None

def _build_user_prompt(question: str, context: str, language: str) -> str:
    """Build the user prompt with clear instructions."""
    return f"""\
USER QUESTION

{question}

DOCUMENTATION CONTEXT

{context}

Provide a logical support answer.

Do NOT copy from documentation. Explain in your own words.

Response format:
Explanation: [Explain the issue]
Steps to Check: [1, 2, 3...]
Reference: [Wiki URL if relevant]

Return ONLY raw JSON:
{{"answer": "...", "source_link": "...", "data_timestamp": "...", "language": "...", "status": "..."}}"""

# ──────────────────────────────────────────────
# 12.  MAIN QUESTION HANDLING
# ──────────────────────────────────────────────

def ask_gemini(question: str, language: str) -> dict:
    """Process question with improved retrieval and answer synthesis."""
    global _gemini_cooldown_until
    cached_response = _get_from_cache(question, language)
    if cached_response:
        print(f"Cache hit for: {question}")
        return cached_response

    if is_greeting(question, language):
        response = {
            "answer": GREETING_RESPONSES.get(language, GREETING_RESPONSES["English"]),
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "greeting",
        }
        _store_in_cache(question, language, response)
        return response

    scored_chunks = search_chunks_scored(question, top_k=10)
    
    if not scored_chunks:
        response = {
            "answer": "I'm sorry, the current documentation does not have enough information to answer this specifically.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "not_found",
        }
        _store_in_cache(question, language, response)
        return response

    top_score = scored_chunks[0][0]
    
    if top_score < MIN_CONFIDENCE_THRESHOLD:
        response = {
            "answer": "I'm sorry, the current documentation does not have enough information to answer this specifically.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "not_found",
        }
        _store_in_cache(question, language, response)
        return response

    ranked_pages = _get_unique_pages(scored_chunks, max_pages=3)
    
    context_block = build_context_for_llm(question, ranked_pages)
    
    user_prompt = _build_user_prompt(question, context_block, language)

    now = datetime.utcnow()
    if _gemini_cooldown_until and now < _gemini_cooldown_until:
        remaining = int((_gemini_cooldown_until - now).total_seconds())
        return {
            "answer": f"Gemini is rate-limiting right now. Please wait {max(1, remaining)} seconds and try again.",
            "source_link": ranked_pages[0][1].get("Url", "") if ranked_pages else "",
            "data_timestamp": ranked_pages[0][1].get("ScrapedAt", "") if ranked_pages else "",
            "language": language,
            "status": "rate_limited",
        }

    # Use Google Generative AI SDK
    try:
        print(f"Calling Gemini API for: {question}")
        
        # Create the model with system instruction
        model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_INSTRUCTION,
            generation_config=generation_config
        )
        
        # Generate content
        response = model.generate_content(user_prompt)
        
        # Get the full response text - use combined text if available
        raw_text = ""
        if hasattr(response, 'text') and response.text:
            raw_text = response.text
        elif hasattr(response, 'parts'):
            raw_text = "".join([part.text for part in response.parts if hasattr(part, 'text')])
        
        raw_text = raw_text.strip()
        
        if not raw_text:
            return {
                "answer": "No response from Gemini API",
                "source_link": ranked_pages[0][1].get("Url", "") if ranked_pages else "",
                "data_timestamp": ranked_pages[0][1].get("ScrapedAt", "") if ranked_pages else "",
                "language": language,
                "status": "error"
            }
        
        # Try to parse as JSON
        result = None
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            result = _extract_json_from_text(raw_text)
        
        if result is None:
            # Return plain text as JSON - full response without truncation
            result = {
                "answer": raw_text,
                "source_link": ranked_pages[0][1].get("Url", "") if ranked_pages else "",
                "data_timestamp": ranked_pages[0][1].get("ScrapedAt", "") if ranked_pages else "",
                "language": language,
                "status": "found"
            }
        
        # Ensure required fields
        result["answer"] = result.get("answer", "")
        result["source_link"] = result.get("source_link", "")
        result["data_timestamp"] = result.get("data_timestamp", "")
        result["language"] = result.get("language", language)
        result["status"] = result.get("status", "found")

        if not result.get("source_link") and ranked_pages:
            result["source_link"] = ranked_pages[0][1].get("Url", "")
        if not result.get("data_timestamp") and ranked_pages:
            result["data_timestamp"] = ranked_pages[0][1].get("ScrapedAt", "")

        if result.get("status") == "not_found" and ranked_pages and top_score >= 8.0:
            best_chunk = ranked_pages[0][2][0] if ranked_pages[0][2] else None
            if best_chunk:
                snippet = best_chunk.text[:400].strip()
                if language == "Greek":
                    fallback = f"Με βάση την τεκμηρίωση: {snippet}..."
                else:
                    fallback = f"Based on the documentation: {snippet}..."
                
                result = {
                    "answer": fallback,
                    "source_link": ranked_pages[0][1].get("Url", ""),
                    "data_timestamp": ranked_pages[0][1].get("ScrapedAt", ""),
                    "language": language,
                    "status": "found",
                }

        _store_in_cache(question, language, result)
        return result

    except json.JSONDecodeError as e:
        print(f"JSON Parse Error: {e}")
        if ranked_pages:
            best_chunk = ranked_pages[0][2][0] if ranked_pages[0][2] else None
            if best_chunk:
                return {
                    "answer": f"Based on the documentation: {best_chunk.text[:300]}...",
                    "source_link": ranked_pages[0][1].get("Url", ""),
                    "data_timestamp": ranked_pages[0][1].get("ScrapedAt", ""),
                    "language": language,
                    "status": "found",
                }
        return {
            "answer": "Error processing response. Please try again.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "error",
        }
    except Exception as exc:
        print(f"Gemini API Error: {exc}")
        if ranked_pages:
            best_chunk = ranked_pages[0][2][0] if ranked_pages[0][2] else None
            if best_chunk:
                return {
                    "answer": f"Based on the documentation: {best_chunk.text[:300]}...",
                    "source_link": ranked_pages[0][1].get("Url", ""),
                    "data_timestamp": ranked_pages[0][1].get("ScrapedAt", ""),
                    "language": language,
                    "status": "found",
                }
        return {
            "answer": f"System error: {str(exc)}",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "error",
        }


# ──────────────────────────────────────────────
# 13.  FASTAPI APPLICATION
# ──────────────────────────────────────────────

load_knowledge_base()
_compute_bm25_stats()

app = FastAPI(
    title="XIT Cognitive Support Agent",
    description="Senior SoftOne ERP Consultant - Analyzes documentation and provides intelligent solutions",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ask", response_class=PlainTextResponse)
async def ask_ai(question: str = Query(..., description="User question")):
    """
    Main endpoint for AI queries.
    
    Features:
    - Chunk-based semantic retrieval with BM25 ranking
    - Multi-language support (English, Greek)
    - Answer synthesis from multiple sources
    - Confidence threshold for weak matches
    - Response caching
    """
    if not corpus_data:
        return json.dumps({
            "answer": "Knowledge base not loaded.",
            "source_link": "",
            "data_timestamp": "",
            "language": "English",
            "status": "error",
        }, ensure_ascii=False)

    language = detect_language(question)
    result = ask_gemini(question, language)

    return json.dumps(result, ensure_ascii=False)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "knowledge_base_loaded": len(corpus_data) > 0,
        "total_pages": len(corpus_data),
        "total_chunks": len(chunks),
        "cache_entries": len(RESPONSE_CACHE),
        "engine": "Google Gemini 2.0 Flash",
        "version": "3.0.0",
    }


@app.get("/reload")
async def reload_knowledge_base():
    """Reload FullWikiData.json without restarting."""
    load_knowledge_base()
    _compute_bm25_stats()
    RESPONSE_CACHE.clear()
    return {
        "status": "reloaded",
        "total_pages": len(corpus_data),
        "total_chunks": len(chunks),
    }


@app.get("/languages")
async def supported_languages():
    """Return supported languages."""
    return {
        "supported": ["English", "Greek"],
        "auto_detection": True,
    }


@app.get("/cache/clear")
async def clear_cache():
    """Clear response cache."""
    RESPONSE_CACHE.clear()
    return {"status": "cache cleared"}


@app.get("/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    return {
        "total_cached": len(RESPONSE_CACHE),
        "cache_size": sum(len(str(v)) for v in RESPONSE_CACHE.values()),
    }


@app.get("/test")
async def test_questions():
    """Sample questions for testing the QA system."""
    return {
        "english": [
            {
                "question": "What is the Projects module in SoftOne?",
                "expected_behavior": "Should explain what Projects module is, its purpose and resources"
            },
            {
                "question": "How do I configure myDATA in Atlantis?",
                "expected_behavior": "Should provide configuration steps from documentation"
            },
            {
                "question": "What is Soft1 B2B?",
                "expected_behavior": "Should explain the B2B module functionality"
            }
        ],
        "greek": [
            {
                "question": "Τι είναι το module Χρηματοοικονομικά;",
                "expected_behavior": "Should explain Financials module in Greek"
            },
            {
                "question": "Πώς ρυθμίζω το myDATA στο SoftOne;",
                "expected_behavior": "Should provide myDATA setup instructions in Greek"
            }
        ]
    }


@app.get("/debug/search")
async def debug_search(q: str = Query(..., description="Search query")):
    """Debug endpoint to inspect search results."""
    scored = search_chunks_scored(q, top_k=5)
    ranked_pages = _get_unique_pages(scored, max_pages=2)
    
    return {
        "query": q,
        "query_tokens": _extract_query_tokens(q),
        "top_chunks": [
            {
                "score": score,
                "chunk_preview": chunk.text[:150] + "...",
                "page_title": corpus_data[chunk.page_index].get("Title", ""),
                "page_url": chunk.page_url
            }
            for score, chunk in scored[:5]
        ],
        "ranked_pages": [
            {
                "score": score,
                "title": page.get("Title", ""),
                "url": page.get("Url", ""),
                "num_chunks": len(page_chunks)
            }
            for score, page, page_chunks in ranked_pages
        ]
    }


# ──────────────────────────────────────────────
# 14.  RUN
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("Starting XIT Cognitive Support Agent v4.0.0...")
    print(f"Loaded: {len(corpus_data)} pages, {len(chunks)} chunks")
    print("Supported Languages: English, Greek")
    print("Features: BM25 Ranking, Chunk-based Retrieval, Cognitive Analysis, Actionable Solutions")
    uvicorn.run(app, host="127.0.0.1", port=8000)
