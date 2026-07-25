from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from codegrapher.crews.cartography_crew.cartography_crew import CartographyCrew
from codegrapher.crews.impact_crew.impact_crew import ImpactCrew


class IngestionState(BaseModel):
    """Everything the ingestion pipeline reads or produces.

    This is the state a repo submission actually needs, end to end: the raw
    parsed facts going in, and every report each stage adds going out.
    Sub-Crew 3 (Feature Architect) deliberately has no field here - it's not
    part of ingestion, it's a separate on-demand action a user triggers
    later against an already-ingested repo (see feature_agent.py).
    """

    parsed_repo: str = ""
    proposed_edit: str = ""
    architecture_report: str = ""
    schema_report: str = ""
    impact_report: str = ""
    anti_pattern_report: str = ""


class IngestionFlow(Flow[IngestionState]):
    """Sequential ingestion pipeline: Cartography -> Impact/Risk analysis.

    This is genuinely sequential and not just "sequential because it's
    simplest" - Impact Analysis needs the architecture/schema reports as
    input, so there's no meaningful way to run these two stages out of
    order or in parallel. That's different from Feature Architect, which
    depends on ingestion having already happened but isn't itself part of
    the ingestion sequence - it's triggered separately, whenever a user
    asks for a feature.
    """

    @start()
    def run_cartography(self) -> None:
        result = CartographyCrew().crew().kickoff(inputs={"parsed_repo": self.state.parsed_repo})
        self.state.architecture_report = result.tasks_output[0].raw
        self.state.schema_report = result.tasks_output[1].raw

    @listen(run_cartography)
    def run_impact_analysis(self) -> None:
        result = ImpactCrew().crew().kickoff(
            inputs={
                "parsed_repo": self.state.parsed_repo,
                "proposed_edit": self.state.proposed_edit,
                "architecture_report": self.state.architecture_report,
                "schema_report": self.state.schema_report,
            }
        )
        self.state.impact_report = result.tasks_output[0].raw
        self.state.anti_pattern_report = result.tasks_output[1].raw
