<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" />
  <img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" />
  <img src="https://img.shields.io/badge/Qdrant-DC244C?style=for-the-badge&logo=qdrant&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/Gemini_2.5_Flash-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white" />
</p>

<h1 align="center">🔬 Prism</h1>

<p align="center">
  <strong>Blast-radius code review that catches what your change breaks downstream, and enforces your team's rules, not just the diff.</strong>
</p>

<p align="center">
  Prism is a GitHub App that reviews the <strong>blast radius</strong> of a pull request: every downstream symbol your change can break, not just the lines in the diff. It builds a full dependency graph of your repository with static analysis, traces every impacted call site, uses LLM reasoning to explain <em>why</em> each impact matters, and checks each one against your repo's own engineering rules (retrieved per change, scoped per repo). Every finding is posted back as a review comment, automatically.
</p>

---

## The Problem

Traditional code review tools show you **what** changed. But the hardest bugs come from what you **didn't** check: the files downstream that silently depend on the function you just refactored. And even a clean-looking change can quietly break your team's own conventions.

> *You rename a parameter in `calculate_tax()`. The diff looks clean. But three services and a test suite call that function, and none of them were updated.*

Prism catches that, and tells you which of your engineering rules the change violates.

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
    G --> R[Scoped Rule Retrieval]
    R --> H[LLM Explanations]
    H --> I[PR Comment Posted]

    style A fill:#1a1a2e,stroke:#e94560,color:#fff
    style B fill:#1a1a2e,stroke:#e94560,color:#fff
    style C fill:#16213e,stroke:#e94560,color:#fff
    style D fill:#16213e,stroke:#0f3460,color:#fff
    style E fill:#0f3460,stroke:#e94560,color:#fff
    style F fill:#0f3460,stroke:#e94560,color:#fff
    style G fill:#533483,stroke:#e94560,color:#fff
    style R fill:#533483,stroke:#e94560,color:#fff
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
| **7. Scoped Rule Retrieval** | Embeds each impact's changed code and retrieves your repo's engineering rules from a per-repo Qdrant index (with a built-in rule set as fallback), so the review enforces your team's standards, not just generic advice |
| **8. LLM Explanations** | Sends the before/after code of the changed function, the call-site context, and the retrieved rules to Gemini 2.5 Flash for a concise, evidence-based explanation that flags rule violations by number, parallelized across all impacts |
| **9. PR Comment** | Posts a structured review summary back to the PR with changed symbols, impacted files, confidence levels, rule violations, and AI explanations |

---

## Key Features

### 🌳 AST-Based Dependency Graph
Not regex. Not string matching. Prism uses **tree-sitter** to parse Python source code into a full Abstract Syntax Tree, then resolves every import (relative imports like `from .. import deep`, aliased imports like `import foo as f`, and multi-imports like `import foo, bar`) to actual file paths.

### 🎯 Symbol-Level Impact Analysis
Goes beyond file-level analysis to track **which functions and classes** changed, then traces the full blast radius: every downstream call site, with exact line numbers. Each impact is scored with a **confidence heuristic** based on:
- Call frequency (single vs. multiple call sites)
- Symbol visibility (public vs. private)
- File context (production code vs. test files)

### 🤖 LLM-Powered Explanations
For each impacted file, Prism extracts the **before/after code** of the changed function and the **call-site context**, then asks Gemini 2.5 Flash to explain the potential impact. The LLM is constrained to reason only from the provided code: no hallucinated dependencies, no assumed bugs.

### 📐 Scoped Rule Enforcement
Beyond generic review, Prism enforces **your repository's own engineering rules**. Commit a `.prism/rules.md` (or `STYLEGUIDE.md`) and Prism embeds it into a **per-repo Qdrant index**, then for each impacted symbol it retrieves the rules relevant to that change and asks the model to flag violations, citing each rule by number. Retrieval is **scoped per repo** with tenant isolation, so one repository's rules never leak into another's review. With no rules file, Prism falls back to a built-in set of Python best-practice checks, so every review enforces something sensible.

### ⚡ Performance by Design
- **Celery + Redis** for non-blocking, horizontally scalable async task processing
- **Redis caching** for dependency graphs, summaries, and retrieved rules (keyed by commit SHA), so re-pushes to the same commit are instant
- **Parallel LLM calls** via `ThreadPoolExecutor` (5 concurrent workers) for multi-impact PRs
- **Webhook deduplication** via Redis `SET NX` to prevent duplicate processing

### 🔐 Secure GitHub App Auth
Full GitHub App authentication flow: JWT generation with RSA private keys, installation ID lookup, then scoped installation access tokens. Webhook payloads are verified using HMAC-SHA256 signatures.

