# 🏛️ HeritageLens AI

> **Google Cloud AI Builder Cup 2026** — *Sustainability & Social Impact Track*  
> **Target Alignment:** [UN SDG 11.4](https://sdgs.un.org/goals/goal11#targets): *Strengthen efforts to protect and safeguard the world’s cultural and natural heritage.*

---

## 🌟 Executive Overview

**HeritageLens AI** is an AI-powered cultural heritage preservation and diagnostic platform designed to assist archaeological curators, architectural conservators, and UNESCO field missions. It combines **deterministic literature grounding** with **multimodal surface deterioration diagnostics** to prevent irreversible damage to ancient monuments caused by incompatible modern restorations, climate-induced salt efflorescence, and biological attacks.

---

## 🚀 Key Capabilities

1. **Academic Document RAG (Zero-Hallucination Retrieval-Augmented Generation)**:
   - Queries are vectorized using Google's `models/text-embedding-004`.
   - Semantic retrieval computes cosine similarity over a verified corpus of peer-reviewed conservation papers, UNESCO charter declarations (Venice Charter 1964, Burra Charter), and archaeological field excavation reports.
   - Powered by **Gemini 1.5 Flash** under strict zero-temperature constraints requiring exact inline academic citations `(Author, Year, p. Page)` and unambiguous uncertainty disclosure when documentary evidence is absent.

2. **Multimodal Preservation Analysis (Computer Vision Diagnostic)**:
   - Field practitioners upload high-resolution surface imagery of masonry, brickwork, or carved reliefs.
   - Evaluates observable degradation markers (subflorescence/salt crusting, biological colonization, schist foliation delamination, mortar washouts).
   - Generates ICOMOS/UNESCO-aligned conservation guidance and highlights critical contraindications (e.g., prohibition of impermeable Portland cement on porous historic limestone).
   - Enforces an uncompromising scientific disclosure stating that visual computer vision is a non-invasive surface screening tool, not a substitute for subterranean structural testing.

---

## 📂 Project Directory Structure

```text
heritagelens-ai/
├── backend/
│   ├── main.py                     # FastAPI application, Gemini RAG & Vision pipelines
│   ├── requirements.txt            # Python dependencies (FastAPI, GenAI, Pillow, NumPy)
│   ├── Dockerfile                  # Lightweight Python 3.11-slim container for Cloud Run
│   └── data/
│       └── knowledge_base.json     # Curated peer-reviewed conservation papers & charters
├── frontend/
│   └── index.html                  # Single-page dashboard (Sandstone/Slate UI, Tailwind CSS)
└── README.md                       # Comprehensive deployment & architecture documentation
```

---

## 🛠️ Local Development Setup

### 1. Prerequisites
- Python 3.10 or 3.11 installed
- A valid [Google Gemini API Key](https://aistudio.google.com/)

### 2. Clone and Navigate to the Repository
```bash
cd heritagelens-ai
```

### 3. Create and Activate a Virtual Environment
```bash
# On Linux / macOS:
python3 -m venv venv
source venv/bin/activate

# On Windows (PowerShell):
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 4. Install Dependencies
```bash
pip install --upgrade pip
pip install -r backend/requirements.txt
```

### 5. Configure Your Gemini API Key
```bash
# On Linux / macOS:
export GEMINI_API_KEY="your-actual-gemini-api-key"

# On Windows (PowerShell):
$env:GEMINI_API_KEY="your-actual-gemini-api-key"

# On Windows (CMD):
set GEMINI_API_KEY=your-actual-gemini-api-key
```

### 6. Launch the Local FastAPI Server
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

### 7. Access the Application
- **Frontend Dashboard:** Open `http://localhost:8080/` in your browser.
- **Interactive OpenAPI Documentation:** Open `http://localhost:8080/docs`.
- Alternatively, you can open `frontend/index.html` directly in your browser.

---

## ☁️ Production Deployment on Google Cloud Run

Deploy HeritageLens AI as a serverless container on **Google Cloud Run** targeting the `asia-southeast1` (Singapore) region.

### 1. Authenticate with Google Cloud SDK
```bash
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT_ID
```

### 2. Enable Required Google Cloud APIs
```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com
```

### 3. Deploy to Cloud Run (Targeting `asia-southeast1`)
Execute the following single command from the project root:

```bash
gcloud run deploy heritagelens-backend \
    --source ./backend \
    --region asia-southeast1 \
    --platform managed \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --set-env-vars GEMINI_API_KEY="YOUR_ACTUAL_GEMINI_API_KEY"
```

> **Security Best Practice for Production:**  
> Store your API key in **Google Secret Manager** and mount it into Cloud Run:
> ```bash
> echo -n "YOUR_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
> gcloud secrets add-iam-policy-binding gemini-api-key \
>     --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
>     --role="roles/secretmanager.secretAccessor"
>
> gcloud run deploy heritagelens-backend \
>     --source ./backend \
>     --region asia-southeast1 \
>     --set-secrets GEMINI_API_KEY=gemini-api-key:latest
> ```

---

## 🧪 Testing & Verification Guide

### Scenario A: Testing Academic Document RAG
Execute a POST request to `/api/research`:

```bash
curl -X POST "http://localhost:8080/api/research" \
     -H "Content-Type: application/json" \
     -d '{"query": "Why is cement plaster harmful to Dharmarajika Stupa in Taxila?"}'
```

**Expected Response Features:**
- Vector similarity score calculated via `models/text-embedding-004`.
- Grounded explanation detailing how impervious Portland cement traps capillary ground moisture and causes destructive subflorescence spalling.
- Inline citation: `(Marshall, J. & Khan, F. A., 2018, p. 142-146)`.

### Scenario B: Testing Multimodal Field Photo Diagnostic
Send a heritage image to `/api/analyze-image`:

```bash
curl -X POST "http://localhost:8080/api/analyze-image" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@/path/to/heritage_site_photo.jpg"
```

**Expected Structured JSON Response:**
- `visible_architectural_elements`: Extracted masonry typologies and features.
- `observable_deterioration_markers`: Categorized decay patterns with severity grades.
- `limitations_disclaimer`: Mandatory scientific qualification banner.
- `conservation_guidance`: Actionable, charter-compliant interventions (e.g. Venice Charter 1964 Art. 10).

---

## 📜 Ethical Disclosures & Charters

1. **Non-Invasive Diagnostic Screen**: HeritageLens AI is designed strictly as a preliminary visual observation aid and should never replace on-site physical core drilling, mortar petrography, or dynamic structural analysis.
2. **Charter Alignment**: Recommendations strictly enforce principles of material reversibility and compatibility established by the **ICOMOS Venice Charter (1964)** and the **ICOMOS Charter on the Conservation of Historic Stone Structures (2004)**.

---

## 📄 License
Released under the [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0). Built for the **Google Cloud AI Builder Cup 2026**.
