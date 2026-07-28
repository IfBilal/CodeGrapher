from pathlib import Path
from typing import TYPE_CHECKING
import json
from collections import Counter

import yaml
from crewai import Agent, Task

from codegrapher.llms import groq_llm

if TYPE_CHECKING:
    from codegrapher.ingestion_state import IngestionState

_CONFIG_DIR = Path(__file__).parent / "config"


def request_feature(feature_request: str, ingestion_state: "IngestionState") -> str:
    """On-demand entry point: ask for a feature against an already-ingested repo.

    This is the boundary between IngestionCrew (auto-runs Cartography and
    Anti-Pattern for every submitted repo) and this stage (runs only when a
    user explicitly asks for a feature, possibly long after ingestion
    finished, possibly never at all for a given repo). Impact Analysis is
    neither of these - it's ImpactCrew, a third, separate on-demand crew.
    Takes the finished IngestionState directly so callers don't need to
    know which report field maps to which task placeholder.
    """
    return run_feature_architect(
        {
            "parsed_repo": ingestion_state.parsed_repo,
            "feature_request": feature_request,
            "primary_language": detect_primary_language(ingestion_state.parsed_repo),
            "architecture_report": ingestion_state.architecture_report,
            "schema_report": ingestion_state.schema_report,
            "anti_pattern_report": ingestion_state.anti_pattern_report,
        }
    )


def detect_primary_language(parsed_repo: str) -> str:
    try:
        parsed_files = json.loads(parsed_repo).get("files", [])
        languages = Counter(file.get("language") for file in parsed_files if file.get("language"))
        return languages.most_common(1)[0][0] if languages else "the repository's primary language"
    except (json.JSONDecodeError, TypeError, IndexError):
        return "the repository's primary language"


def run_feature_architect(inputs: dict[str, str]) -> str:
    """Synthesis & Feature Architecture stage (PRD's "Sub-Crew 3").

    Only one agent does real work here (the PRD's Sub-Crew 1 and 2 each
    needed two agents cross-checking or building on each other; this stage
    doesn't), so it's a plain Agent + Task, not a Crew. A Crew's job is
    coordinating multiple agents through a process (sequential/hierarchical)
    - with a single agent there's nothing to coordinate, so wrapping it in
    one would just be indirection with no behavior behind it.
    """
    # Keep direct callers and long-lived API processes compatible whenever
    # the task prompt grows a new required interpolation variable.
    inputs.setdefault("primary_language", detect_primary_language(inputs.get("parsed_repo", "")))

    agents_config = yaml.safe_load((_CONFIG_DIR / "agents.yaml").read_text())
    tasks_config = yaml.safe_load((_CONFIG_DIR / "tasks.yaml").read_text())

    feature_architect = Agent(config=agents_config["feature_architect"], llm=groq_llm(), verbose=True)
    task = Task(config=tasks_config["generate_feature_stub_task"], agent=feature_architect)
    task.interpolate_inputs_and_add_conversation_history(inputs)

    result = feature_architect.execute_task(task)
    return result
