"""Node 3: turn a synced repo's Neo4j graph into Cytoscape.js's node/edge
shape, so the frontend can render it directly with react-cytoscapejs
without any further transformation. This is the same "no AI, mechanical
translation" category of work as graph_sync.py, just running in reverse -
Cypher rows in, Cytoscape JSON out.
"""

from neo4j import Driver


def get_cytoscape_graph(repo_name: str, driver: Driver) -> dict:
    with driver.session() as session:
        nodes = session.run(
            """
            MATCH (n {repo_name: $repo_name})
            RETURN elementId(n) AS id, labels(n)[0] AS label, coalesce(n.name, n.path) AS display_name
            """,
            repo_name=repo_name,
        ).data()

        edges = session.run(
            """
            MATCH (a {repo_name: $repo_name})-[r]->(b {repo_name: $repo_name})
            RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS rel_type, elementId(r) AS id
            """,
            repo_name=repo_name,
        ).data()

    return {
        "elements": {
            "nodes": [
                {"data": {"id": n["id"], "label": n["label"], "name": n["display_name"]}}
                for n in nodes
            ],
            "edges": [
                {"data": {"id": e["id"], "source": e["source"], "target": e["target"], "label": e["rel_type"]}}
                for e in edges
            ],
        }
    }
