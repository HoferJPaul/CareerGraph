"""Ingest the first CareerGraph data slice from data/career_seed.json.

Every node and relationship write uses MERGE, so rerunning this script
against the same data/career_seed.json creates no duplicates. Does not touch
the schema (no CREATE CONSTRAINT / CREATE INDEX here) and does not
delete anything.
"""
import json
import sys
from pathlib import Path

from neo4j import GraphDatabase

from setup_schema import load_env

KEY_PROPERTY = {
    "Person": "personId",
    "Role": "roleId",
    "Company": "name",
    "Project": "projectId",
    "Education": "educationId",
    "Achievement": "achievementId",
    "Skill": "name",
}

ALLOWED_RELATIONSHIP_TYPES = {
    "HAD_ROLE", "AT", "BUILT", "DURING", "USED", "DEMONSTRATES", "ACHIEVED",
    "STUDIED_AT", "LEARNED", "PART_OF",
}


def merge_node(session, label: str, key_value: str, props: dict) -> None:
    if label not in KEY_PROPERTY:
        raise ValueError(f"Label not allowed by existing schema: {label}")
    key_prop = KEY_PROPERTY[label]
    statement = f"MERGE (n:{label} {{{key_prop}: $key}}) SET n += $props"
    session.run(statement, key=key_value, props=props).consume()


def merge_relationship(session, rel: dict) -> None:
    rel_type = rel["type"]
    if rel_type not in ALLOWED_RELATIONSHIP_TYPES:
        raise ValueError(f"Relationship type not allowed by existing schema: {rel_type}")

    from_label, from_key = rel["from"]["label"], rel["from"]["key"]
    to_label, to_key = rel["to"]["label"], rel["to"]["key"]
    if from_label not in KEY_PROPERTY or to_label not in KEY_PROPERTY:
        raise ValueError(f"Unknown label in relationship: {rel}")

    from_key_prop = KEY_PROPERTY[from_label]
    to_key_prop = KEY_PROPERTY[to_label]
    statement = (
        f"MATCH (a:{from_label} {{{from_key_prop}: $fromKey}}) "
        f"MATCH (b:{to_label} {{{to_key_prop}: $toKey}}) "
        f"MERGE (a)-[r:{rel_type}]->(b)"
    )
    session.run(statement, fromKey=from_key, toKey=to_key).consume()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    seed_path = root / "data" / "career_seed.json"

    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        return 1
    if not seed_path.exists():
        print(f"ERROR: career_seed.json not found at {seed_path}", file=sys.stderr)
        return 1

    env = load_env(env_path)
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    driver = GraphDatabase.driver(
        env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])
    )

    try:
        driver.verify_connectivity()
        print(f"Connected to {env['NEO4J_URI']} (database: {env['NEO4J_DATABASE']})\n")

        with driver.session(database=env["NEO4J_DATABASE"]) as session:
            print("Merging Person:")
            p = seed["person"]
            merge_node(session, "Person", p["personId"], {"name": p["name"]})
            print(f"  -> {p['personId']} ok")

            print("\nMerging Roles:")
            for r in seed["roles"]:
                props = {k: v for k, v in r.items() if k != "roleId"}
                merge_node(session, "Role", r["roleId"], props)
                print(f"  -> {r['roleId']} ok")

            print("\nMerging Companies:")
            for c in seed["companies"]:
                props = {k: v for k, v in c.items() if k != "name"}
                merge_node(session, "Company", c["name"], props)
                print(f"  -> {c['name']} ok")

            print("\nMerging Projects:")
            for proj in seed["projects"]:
                props = {k: v for k, v in proj.items() if k != "projectId"}
                merge_node(session, "Project", proj["projectId"], props)
                print(f"  -> {proj['projectId']} ok")

            print("\nMerging Education:")
            for e in seed["education"]:
                props = {k: v for k, v in e.items() if k != "educationId"}
                merge_node(session, "Education", e["educationId"], props)
                print(f"  -> {e['educationId']} ok")

            print("\nMerging Achievements:")
            for a in seed.get("achievements", []):
                props = {k: v for k, v in a.items() if k != "achievementId"}
                merge_node(session, "Achievement", a["achievementId"], props)
                print(f"  -> {a['achievementId']} ok")

            print("\nMerging Skills:")
            for s in seed["skills"]:
                props = {k: v for k, v in s.items() if k != "name"}
                merge_node(session, "Skill", s["name"], props)
                print(f"  -> {s['name']} ok")

            print("\nMerging relationships:")
            for rel in seed["relationships"]:
                merge_relationship(session, rel)
                print(
                    f"  -> ({rel['from']['label']} {rel['from']['key']})"
                    f"-[:{rel['type']}]->"
                    f"({rel['to']['label']} {rel['to']['key']}) ok"
                )

    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
