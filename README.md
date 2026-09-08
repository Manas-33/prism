<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" />
  <img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/Gemini_2.5_Flash-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white" />
</p>

<h1 align="center">🔬 Prism</h1>

<p align="center">
  <strong>AI-powered code review that understands your codebase — not just your diff.</strong>
</p>

<p align="center">
  Prism is a GitHub App that goes beyond line-by-line diffing. It builds a full dependency graph of your repository using static analysis, identifies every downstream file impacted by a PR, and uses LLM reasoning to explain <em>why</em> each impact matters — all posted as a review comment, automatically.
</p>

---

## The Problem

Traditional code review tools show you **what** changed. But the hardest bugs come from what you **didn't** check — the files downstream that silently depend on the function you just refactored.

> *You rename a parameter in `calculate_tax()`. The diff looks clean. But three services and a test suite call that function, and none of them were updated.*

Prism catches that.

---

## How It Works

```mermaid
graph LR
    A[PR Opened] --> B[GitHub Webhook]
    B --> C[Celery Worker]
    C --> D[Clone & Diff]
    D --> E[AST Parsing]
    E --> F[Dependency Graph]
    F --> G[Impact Analysis]
    G --> H[LLM Explanations]
    H --> I[PR Comment Posted]

    style A fill:#1a1a2e,stroke:#e94560,color:#fff
    style B fill:#1a1a2e,stroke:#e94560,color:#fff
    style C fill:#16213e,stroke:#e94560,color:#fff
    style D fill:#16213e,stroke:#0f3460,color:#fff
    style E fill:#0f3460,stroke:#e94560,color:#fff
    style F fill:#0f3460,stroke:#e94560,color:#fff
    style G fill:#533483,stroke:#e94560,color:#fff
    style H fill:#533483,stroke:#e94560,color:#fff
    style I fill:#e94560,stroke:#fff,color:#fff
```

### Pipeline Breakdown

