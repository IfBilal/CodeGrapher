import json
import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from qdrant_client import QdrantClient

from codegrapher.crews.feature_agent.feature_agent import request_feature
from codegrapher.flows.ingestion_flow import IngestionFlow
from codegrapher.parser.ast_parser import parse_repo
from codegrapher.storage.graph_sync import sync_to_neo4j
from codegrapher.storage.vector_sync import search, sync_to_qdrant

load_dotenv()

# Node 1 now runs for real against this toy repo's actual .py files, instead
# of the hand-written sample_parsed_repo.json that used to stand in for it.
TOY_REPO_PATH = Path(__file__).parent / "sample_data" / "toy_repo"

PROPOSED_EDIT = (
    "Change cancel_order() in app/routes/orders.py so that, in addition to "
    "deleting the Order row, it also calls billing.charge_card() to issue a "
    "refund."
)

FEATURE_REQUEST = (
    "Add a refund_order feature: given an order id, refund the payment via "
    "the billing service and update the order's status to 'refunded' "
    "instead of deleting the row."
)


def run() -> None:
    parsed_repo = parse_repo(TOY_REPO_PATH)
    parsed_repo_json = json.dumps(parsed_repo, indent=2)

    print("\n\n===== NODE 1: PARSED REPO (real ast parser, not mocked) =====\n")
    print(parsed_repo_json)

    print("\n\n===== NODE 2: SYNCING TO NEO4J + QDRANT =====\n")
    neo4j_driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    )
    sync_to_neo4j(parsed_repo, neo4j_driver)
    neo4j_driver.close()
    print("Synced to Neo4j.")

    qdrant_client = QdrantClient(url=os.environ["QDRANT_URL"])
    sync_to_qdrant(parsed_repo, qdrant_client)
    print("Synced to Qdrant.")

    print("\n\n===== NODE 2 DEMO: SEMANTIC SEARCH OVER SYNCED CODE =====\n")
    query = "code that charges a customer's credit card"
    print(f'Query: "{query}"')
    for hit in search(query, qdrant_client, repo_name=parsed_repo["repo_name"]):
        print(f"  {hit['score']:.3f}  {hit['kind']:8s} {hit['name']:15s} ({hit['file_path']})")

    print("\n\n===== INGESTION FLOW: CARTOGRAPHY -> IMPACT ANALYSIS =====\n")
    ingestion_flow = IngestionFlow()
    ingestion_flow.kickoff(inputs={"parsed_repo": parsed_repo_json, "proposed_edit": PROPOSED_EDIT})
    ingestion_state = ingestion_flow.state

    # This call is deliberately separate from the Flow above - in the real
    # app it would happen much later, triggered by a user action in the
    # frontend, against a repo that was ingested at some earlier time.
    print("\n\n===== ON-DEMAND: FEATURE REQUEST =====\n")
    feature_result = request_feature(FEATURE_REQUEST, ingestion_state)

    print("\n\n===== FINAL FEATURE STUB OUTPUT =====\n")
    print(feature_result)


if __name__ == "__main__":
    run()
