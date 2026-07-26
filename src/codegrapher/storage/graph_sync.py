"""Node 2 (graph half): push Node 1's parsed-repo facts into Neo4j.

This is the piece that was described early on as "for each fact, write two
nodes and an edge" - and that's really what this file does. No LLM calls,
no interpretation: every MERGE below corresponds directly to a field
already present in the parsed_repo dict.

Schema:
    (:File)-[:DEFINES]->(:Class)
    (:File)-[:DEFINES]->(:Function)
    (:File)-[:IMPORTS]->(:ExternalSymbol)
    (:Class)-[:HAS_FIELD]->(:Field)
    (:Class)-[:RELATES_TO {cascade}]->(:Class)
    (:Function)-[:CALLS]->(:Function)          # only calls resolved to a
                                                # function defined elsewhere
                                                # in this same repo
    (:Function)-[:CALLS_EXTERNAL]->(:ExternalSymbol)   # everything else
    (:Function)-[:MUTATES]->(:Class)
    (:Function)-[:USES_EXTERNAL_SERVICE]->(:ExternalService)

Neo4j node properties can only hold primitives/arrays of primitives, not
nested objects - that's why a field dict becomes its own (:Field) node
instead of a JSON blob property, and why a "relationship" field becomes a
real (:Class)-[:RELATES_TO]->(:Class) edge instead of a field at all.

Every node is namespaced by repo_name in its MERGE key, so syncing multiple
repos into the same Neo4j instance later won't collide two different
repos' "User" classes into one node.
"""

from neo4j import Driver


def sync_to_neo4j(parsed_repo: dict, driver: Driver) -> None:
    repo_name = parsed_repo["repo_name"]
    known_function_names = {
        func["name"] for file in parsed_repo["files"] for func in file.get("functions", [])
    }

    with driver.session() as session:
        for file in parsed_repo["files"]:
            session.execute_write(_write_file, repo_name, file)
            for cls in file.get("classes", []):
                session.execute_write(_write_class, repo_name, file["path"], cls)
            for func in file.get("functions", []):
                session.execute_write(_write_function, repo_name, file["path"], func, known_function_names)


def _write_file(tx, repo_name: str, file: dict) -> None:
    tx.run(
        """
        MERGE (f:File {repo_name: $repo_name, path: $path})
        SET f.language = $language
        """,
        repo_name=repo_name,
        path=file["path"],
        language=file.get("language"),
    )
    for import_name in file.get("imports", []):
        tx.run(
            """
            MATCH (f:File {repo_name: $repo_name, path: $path})
            MERGE (s:ExternalSymbol {repo_name: $repo_name, name: $import_name})
            MERGE (f)-[:IMPORTS]->(s)
            """,
            repo_name=repo_name,
            path=file["path"],
            import_name=import_name,
        )


def _write_class(tx, repo_name: str, file_path: str, cls: dict) -> None:
    tx.run(
        """
        MATCH (f:File {repo_name: $repo_name, path: $file_path})
        MERGE (c:Class {repo_name: $repo_name, name: $name})
        SET c.is_orm_model = $is_orm_model, c.base_classes = $base_classes
        MERGE (f)-[:DEFINES]->(c)
        """,
        repo_name=repo_name,
        file_path=file_path,
        name=cls["name"],
        is_orm_model=cls.get("is_orm_model", False),
        base_classes=cls.get("base_classes", []),
    )

    for field in cls.get("fields", []):
        if field.get("type") == "relationship":
            tx.run(
                """
                MATCH (c:Class {repo_name: $repo_name, name: $name})
                MERGE (target:Class {repo_name: $repo_name, name: $target})
                MERGE (c)-[r:RELATES_TO]->(target)
                SET r.cascade = $cascade
                """,
                repo_name=repo_name,
                name=cls["name"],
                target=field.get("target"),
                cascade=field.get("cascade"),
            )
        else:
            tx.run(
                """
                MATCH (c:Class {repo_name: $repo_name, name: $class_name})
                MERGE (field:Field {repo_name: $repo_name, class_name: $class_name, name: $field_name})
                SET field.type = $type,
                    field.primary_key = $primary_key,
                    field.unique = $unique,
                    field.foreign_key = $foreign_key
                MERGE (c)-[:HAS_FIELD]->(field)
                """,
                repo_name=repo_name,
                class_name=cls["name"],
                field_name=field["name"],
                type=field.get("type"),
                primary_key=field.get("primary_key", False),
                unique=field.get("unique", False),
                foreign_key=field.get("foreign_key"),
            )


def _write_function(tx, repo_name: str, file_path: str, func: dict, known_function_names: set[str]) -> None:
    tx.run(
        """
        MATCH (f:File {repo_name: $repo_name, path: $file_path})
        MERGE (fn:Function {repo_name: $repo_name, name: $name, file_path: $file_path})
        SET fn.http_method = $http_method, fn.route = $route
        MERGE (f)-[:DEFINES]->(fn)
        """,
        repo_name=repo_name,
        file_path=file_path,
        name=func["name"],
        http_method=func.get("http_method"),
        route=func.get("route"),
    )

    for call_name in func.get("calls", []):
        # Called names can be dotted (e.g. "db_session.add") - a call only
        # resolves to an in-repo :Function if its last segment matches a
        # function we actually parsed; everything else is external.
        short_name = call_name.split(".")[-1]
        if short_name in known_function_names:
            tx.run(
                """
                MATCH (caller:Function {repo_name: $repo_name, name: $caller, file_path: $file_path})
                MERGE (callee:Function {repo_name: $repo_name, name: $callee})
                MERGE (caller)-[:CALLS]->(callee)
                """,
                repo_name=repo_name,
                file_path=file_path,
                caller=func["name"],
                callee=short_name,
            )
        else:
            tx.run(
                """
                MATCH (caller:Function {repo_name: $repo_name, name: $caller, file_path: $file_path})
                MERGE (s:ExternalSymbol {repo_name: $repo_name, name: $call_name})
                MERGE (caller)-[:CALLS_EXTERNAL]->(s)
                """,
                repo_name=repo_name,
                file_path=file_path,
                caller=func["name"],
                call_name=call_name,
            )

    for model_name in func.get("mutates_models", []):
        tx.run(
            """
            MATCH (fn:Function {repo_name: $repo_name, name: $name, file_path: $file_path})
            MERGE (c:Class {repo_name: $repo_name, name: $model_name})
            MERGE (fn)-[:MUTATES]->(c)
            """,
            repo_name=repo_name,
            file_path=file_path,
            name=func["name"],
            model_name=model_name,
        )

    if external := func.get("external_dependency"):
        tx.run(
            """
            MATCH (fn:Function {repo_name: $repo_name, name: $name, file_path: $file_path})
            MERGE (s:ExternalService {repo_name: $repo_name, name: $external})
            MERGE (fn)-[:USES_EXTERNAL_SERVICE]->(s)
            """,
            repo_name=repo_name,
            file_path=file_path,
            name=func["name"],
            external=external,
        )
