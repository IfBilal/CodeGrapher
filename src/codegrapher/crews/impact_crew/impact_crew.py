from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from codegrapher.llms import groq_llm


@CrewBase
class ImpactCrew:
    """The on-demand impact-analysis crew: Impact Analysis + Anti-Pattern.

    Triggered separately from IngestionCrew, any time after ingestion (or
    never) via POST /repos/{job_id}/impact - never as part of the automatic
    ingestion sequence. Consumes the parsed repo JSON plus the architecture
    and schema reports produced by the original IngestionCrew run, passed
    in as plain-text inputs read back out of Postgres. This crew starts
    cold, with no in-memory context from that earlier run, so it can't use
    CrewAI's automatic sequential context-passing the way IngestionCrew's
    own tasks can - the caller has to feed the reports in explicitly.
    """

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def impact_analysis_agent(self) -> Agent:
        return Agent(config=self.agents_config["impact_analysis_agent"], llm=groq_llm(), verbose=True)

    @agent
    def anti_pattern_agent(self) -> Agent:
        return Agent(config=self.agents_config["anti_pattern_agent"], llm=groq_llm(), verbose=True)

    @task
    def impact_analysis_task(self) -> Task:
        return Task(config=self.tasks_config["impact_analysis_task"])

    @task
    def anti_pattern_task(self) -> Task:
        return Task(config=self.tasks_config["anti_pattern_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
