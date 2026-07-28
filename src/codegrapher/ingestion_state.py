from pydantic import BaseModel


class IngestionState(BaseModel):
    """Everything a repo submission produces during ingestion, and
    everything the on-demand Feature Agent needs to act on it afterward.

    Not tied to any particular orchestration mechanism (Crew, Flow, or
    otherwise) - it's a plain data container, populated by
    api/tasks.py's ingestion Crew and read back out of Postgres by
    server.py to reconstruct it for a later, separate Feature Agent call.
    """

    parsed_repo: str = ""
    proposed_edit: str = ""
    architecture_report: str = ""
    schema_report: str = ""
    impact_report: str = ""
    anti_pattern_report: str = ""
