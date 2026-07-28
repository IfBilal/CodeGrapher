import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from codegrapher.api.db import Base


class IngestionJob(Base):
    """One row per repo submitted for ingestion. Status moves
    pending -> running -> done (or failed), updated by the Celery task as
    it progresses through Node 1 -> Node 2 -> IngestionCrew. Report columns
    stay NULL until the crew actually produces them, so a client polling
    status can distinguish "still running" from "done but a report was
    empty" (shouldn't happen, but NULL vs "" makes the difference
    unambiguous instead of guessed from an empty string). impact_report
    stays NULL until the separate, on-demand ImpactCrew run happens, which
    may be much later or never.
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_path: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")  # pending | running | done | failed
    error: Mapped[str | None] = mapped_column(Text, default=None)

    parsed_repo_json: Mapped[str | None] = mapped_column(Text, default=None)
    architecture_report: Mapped[str | None] = mapped_column(Text, default=None)
    schema_report: Mapped[str | None] = mapped_column(Text, default=None)
    impact_report: Mapped[str | None] = mapped_column(Text, default=None)
    anti_pattern_report: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
