"""The Celery task that actually runs a repo through the whole pipeline:
Node 1 (parse) -> Node 2 (Neo4j + Qdrant sync) -> the ingestion Crew
(Cartography -> Anti-Pattern, one Crew - see ingestion_crew.py for why this
isn't a Flow anymore). Runs in a separate
worker process from the FastAPI app, so nothing here can share in-memory
state with the API - every result gets written to Postgres, and every
future read comes from Postgres, not from whatever's sitting in a Python
variable somewhere.

run_impact_analysis (below) is a different thing entirely: an on-demand
re-assessment against an already-ingested repo's saved reports, not part of
this automatic sequence - see its own docstring.
"""

import asyncio
import json
import os
import uuid

from neo4j import GraphDatabase
from qdrant_client import QdrantClient

from codegrapher.api.celery_app import celery_app
from codegrapher.api.db import SessionLocal
from codegrapher.api.models import IngestionJob
from codegrapher.api.progress import publish_event
from codegrapher.api.repo_source import is_git_url, resolve_repo
from codegrapher.crews.impact_crew.impact_crew import ImpactCrew
from codegrapher.crews.ingestion_crew.ingestion_crew import build_ingestion_crew
from codegrapher.parser.ast_parser import parse_repo
from codegrapher.storage.graph_sync import sync_to_neo4j
from codegrapher.storage.vector_sync import sync_to_qdrant


@celery_app.task(name="codegrapher.run_ingestion")
def run_ingestion(job_id: str, repo_path: str) -> None:
    asyncio.run(_run_ingestion_async(job_id, repo_path))


@celery_app.task(name="codegrapher.run_impact_analysis")
def run_impact_analysis(job_id: str, proposed_edit: str) -> None:
    """Assess a user-supplied change against an already-ingested repository."""
    asyncio.run(_run_impact_analysis_async(job_id, proposed_edit))


async def _set_status(job_id: str, **fields) -> None:
    async with SessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        for key, value in fields.items():
            setattr(job, key, value)
        await session.commit()


async def _run_ingestion_async(job_id: str, repo_path: str) -> None:
    await _set_status(job_id, status="running")

    try:
        if is_git_url(repo_path):
            publish_event(job_id, f"Cloning {repo_path}...")
        with resolve_repo(repo_path) as local_path:
            publish_event(job_id, "Node 1: parsing repository with the AST parser...")
            parsed_repo = parse_repo(local_path)
        file_count = len(parsed_repo["files"])
        publish_event(job_id, f"Node 1: parsed {file_count} files.")

        publish_event(job_id, "Node 2: syncing parsed facts into Neo4j...")
        neo4j_driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
        )
        try:
            sync_to_neo4j(parsed_repo, neo4j_driver)
        finally:
            neo4j_driver.close()
        publish_event(job_id, "Node 2: Neo4j sync complete.")

        publish_event(job_id, "Node 2: embedding code facts into Qdrant...")
        qdrant_client = QdrantClient(url=os.environ["QDRANT_URL"])
        sync_to_qdrant(parsed_repo, qdrant_client)
        publish_event(job_id, "Node 2: Qdrant sync complete.")

        parsed_repo_json = json.dumps(parsed_repo)
        publish_event(
            job_id,
            "Ingestion Crew starting: Cartographer + ORM Schema Agent (parallel), then "
            "Anti-Pattern Agent. This runs the whole crew to completion before returning, "
            "so no intermediate update lands here until it's done.",
        )
        crew = build_ingestion_crew()
        result = crew.kickoff(inputs={"parsed_repo": parsed_repo_json})
        publish_event(job_id, "Ingestion crew complete.")

        # Task order is fixed by build_ingestion_crew(): architecture, schema,
        # join (discarded - see IngestionCrew's docstring), anti-pattern.
        outputs = result.tasks_output
        await _set_status(
            job_id,
            status="done",
            parsed_repo_json=parsed_repo_json,
            architecture_report=outputs[0].raw,
            schema_report=outputs[1].raw,
            anti_pattern_report=outputs[3].raw,
        )
    except Exception as exc:
        publish_event(job_id, f"Failed: {exc}")
        await _set_status(job_id, status="failed", error=str(exc))
        raise


async def _run_impact_analysis_async(job_id: str, proposed_edit: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        if job is None or not job.parsed_repo_json:
            return
        parsed_repo = job.parsed_repo_json
        architecture_report = job.architecture_report or ""
        schema_report = job.schema_report or ""

    try:
        result = ImpactCrew().crew().kickoff(
            inputs={
                "parsed_repo": parsed_repo,
                "proposed_edit": proposed_edit,
                "architecture_report": architecture_report,
                "schema_report": schema_report,
            }
        )
        await _set_status(
            job_id,
            impact_report=result.tasks_output[0].raw,
            anti_pattern_report=result.tasks_output[1].raw,
            error=None,
        )
    except Exception as exc:
        await _set_status(job_id, error=f"Impact analysis failed: {exc}")
        raise
