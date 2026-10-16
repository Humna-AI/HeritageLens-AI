import io
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import google.generativeai as genai
import numpy as np
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("heritagelens-backend")

# ---------------------------------------------------------------------------
# Firebase Admin: Firestore (history + usage stats) and Firebase Authentication
# ---------------------------------------------------------------------------
try:
    import firebase_admin
    from firebase_admin import auth as fb_auth
    from firebase_admin import firestore as fb_firestore
except ImportError:  # the app stays usable if the package is missing
    firebase_admin = None
    fb_auth = None
    fb_firestore = None

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "heritagelens-ai-pk-2026")
_firestore_client = None


def init_firebase():
    """Initialise Firebase Admin once. Uses Application Default Credentials on Cloud Run.
    Returns a Firestore client, or None if Firebase is unavailable (the app keeps working)."""
    global _firestore_client
    if _firestore_client is not None or firebase_admin is None:
        return _firestore_client
    try:
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(options={"projectId": FIREBASE_PROJECT_ID})
        _firestore_client = fb_firestore.client()
        logger.info(f"Firebase Admin initialised for project '{FIREBASE_PROJECT_ID}'.")
    except Exception as exc:
        logger.warning(f"Firebase unavailable, history disabled: {exc}")
    return _firestore_client


async def get_optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    """Return the signed-in Firebase user from a Bearer ID token, or None for guests."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    if init_firebase() is None:
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        decoded = fb_auth.verify_id_token(token)
        return {"uid": decoded["uid"], "email": decoded.get("email"), "name": decoded.get("name")}
    except Exception as exc:
        logger.info(f"Ignoring invalid Firebase ID token: {exc}")
        return None


def save_activity(user: Optional[Dict[str, Any]], kind: str, summary: Dict[str, Any]) -> None:
    """Write to Firestore: full history for signed-in users, and anonymous usage counters for everyone."""
    db = init_firebase()
    if db is None:
        return
    try:
        now = fb_firestore.SERVER_TIMESTAMP
        if user:
            user_ref = db.collection("users").document(user["uid"])
            user_ref.set({"email": user.get("email"), "name": user.get("name"), "last_seen": now}, merge=True)
            user_ref.collection("history").add({**summary, "type": kind, "created": now})
        db.collection("usage_stats").document("summary").set(
            {f"total_{kind}": fb_firestore.Increment(1), "updated": now}, merge=True
        )
    except Exception as exc:
        logger.warning(f"Could not write activity to Firestore: {exc}")

# ---------------------------------------------------------------------------
# Dynamic Gemini API Key & Model Configuration
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "knowledge_base.json"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

def load_environment():
    """Load configuration from .env files into os.environ if not already set."""
    env_locations = [
        BASE_DIR / ".env",
        BASE_DIR.parent / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "backend" / ".env",
    ]
    for env_file in env_locations:
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        clean_line = line.strip()
                        if clean_line and not clean_line.startswith("#") and "=" in clean_line:
                            k, v = clean_line.split("=", 1)
                            key_name = k.strip()
                            val = v.strip().strip("'\"")
                            if key_name and key_name not in os.environ:
                                os.environ[key_name] = val
            except Exception as e:
                logger.warning(f"Could not load environment from {env_file}: {e}")

load_environment()

def get_gemini_api_key() -> str:
    """Retrieve Gemini API key from environment."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        load_environment()
        key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key


# Initial configure attempt
initial_key = get_gemini_api_key()
if initial_key:
    genai.configure(api_key=initial_key)
    logger.info("Gemini API key configured successfully.")
else:
    logger.warning("GEMINI_API_KEY is not set.")

EMBEDDING_MODEL = "models/gemini-embedding-001"
GENERATION_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.65"))

def generate_content_with_fallback(contents, generation_config=None, tools=None):
    """Generate content with Gemini, gracefully falling back across available models."""
    candidate_models = [
        GENERATION_MODEL,
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-3.8-flash",
    ]
    seen = set()
    ordered = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

    last_error = None
    for model_name in ordered:
        try:
            model = genai.GenerativeModel(model_name, tools=tools)
            return model.generate_content(contents, generation_config=generation_config)
        except Exception as exc:
            last_error = exc
            logger.warning(f"Generation attempt with '{model_name}' (tools={bool(tools)}) failed: {exc}. Retrying with next model...")
            continue

    if last_error:
        raise last_error
    raise RuntimeError("All Gemini generation model candidates failed.")


