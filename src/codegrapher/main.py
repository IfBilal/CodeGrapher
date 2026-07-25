import json
from pathlib import Path

from dotenv import load_dotenv

from codegrapher.crews.feature_agent.feature_agent import request_feature
from codegrapher.flows.ingestion_flow import IngestionFlow

load_dotenv()

SAMPLE_REPO_PATH = Path(__file__).parent / "sample_data" / "sample_parsed_repo.json"

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
    parsed_repo = json.loads(SAMPLE_REPO_PATH.read_text())
    parsed_repo_json = json.dumps(parsed_repo, indent=2)

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
