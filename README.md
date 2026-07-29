# 🕸️ CodeGrapher

**Turn any codebase into an interactive, queryable knowledge graph — then let a crew of AI agents reason over it.**

CodeGrapher clones a repository, parses it deterministically into structural facts (files, classes, functions, ORM models, HTTP routes, call graphs), pushes those facts into a real graph database and a vector database, and hands them to a multi-agent CrewAI pipeline that maps the architecture, traces blast radius for proposed changes, flags anti-patterns, and drafts new feature stubs that match the codebase's own conventions.

No part of the *extraction* is AI-guessed. Only the *reasoning* is.

---

## Table of Contents

- [What it actually does](#what-it-actually-does)
- [Architecture](#architecture)
- [How the pipeline works](#how-the-pipeline-works)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [API reference](#api-reference)
- [Accuracy testing — not just "does it run"](#accuracy-testing--not-just-does-it-run)
- [Known limitations](#known-limitations-read-this)
- [Design notes worth knowing](#design-notes-worth-knowing)

---

## What it actually does

1. **You give it a repo** — a git URL or a local path.
2. **Node 1** parses it with zero AI: Python's own `ast` module for `.py` files, Tree-Sitter for `.js/.jsx/.ts/.tsx`. Every extracted fact traces back to a specific line of source.
3. **Node 2** pushes those facts into **Neo4j** (files/classes/functions/relationships as real graph nodes and edges) and **Qdrant** (meaning-searchable embeddings of each symbol).
4. **`IngestionCrew`**, one CrewAI `Crew`, runs automatically on every submitted repo: the Cartographer and the ORM Schema Agent map architecture and schema in parallel, a small model joins their reports, then the Anti-Pattern Agent scans the result.
5. **Two more agents run later, only on demand**, never as part of ingestion: `ImpactCrew` traces blast radius for a proposed edit (whenever the user actually asks), and a standalone Feature Architect agent drafts a feature stub matching the codebase's own conventions.
6. **The frontend** shows it all: live agent progress over SSE, the four analysis reports, an interactive Cytoscape graph, Mermaid call-sequence diagrams, and Monaco-rendered code stubs.

---

## Architecture

```mermaid
flowchart TD
    U["User submits a repo<br/>git URL or local path"]:::io
    U --> API["FastAPI · POST /repos<br/>queues a Celery task"]:::io

    API --> N1

    subgraph N1["NODE 1 · Parser  (deterministic — no LLM)"]
      direction LR
      N1A["ast_parser.py<br/>Python's ast module"]:::mech
      N1B["js_ts_parser.py<br/>Tree-Sitter (JS / TS)"]:::mech
    end

    N1 --> N2

    subgraph N2["NODE 2 · Storage Sync  (deterministic — no LLM)"]
      direction LR
      N2A["graph_sync.py"]:::mech
      N2B["vector_sync.py<br/>fastembed"]:::mech
    end

    N2A --> NEO[("Neo4j")]:::data
    N2B --> QD[("Qdrant")]:::data

    N2 --> CREW

    subgraph CREW["IngestionCrew · CrewAI Crew (Process.sequential) — always runs, always these 4 tasks"]
      direction TB
      AG1["Agent: Codebase Cartographer<br/>map_architecture_task (async)"]:::agent
      AG2["Agent: Data & ORM Schema Agent<br/>extract_schema_task (async)"]:::agent
      JN["Agent: report_joiner (8B model)<br/>join_reports_task — no analysis,<br/>closes out the async pair"]:::agent
      AG4["Agent: Architectural Anti-Pattern Agent<br/>anti_pattern_task"]:::agent
      AG1 --> JN
      AG2 --> JN
      JN --> AG4
    end

    CREW --> PG[("Postgres<br/>jobs + reports")]:::data

    PG --> N3

    subgraph N3["NODE 3 · Graph Query API  (deterministic — no LLM)"]
      direction LR
      N3A["Cytoscape JSON"]:::mech
      N3B["Mermaid sequence<br/>diagrams"]:::mech
    end

    N3 --> UI["Next.js Frontend<br/>live logs · reports · graph · diagrams"]:::io

    UI -.->|"on demand, user-triggered, any time after ingestion"| SC2
    UI -.->|"on demand, user-triggered, any time after ingestion"| SC3

    subgraph SC2["ImpactCrew · a second, separate Crew"]
      direction LR
      AG3["Agent: Impact Analysis &<br/>Blast Radius Agent"]:::agent
      AG3B["Agent: Architectural<br/>Anti-Pattern Agent"]:::agent
      AG3 --> AG3B
    end

    subgraph SC3["Feature Agent · plain Agent+Task, no Crew"]
      AG5["Agent:<br/>Feature Architect &<br/>Contract Agent"]:::agent
    end

    SC2 -.-> PG
    SC3 -.-> STUB["Monaco-rendered code stub"]:::io

    classDef mech fill:#eaedf2,stroke:#5b6b82,color:#1a1f2b,stroke-width:1.4px
    classDef agent fill:#f1edfc,stroke:#6e56cf,color:#1a1f2b,stroke-width:1.4px
    classDef data fill:#faf1de,stroke:#a8710f,color:#1a1f2b,stroke-width:1.4px
    classDef io fill:#e6f5f1,stroke:#1e8f79,color:#1a1f2b,stroke-width:1.4px

    style N1 fill:none,stroke:#5b6b82,stroke-width:1.2px
    style N2 fill:none,stroke:#5b6b82,stroke-width:1.2px
    style N3 fill:none,stroke:#5b6b82,stroke-width:1.2px
    style CREW fill:none,stroke:#6e56cf,stroke-width:1.6px
    style SC2 fill:none,stroke:#6e56cf,stroke-width:1.2px,stroke-dasharray:3 3
    style SC3 fill:none,stroke:#6e56cf,stroke-width:1.2px,stroke-dasharray:4 4
```

**Reading the diagram:** solid arrows are the automatic sequence — every submitted repo runs Node 1 → Node 2 → `IngestionCrew` without stopping, and without any conditional branch (`IngestionCrew` is always the same 4 tasks; there's no ingestion-time proposed-edit path). The dashed paths around `ImpactCrew` and the Feature Agent are both separate, user-triggered actions that can happen any time after ingestion, or never — `ImpactCrew` is a second, distinct `Crew` object (its own two agents) rather than a re-run of `IngestionCrew`, since it starts cold and has to read the earlier reports back out of Postgres instead of getting them via CrewAI's in-crew context passing. Redis isn't drawn as a pipeline stage because it isn't one — it's the Celery broker and the backing store for live progress events, sitting underneath the whole thing rather than inside it.

---

## How the pipeline works

### Node 1 — Parser (`src/codegrapher/parser/`)

Two kinds of facts come out of this node, and they carry different confidence levels:

| Fact type | Reliability | Example |
|---|---|---|
| Grammar facts | 100% certain — there's only one way a given piece of syntax parses | function/class names, imports, call sites |
| Framework heuristics | Pattern-matched against known conventions, not guaranteed | "is this class an ORM model?", "does this function mutate the database?", "is this an HTTP route?" |

- **Python** (`ast_parser.py`) — uses the standard library `ast` module. ORM detection is driven by a **framework profile registry** (`frameworks.py`) covering **SQLAlchemy** and **Django** conventions, so adding a third ORM is "register a new profile," not "rewrite the parser." Mutation tracking follows variable reassignment and one level of same-file helper-function calls, and explicitly surfaces `unresolved_mutation_calls` instead of silently dropping ambiguous cases.
- **JavaScript / TypeScript** (`js_ts_parser.py`) — uses **Tree-Sitter**. Extracts functions (declarations, arrow functions, class methods), classes, imports (both ESM `import` and CommonJS `require`), calls, and Express-style routes (`router.post(path, handler)`). ORM/mutation detection is intentionally **not** implemented for JS/TS — see [Known Limitations](#known-limitations-read-this).

Both parsers emit the same shape, merged by `parse_repo()` under one `repo_name`, so everything downstream is language-agnostic.

### Node 2 — Storage Sync (`src/codegrapher/storage/`)

Pure mechanical translation, no LLM involved:

- **`graph_sync.py`** — one Cypher `MERGE` per fact. `File`/`Class`/`Function`/`Field` become nodes; `DEFINES`/`CALLS`/`MUTATES`/`RELATES_TO`/`IMPORTS`/`CALLS_EXTERNAL`/`USES_EXTERNAL_SERVICE` become edges. Every node is namespaced by `repo_name` so multiple repos can share one Neo4j instance without collisions.
- **`vector_sync.py`** — since there's no raw source text to embed (only structural metadata), each function/class gets a short synthesized description ("Function `charge_card` in `billing.py`. Calls: `stripe.Client`. Uses external service: stripe.") which is what actually gets embedded via **fastembed** (ONNX-based, no PyTorch/CUDA dependency) and stored in **Qdrant**.

### IngestionCrew (`src/codegrapher/crews/ingestion_crew/`)

One CrewAI `Crew`, `Process.sequential`, always the same four tasks — no Flow, no conditional branches:

- **Cartographer** (`map_architecture_task`) and **ORM Schema Agent** (`extract_schema_task`) both have `async_execution: true` and run **in parallel** — they're independent, so there's no reason to serialize them.
- **`report_joiner`** (`join_reports_task`, running on the small `llama-3.1-8b-instant` model) exists purely to satisfy CrewAI's rule that a sequential crew can end with at most one trailing async task; it does no analysis of its own, only reproduces both reports verbatim.
- **Anti-Pattern Agent** (`anti_pattern_task`) runs last, reading the architecture and schema reports through CrewAI's own automatic sequential context-passing — every task in a sequential crew implicitly receives every earlier task's output, so nothing here is threaded manually between agents in Python.

This crew was originally a two-sub-crew `Flow`, then briefly grew a fifth, conditional Impact Analysis task for a proposed-edit-at-ingestion-time path the real app never actually exercises (the `/repos` endpoint never accepts a proposed edit). Both were simplified away once neither was solving a real problem — see [Design notes](#design-notes-worth-knowing).

### ImpactCrew (`src/codegrapher/crews/impact_crew/`)

A second, separate `Crew` — **not** a re-run of `IngestionCrew`. Triggered on demand by `POST /repos/{job_id}/impact`, any time after ingestion (or never). Its two agents, **Impact Analysis & Blast Radius Agent** and **Architectural Anti-Pattern Agent**, run sequentially and are fed `architecture_report`/`schema_report` as explicit template inputs pulled back out of Postgres — this crew starts cold, with no in-memory context from the original ingestion run, so it can't rely on CrewAI's automatic context-passing the way `IngestionCrew`'s tasks can.

### Feature Agent (`src/codegrapher/crews/feature_agent/`)

Deliberately **not** a `Crew` at all. One agent has nothing to coordinate with, so it's a plain `Agent` + `Task` call — and it's triggered on demand (`request_feature()`), whenever a user actually asks for something, independent of when ingestion happened.

### Node 3 — Graph Query API (`src/codegrapher/api/graph_query.py`, `sequence_diagram.py`)

Folded into the API layer rather than built as a standalone script, since its whole job is formatting graph data for a UI to render:

- `GET /repos/{id}/graph` → Cytoscape.js-shaped JSON, queried live from Neo4j.
- `GET /repos/{id}/sequence/{function}` → a Mermaid sequence diagram of that function's call chain (up to 4 hops, following both in-repo `CALLS` and `CALLS_EXTERNAL` edges).

### API + Frontend (`src/codegrapher/api/`, `frontend/`)

- **FastAPI** wraps the whole pipeline behind HTTP: submit a repo, poll status, stream live progress over **SSE** (backed by a Redis list, so a client connecting mid-run still sees full history), pull the graph, pull a sequence diagram, request a feature.
- **Celery + Redis** run ingestion asynchronously — parsing + syncing + two sub-crews can take a while, so it doesn't block the request.
- **Postgres** persists every job's status and reports, so the API is stateless across requests — nothing relies on in-memory state from the Celery worker process.
- **Next.js** frontend: submit a repo (URL or path), watch live logs stream in, view all four reports, explore the synced graph, render a sequence diagram for any function, and request a feature with the result shown in a **Monaco** editor.

---

## Tech stack

<table>
<tr><th>Layer</th><th>Technology</th><th>Why</th></tr>

<tr><td rowspan="4"><strong>Agents & LLM</strong></td>
<td><a href="https://github.com/crewAIInc/crewAI">CrewAI</a> (Crews, Agents, Tasks)</td><td>Multi-agent orchestration — IngestionCrew (always-on), ImpactCrew and the Feature Agent (both on-demand)</td></tr>
<tr><td>Groq (Llama 3.3 70B) via LiteLLM</td><td>Fast inference for every agent — CrewAI 1.x talks to it through its native LiteLLM path, no LangChain</td></tr>
<tr><td>Custom <code>GroqLLM</code> wrapper (<code>llms.py</code>)</td><td>Strips a CrewAI/LiteLLM prompt-cache flag Groq rejects; retries short rate limits, fails fast on long ones instead of hanging a worker</td></tr>
<tr><td>Framework profile registry (<code>frameworks.py</code>)</td><td>Pluggable ORM-convention detection (SQLAlchemy, Django) instead of one hardcoded set</td></tr>

<tr><td rowspan="2"><strong>Parsing (Node 1)</strong></td>
<td>Python <code>ast</code> (stdlib)</td><td>Deterministic Python parsing — no dependency, no LLM</td></tr>
<tr><td><a href="https://tree-sitter.github.io/tree-sitter/">Tree-Sitter</a> (JS/TS grammars)</td><td>Deterministic JS/TS parsing via the same grammar-first philosophy</td></tr>

<tr><td rowspan="2"><strong>Storage (Node 2)</strong></td>
<td><a href="https://neo4j.com/">Neo4j</a></td><td>Graph database — structural facts as real nodes/edges, fast N-hop traversal</td></tr>
<tr><td><a href="https://qdrant.tech/">Qdrant</a> + <a href="https://github.com/qdrant/fastembed">fastembed</a></td><td>Vector search over synthesized symbol descriptions; fastembed is ONNX-based (no PyTorch/CUDA)</td></tr>

<tr><td rowspan="5"><strong>Backend / API</strong></td>
<td><a href="https://fastapi.tiangolo.com/">FastAPI</a></td><td>REST + SSE endpoints wrapping the whole pipeline</td></tr>
<tr><td><a href="https://docs.celeryq.dev/">Celery</a> + Redis</td><td>Async ingestion jobs, run with <code>--pool=solo</code> (prefork breaks the async Postgres engine — see Design Notes)</td></tr>
<tr><td>SQLAlchemy 2.0 (async) + asyncpg</td><td>Job/report persistence</td></tr>
<tr><td>PostgreSQL</td><td>Job history and report storage</td></tr>
<tr><td>Redis</td><td>Celery broker + live SSE progress event log</td></tr>

<tr><td rowspan="6"><strong>Frontend</strong></td>
<td><a href="https://nextjs.org/">Next.js 16</a> (App Router)</td><td>React framework, client-rendered dashboard</td></tr>
<tr><td>TypeScript + Tailwind CSS 4</td><td>Type safety, styling</td></tr>
<tr><td><a href="https://js.cytoscape.org/">Cytoscape.js</a> (<code>react-cytoscapejs</code>)</td><td>Interactive graph rendering</td></tr>
<tr><td><a href="https://www.mermaidchart.com/">Mermaid</a></td><td>Call-sequence diagram rendering, client-side</td></tr>
<tr><td><a href="https://microsoft.github.io/monaco-editor/">Monaco Editor</a></td><td>Syntax-highlighted, read-only rendering of generated feature stubs</td></tr>
<tr><td>Server-Sent Events (native <code>EventSource</code>)</td><td>Live ingestion log streaming, no extra client library</td></tr>

<tr><td rowspan="3"><strong>DevOps</strong></td>
<td>Docker + Docker Compose</td><td>Infra services (Neo4j/Qdrant/Postgres/Redis) always containerized; API/worker/frontend images written and ready (<a href="#known-limitations-read-this">see limitations</a>)</td></tr>
<tr><td><a href="https://docs.astral.sh/uv/">uv</a></td><td>Python dependency management, fast installs, lockfile</td></tr>
<tr><td>npm</td><td>Frontend dependency management</td></tr>

</table>

---

## Project structure

```
repo-graph-builder/
├── docker-compose.yml          # neo4j, qdrant, postgres, redis, api, worker, frontend
├── Dockerfile.api               # shared image for the FastAPI server + Celery worker
├── pyproject.toml / uv.lock
│
├── src/codegrapher/
│   ├── parser/
│   │   ├── ast_parser.py        # Node 1 — Python (stdlib ast)
│   │   ├── js_ts_parser.py      # Node 1 — JS/TS (Tree-Sitter)
│   │   └── frameworks.py        # ORM profile registry (SQLAlchemy, Django)
│   │
│   ├── storage/
│   │   ├── graph_sync.py        # Node 2 — Neo4j
│   │   └── vector_sync.py       # Node 2 — Qdrant (fastembed)
│   │
│   ├── crews/
│   │   ├── ingestion_crew/      # Always-on Crew: Cartographer + ORM Schema Agent
│   │   │                        # (parallel) -> report_joiner -> Anti-Pattern Agent
│   │   ├── impact_crew/         # On-demand Crew: Impact Analysis + Anti-Pattern Agent
│   │   └── feature_agent/       # On-demand Feature Architect (no Crew wrapper)
│   │
│   ├── ingestion_state.py       # IngestionState — plain data container passed to
│   │                            # the Feature Agent, populated from Postgres
│   │
│   ├── llms.py                  # GroqLLM wrapper (cache-flag fix, rate-limit retry)
│   │
│   ├── api/
│   │   ├── server.py            # FastAPI app — all HTTP/SSE endpoints
│   │   ├── tasks.py             # Celery tasks: run_ingestion, run_impact_analysis
│   │   ├── models.py            # IngestionJob (Postgres)
│   │   ├── graph_query.py       # Node 3 — Cytoscape JSON
│   │   ├── sequence_diagram.py  # Node 3 — Mermaid diagrams
│   │   ├── repo_source.py       # git-clone-or-local-path resolution
│   │   └── progress.py          # Redis-backed live event log
│   │
│   └── sample_data/             # toy_repo (FastAPI+SQLAlchemy), toy_django_repo,
│                                 # toy_express_repo — fixtures proving each parser path
│
└── frontend/
    ├── Dockerfile
    └── src/app/page.tsx          # the whole UI: submit form, live logs, reports,
                                   # Cytoscape graph, Mermaid diagrams, Monaco stub viewer
```

---

## Getting started

### Prerequisites

- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- Node.js 22+, npm
- Docker + Docker Compose
- A [Groq API key](https://console.groq.com/)

### 1. Environment

```bash
cp .env.example .env
# fill in GROQ_API_KEY — the rest of the defaults match docker-compose.yml
```

### 2. Infrastructure

```bash
docker compose up -d neo4j qdrant postgres redis
```

### 3. Backend

```bash
uv sync
uv run uvicorn codegrapher.api.server:app --port 8000          # terminal 1
uv run celery -A codegrapher.api.celery_app worker --loglevel=info --pool=solo   # terminal 2
```

> `--pool=solo` is required — see [Design Notes](#design-notes-worth-knowing).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`, submit a repo (try `src/codegrapher/sample_data/toy_repo` as an absolute path, or any public git URL), and watch it go.

### Or: everything in containers

```bash
docker compose up -d --build
```

(Application images are written and compose-wired but untested in this environment — see [Known Limitations](#known-limitations-read-this).)

---

## API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/repos` | Submit a repo (`repo_path`: git URL or local path) — returns a `job_id`, kicks off `IngestionCrew` asynchronously |
| `GET` | `/repos/{job_id}` | Poll job status (`pending`/`running`/`done`/`failed`) and the four reports once done |
| `GET` | `/repos/{job_id}/events` | SSE stream of live ingestion progress |
| `GET` | `/repos/{job_id}/graph` | Cytoscape.js-shaped JSON of the synced graph |
| `GET` | `/repos/{job_id}/sequence/{function_name}` | Mermaid sequence diagram for one function's call chain |
| `POST` | `/repos/{job_id}/impact` | On-demand impact analysis (`proposed_edit`: plain-language description) — runs `ImpactCrew` against the already-ingested repo's saved reports |
| `POST` | `/repos/{job_id}/feature` | On-demand feature stub generation (`feature_request`: plain-language description) |

---

## Accuracy testing — not just "does it run"

A crew that runs without crashing and a crew that's *right* are different claims. Every agent below was tested against ground truth I verified independently (real GitHub repos, or synthetic repos built specifically to contain a known violation), not just exercised until it returned 200.

**Reliable — verified correct across languages, frameworks, and adversarial cases:**
- **Both parsers** (Python `ast`, JS/TS Tree-Sitter): every import/call/route/field checked against source, zero errors found.
- **Cartographer / ORM Schema Agent**: correct across a Python+SQLAlchemy toy repo, a Django repo, a JS/Express repo, and one real 15-file production repo (architecture layers, all 5 routes, and the ORM-model-vs-Pydantic-schema distinction all verified against the actual GitHub source) — and it correctly *declines* to invent files it wasn't given data for, rather than guessing.
- **Anti-Pattern Agent**: correctly caught real circular dependencies (including an indirect 3-file cycle), a real god object, and real tight coupling on synthetic repos built to contain them — and correctly reported "none found" on clean code, including the real repo above.
- **Impact Analysis**: correctly distinguished a genuine high-impact edit (traced mutation + external dependency + FK relation) from a genuinely isolated no-impact edit (nothing calls the changed function) — and, after a fix, correctly holds call-graph boundaries instead of treating "same file" as "reachable," and correctly refuses to substitute a different real function when the requested edit target doesn't exist.

**Not reliable — a real, demonstrated limitation, not a hedge:** the **Feature Agent** (code-stub generation) failed 4 out of roughly 6 adversarial test cases before each was individually patched: inventing an ungrounded status value, repurposing an existing field for an unrelated meaning, silently regenerating an already-existing endpoint as if it were new, and — worst — producing a stub that called a real function with the wrong argument shape (would raise `TypeError` if actually run), while using an ORM access pattern that isn't used anywhere else in the codebase. Each was caught and fixed with a more explicit prompt constraint, but the failure modes kept recurring on new axes rather than plateauing, which is the actual signal here: **this specific task (synthesizing new code that must stay correct against real signatures) is past what a 70B open model reliably does**, even though the exact same model handles the other four agents' extraction/citation tasks well. The other agents only have to report what's already in the facts; this one has to invent something new and get every detail of it right — a categorically harder bar, and the evidence bears that out. Treat feature-stub output as a rough draft that needs real review, not as trustworthy generated code.

---

## Known limitations (read this)

Being upfront about scope boundaries is part of the design, not an afterthought:

- **The Feature Agent needs a stronger model to be production-reliable** — see [Accuracy testing](#accuracy-testing--not-just-does-it-run) above for the specific, demonstrated failure modes. This is the one component of the system that isn't there yet, and it's a model-capability ceiling, not a prompt-engineering gap.
- **Heuristics can be wrong, on purpose transparently.** "Is this an ORM model?", "does this mutate the database?" are pattern-matches against common conventions, not certainties. Unresolved mutation calls are surfaced explicitly (`unresolved_mutation_calls`) instead of silently dropped.
- **JS/TS parsing has no ORM/mutation detection.** Sequelize, Mongoose, Prisma, and TypeORM each have different-enough conventions that replicating the Python side's heuristics per-framework is a separate, larger piece of work — not built here.
- **Same-file mutation tracing only goes one level deep**, and matches helper functions by name only (not fully-qualified) — a real limitation on codebases with naming collisions or deeper call chains.
- **Docker application images are untested.** `Dockerfile.api` and `frontend/Dockerfile` are written and `docker compose config` validates cleanly, but the actual build was never completed in the development environment (a restrictive network blocked container-level internet access). Infra containers (Neo4j/Qdrant/Postgres/Redis) run and were used throughout.
- **Express route detection only covers named handlers** (`router.post("/x", handlerName)`) — an inline handler (`router.post("/x", (req, res) => {...})`) has no name to attach the route fact to.

---

## Design notes worth knowing

- **Why Celery runs with `--pool=solo`:** the default prefork pool forks worker processes *after* SQLAlchemy's async engine is created, and forked children end up sharing a corrupted asyncpg connection. Fixed with `NullPool` (no connection reuse across event loops) *and* `--pool=solo` (no forking at all) — both were needed; each alone still failed under real testing.
- **Why `join_reports_task` exists:** Cartographer and the ORM Schema Agent are independent, so they run in parallel (`async_execution: true`). CrewAI requires a crew to end with at most one trailing async task, so a third task exists purely as the synchronization point — it does no analysis, it only reproduces both reports verbatim.
- **Why there's no `Flow` anymore:** the pipeline originally chained two separate `Crew`s (Cartography, Impact) with a CrewAI `Flow`. Once every downstream task's job was just "read the previous tasks' outputs," CrewAI's own automatic sequential context-passing did that for free — the `Flow` wasn't solving a coordination problem that was still there, so it was removed and the two crews merged into one `IngestionCrew`.
- **Why Impact Analysis isn't in `IngestionCrew`:** it was, briefly, as a 5th task added only when a `proposed_edit` was supplied at submit time. But the real `/repos` endpoint never accepts one — only the separate `/repos/{job_id}/impact` endpoint does, later, on demand. Keeping a conditional branch for an input that can never arrive was complexity with no payoff, so it was pulled out entirely; `ImpactCrew` is now the only place impact analysis ever runs.
- **Why `ImpactCrew` is a separate `Crew`, not a re-run of `IngestionCrew`:** `IngestionCrew`'s later tasks get the architecture/schema reports "for free" via CrewAI's context-passing, because they run in the same crew execution as the agents that produced them. `ImpactCrew` starts cold, possibly days later, with no such context available — so it takes `architecture_report`/`schema_report` as explicit inputs, read back out of Postgres. Same underlying reports, two different plumbing mechanisms, because the two crews start from different circumstances.
- **Why the Feature Agent isn't a `Crew`:** a `Crew` coordinates *multiple* agents through a process. With one agent, there's nothing to coordinate — wrapping it would be indirection with no behavior behind it.
- **Why repo_name derivation is centralized (`repo_source.py`):** Neo4j/Qdrant namespace everything by `repo_name`, derived from the directory's basename. A cloned git repo lands in a randomly-named temp directory unless the clone step explicitly names it after the repo — so `derive_repo_name()` is the single source of truth both the sync step and the later graph-lookup step call, keeping them from drifting apart.
- **Why `report_joiner` runs on the full model, not the 8B one:** the cheap model was tried first since verbatim transcription seemed like it needed no real reasoning. Live testing proved otherwise — it renamed sections/fields and fabricated an "Indexes" subsection neither source report ever stated. Switched to the same model as every other agent; a follow-up run confirmed both reports now come through as an exact verbatim substring of the joined output.
- **Why Impact Analysis explicitly forbids "reached this file, so everything in it is reachable":** a synthetic 3-hop test showed the agent would sometimes pull in a sibling function in an already-reached file even with no real call edge to it, inflating blast radius with things that can't actually be affected. Fixed by requiring every hop to cite a concrete edge from the specific function just reached, not the file it lives in.
- **Why Impact Analysis explicitly forbids substituting a different real symbol for a missing one:** asked to assess an edit to a function that doesn't exist anywhere in the repo, the agent used to quietly analyze a different, real function that happened to match the traced facts instead — producing a confident, detailed report for the wrong target, with the substitution buried in one sentence. A wrong-but-confident answer is worse than a refusal, so it now names the missing symbol and stops.
- **Why the Feature Agent explicitly checks for existing functionality first:** asked for a feature that already existed almost verbatim in the repo (same route, same method, same function name — visible directly in its own input), it silently generated a near-duplicate with no mention that the functionality already existed. It now has to name the existing match and only proceed if there's a genuine, evidenced gap.

---

<p align="center"><sub>Built phase by phase — agentic core first, then Node 1, then Node 2, then the full-stack wrap — with an actual human reviewing each stage.</sub></p>