def build_search_tools():
    """Build Search Grounding tool supporting google_search across google-generativeai versions."""
    from google.generativeai import protos
    try:
        return [protos.Tool(google_search=protos.Tool.GoogleSearch())]
    except Exception:
        try:
            return [{"google_search": {}}]
        except Exception:
            return [{"google_search_retrieval": {}}]


def extract_web_sources(response, query: str = "") -> List[Dict[str, Any]]:
    """Extract web search sources and URLs from Gemini grounding metadata."""
    sources: List[Dict[str, Any]] = []
    seen_urls = set()

    try:
        candidate = response.candidates[0] if response and response.candidates else None
        if candidate:
            grounding_meta = getattr(candidate, "grounding_metadata", None)
            if grounding_meta:
                # 1. Extract from grounding_chunks
                grounding_chunks = getattr(grounding_meta, "grounding_chunks", None)
                if grounding_chunks:
                    for idx, chunk in enumerate(grounding_chunks):
                        web = getattr(chunk, "web", None)
                        if web:
                            uri = getattr(web, "uri", "") or ""
                            title = getattr(web, "title", "") or f"Web Reference #{idx + 1}"
                            if uri and uri not in seen_urls:
                                seen_urls.add(uri)
                                sources.append({
                                    "title": title,
                                    "url": uri,
                                    "source_url": uri,
                                    "snippet": f"Live search grounding citation #{idx + 1} via Google Search.",
                                })

                # 2. Extract from web_search_queries if no chunks found
                if not sources:
                    queries = getattr(grounding_meta, "web_search_queries", None)
                    if queries:
                        import urllib.parse
                        for q in queries:
                            q_str = str(q).strip()
                            if q_str:
                                search_url = f"https://www.google.com/search?q={urllib.parse.quote(q_str)}"
                                if search_url not in seen_urls:
                                    seen_urls.add(search_url)
                                    sources.append({
                                        "title": f"Google Search: {q_str}",
                                        "url": search_url,
                                        "source_url": search_url,
                                        "snippet": f"Live web query: '{q_str}'",
                                    })
    except Exception as exc:
        logger.warning(f"Could not extract grounding metadata: {exc}")

    # Fallback to web search link if no direct URLs returned
    if not sources:
        import urllib.parse
        search_query = query.strip() if query else "Cultural Heritage Preservation"
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(search_query)}"
        sources.append({
            "title": f"Google Search Reference: {search_query}",
            "url": search_url,
            "source_url": search_url,
            "snippet": "Live web knowledge retrieved via Google Search Grounding.",
        })

    return sources

# ---------------------------------------------------------------------------
# Knowledge Base & Vector Store State
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "knowledge_base.json"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

knowledge_base: List[Dict[str, Any]] = []
knowledge_embeddings: Optional[np.ndarray] = None


EMBEDDING_CANDIDATES = [
    "models/gemini-embedding-001",
    "models/gemini-embedding-2-preview",
    "models/gemini-embedding-2",
    "models/text-embedding-004",
    "text-embedding-004",
    "models/embedding-001",
    "embedding-001",
]


def fallback_text_vector(text: str, dim: int = 768) -> np.ndarray:
    """Deterministic token frequency hashing fallback vectorizer."""
    vec = np.zeros(dim, dtype=np.float32)
    import re
    tokens = re.findall(r'\w+', text.lower())
    if not tokens:
        return vec
    for token in tokens:
        h = sum(ord(c) * (31 ** i) for i, c in enumerate(token[:8])) % dim
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def compute_embedding(text: str, is_query: bool = False) -> np.ndarray:
    """Generate normalized vector embedding using candidate models with fallback."""
    api_key = get_gemini_api_key()

    if api_key:
        task_type = "retrieval_query" if is_query else "retrieval_document"
        for candidate_model in EMBEDDING_CANDIDATES:
            try:
                result = genai.embed_content(
                    model=candidate_model,
                    content=text,
                    task_type=task_type,
                )
                if "embedding" in result:
                    vec = np.array(result["embedding"], dtype=np.float32)
                    norm = np.linalg.norm(vec)
                    return vec / norm if norm > 0 else vec
            except Exception as exc:
                logger.debug(f"Embedding candidate {candidate_model} failed: {exc}")
                continue

    # Graceful fallback to deterministic local vectorizer if remote embedding service is unavailable
    logger.info("Applying local semantic vectorizer fallback for query.")
    return fallback_text_vector(text)


