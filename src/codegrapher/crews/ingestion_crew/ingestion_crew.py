from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, task

from codegrapher.llms import groq_llm


@CrewBase
class IngestionCrew:
    """The whole automatic ingestion sequence, as one Crew.

    Cartographer and the ORM Schema Agent run in parallel (async_execution).
    Everything after join_reports_task uses CrewAI's own sequential context
    passing - every task in a sequential Crew automatically receives every
    earlier task's output as context, so Anti-Pattern reads the
    architecture/schema reports without us threading strings between
    separate kickoff() calls in Python. This replaced an earlier
    design with two separate Crews stitched together by a Flow - once the
    framework's own context-passing does the job, the Flow wasn't solving
    a real problem, just adding structure for its own sake.

    join_reports_task exists purely to satisfy CrewAI's rule that a Crew
    must end with at most one trailing async task - it runs on a small,
    cheap model (report_joiner, llama-3.1-8b-instant) since transcribing
    two already-finished reports verbatim needs no real reasoning.

    Impact analysis is deliberately not part of this crew - the real app
    never has a proposed edit at ingestion time, only later, as a separate
    user-triggered action (see crews/impact_crew/). This crew always runs
    the same four tasks, no conditional branch.
    """

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def cartographer(self) -> Agent:
        return Agent(config=self.agents_config["cartographer"], llm=groq_llm(), verbose=True)

    @agent
    def orm_schema_agent(self) -> Agent:
        return Agent(config=self.agents_config["orm_schema_agent"], llm=groq_llm(), verbose=True)

    @agent
    def report_joiner(self) -> Agent:
        return Agent(
            config=self.agents_config["report_joiner"],
            llm=groq_llm(model="llama-3.1-8b-instant"),
            verbose=True,
        )

    @agent
    def anti_pattern_agent(self) -> Agent:
        return Agent(config=self.agents_config["anti_pattern_agent"], llm=groq_llm(), verbose=True)

    @task
    def map_architecture_task(self) -> Task:
        return Task(config=self.tasks_config["map_architecture_task"])

    @task
    def extract_schema_task(self) -> Task:
        return Task(config=self.tasks_config["extract_schema_task"])

    @task
    def join_reports_task(self) -> Task:
        return Task(config=self.tasks_config["join_reports_task"])

    @task
    def anti_pattern_task(self) -> Task:
        return Task(config=self.tasks_config["anti_pattern_task"])


def build_ingestion_crew() -> Crew:
    """Assembles the real Crew for a run: always the same four tasks."""
    ingestion = IngestionCrew()
    agents = [
        ingestion.cartographer(),
        ingestion.orm_schema_agent(),
        ingestion.report_joiner(),
        ingestion.anti_pattern_agent(),
    ]
    tasks = [
        ingestion.map_architecture_task(),
        ingestion.extract_schema_task(),
        ingestion.join_reports_task(),
        ingestion.anti_pattern_task(),
    ]
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=True)
