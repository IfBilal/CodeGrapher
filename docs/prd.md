# Product Requirement Document (PRD)

## Project Title
**CodeGrapher: Autonomous Codebase Knowledge Graph & Impact Analysis Engine**

---

## 1. Executive Summary
**CodeGrapher** is an agentic AI platform engineered to eliminate codebase context loss, architectural debt, and silent breaking changes. By combining **deterministic Abstract Syntax Tree (AST) parsing**, **Graph Database indexing (Neo4j)**, **Vector Embeddings (Qdrant)**, and **CrewAI multi-agent orchestration** powered by **Groq (Llama 3.3 70B)**, CodeGrapher converts raw software repositories into an interactive, queryable Knowledge Graph.

Instead of relying on flat text search or standard RAG, CodeGrapher gives software teams structural visibility into their system control flows, ORM data mutations, and component dependencies.

---

## 2. Complete Technical Stack & Infrastructure Architecture

### 🛠️ Core Technology Matrix

| Layer | Component | Version / Library | Purpose |
| :--- | :--- | :--- | :--- |
| **Frontend Framework** | Next.js | `14.2+` (App Router) | Web Workspace, Streaming UI, Dashboard Layout |
| **Styling & Components** | Tailwind CSS + Shadcn UI | Tailwind `3.4+`, Radix UI | Dark-mode UI components, Sliders, Sheet drawers |
| **Graph Visualization** | Cytoscape.js | `react-cytoscapejs 1.2+` | 2D/3D Interactive Node/Edge Graph Rendering |
| **Code Editor Component** | Monaco Editor | `@monaco-editor/react 4.6+` | Code preview, syntax highlighting, diff viewer |
| **Backend API Server** | FastAPI | `0.110+` (Python 3.11) | Async REST endpoints, SSE, WebSocket manager |
| **Async Worker Queue** | Celery + Redis | Celery `5.3+`, Redis `7.2+` | Background repo ingestion & CrewAI Flow execution |
| **Agent Orchestration** | CrewAI Flows | `crewai 0.30+`, `langchain-groq` | Multi-agent coordination, routing, and Pydantic state |
| **LLM Inference Provider** | Groq LPU API | Llama-3.3-70b-versatile | Ultra-fast agent reasoning (~300+ tokens/sec) |
| **Graph Database** | Neo4j Community / Aura | `5.18+` (Bolt protocol) | Directional call trees, dependency paths, Cypher queries |
| **Vector Database** | Qdrant | `1.8+` (gRPC client) | Code snippet vector search and docstring embeddings |
| **Relational Metadata DB** | PostgreSQL | `16+` (SQLAlchemy 2.0 / asyncpg) | User sessions, job history, repository indexing traces |
| **Static Code Parsing** | Tree-Sitter & Native `ast` | `tree-sitter 0.21+` | Language-agnostic AST parsing (Python, TS/JS) |

---

## 3. High-Level Architecture & Flow System Diagram

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      TARGET REPOSITORY / SOURCE CODE                   │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ Clone / Ingest via Git
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │             NODE 1: Pure Python AST / Tree-Sitter Parser Node          │
 │  Deterministically parses files, extracts raw Nodes (Files, Classes,   │
 │  Functions, API Routes, ORM Models) and Edges (CALLS, IMPORTS, etc.).  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │               NODE 2: Pure Python Hybrid Storage Sync                  │
 │  Pushes directional call graph to Neo4j and indexes code snippet       │
 │  embeddings into Qdrant. Writes job metadata to PostgreSQL.            │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                  CREWAI MULTI-AGENT FLOW PIPELINE                      │
 │                 (Powered by Groq / Llama-3.3 70B Engine)               │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ SUB-CREW 1: Structural Cartography & Schema Mapping              │  │
 │  │                                                                  │  │
 │  │ • Agent 1: Codebase Cartographer Agent                            │  │
 │  │   Maps high-level module boundaries, architectural layers, and   │  │
 │  │   system entry points.                                           │  │
 │  │                                                                  │  │
 │  │ • Agent 2: Data & ORM Schema Agent                               │  │
 │  │   Extracts ORM models, foreign keys, cascade paths, and connects │  │
 │  │   mutations (INSERT/UPDATE/DELETE) to API routes.                │  │
 │  └──────────────────────────────────┬───────────────────────────────┘  │
 │                                     │                                  │
 │                                     ▼                                  │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ SUB-CREW 2: Graph Traversal & Risk Analysis                      │  │
 │  │                                                                  │  │
 │  │ • Agent 3: Impact Analysis & Blast Radius Agent                  │  │
 │  │   Traverses graph paths up to N-hops to calculate all downstream │  │
 │  │   components impacted by a proposed code edit.                   │  │
 │  │                                                                  │  │
 │  │ • Agent 4: Architectural Anti-Pattern Agent                      │  │
 │  │   Scans graph cycles for circular dependencies, god objects, and │  │
 │  │   tight coupling.                                                │  │
 │  └──────────────────────────────────┬───────────────────────────────┘  │
 │                                     │                                  │
 │                                     ▼                                  │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ SUB-CREW 3: Synthesis & Feature Architecture                     │  │
 │  │                                                                  │  │
 │  │ • Agent 5: Feature Architect & Contract Agent                    │  │
 │  │   Generates new code stubs adhering strictly to upstream interface│  │
 │  │   contracts, ORM patterns, and return types.                     │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │             NODE 3: Pure Python Graph Visualizer & Streamer            │
 │  Converts Neo4j paths into Cytoscape JSON + Mermaid sequence diagrams  │
 │  and streams updates via SSE / WebSockets to Redis Pub/Sub.            │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ WebSockets / SSE
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                        NEXT.JS 14 FRONTEND UI                          │
 │  (Interactive Cytoscape Graph Canvas, Monaco Editor, Live Agent Logs)  │
 └────────────────────────────────────────────────────────────────────────┘