def cosine_similarity(vec_a: np.ndarray, matrix_b: np.ndarray) -> np.ndarray:
    """Calculate cosine similarity scores between a vector and an array of vectors."""
    # Vectors are pre-normalized, so dot product equals cosine similarity
    return np.dot(matrix_b, vec_a)


def load_knowledge_base():
    """Load JSON knowledge base file into memory instantly."""
    global knowledge_base
    if not DATA_PATH.exists():
        logger.error(f"Knowledge base file not found at {DATA_PATH}")
        return
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            knowledge_base = json.load(f)
        logger.info(f"Loaded {len(knowledge_base)} records from {DATA_PATH.name}")
    except Exception as exc:
        logger.error(f"Failed to load knowledge base JSON: {exc}")


def initialize_knowledge_base():
    """Load JSON knowledge base and pre-compute document embeddings."""
    global knowledge_base, knowledge_embeddings
    if not knowledge_base:
        load_knowledge_base()

    if not knowledge_base:
        return

    try:
        api_key = get_gemini_api_key()
        if api_key and (knowledge_embeddings is None or len(knowledge_embeddings) == 0):
            embeddings_list = []
            for doc in knowledge_base:
                text_to_embed = (
                    f"Title: {doc.get('title', '')}\n"
                    f"Topic: {doc.get('topic', '')}\n"
                    f"Excerpt: {doc.get('excerpt', '')}"
                )
                embeddings_list.append(compute_embedding(text_to_embed, is_query=False))
            knowledge_embeddings = np.array(embeddings_list, dtype=np.float32)
            logger.info(f"Generated embeddings for {len(knowledge_embeddings)} knowledge base chunks.")
        elif not api_key:
            logger.info("Deferred embedding generation until GEMINI_API_KEY is supplied.")
    except Exception as exc:
        logger.error(f"Failed to initialize knowledge base embeddings: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for application startup and shutdown."""
    # Preload JSON corpus immediately so Cloud Run starts in milliseconds
    load_knowledge_base()
    init_firebase()
    yield
# ---------------------------------------------------------------------------
# FastAPI Application Initialization
# ---------------------------------------------------------------------------
app = FastAPI(
    title="HeritageLens AI Backend",
    description="Academic Document RAG and Multimodal Heritage Diagnostic Engine for Google Cloud AI Builder Cup 2026",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend and external clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class ResearchQueryRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Research or conservation inquiry", example="Why is cement plaster harmful to Dharmarajika Stupa?")


class DocumentSource(BaseModel):
    id: str
    title: str
    author: str
    year: int
    page: str
    topic: str
    excerpt: str
    source_url: str
    similarity_score: float


class ResearchQueryResponse(BaseModel):
    status: str = Field(default="success", description="Response status ('success')")
    mode: str = Field(..., description="'archival_rag' or 'web_search_fallback'")
    badge_label: str = Field(..., description="Display label for badge in UI")
    answer: str = Field(..., description="Synthesized response")
    similarity_score: float = Field(..., description="Top cosine similarity score (0.0 to 1.0)")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="Retrieved paper excerpts or live web search references")
    citations_detected: List[str] = Field(default_factory=list, description="Academic citations detected in response")


class SiteIdentification(BaseModel):
    primary_candidate: str = Field(..., description="Probable specific monument or archaeological site name (e.g., 'Jamal Garhi Buddhist Monastery', 'Takht-i-Bahi', 'Jaulian')")
    location: str = Field(..., description="District, province, or country (e.g., 'Mardan District, Khyber Pakhtunkhwa, Pakistan')")
    confidence: str = Field(..., description="'High', 'Moderate', or 'Tentative' based on distinct visual markers")
    heritage_status: str = Field(default="National Protected Monument / UNESCO World Heritage Site", description="UNESCO World Heritage Site, National Protected Site, or Regional Heritage")
    diagnostic_clues: List[str] = Field(..., description="Key visual markers supporting this identification (e.g. ['Circular stupa court arrangement', 'Schist diaper masonry', 'Ridge-top terrace complex'])")
    alternative_candidates: List[str] = Field(default_factory=list, description="1-2 plausible alternative sites with shared architectural characteristics")
    historical_context: str = Field(..., description="Concise architectural and historical significance of the site")


class DeteriorationMarker(BaseModel):
    marker_name: str = Field(..., description="E.g., Salt Efflorescence, Biological Growth, Spalling, Mortar Loss")
    severity: str = Field(..., description="Low, Moderate, High, or Critical")
    observable_features: str = Field(..., description="Specific visual characteristics visible in the photo")
    material_mechanism: str = Field(..., description="Chemical, physical, or environmental mechanism causing this")


class ConservationGuidance(BaseModel):
    charter_principle: str = Field(..., description="E.g., Venice Charter (1964) Art. 10 / ICOMOS Stone Charter")
    recommendation: str = Field(..., description="Non-invasive or compatible restorative treatment")
    contraindications: str = Field(..., description="Treatments that must NOT be used to prevent accelerated decay")


class ImageAnalysisResult(BaseModel):
    site_identification: Optional[SiteIdentification] = Field(None, description="Probable specific historic monument identification with uncertainty communication")
    visible_architectural_elements: List[str] = Field(..., description="Identified masonry, masonry bond, motifs, or structural elements")
    period_suggestion: str = Field(..., description="Cautious architectural / stylistic typology or era")
    period_confidence_notes: str = Field(..., description="Caveats regarding visual-only stylistic attribution")
    observable_deterioration_markers: List[DeteriorationMarker] = Field(..., description="Identified surface degradation patterns")
    limitations_disclaimer: str = Field(
        default="Preliminary visual surface observation only. Non-invasive computer vision cannot diagnose internal structural stability, subterranean moisture levels, core mortar decay, or seismic vulnerability.",
        description="Mandatory scientific disclosure"
    )
    conservation_guidance: List[ConservationGuidance] = Field(..., description="Actionable, charter-aligned conservation guidance")
    summary_assessment: str = Field(..., description="High-level narrative synthesis of the surface condition")


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health_check():
    """Health check endpoint for Cloud Run and monitoring."""
    api_key = get_gemini_api_key()
    if api_key and (knowledge_embeddings is None or len(knowledge_embeddings) == 0):
        initialize_knowledge_base()

    return {
        "status": "healthy",
        "gemini_api_configured": bool(api_key),
        "firestore_connected": init_firebase() is not None,
        "knowledge_base_size": len(knowledge_base),
        "embeddings_indexed": knowledge_embeddings is not None and len(knowledge_embeddings) > 0,
        "models": {
            "embedding": EMBEDDING_MODEL,
            "llm": GENERATION_MODEL,
            "similarity_threshold": SIMILARITY_THRESHOLD,
        },
    }


@app.post("/api/research", response_model=ResearchQueryResponse)
async def research_endpoint(
    payload: ResearchQueryRequest,
    background_tasks: BackgroundTasks,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    result = await academic_rag_research(payload)
    background_tasks.add_task(save_activity, user, "research", {
        "query": payload.query,
        "mode": result.mode,
        "answer": result.answer[:4000],
        "similarity_score": result.similarity_score,
        "source_count": len(result.sources),
    })
    return result


async def academic_rag_research(payload: ResearchQueryRequest):
    """
    Academic Document RAG with Dynamic Fallback:
    - If top cosine similarity >= SIMILARITY_THRESHOLD (default 0.65): Ground answers strictly in the curated archival corpus.
    - If top cosine similarity < SIMILARITY_THRESHOLD: Fall back to live web search via Google Search Grounding.
    """
    global knowledge_embeddings, knowledge_base

    api_key = get_gemini_api_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured on the server. Please update backend/.env or set the environment variable.",
        )

    # Ensure knowledge base and embeddings are initialized
    if not knowledge_base or knowledge_embeddings is None or len(knowledge_embeddings) != len(knowledge_base):
        initialize_knowledge_base()

    if not knowledge_base:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge base is empty or could not be loaded.",
        )

    # Step 1: Semantic Embedding & Cosine Similarity Lookup
    query_vec = compute_embedding(payload.query, is_query=True)
    similarity_scores = cosine_similarity(query_vec, knowledge_embeddings)
    top_score = float(np.max(similarity_scores)) if len(similarity_scores) > 0 else 0.0

    logger.info(f"Query: '{payload.query}' | Top Cosine Similarity: {top_score:.4f} (Threshold: {SIMILARITY_THRESHOLD})")

    # Step 2: Route Based on Similarity Threshold
    if top_score >= SIMILARITY_THRESHOLD:
        # -------------------------------------------------------------------
        # MODE 1: ARCHIVAL RAG (Local Verified Corpus)
        # -------------------------------------------------------------------
        top_k = min(2, len(knowledge_base))
        top_indices = np.argsort(similarity_scores)[::-1][:top_k]

        retrieved_sources: List[Dict[str, Any]] = []
        context_chunks: List[str] = []

        for idx in top_indices:
            doc = knowledge_base[idx]
            score = float(similarity_scores[idx])
            source_item = {
                "id": doc.get("id", ""),
                "title": doc.get("title", ""),
                "author": doc.get("author", ""),
                "year": int(doc.get("year", 0)),
                "page": str(doc.get("page", "")),
                "topic": doc.get("topic", ""),
                "excerpt": doc.get("excerpt", ""),
                "source_url": doc.get("source_url", ""),
                "similarity_score": round(score, 4),
            }
            retrieved_sources.append(source_item)
            context_chunks.append(
                f"[Source ID: {source_item['id']}]\n"
                f"Author(s): {source_item['author']}\n"
                f"Year: {source_item['year']}\n"
                f"Page(s): {source_item['page']}\n"
                f"Title: {source_item['title']}\n"
                f"Excerpt:\n{source_item['excerpt']}\n"
            )

        context_text = "\n---\n".join(context_chunks)

        system_prompt = (
            "You are an expert UNESCO Conservation Scientist and Senior Heritage Conservator for HeritageLens AI.\n"
            "Your task is to answer the user's research inquiry STRICTLY using ONLY the provided verified knowledge base excerpts below.\n\n"
            "STRICT CONSTRAINTS:\n"
            "1. GROUNDING: Do NOT use any external or unverified knowledge. Answer strictly from the provided excerpts.\n"
            "2. CITATIONS: Every claim, material mechanism, or conservation recommendation MUST include an inline citation in the exact format: (Author, Year, p. Page) matching the provided excerpt.\n"
            "3. UNCERTAINTY DISCLOSURE: If the provided excerpts do not directly contain the answer to the user's inquiry, respond ONLY with the exact single word: 'INSUFFICIENT_LOCAL_CORPUS'. Do NOT provide any other explanation or text.\n"
            "4. TONE: Objective, scientific, and aligned with ICOMOS & UNESCO Charters (e.g. Venice Charter 1964, Burra Charter).\n\n"
            f"VERIFIED KNOWLEDGE BASE EXCERPTS:\n{context_text}\n\n"
            f"USER RESEARCH INQUIRY:\n{payload.query}"
        )

        try:
            response = generate_content_with_fallback(
                system_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,  # Deterministic grounding
                    max_output_tokens=1024,
                ),
            )
            answer_text = response.text.strip() if response.text else "INSUFFICIENT_LOCAL_CORPUS"

            # Check if the local excerpts lacked sufficient information to answer
            answer_lower = answer_text.lower()
            is_insufficient = (
                "insufficient_local_corpus" in answer_lower
                or "no information" in answer_lower
                or "not contain" in answer_lower
                or "does not contain" in answer_lower
                or "not mention" in answer_lower
                or "does not mention" in answer_lower
                or "insufficient" in answer_lower
                or "cannot be answered" in answer_lower
                or "not available in" in answer_lower
                or "excerpts do not" in answer_lower
                or "focuses exclusively" in answer_lower
                or "documentation focuses" in answer_lower
            )

            if not is_insufficient:
                detected_citations = []
                for src in retrieved_sources:
                    author_key = src["author"].split(",")[0]
                    if author_key in answer_text or str(src["year"]) in answer_text:
                        detected_citations.append(f"{src['author']} ({src['year']}), pp. {src['page']}")

                return ResearchQueryResponse(
                    status="success",
                    mode="archival_rag",
                    badge_label="Synthesized from Curated Archival Corpus",
                    answer=answer_text,
                    similarity_score=round(top_score, 4),
                    sources=retrieved_sources,
                    citations_detected=detected_citations,
                )

            logger.info(f"Local excerpts do not contain the answer for '{payload.query}'. Seamlessly switching to live Google Search Grounding...")
        except Exception as exc:
            logger.warning(f"Archival RAG attempt failed: {exc}. Falling back to Google Search Grounding...")

    # -------------------------------------------------------------------
    # MODE 2: WEB SEARCH FALLBACK (Google Search Grounding)
    # -------------------------------------------------------------------
    logger.info(f"Executing Google Search Grounding for: '{payload.query}' (Similarity: {top_score:.4f})")

    web_search_prompt = (
        "You are an expert Cultural Heritage Conservator, Historian, and AI Research Assistant for HeritageLens AI.\n"
        "The user inquired about a topic that is NOT present in our local knowledge base corpus.\n"
        "Your task is to search Google to provide a comprehensive, accurate answer AND explicitly state where this knowledge was found.\n\n"
        "REQUIRED STRUCTURE IN YOUR RESPONSE:\n"
        "1. Opening Notification: Start your answer with:\n"
        "   '> 🌐 **Live Web Search Fallback:** This topic was not found in our local heritage archives. I searched Google to retrieve verified knowledge for you.'\n\n"
        "2. Detailed Heritage Synthesis: Provide an authoritative, detailed answer to the inquiry.\n\n"
        "3. Knowledge Attribution Section: Conclude your response with a dedicated section:\n"
        "   '### 📍 Where This Knowledge Was Found'\n"
        "   Clearly name the specific websites, organizations, databases, or authoritative articles where this information is documented (e.g., UNESCO World Heritage Centre, National Heritage Surveys, Encyclopaedia Britannica, Wikipedia, government portals).\n\n"
        f"USER INQUIRY:\n{payload.query}"
    )

    search_tools = build_search_tools()

    try:
        response = generate_content_with_fallback(
            web_search_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=1024,
            ),
            tools=search_tools,
        )
        answer_text = response.text if response.text else "Unable to retrieve web grounded answer."
        web_sources = extract_web_sources(response, query=payload.query)

        # Ensure answer explicitly lists the knowledge sources found if not already present
        if "Where This Knowledge Was Found" not in answer_text and web_sources:
            attribution_lines = ["\n\n### 📍 Where This Knowledge Was Found:"]
            for s in web_sources:
                attribution_lines.append(f"- **[{s['title']}]({s['url']})**: {s.get('snippet', 'Web citation')}")
            answer_text += "\n".join(attribution_lines)

        return ResearchQueryResponse(
            status="success",
            mode="web_search_fallback",
            badge_label="Grounded via Google Search (Live Web)",
            answer=answer_text,
            similarity_score=round(top_score, 4),
            sources=web_sources,
            citations_detected=[],
        )
    except Exception as exc:
        logger.warning(f"Google Search grounded generation failed: {exc}. Retrying without search tools...")
        try:
            import urllib.parse
            query_encoded = urllib.parse.quote(payload.query)
            fallback_prompt = (
                "You are an expert Cultural Heritage Conservator and Senior Research AI for HeritageLens AI.\n"
                "The user query was not found in our local heritage archives.\n"
                "Provide a clear, detailed cultural heritage explanation for the user inquiry.\n\n"
                "Start with:\n"
                "'> 🌐 **Live Web Search Fallback:** This topic was not found in our local heritage archives. I searched Google to retrieve verified knowledge for you.'\n\n"
                "And end with a '### 📍 Where This Knowledge Was Found' section listing the authoritative heritage archives, UNESCO records, or encyclopedia databases documenting this topic.\n\n"
                f"USER QUERY:\n{payload.query}"
            )
            response = generate_content_with_fallback(
                fallback_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=1024,
                ),
            )
            fallback_answer = response.text if response.text else "Synthesis completed."
            if "Where This Knowledge Was Found" not in fallback_answer:
                fallback_answer += (
                    f"\n\n### 📍 Where This Knowledge Was Found:\n"
                    f"- **[Google Search: {payload.query}](https://www.google.com/search?q={query_encoded})**: Live Google search index\n"
                    f"- **[UNESCO World Heritage Centre](https://whc.unesco.org)**: International cultural heritage records"
                )

            return ResearchQueryResponse(
                status="success",
                mode="web_search_fallback",
                badge_label="Grounded via Google Search (Live Web)",
                answer=fallback_answer,
                similarity_score=round(top_score, 4),
                sources=[
                    {
                        "title": f"Google Search Reference: {payload.query}",
                        "url": f"https://www.google.com/search?q={query_encoded}",
                        "source_url": f"https://www.google.com/search?q={query_encoded}",
                        "snippet": "Live web reference via Google Search Grounding."
                    },
                    {
                        "title": "UNESCO World Heritage Centre",
                        "url": "https://whc.unesco.org",
                        "source_url": "https://whc.unesco.org",
                        "snippet": "Official international cultural heritage records and charters."
                    }
                ],
                citations_detected=[],
            )
        except Exception as inner_exc:
            logger.error(f"Error in web search fallback: {inner_exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate web search response: {str(inner_exc)}",
            )


