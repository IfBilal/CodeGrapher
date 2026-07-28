import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from neo4j import GraphDatabase
from pydantic import BaseModel
from sqlalchemy import select

from codegrapher.api.db import SessionLocal, init_models
from codegrapher.api.graph_query import get_cytoscape_graph
from codegrapher.api.models import IngestionJob
from codegrapher.api.progress import read_events
from codegrapher.api.repo_source import derive_repo_name, is_git_url
from codegrapher.api.sequence_diagram import get_call_sequence_diagram
from codegrapher.api.tasks import run_impact_analysis, run_ingestion
from codegrapher.crews.feature_agent.feature_agent import request_feature
from codegrapher.ingestion_state import IngestionState

load_dotenv()

app = FastAPI(title="CodeGrapher API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    await init_models()


class SubmitRepoRequest(BaseModel):
    # Either a git URL (http(s):// or ending in .git - shallow-cloned to a
    # temp dir and cleaned up after ingestion) or an already-local path.
    repo_path: str


class SubmitRepoResponse(BaseModel):
    job_id: uuid.UUID


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    repo_path: str
    status: str
    error: str | None
    architecture_report: str | None
    schema_report: str | None
    impact_report: str | None
    anti_pattern_report: str | None


class FeatureRequest(BaseModel):
    feature_request: str


class ImpactRequest(BaseModel):
    proposed_edit: str


@app.post("/repos", response_model=SubmitRepoResponse)
async def submit_repo(body: SubmitRepoRequest) -> SubmitRepoResponse:
    if not is_git_url(body.repo_path) and not Path(body.repo_path).is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory or a git URL: {body.repo_path}")

    job = IngestionJob(repo_path=body.repo_path)
    async with SessionLocal() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    run_ingestion.delay(str(job.id), body.repo_path)
    return SubmitRepoResponse(job_id=job.id)


@app.get("/repos/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: uuid.UUID) -> JobStatusResponse:
    job = await _get_job_or_404(job_id)
    return JobStatusResponse(
        job_id=job.id,
        repo_path=job.repo_path,
        status=job.status,
        error=job.error,
        architecture_report=job.architecture_report,
        schema_report=job.schema_report,
        impact_report=job.impact_report,
        anti_pattern_report=job.anti_pattern_report,
    )


@app.post("/repos/{job_id}/impact")
async def assess_impact(job_id: uuid.UUID, body: ImpactRequest) -> dict:
    job = await _get_job_or_404(job_id)
    if job.status != "done" or not job.parsed_repo_json:
        raise HTTPException(status_code=409, detail="Repository ingestion must finish before impact analysis can run")
    if not body.proposed_edit.strip():
        raise HTTPException(status_code=422, detail="Describe a proposed edit before running impact analysis")

    # Clear a previous result so the client can wait for this new request.
    async with SessionLocal() as session:
        stored_job = await session.get(IngestionJob, job_id)
        assert stored_job is not None
        stored_job.impact_report = None
        stored_job.error = None
        await session.commit()

    run_impact_analysis.delay(str(job_id), body.proposed_edit.strip())
    return {"status": "queued"}


@app.get("/repos/{job_id}/events")
async def stream_events(job_id: uuid.UUID) -> StreamingResponse:
    """SSE stream of ingestion progress. Reads from a persisted Redis list
    (see progress.py) rather than subscribing to pub/sub directly, so a
    client connecting after the job already started still gets every event
    from the beginning, not just whatever's published from that point on."""

    async def event_generator():
        job_id_str = str(job_id)
        next_index = 0
        while True:
            events = read_events(job_id_str, next_index)
            for event in events:
                yield f"data: {event}\n\n"
            next_index += len(events)

            async with SessionLocal() as session:
                job = await session.get(IngestionJob, job_id)
            if job is None or (job.status in ("done", "failed") and not events):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/repos/{job_id}/graph")
async def get_graph(job_id: uuid.UUID) -> dict:
    job = await _get_job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not done yet")

    repo_name = derive_repo_name(job.repo_path)
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    )
    try:
        return get_cytoscape_graph(repo_name, driver)
    finally:
        driver.close()


@app.get("/repos/{job_id}/sequence/{function_name}")
async def get_sequence_diagram(job_id: uuid.UUID, function_name: str) -> dict:
    job = await _get_job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not done yet")

    repo_name = derive_repo_name(job.repo_path)
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    )
    try:
        return {"mermaid": get_call_sequence_diagram(repo_name, function_name, driver)}
    finally:
        driver.close()


@app.post("/repos/{job_id}/feature")
async def submit_feature_request(job_id: uuid.UUID, body: FeatureRequest) -> dict:
    job = await _get_job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not done yet")

    ingestion_state = IngestionState(
        parsed_repo=job.parsed_repo_json or "",
        architecture_report=job.architecture_report or "",
        schema_report=job.schema_report or "",
        anti_pattern_report=job.anti_pattern_report or "",
    )
    # request_feature runs CrewAI synchronously, which can't execute inside
    # an already-running asyncio event loop (this endpoint's own loop) -
    # push it onto a separate thread instead of calling it directly.
    try:
        stub = await asyncio.to_thread(request_feature, body.feature_request, ingestion_state)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Feature generation failed: {exc}") from exc
    return {"feature_stub": stub}


async def _get_job_or_404(job_id: uuid.UUID) -> IngestionJob:
    async with SessionLocal() as session:
        job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