| Stage | What Happens |
|---|---|
| **1. Webhook Ingestion** | Receives `pull_request` events, verifies HMAC signatures, deduplicates via Redis, and filters out drafts, merges, and forks |
| **2. Async Processing** | Dispatches analysis to a Celery worker pool so the webhook returns instantly |
| **3. Clone & Diff** | Shallow-clones the repo, checks out the PR branch, and computes a `git diff` against the base SHA |
| **4. Static Analysis** | Parses every `.py` file into an AST using [tree-sitter](https://tree-sitter.github.io/), extracting functions, classes, and all import statements (absolute, relative, aliased) |
| **5. Dependency Graph** | Resolves imports to actual file paths, building a file-level dependency graph *and* a symbol-level call graph with call-site line numbers |
| **6. Impact Detection** | Cross-references changed symbols against the call graph to find every file that calls them, then scores each impact with a confidence heuristic |
| **7. LLM Explanations** | Sends the before/after code of the changed function plus the call-site context to Gemini 2.5 Flash for a concise, evidence-based impact explanation — parallelized across all impacts |
| **8. PR Comment** | Posts a structured review summary back to the PR with changed symbols, impacted files, confidence levels, and AI explanations |

---

## Key Features

### 🌳 AST-Based Dependency Graph
Not regex. Not string matching. Prism uses **tree-sitter** to parse Python source code into a full Abstract Syntax Tree, then resolves every import — including relative imports (`from .. import deep`), aliased imports (`import foo as f`), and multi-imports (`import foo, bar`) — to actual file paths.

### 🎯 Symbol-Level Impact Analysis
Goes beyond file-level analysis to track **which functions and classes** changed, then traces every downstream call site with exact line numbers. Each impact is scored with a **confidence heuristic** based on:
- Call frequency (single vs. multiple call sites)
- Symbol visibility (public vs. private)
- File context (production code vs. test files)

### 🤖 LLM-Powered Explanations
For each impacted file, Prism extracts the **before/after code** of the changed function and the **call-site context**, then asks Gemini 2.5 Flash to explain the potential impact. The LLM is constrained to reason only from the provided code — no hallucinated dependencies, no assumed bugs.

### ⚡ Performance by Design
- **Celery + Redis** for non-blocking, horizontally scalable async task processing
- **Redis caching** for dependency graphs and summaries (keyed by commit SHA), so re-pushes to the same commit are instant
- **Parallel LLM calls** via `ThreadPoolExecutor` (5 concurrent workers) for multi-impact PRs
- **Webhook deduplication** via Redis `SET NX` to prevent duplicate processing

### 🔐 Secure GitHub App Auth
Full GitHub App authentication flow: JWT generation with RSA private keys → installation ID lookup → scoped installation access tokens. Webhook payloads are verified using HMAC-SHA256 signatures.

### 🛡️ Multi-Layer Rate Limiting
Protects both the webhook ingestion and LLM inference layers from abuse and cost overruns:
- **Webhook throttling** via [SlowAPI](https://github.com/laurentS/slowapi) — configurable per-IP and per-repo rate limits on the `/webhook/github` endpoint to prevent denial-of-wallet attacks
- **LLM call budgeting** — per-PR token budget with a max concurrent requests cap, preventing a single massive PR from exhausting the Gemini API quota and starving other reviews

### 📊 Evaluation Pipeline
Built-in precision/recall evaluation framework to continuously measure the accuracy of Prism's impact detection against ground-truth annotations:
- **Precision** — What percentage of flagged impacts are true positives? Ensures reviews aren't noisy
- **Recall** — What percentage of real downstream impacts are caught? Ensures nothing slips through
- Configurable test harness that runs against annotated PRs, producing per-run metrics to track detection quality as the analysis engine evolves

### 📈 Engineering Dashboard
Real-time frontend dashboard backed by PostgreSQL for engineering teams to track code health metrics over time:
- **Impact Hotspot Map** — Identifies modules and symbols with the highest downstream fragility, surfacing architectural risk before it becomes tech debt
- **PR Risk Trends** — Tracks average confidence scores and impact counts per repo over time, giving engineering leads visibility into code quality trajectory
- **Symbol Fragility Scores** — Ranks the most frequently impacted symbols across PRs, highlighting candidates for refactoring or increased test coverage

---

## Architecture

```
prism/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── webhook.py           # GitHub webhook handler + signature verification
│   ├── git_ops.py           # Clone, checkout, diff, and full analysis orchestration
│   ├── static_analysis.py   # tree-sitter AST parsing, symbol/import extraction
│   ├── dependency_graph.py  # File + symbol graph construction, impact detection
│   ├── confidence.py        # Heuristic impact scoring engine
│   ├── llm_service.py       # LLM prompt engineering for impact explanations
│   ├── api_service.py       # Gemini API client wrapper
│   ├── repo_index.py        # Full-repo indexer (symbols + imports per file)
│   ├── github.py            # GitHub API interactions (comments, tokens)
│   ├── github_auth.py       # JWT generation for GitHub App auth
│   ├── models.py            # Data models + serialization (Symbol, FileIndex)
│   ├── cache.py             # Redis cache get/set abstraction
│   ├── redis_client.py      # Redis connection factory
│   └── workspace.py         # Temporary workspace context manager
├── worker/
│   └── tasks.py             # Celery task definitions
├── docker-compose.yml        # API + Worker + Redis stack
├── Dockerfile                # Python 3.12 + uv package manager
└── pyproject.toml            # Dependencies managed with uv
```

---

## Getting Started

### Prerequisites
- Docker & Docker Compose
- A [GitHub App](https://docs.github.com/en/apps/creating-github-apps) with **Pull Request** read permissions and webhook events enabled
- A [Gemini API key](https://aistudio.google.com/apikey)

### 1. Clone the repo
```bash
git clone https://github.com/Manas-33/prism.git
cd prism
```

### 2. Configure environment
```bash
cp example.env .env
```

Edit `.env` with your credentials:
```env
GITHUB_WEBHOOK_SECRET=your_webhook_secret
GITHUB_APP_ID=your_app_id
GITHUB_PRIVATE_KEY_PATH=./path/to/private_key.pem
REDIS_HOST=redis
REDIS_PORT=6379
GEMINI_API_KEY=your_gemini_api_key
```

### 3. Launch the stack
```bash
docker compose up --build
```

This spins up three services:
| Service | Description | Port |
|---|---|---|
| `api` | FastAPI webhook server | `8000` |
| `worker` | Celery task worker | — |
| `redis` | Message broker + cache | `6379` |

### 4. Point your GitHub App webhook to
```
https://your-domain.com/webhook/github
```

Open a PR on any repo with the app installed — Prism takes it from there.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **API** | FastAPI | Async-first, auto-generated OpenAPI docs |
| **Task Queue** | Celery | Battle-tested distributed task processing |
| **Broker/Cache** | Redis | Sub-millisecond caching + reliable message brokering |
| **Static Analysis** | tree-sitter | Production-grade incremental parser used by GitHub, Neovim, and Zed |
| **LLM** | Gemini 2.5 Flash | Fast, cost-efficient reasoning with grounded search |
| **Auth** | PyJWT + RSA | Secure GitHub App JWT authentication |
| **Packaging** | uv | 10-100x faster than pip, with lockfile support |
| **Deployment** | Docker Compose | Single-command reproducible stack |

---

## Evaluation

PRism ships a precision/recall harness that measures impact-detection quality against annotated, hand-verified fixtures — **graph-only**, so no GitHub/Redis/LLM secrets are needed:

```bash
uv run python -m eval
```

Current baseline: **precision 1.00, recall 0.75** on the demo fixture set — every miss is an attribute-access dependency the call-graph can't see. See [`eval/README.md`](eval/README.md) for the annotation format, adding cases, and CI wiring (`.github/workflows/eval.yml`).

---

## Roadmap

- [ ] Inline PR review comments on specific changed lines
- [ ] GitHub Checks API integration with pass/fail status
- [ ] Persistent graph database for cross-PR impact tracking
- [ ] Slack/Discord notifications for high-confidence impacts

---

<p align="center">
  Built by <a href="https://github.com/Manas-33">Manas Dalvi</a>
</p>
