"""Node 2 (vector half): embed code facts into Qdrant for meaning-based search.

We have no raw source text to embed (Node 1 only extracts structural
metadata, deliberately - see ast_parser.py's docstring) so each function
and class gets a short natural-language description synthesized from its
structural facts, and that description is what gets embedded. This is
still "no LLM" work - _describe_function/_describe_class are plain string
formatting, not generation.

Point IDs are a stable uuid5 hash of (repo_name, kind, file_path, name), so
re-syncing the same repo overwrites its existing points instead of
duplicating them.
"""

import uuid

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

_COLLECTION_NAME = "code_symbols"
_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBEDDING_DIM = 384

_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)
    return _model


def sync_to_qdrant(parsed_repo: dict, client: QdrantClient) -> None:
    _ensure_collection(client)
    repo_name = parsed_repo["repo_name"]

    texts: list[str] = []
    payloads: list[dict] = []
    for file in parsed_repo["files"]:
        for cls in file.get("classes", []):
            texts.append(_describe_class(file["path"], cls))
            payloads.append({"repo_name": repo_name, "kind": "class", "name": cls["name"], "file_path": file["path"]})
        for func in file.get("functions", []):
            texts.append(_describe_function(file["path"], func))
            payloads.append({"repo_name": repo_name, "kind": "function", "name": func["name"], "file_path": file["path"]})

    if not texts:
        return

    vectors = [vector.tolist() for vector in _get_model().embed(texts)]
    points = [
        PointStruct(id=_point_id(repo_name, payload), vector=vector, payload={**payload, "text": text})
        for vector, payload, text in zip(vectors, payloads, texts, strict=True)
    ]
    client.upsert(collection_name=_COLLECTION_NAME, points=points)


def search(query: str, client: QdrantClient, repo_name: str | None = None, limit: int = 5) -> list[dict]:
    """Meaning-based search over synced code, e.g. "code that charges a
    customer" finds charge_card() even though that phrase never appears in
    the codebase - this is what Qdrant buys us over exact-text search."""
    query_filter = None
    if repo_name is not None:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        query_filter = Filter(must=[FieldCondition(key="repo_name", match=MatchValue(value=repo_name))])

    (query_vector,) = _get_model().embed([query])
    results = client.query_points(
        collection_name=_COLLECTION_NAME,
        query=query_vector.tolist(),
        query_filter=query_filter,
        limit=limit,
    )
    return [{"score": point.score, **point.payload} for point in results.points]


def _ensure_collection(client: QdrantClient) -> None:
    if not client.collection_exists(_COLLECTION_NAME):
        client.create_collection(
            collection_name=_COLLECTION_NAME,
            vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE),
        )


def _point_id(repo_name: str, payload: dict) -> str:
    key = f"{repo_name}:{payload['kind']}:{payload['file_path']}:{payload['name']}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _describe_class(file_path: str, cls: dict) -> str:
    parts = [f"Class {cls['name']} defined in {file_path}."]
    if cls.get("is_orm_model"):
        parts.append("It is an ORM model.")
    field_descriptions = []
    for field in cls.get("fields", []):
        if field.get("type") == "relationship":
            field_descriptions.append(f"relates to {field.get('target')} (cascade: {field.get('cascade')})")
        else:
            tags = []
            if field.get("primary_key"):
                tags.append("primary key")
            if field.get("unique"):
                tags.append("unique")
            if field.get("foreign_key"):
                tags.append(f"foreign key to {field['foreign_key']}")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            field_descriptions.append(f"{field['name']}: {field.get('type')}{tag_str}")
    if field_descriptions:
        parts.append("Fields: " + "; ".join(field_descriptions) + ".")
    return " ".join(parts)


def _describe_function(file_path: str, func: dict) -> str:
    parts = [f"Function {func['name']} defined in {file_path}."]
    if func.get("http_method") and func.get("route"):
        parts.append(f"Handles {func['http_method']} {func['route']}.")
    if func.get("calls"):
        parts.append("Calls: " + ", ".join(func["calls"]) + ".")
    if func.get("mutates_models"):
        parts.append("Mutates: " + ", ".join(func["mutates_models"]) + ".")
    if func.get("external_dependency"):
        parts.append(f"Uses external service: {func['external_dependency']}.")
    return " ".join(parts)