### 🛡️ Multi-Layer Rate Limiting
Protects both the webhook ingestion and LLM inference layers from abuse and cost overruns:
- **Webhook throttling** via [SlowAPI](https://github.com/laurentS/slowapi): configurable per-IP and per-repo rate limits on the `/webhook/github` endpoint to prevent denial-of-wallet attacks
- **LLM call budgeting**: a per-PR token budget with a max concurrent requests cap, preventing a single massive PR from exhausting the Gemini API quota and starving other reviews

---

## Architecture

```
prism/
├── app/
│   ├── main.py              # FastAPI entrypoint
│   ├── webhook.py           # GitHub webhook handler + signature verification
│   ├── analysis.py          # Analysis core: (repo, base, head) -> structured impacts
│   ├── pipeline.py          # Online (webhook) + offline wrappers around the core
│   ├── git_ops.py           # Clone, checkout, diff, offline repo preparation
│   ├── static_analysis.py   # tree-sitter AST parsing, symbol/import extraction
│   ├── dependency_graph.py  # File + symbol graph construction, impact detection
│   ├── confidence.py        # Heuristic impact scoring engine
│   ├── rules.py             # Engineering-rules seam (file discovery + retrieval)
│   ├── rag.py               # Scoped rule ingestion + retrieval over Qdrant
│   ├── embeddings.py        # Gemini embeddings wrapper
│   ├── llm_service.py       # LLM prompt assembly for impact explanations
│   ├── api_service.py       # Gemini API client wrapper
│   ├── render.py            # AnalysisResult -> PR-comment summary
│   ├── cli.py               # Offline CLI (python -m app.cli)
│   ├── repo_index.py        # Full-repo indexer (symbols + imports per file)
│   ├── github.py            # GitHub API interactions (comments, tokens)
│   ├── github_auth.py       # JWT generation for GitHub App auth
│   ├── models.py            # Data models + serialization (Symbol, FileIndex)
│   ├── cache.py             # Redis cache get/set abstraction
│   ├── redis_client.py      # Redis connection factory
│   └── workspace.py         # Temporary workspace context manager
├── worker/
│   └── tasks.py             # Celery task definitions
├── eval/                     # Precision/recall evaluation harness (python -m eval)
├── tests/                    # Zero-dependency unit checks (python -m tests)
├── docker-compose.yml        # API + Worker + Redis + Qdrant stack
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
QDRANT_URL=http://localhost:6333
```

> `QDRANT_URL` is only needed for local (non-Docker) runs. Under Docker Compose it is set automatically to `http://qdrant:6333`.

### 3. Launch the stack
```bash
docker compose up --build
```

This spins up four services:
| Service | Description | Port |
|---|---|---|
| `api` | FastAPI webhook server | `8000` |
| `worker` | Celery task worker | (none) |
| `redis` | Message broker + cache | `6379` |
| `qdrant` | Vector store for scoped rules | `6333` |

### 4. Point your GitHub App webhook to
```
https://your-domain.com/webhook/github
```

### 5. (Optional) Enforce your own engineering rules
Commit a `.prism/rules.md` (or `STYLEGUIDE.md`) to your repository root:
```markdown
## Error handling
- Never swallow exceptions silently; every `except` must log or re-raise.

## API design
- Never change a public function's return type without updating all call sites.
```
On the next PR, Prism embeds these into its per-repo index and enforces them, citing any violation by number. Rules work best when they are **locally checkable** (verifiable from the changed function and its call site). With no rules file, Prism uses a built-in set of Python best-practice checks.

Open a PR on any repo with the app installed, and Prism takes it from there.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **API** | FastAPI | Async-first, auto-generated OpenAPI docs |
| **Task Queue** | Celery | Battle-tested distributed task processing |
| **Broker/Cache** | Redis | Sub-millisecond caching + reliable message brokering |
| **Static Analysis** | tree-sitter | Production-grade incremental parser used by GitHub, Neovim, and Zed |
| **LLM** | Gemini 2.5 Flash | Fast, cost-efficient reasoning with grounded search |
| **Embeddings** | gemini-embedding-001 | Embeds rules and change context for scoped retrieval |
| **Vector Store** | Qdrant | Per-repo scoped rule retrieval with tenant isolation |
| **Auth** | PyJWT + RSA | Secure GitHub App JWT authentication |
| **Packaging** | uv | 10-100x faster than pip, with lockfile support |
| **Deployment** | Docker Compose | Single-command reproducible stack |

---

## Roadmap

- [ ] Inline PR review comments on specific changed lines
- [ ] GitHub Checks API integration with pass/fail status
- [ ] Persistent graph database for cross-PR impact tracking
- [ ] Engineering dashboard (PostgreSQL): impact hotspot map, PR risk trends, and symbol fragility scores
- [ ] Slack/Discord notifications for high-confidence impacts

---

<p align="center">
  Built by <a href="https://github.com/Manas-33">Manas Dalvi</a>
</p>
