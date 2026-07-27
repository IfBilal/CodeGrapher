"""Node 3's other output format: a Mermaid sequence diagram for a single
function's call chain, alongside graph_query.py's Cytoscape JSON for the
whole graph. Same category of work - mechanical Cypher-rows-to-text
translation, no LLM, no interpretation of what the calls mean.
"""

from neo4j import Driver

_MAX_HOPS = 4


def get_call_sequence_diagram(repo_name: str, start_function: str, driver: Driver) -> str:
    with driver.session() as session:
        rows = session.run(
            f"""
            MATCH path = (start:Function {{repo_name: $repo_name, name: $start_function}})
                -[:CALLS|CALLS_EXTERNAL*1..{_MAX_HOPS}]->(callee)
            RETURN [n IN nodes(path) | n.name] AS chain
            """,
            repo_name=repo_name,
            start_function=start_function,
        ).data()

    lines = ["sequenceDiagram"]
    seen_participants: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()

    def add_participant(name: str) -> None:
        if name not in seen_participants:
            lines.append(f"    participant {_sanitize(name)}")
            seen_participants.add(name)

    add_participant(start_function)
    for row in rows:
        chain = row["chain"]
        for name in chain:
            add_participant(name)
        for caller, callee in zip(chain, chain[1:], strict=False):
            if (caller, callee) not in seen_edges:
                lines.append(f"    {_sanitize(caller)}->>+{_sanitize(callee)}: calls")
                seen_edges.add((caller, callee))

    if not rows:
        lines.append(f"    Note over {_sanitize(start_function)}: No outgoing calls found within {_MAX_HOPS} hops")

    return "\n".join(lines)


def _sanitize(name: str) -> str:
    # Mermaid participant identifiers can't contain "." or "-".
    return name.replace(".", "_").replace("-", "_")
