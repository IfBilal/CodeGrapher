from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from codegrapher.llms import groq_llm


@CrewBase
class CartographyCrew:
    """Sub-Crew 1: Structural Cartography & Schema Mapping.

    Takes the parser's structural JSON for a repo and produces two reports:
    an architectural layer map and an ORM/mutation schema map. This is the
    first sub-crew in the pipeline; later sub-crews (Impact Analysis,
    Feature Architect) will consume its output.

    The two tasks are independent - neither needs the other's output - so
    they run concurrently (async_execution). CrewAI requires a Crew to end
    with at most one trailing async task, so join_reports_task exists purely
    to be that synchronization point: it waits for both, and (per its own
    task description) does no analysis of its own. Callers still read the
    original two reports from tasks_output[0] and [1] - the join task's
    output isn't used downstream, it only exists to make the parallelism
    valid within Crew's rules.
    """

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def cartographer(self) -> Agent:
        return Agent(config=self.agents_config["cartographer"], llm=groq_llm(), verbose=True)

    @agent
    def orm_schema_agent(self) -> Agent:
        return Agent(config=self.agents_config["orm_schema_agent"], llm=groq_llm(), verbose=True)

    @task
    def map_architecture_task(self) -> Task:
        return Task(config=self.tasks_config["map_architecture_task"])

    @task
    def extract_schema_task(self) -> Task:
        return Task(config=self.tasks_config["extract_schema_task"])

    @task
    def join_reports_task(self) -> Task:
        return Task(config=self.tasks_config["join_reports_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
