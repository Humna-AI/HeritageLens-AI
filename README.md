# 🏛️ HeritageLens AI

> **Google Cloud AI Builder Cup 2026** — *Sustainability & Social Impact Track*  
> **Target Alignment:** UN SDG 11.4 — *Strengthen efforts to protect and safeguard the world’s cultural and natural heritage.*  
> **Live Production URL:** [https://heritagelens-ai-93118475309.asia-southeast1.run.app](https://heritagelens-ai-93118475309.asia-southeast1.run.app)

---

## Executive Overview

**HeritageLens AI** is a production-grade, AI-powered cultural heritage preservation and diagnostic platform built to assist archaeological curators, architectural conservators, and field preservationists. By pairing deterministic literature grounding with multimodal surface deterioration diagnostics, HeritageLens AI prevents irreversible damage to ancient monuments caused by incompatible modern restorations, microclimate salt efflorescence, and biological colonization.

---

## Key Capabilities

### 1. Academic Document RAG (*Zero-Hallucination Retrieval-Augmented Generation*)
- **Semantic Vector Store:** Queries are vectorized using Google's `models/text-embedding-004` (with fallback to `models/gemini-embedding-001`).
- **Verified Archival Corpus:** Computes cosine similarity over a curated dataset of peer-reviewed conservation papers, UNESCO charter declarations (*Venice Charter 1964, Burra Charter*), and archaeological excavation reports.
- **Grounded Synthesis:** Powered by **Gemini 1.5 Flash** under strict zero-temperature grounding constraints requiring exact inline academic citations `(Author, Year, p. Page)`.
- **Live Search Fallback:** If query similarity falls below threshold or local records lack coverage, it seamlessly transitions to **Google Search Grounding** while retaining source attribution.

### 2. Multimodal Preservation Analysis (*Computer Vision Diagnostic*)
- **Field Imagery Analysis:** Field practitioners upload high-resolution photos of masonry, brick bonds, or carved reliefs.
- **Probable Site Identification:** Identifies candidate monuments, locations, regional typologies, and diagnostic architectural clues (*e.g., diaper schist masonry, circular stupa court layout*).
- **Deterioration Profiling:** Evaluates surface degradation markers (*salt efflorescence, biological colonization, spalling, mortar washout*) with severity ratings.
- **Charter-Aligned Guidance:** Generates non-invasive restoration guidance alongside strict contraindications (*e.g., prohibition of Portland cement on porous stone*).
- **Mandatory Disclaimer:** Enforces scientific disclosures highlighting that surface computer vision is a preliminary screening tool, not a substitute for subsurface structural testing.

---

## System Architecture & Technology Stack

| Layer | Technology / Service | Purpose |
| :--- | :--- | :--- |
| **Cloud Hosting** | **Google Cloud Run** | Serverless, containerized deployment in `asia-southeast1` |
| **Image Registry** | **Google Container Registry (GCR)** | Automated container artifact management |
| **Secrets Management** | **Google Secret Manager** | Secure, zero-plaintext storage for `GEMINI_API_KEY` |
| **Build Pipeline** | **Google Cloud Build** | Remote container build and image packaging |
| **AI / LLM Engine** | **Gemini 1.5 Flash** (`google-generativeai`) | Grounded RAG synthesis, web search grounding, multimodal vision analysis |
| **Embeddings** | `text-embedding-004` / `gemini-embedding-001` | High-dimensional semantic vector indexing |
| **Backend Framework** | **FastAPI** + **Uvicorn** | High-performance Python async web framework |
| **Frontend UI** | **HTML5** + **Tailwind CSS** + **JavaScript** | Single-container, sandstone/slate aesthetic UI dashboard |

---

## 📂 Project Directory Structure

```text
heritagelens-ai/
├── backend/
│   ├── main.py                 # FastAPI app, RAG engine, Gemini Vision pipeline & static file server
│   ├── requirements.txt        # Backend dependencies (FastAPI, GenAI, Pillow, NumPy)
│   └── data/
│       └── knowledge_base.json # Verified peer-reviewed conservation papers & UNESCO charters
├── frontend/
│   └── index.html              # Responsive web dashboard (Tailwind CSS, Sandstone/Slate UI)
├── Dockerfile                  # Production multi-stage Docker build for Cloud Run deployment
├── requirements.txt            # Root dependencies declaration
├── .dockerignore               # Ignores local venv, cache, git, and local environment files
└── README.md                   # Complete documentation

```

---

## Live Production Deployment on Google Cloud Run

The production deployment runs containerized on Google Cloud Run in the **`asia-southeast1`** (Singapore) region with secure API key mounting via **Google Secret Manager**.

### Deployment Commands Executed

1. **Secret Manager Configuration:**
```cmd
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
    --member="serviceAccount:93118475309-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

```


2. **Container Packaging via Cloud Build:**
```cmd
gcloud builds submit . --tag gcr.io/heritagelens-ai-pk-2026/heritagelens-ai

```


3. **Cloud Run Production Release:**
```cmd
gcloud run deploy heritagelens-ai \
    --image gcr.io/heritagelens-ai-pk-2026/heritagelens-ai \
    --region asia-southeast1 \
    --platform managed \
    --allow-unauthenticated \
    --timeout=300s \
    --set-secrets="GEMINI_API_KEY=GEMINI_API_KEY:latest"

```



---

## 🛠️ Local Development Setup

### 1. Prerequisites

* Python 3.10 or 3.11
* Google Gemini API Key

### 2. Installation

```bash
# Clone repository
git clone [https://github.com/your-username/heritagelens-ai.git](https://github.com/your-username/heritagelens-ai.git)
cd heritagelens-ai

# Create & activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

```

### 3. Environment Setup & Execution

```bash
# Set Gemini API Key
export GEMINI_API_KEY="your-actual-gemini-api-key"   # On Windows (cmd): set GEMINI_API_KEY=your_key

# Launch server from project root
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080

```

Access locally at `http://localhost:8080/`.

---

## 🧪 Testing & API Verification

### Scenario A: Testing Academic Document RAG Endpoint

**Request:**

```bash
curl -X POST "[https://heritagelens-ai-93118475309.asia-southeast1.run.app/api/research](https://heritagelens-ai-93118475309.asia-southeast1.run.app/api/research)" \
     -H "Content-Type: application/json" \
     -d '{"query": "Why is cement plaster harmful to Dharmarajika Stupa in Taxila?"}'

```

**Expected Response Features:**

* Cosine similarity calculated via `text-embedding-004`.
* Grounded scientific explanation detailing how impermeable cement traps capillary ground moisture and drives subflorescence spalling.
* Inline citation format: `(Marshall, J. & Khan, F. A., 2018, p. 142-146)`.

### Scenario B: Testing Multimodal Image Diagnostic Endpoint

**Request:**

```bash
curl -X POST "[https://heritagelens-ai-93118475309.asia-southeast1.run.app/api/analyze-image](https://heritagelens-ai-93118475309.asia-southeast1.run.app/api/analyze-image)" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@/path/to/heritage_site_photo.jpg"

```

**Expected JSON Response:**

* `site_identification`: Probable site name (*e.g., Jamal Garhi / Takht-i-Bahi*), location, and visual diagnostic clues.
* `observable_deterioration_markers`: Categorized decay patterns with severity grades.
* `conservation_guidance`: Charter-compliant non-invasive interventions (*Venice Charter 1964 Art. 10*).
* `limitations_disclaimer`: Scientific disclosure banner.

---

## 📜 Ethical Disclosures & Standards Alignment

1. **Non-Invasive Diagnostic Screen:** HeritageLens AI is designed exclusively as a preliminary visual observation aid and does not replace on-site physical core drilling, mortar petrography, or dynamic structural testing.
2. **ICOMOS & UNESCO Charter Alignment:** Recommendations strictly enforce principles of material reversibility and compatibility established by the **ICOMOS Venice Charter (1964)** and the **ICOMOS Principles for the Analysis, Conservation and Structural Restoration of Architectural Heritage (2003)**.

---

## 📄 License

Released under the **Apache 2.0 License**. Built for the **Google Cloud AI Builder Cup 2026**.

```

```
