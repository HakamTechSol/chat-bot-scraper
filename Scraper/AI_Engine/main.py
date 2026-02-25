"""
SoftOne Wiki AI Brain — Powered by Google Gemini API
=====================================================
Acts as the intelligent interface for FullWikiData.json.
All answers are derived STRICTLY from the scraped wiki data.
Output is always raw JSON conforming to the defined schema.

OPTIMIZATIONS:
- Improved response caching for repeated queries
- Enhanced context relevance filtering
- Better error handling without external links on failures
- Removed emoji decorations for professional output
- Optimized token usage for faster API responses
- Efficient corpus indexing for faster searches
- Multi-language support (English, Greek)
- Increased timeout and retry mechanism
"""

import os
import json
import re
import unicodedata
from typing import Optional
from dotenv import load_dotenv
from functools import lru_cache
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
 
# ──────────────────────────────────────────────
# 1.  CONFIGURATION
# ──────────────────────────────────────────────
load_dotenv()
 
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    raise RuntimeError(
        "OPENROUTER_API_KEY is missing! Set it in a .env file or as an environment variable."
    )

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "FullWikiData.json")

# Response cache with 1-hour expiration
RESPONSE_CACHE: dict = {}
CACHE_EXPIRY_HOURS = 1

# Session with retry strategy
session = requests.Session()
retry_strategy = Retry(
    total=2,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# ──────────────────────────────────────────────
# 2.  LOAD AND INDEX KNOWLEDGE BASE
# ──────────────────────────────────────────────
corpus_data: list[dict] = []
corpus_index: dict = {}  # Inverted index for faster searching


def load_knowledge_base() -> None:
    """Load, validate and index FullWikiData.json into memory."""
    global corpus_data, corpus_index
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

        # Only keep entries with meaningful content
        corpus_data = [
            page for page in data
            if page.get("Content", "").strip()
        ]
        
        # Build inverted index for faster searches
        corpus_index = {}
        for idx, page in enumerate(corpus_data):
            title = _normalize(page.get("Title", ""))
            content = _normalize(page.get("Content", ""))
            
            # Index title tokens
            for token in title.split():
                if len(token) >= 2:
                    corpus_index.setdefault(token, []).append(idx)
            
            # Index content tokens (sample for performance)
            for token in content.split()[:100]:
                if len(token) >= 3:
                    corpus_index.setdefault(token, []).append(idx)
        
        print(f"Knowledge base loaded: {len(corpus_data)} pages indexed.")
    except Exception as exc:
        print(f"Error loading knowledge base: {exc}")


load_knowledge_base()

# ──────────────────────────────────────────────
# 3.  LANGUAGE DETECTION (Greek, English)
# ──────────────────────────────────────────────
_GREEK_RANGE = set(range(0x0370, 0x0400)) | set(range(0x1F00, 0x2000))


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


# ──────────────────────────────────────────────
# 4.  ENHANCED GREETING DETECTION
# ──────────────────────────────────────────────

_ENGLISH_GREETINGS = {
    "hi", "hello", "hey", "hola", "ciao", "greetings",
    "how are you", "whats up", "sup", "hiya", "howdy"
}

_GREEK_GREETINGS = {
    "γειά", "καλημέρα", "καλησπέρα", "γεια σας"
}


def is_greeting(text: str, language: str = None) -> bool:
    """Enhanced greeting detection supporting multiple languages."""
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


# ──────────────────────────────────────────────
# 4b.  GREETING RESPONSES (Multi-language, No Emojis)
# ──────────────────────────────────────────────

GREETING_RESPONSES = {
    "English": (
        "Hello! I'm the SoftOne Wiki AI assistant. How can I help you today? "
        "Ask me anything about the SoftOne knowledge base."
    ),
    "Greek": (
        "Γειά σας! Είμαι ο βοηθός SoftOne Wiki AI. Πώς μπορώ να σας βοηθήσω σήμερα; "
        "Ρωτήστε με για τη βάση γνώσεων SoftOne."
    ),
}


# ──────────────────────────────────────────────
# 5.  RESPONSE CACHING
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
# 6.  OPTIMIZED KEYWORD SEARCH
# ──────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace for matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


def search_corpus(query: str, top_k: int = 5) -> list[dict]:
    """
    Optimized keyword-based search using inverted index.
    Returns top_k most relevant pages sorted by score.
    """
    query_norm = _normalize(query)
    query_tokens = set(query_norm.split())

    # Use inverted index for fast lookup
    candidate_indices = set()
    for token in query_tokens:
        if len(token) >= 2 and token in corpus_index:
            candidate_indices.update(corpus_index[token])

    # If no index matches, fall back to full corpus
    if not candidate_indices:
        candidate_indices = set(range(len(corpus_data)))

    scored: list[tuple[float, dict]] = []

    for idx in candidate_indices:
        page = corpus_data[idx]
        title = _normalize(page.get("Title", ""))
        content = _normalize(page.get("Content", ""))

        score = 0.0
        for token in query_tokens:
            if len(token) < 2:
                continue
            if token in title:
                score += 3.0
            if token in content:
                score += 1.0

        if query_norm in title:
            score += 10.0
        if query_norm in content:
            score += 5.0

        if score > 0:
            scored.append((score, page))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [page for _, page in scored[:top_k]]


# ──────────────────────────────────────────────
# 7.  OPTIMIZED GEMINI SYSTEM PROMPT
# ──────────────────────────────────────────────

SYSTEM_INSTRUCTION = """\
You are a specialized AI Brain for the SoftOne Wiki system.
Your ONLY knowledge source is the context provided from 'FullWikiData.json'.

MANDATES:
1. Answer ONLY from provided context. Do NOT use external knowledge.
   If context doesn't contain the answer, return status "not_found".
2. MULTI-LINGUAL: Respond in the user's detected language (English or Greek).
3. JSON OUTPUT: Return raw JSON only. No markdown, no preamble.
4. Be concise and direct.

JSON SCHEMA:
{
  "answer": "<Response based ONLY on provided context>",
  "source_link": "<The Url field from context>",
  "data_timestamp": "<The ScrapedAt value>",
  "language": "<English or Greek>",
  "status": "<found or not_found>"
}

If no answer is found: {"answer": "Information not available in knowledge base", "source_link": "", "data_timestamp": "", "language": "<language>", "status": "not_found"}

Output ONLY the raw JSON object.
"""

# ──────────────────────────────────────────────
# 8.  BUILD CONTEXT WITH LIMIT
# ──────────────────────────────────────────────

def build_context_block(pages: list[dict]) -> str:
    """Build structured context block from matched pages with token limit."""
    if not pages:
        return "NO RELEVANT PAGES FOUND."

    blocks: list[str] = []
    total_chars = 0
    max_chars = 1500  # Reduced to speed up API calls

    for i, page in enumerate(pages, 1):
        title = page.get('Title', 'N/A')
        url = page.get('Url', 'N/A')
        scraped = page.get('ScrapedAt', 'N/A')
        content = page.get("Content", "")[:1000]  # Reduced content
        
        block = (
            f"Page {i}:\n"
            f"Title: {title}\n"
            f"Url: {url}\n"
            f"ScrapedAt: {scraped}\n"
            f"Content: {content}\n"
        )
        
        if total_chars + len(block) <= max_chars:
            blocks.append(block)
            total_chars += len(block)
        else:
            break

    return "\n".join(blocks) if blocks else "NO RELEVANT PAGES FOUND."


# ──────────────────────────────────────────────
# 9.  GEMINI CALL WITH IMPROVED TIMEOUT
# ──────────────────────────────────────────────

def ask_gemini(question: str, language: str) -> dict:
    """
    Send question + context to OpenRouter (Gemini).
    Uses caching and improved timeout handling.
    """
    # Check cache first
    cached_response = _get_from_cache(question, language)
    if cached_response:
        print(f"Cache hit for: {question}")
        return cached_response

    # Handle greetings without API call
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

    # Search knowledge base
    matched_pages = search_corpus(question, top_k=3)  # Reduced from 5 to 3

    # No matching content found
    if not matched_pages:
        response = {
            "answer": "I couldn't find information about that in the SoftOne knowledge base. Please try a different query.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "not_found",
        }
        _store_in_cache(question, language, response)
        return response

    context_block = build_context_block(matched_pages)

    user_prompt = (
        f"Language: {language}\n\n"
        f"CONTEXT:\n{context_block}\n\n"
        f"QUESTION: {question}\n\n"
        f"Respond ONLY with raw JSON."
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
 
    payload = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,  # Lower temp for faster processing
        "max_tokens": 300,   # Reduced from 512
    }
 
    try:
        print(f"Calling OpenRouter API for: {question}")
        resp = session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,  # Increased from 15 to 30
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"].strip()
 
        # Remove markdown wrappers if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?", "", raw_text)
            raw_text = re.sub(r"```$", "", raw_text).strip()
 
        result = json.loads(raw_text)

        # Ensure all required fields
        result.setdefault("answer", "Information not available")
        result.setdefault("source_link", "")
        result.setdefault("data_timestamp", "")
        result.setdefault("language", language)
        result.setdefault("status", "not_found")

        # Fill missing fields from context
        if matched_pages and not result.get("source_link"):
            result["source_link"] = matched_pages[0].get("Url", "")
        if matched_pages and not result.get("data_timestamp"):
            result["data_timestamp"] = matched_pages[0].get("ScrapedAt", "")

        _store_in_cache(question, language, result)
        return result
 
    except json.JSONDecodeError as e:
        print(f"JSON Parse Error: {e}")
        response = {
            "answer": "Error processing response. Please try again.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "error",
        }
        return response
    except requests.exceptions.Timeout:
        print(f"Timeout Error: Request took too long")
        response = {
            "answer": "The service is responding slowly. Please try again in a moment.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "timeout",
        }
        return response
    except requests.exceptions.ConnectionError as e:
        print(f"Connection Error: {e}")
        response = {
            "answer": "Unable to connect to the service. Please check your internet connection.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "connection_error",
        }
        return response
    except Exception as exc:
        print(f"OpenRouter API Error: {exc}")
        response = {
            "answer": "System error. Please try again later.",
            "source_link": "",
            "data_timestamp": "",
            "language": language,
            "status": "error",
        }
        return response


# ──────────────────────────────────────────────
# 10.  FASTAPI APPLICATION
# ──────────────────────────────────────────────

app = FastAPI(
    title="SoftOne Wiki AI Brain",
    description="Optimized Gemini-powered intelligent interface for FullWikiData.json",
    version="2.3.0",
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
    - Multi-language support (English, Greek)
    - Response caching for improved performance
    - Intelligent greeting detection
    - Context-based answers from knowledge base
    - Improved timeout and error handling
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
        "cache_entries": len(RESPONSE_CACHE),
        "engine": "Google Gemini 2.0 Flash",
        "version": "2.3.0",
    }


@app.get("/reload")
async def reload_knowledge_base():
    """Reload FullWikiData.json without restarting."""
    load_knowledge_base()
    RESPONSE_CACHE.clear()
    return {
        "status": "reloaded",
        "total_pages": len(corpus_data),
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


# ──────────────────────────────────────────────
# 11.  RUN
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("Starting SoftOne Wiki AI Brain v2.3.0...")
    print("Supported Languages: English, Greek")
    print("Features: Smart Caching, Optimized Search, Improved Timeout, Context-based Responses")
    uvicorn.run(app, host="127.0.0.1", port=8000)