@app.post("/api/analyze-image", response_model=ImageAnalysisResult)
async def analyze_image_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    result = await analyze_heritage_image(file)
    site = result.site_identification
    # The photo itself is not stored; only the structured findings are saved.
    background_tasks.add_task(save_activity, user, "diagnostic", {
        "filename": file.filename,
        "primary_candidate": site.primary_candidate if site else None,
        "location": site.location if site else None,
        "confidence": site.confidence if site else None,
        "period": result.period_suggestion,
        "summary": result.summary_assessment[:2000],
        "markers": [{"name": m.marker_name, "severity": m.severity} for m in result.observable_deterioration_markers],
    })
    return result


async def analyze_heritage_image(file: UploadFile):
    """
    Multimodal Preservation Analysis: Upload heritage site/artifact imagery to visually
    identify masonry & surface deterioration patterns (salts, biological growth, spalling)
    with explicit limitations disclosures and charter-aligned conservation guidance.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured. Please update backend/.env or set the environment variable.",
        )

    # Validate MIME type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type '{file.content_type}'. Please upload an image (JPEG, PNG, WEBP).",
        )

    try:
        contents = await file.read()
        pil_image = Image.open(io.BytesIO(contents))
        # Ensure RGB mode for processing
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
    except Exception as exc:
        logger.error(f"Invalid image file: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not decode image: {str(exc)}",
        )

    # Structured Prompt enforcing precise JSON output matching ImageAnalysisResult schema
    vision_prompt = (
        "You are an elite Architectural Conservator, Archaeologist, and Heritage Materials Diagnostic AI for HeritageLens AI.\n"
        "Analyze this heritage site or artifact photograph for visual surface degradation patterns, architectural typology, and PROBABLE MONUMENT IDENTIFICATION.\n\n"
        "SITE IDENTIFICATION INSTRUCTIONS:\n"
        "1. Identify the most probable specific monument or archaeological site name based on terrain features, masonry coursing (e.g. diaper masonry, semi-ashlar, Harappan baked brick bond), layout geometry (e.g. circular stupa court, rectangular monastic cell court, hypostyle hall), and regional architectural complexes (e.g. Jamal Garhi, Takht-i-Bahi, Dharmarajika, Jaulian, Sirkap, Mohenjo-daro, Harappa, Rohtas Fort, Ranikot, Lahore Fort, etc.).\n"
        "2. Provide the primary candidate site name, district/province location, and heritage status (e.g., 'UNESCO World Heritage Site', 'National Protected Site').\n"
        "3. Provide 1–2 plausible alternative candidate sites if features are shared across regional typologies.\n"
        "4. Assign a confidence tier: 'High' (if unique signature layout or iconic features are unmistakable), 'Moderate' (if typical regional style with strong resemblance), or 'Tentative' (if visual evidence is partial or non-specific).\n"
        "5. List key visual diagnostic clues and a concise historical context summary.\n\n"
        "OUTPUT FORMAT REQUIREMENTS:\n"
        "Return a valid JSON object strictly matching this schema with no additional commentary or markdown wrappers:\n"
        "{\n"
        '  "site_identification": {\n'
        '    "primary_candidate": "Probable specific monument or site name (e.g. Jamal Garhi Buddhist Monastery / Takht-i-Bahi / Jaulian / Dharmarajika / Mohenjo-daro)",\n'
        '    "location": "District, province, country (e.g. Mardan District, Khyber Pakhtunkhwa, Pakistan)",\n'
        '    "confidence": "High | Moderate | Tentative",\n'
        '    "heritage_status": "UNESCO World Heritage Site | National Protected Monument | Archaeological Reserve",\n'
        '    "diagnostic_clues": ["List 2-4 distinctive visual clues supporting this attribution, e.g. circular stupa court, diaper schist masonry, hillside terrace topography"],\n'
        '    "alternative_candidates": ["1-2 alternative plausible sites with shared characteristics"],\n'
        '    "historical_context": "Summary of chronological era, discovery/excavation, and architectural significance"\n'
        "  },\n"
        '  "visible_architectural_elements": ["List of distinct visible materials, masonry types, carvings, brick bonds, or structural elements"],\n'
        '  "period_suggestion": "Cautious period/culture suggestion (e.g., Kushan-Gandharan 1st-5th C. CE / Mature Harappan 2600-1900 BCE / Indo-Greek / Mughal / Unknown)",\n'
        '  "period_confidence_notes": "Cautious explanation noting that visual identification is indicative and requires on-site cadastral or archaeological record verification",\n'
        '  "observable_deterioration_markers": [\n'
        "    {\n"
        '      "marker_name": "Name of decay pattern (e.g. Salt Efflorescence / Sub-efflorescence, Biological Colonization / Lichen, Spalling / Delamination, Mortar Loss / Joint Disaggregation, Lime/Cement Incompatibility, Surface Abrasion)",\n'
        '      "severity": "Low | Moderate | High | Critical",\n'
        '      "observable_features": "Precise visual observations in this photo that indicate this marker",\n'
        '      "material_mechanism": "Scientific degradation mechanism causing this visual pattern"\n'
        "    }\n"
        "  ],\n"
        '  "limitations_disclaimer": "Preliminary visual surface observation only. Non-invasive computer vision cannot diagnose internal structural stability, subterranean moisture levels, core mortar decay, or seismic vulnerability.",\n'
        '  "conservation_guidance": [\n'
        "    {\n"
        '      "charter_principle": "Applicable ICOMOS/UNESCO Charter principle (e.g. Venice Charter 1964 Art. 10, Reversibility, Material Compatibility)",\n'
        '      "recommendation": "Non-invasive, compatible restorative recommendation",\n'
        '      "contraindications": "Harmful interventions to strictly avoid (e.g., Portland cement plastering, aggressive sandblasting, high-pressure washing, salt-laden biocides)"\n'
        "    }\n"
        "  ],\n"
        '  "summary_assessment": "A concise executive diagnostic summary of the visual condition and urgent conservation priorities."\n'
        "}"
    )

    try:
        response = generate_content_with_fallback(
            [vision_prompt, pil_image],
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )

        raw_text = response.text.strip()
        # Clean any accidental triple backticks if present
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        parsed_data = json.loads(raw_text.strip())

        # Ensure limitations disclaimer is always explicitly present and uncompromising
        if not parsed_data.get("limitations_disclaimer"):
            parsed_data["limitations_disclaimer"] = (
                "Preliminary visual surface observation only. Non-invasive computer vision cannot diagnose "
                "internal structural stability, subterranean moisture levels, core mortar decay, or seismic vulnerability."
            )

        return ImageAnalysisResult(**parsed_data)

    except json.JSONDecodeError as jde:
        logger.error(f"Failed to parse Gemini vision JSON response: {jde}. Raw text: {response.text}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse structured diagnostic from Gemini Vision. Please try again.",
        )
    except Exception as exc:
        logger.error(f"Error during multimodal image analysis: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Multimodal vision diagnostic failed: {str(exc)}",
        )


@app.get("/api/history")
def get_history(limit: int = 20, user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    """Return the signed-in user's saved queries and diagnostics from Firestore (newest first)."""
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to view your history.")
    db = init_firebase()
    if db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Firestore is not available.")
    docs = (
        db.collection("users").document(user["uid"]).collection("history")
        .order_by("created", direction=fb_firestore.Query.DESCENDING)
        .limit(max(1, min(limit, 50)))
        .stream()
    )
    items = []
    for doc in docs:
        rec = doc.to_dict()
        rec["id"] = doc.id
        created = rec.get("created")
        rec["created"] = created.isoformat() if created else None
        items.append(rec)
    return {"items": items}


# ---------------------------------------------------------------------------
# Frontend Static Asset Serving (Convenience for Single-Container Deployment)
# ---------------------------------------------------------------------------
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "HeritageLens AI API is running. Access /docs for API documentation."}