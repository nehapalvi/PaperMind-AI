import os
import re
import fitz
import faiss
import json
import uuid
import time
import logging
import numpy as np
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
load_dotenv()
from typing import List, Dict
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
from openai import OpenAI

# ==============================
# CONFIG
# ==============================

API_KEY = os.getenv("GROQ_API_KEY")
if not API_KEY:
    raise ValueError("GROQ_API_KEY not set in environment variables.")

client = OpenAI(
    api_key=API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

logging.basicConfig(level=logging.INFO)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("templates/index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

# ==============================
# # GORK FALLBACK CONFIG
# ==============================

GORK_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]

MAX_RETRIES_PER_MODEL = 2
MODEL_TIMEOUT_SECONDS = 20

# ==============================
# GLOBAL STORES
# ==============================

faiss_index = None
bm25 = None
chunk_store = []
metadata_store = []

# ==============================
# PDF PROCESSING
# ==============================

def extract_pdf_with_metadata(file_path: str):
    doc = fitz.open(file_path)
    documents = []

    for page_num, page in enumerate(doc):
        text = page.get_text()
        sections = detect_sections(text)

        for section_title, section_text in sections:
            documents.append({
                "text": section_text,
                "page": page_num + 1,
                "section": section_title
            })

    doc.close()
    return documents


def detect_sections(text: str):
    section_patterns = r"\n([A-Z][A-Z\s]{3,})\n"
    splits = re.split(section_patterns, text)

    sections = []
    for i in range(1, len(splits), 2):
        title = splits[i].strip()
        content = splits[i + 1].strip()
        sections.append((title, content))

    if not sections:
        sections.append(("GENERAL", text))

    return sections

# ==============================
# SEMANTIC CHUNKING
# ==============================

def semantic_chunking(text: str, max_tokens=300):
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_tokens:
            current_chunk += " " + sentence
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

# ==============================
# INDEX BUILDING
# ==============================

def build_indexes(documents: List[Dict]):
    global faiss_index, bm25, chunk_store, metadata_store

    chunk_store = []
    metadata_store = []

    for doc in documents:
        chunks = semantic_chunking(doc["text"])
        for chunk in chunks:
            chunk_store.append(chunk)
            metadata_store.append({
                "page": doc["page"],
                "section": doc["section"]
            })

    if not chunk_store:
        raise ValueError("No text extracted from PDF.")

    embeddings = embedding_model.encode(chunk_store)
    dim = embeddings.shape[1]

    faiss_index = faiss.IndexFlatL2(dim)
    faiss_index.add(np.array(embeddings))

    tokenized = [chunk.split() for chunk in chunk_store]
    bm25 = BM25Okapi(tokenized)

# ==============================
# HYBRID RETRIEVAL
# ==============================

def hybrid_search(query: str, k=5):

    if faiss_index is None or bm25 is None:
        raise HTTPException(status_code=400, detail="No document uploaded yet.")

    query_vec = embedding_model.encode([query])
    D, I = faiss_index.search(np.array(query_vec), k)
    semantic_results = list(I[0])

    tokenized_query = query.split()
    bm25_scores = bm25.get_scores(tokenized_query)
    keyword_results = np.argsort(bm25_scores)[::-1][:k]

    combined = list(set(semantic_results) | set(keyword_results))

    results = []
    for idx in combined:
        results.append({
            "text": chunk_store[idx],
            "metadata": metadata_store[idx]
        })

    return results

# ==============================
# PROMPT BUILDER (GUARDRAILS)
# ==============================
def build_prompt(context_chunks, question, mode, level):

    context_text = "\n\n".join(
        [f"(Page {c['metadata']['page']} | {c['metadata']['section']})\n{c['text']}"
         for c in context_chunks]
    )

    base_instruction = """
You are a strict research assistant.
Use ONLY the provided context.
If answer not found, respond exactly: "Not available in document."
Cite page numbers.
Return structured JSON only.
Ensure every field in the JSON is filled if information exists in context.
"""

    # LEVEL LOGIC
    level_lower = level.lower()
    if level_lower in ["10 year old", "child", "beginner"]:
        level_instruction = """
Explain in very simple words.
Avoid technical terms.
Use short sentences.
Use analogies.
Make it easy enough for a 10 year old.
For key concepts, provide a simple explanation for each term in full sentences.
"""
    elif level_lower in ["college student", "undergraduate", "student"]:
        level_instruction = """
Explain clearly with moderate technical depth.
Define important terms.
Keep it academically accurate.
Assume basic background knowledge.
For key concepts, list each term with a clear explanation in complete sentences.
Do not leave any explanations blank.
"""
    elif level_lower in ["researcher", "expert", "phd"]:
        level_instruction = """
Explain with full technical depth.
Use formal academic language.
Include equations if present.
Discuss assumptions and limitations.
Be precise and rigorous.
For key concepts, provide detailed explanations, examples, and references to pages.
"""
    else:
        level_instruction = "Explain clearly and appropriately. Provide explanations for all key concepts."

    # Mode logic
    if mode == "equation":
        task = "Explain equations step by step with variable meanings."
    elif mode == "analysis":
        task = "Provide detailed paper analysis including strengths and weaknesses."
    else:
        task = "Answer normally with structured explanation."

    return f"""
{base_instruction}

LEVEL INSTRUCTION:
{level_instruction}

TASK:
{task}

CONTEXT:
{context_text}

QUESTION:
{question}

Return JSON with:
{{
  "main_idea": "",
  "key_concepts": [{{"concept": "", "explanation": ""}}],
  "equations_explained": "",
  "real_world_example": "",
  "simple_summary": ""
}}
"""

# ==============================
#  GORK MULTI-MODEL FALLBACK
# ==============================

def ask_gork_with_fallback(prompt: str):

    last_error = None

    for model_name in GORK_MODELS:

        for attempt in range(MAX_RETRIES_PER_MODEL):

            try:
                logging.info(f"Trying {model_name} | Attempt {attempt+1}")

                response = client.responses.create(
                    model=model_name,
                    input=prompt
                )

                if response and response.output_text:
                    logging.info(f"Success with {model_name}")
                    return response.output_text

                raise Exception("Empty response")

            except Exception as e:
                last_error = str(e)
                logging.warning(f"{model_name} failed: {e}")
                time.sleep(1.5)

        logging.info(f"Switching model from {model_name}")

    logging.error("All models failed.")

    return json.dumps({
        "error": "All models unavailable",
        "details": last_error
    })

# ==============================
# REQUEST MODEL
# ==============================

class Query(BaseModel):
    question: str
    level: str = "undergraduate"
    mode: str = "normal"

# ==============================
# API ROUTES
# ==============================

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):

    file_id = str(uuid.uuid4())
    file_path = f"temp_{file_id}.pdf"

    with open(file_path, "wb") as f:
        f.write(await file.read())

    documents = extract_pdf_with_metadata(file_path)
    build_indexes(documents)

    return {"message": "PDF processed with hybrid index"}

@app.post("/ask")
async def ask_question(query: Query):
    # Retrieve context and build prompt
    results = hybrid_search(query.question)
    prompt = build_prompt(results, query.question, query.mode, query.level)

    # Ask Gork
    answer = ask_gork_with_fallback(prompt)

    # Clean the answer string
    cleaned = answer.strip()

    # Remove triple backticks and optional 'json' label
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```json\s*|```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # If parsing fails, return as raw_response
        parsed = {"raw_response": cleaned}

    # Return as 'answer' key for frontend consistency
    return {"answer": parsed}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
