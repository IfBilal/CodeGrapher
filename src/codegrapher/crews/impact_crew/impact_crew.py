from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from codegrapher.llms import groq_llm


@CrewBase
class ImpactCrew:
    """Sub-Crew 2: Graph Traversal & Risk Analysis.

    Consumes the parsed repo JSON plus Sub-Crew 1's architecture and schema
    reports (passed in as plain-text inputs - these crews aren't chained via
    CrewAI's intra-crew `context`, since they're separate Crew objects; the
    caller is responsible for feeding one crew's output into the next one's
    inputs, which is exactly what a Flow will formalize in a later phase).
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
