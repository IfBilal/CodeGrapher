"""The Celery task that actually runs a repo through the whole pipeline:
Node 1 (parse) -> Node 2 (Neo4j + Qdrant sync) -> ingestion Flow (Cartography
-> Impact analysis). Runs in a separate worker process from the FastAPI app,
so nothing here can share in-memory state with the API - every result gets
written to Postgres, and every future read comes from Postgres, not from
whatever's sitting in a Python variable somewhere.
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
from codegrapher.flows.ingestion_flow import IngestionFlow
from codegrapher.parser.ast_parser import parse_repo
from codegrapher.storage.graph_sync import sync_to_neo4j
from codegrapher.storage.vector_sync import sync_to_qdrant


@celery_app.task(name="codegrapher.run_ingestion")
def run_ingestion(job_id: str, repo_path: str, proposed_edit: str) -> None:
    asyncio.run(_run_ingestion_async(job_id, repo_path, proposed_edit))


async def _set_status(job_id: str, **fields) -> None:
    async with SessionLocal() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        for key, value in fields.items():
            setattr(job, key, value)
        await session.commit()


async def _run_ingestion_async(job_id: str, repo_path: str, proposed_edit: str) -> None:
    await _set_status(job_id, status="running")

    try:
        parsed_repo = parse_repo(repo_path)

        neo4j_driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
        )
        sync_to_neo4j(parsed_repo, neo4j_driver)
        neo4j_driver.close()

        qdrant_client = QdrantClient(url=os.environ["QDRANT_URL"])
        sync_to_qdrant(parsed_repo, qdrant_client)

        parsed_repo_json = json.dumps(parsed_repo)
        ingestion_flow = IngestionFlow()
        ingestion_flow.kickoff(inputs={"parsed_repo": parsed_repo_json, "proposed_edit": proposed_edit})
        state = ingestion_flow.state

        await _set_status(
            job_id,
            status="done",
            parsed_repo_json=parsed_repo_json,
            architecture_report=state.architecture_report,
            schema_report=state.schema_report,
            impact_report=state.impact_report,
            anti_pattern_report=state.anti_pattern_report,
        )
    except Exception as exc:
        await _set_status(job_id, status="failed", error=str(exc))
        raise
