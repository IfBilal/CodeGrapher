import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase
from pydantic import BaseModel
from sqlalchemy import select

from codegrapher.api.db import SessionLocal, init_models
from codegrapher.api.graph_query import get_cytoscape_graph
from codegrapher.api.models import IngestionJob
from codegrapher.api.tasks import run_ingestion
from codegrapher.crews.feature_agent.feature_agent import request_feature
from codegrapher.flows.ingestion_flow import IngestionState

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
    repo_path: str
    proposed_edit: str = "General review: identify any changes across the codebase's mutation paths and risk areas."


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


@app.post("/repos", response_model=SubmitRepoResponse)
async def submit_repo(body: SubmitRepoRequest) -> SubmitRepoResponse:
    if not Path(body.repo_path).is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {body.repo_path}")

    job = IngestionJob(repo_path=body.repo_path)
    async with SessionLocal() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    run_ingestion.delay(str(job.id), body.repo_path, body.proposed_edit)
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


@app.get("/repos/{job_id}/graph")
async def get_graph(job_id: uuid.UUID) -> dict:
    job = await _get_job_or_404(job_id)
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not done yet")

    repo_name = Path(job.repo_path).name
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    )
    try:
        return get_cytoscape_graph(repo_name, driver)
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
    stub = await asyncio.to_thread(request_feature, body.feature_request, ingestion_state)
    return {"feature_stub": stub}


async def _get_job_or_404(job_id: uuid.UUID) -> IngestionJob:
    async with SessionLocal() as session:
        job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
