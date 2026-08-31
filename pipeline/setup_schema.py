"""Create CareerGraph uniqueness constraints and the Skill fulltext index.

Loads connection details from .env, applies only the approved schema DDL,
then prints SHOW CONSTRAINTS / SHOW INDEXES. Writes no career data.
Stops on the first error instead of attempting any alternative changes.
"""
import sys
from pathlib import Path

from neo4j import GraphDatabase

CONSTRAINTS = [
    ("person_id_unique",
     "CREATE CONSTRAINT person_id_unique IF NOT EXISTS "
     "FOR (p:Person) REQUIRE p.personId IS UNIQUE"),
    ("role_id_unique",
     "CREATE CONSTRAINT role_id_unique IF NOT EXISTS "
     "FOR (r:Role) REQUIRE r.roleId IS UNIQUE"),
    ("company_name_unique",
     "CREATE CONSTRAINT company_name_unique IF NOT EXISTS "
     "FOR (c:Company) REQUIRE c.name IS UNIQUE"),
    ("project_id_unique",
     "CREATE CONSTRAINT project_id_unique IF NOT EXISTS "
     "FOR (p:Project) REQUIRE p.projectId IS UNIQUE"),
    ("skill_name_unique",
     "CREATE CONSTRAINT skill_name_unique IF NOT EXISTS "
     "FOR (s:Skill) REQUIRE s.name IS UNIQUE"),
    ("achievement_id_unique",
     "CREATE CONSTRAINT achievement_id_unique IF NOT EXISTS "
     "FOR (a:Achievement) REQUIRE a.achievementId IS UNIQUE"),
    ("education_id_unique",
     "CREATE CONSTRAINT education_id_unique IF NOT EXISTS "
     "FOR (e:Education) REQUIRE e.educationId IS UNIQUE"),
]

FULLTEXT_INDEX = (
    "skill_search",
    "CREATE FULLTEXT INDEX skill_search IF NOT EXISTS "
    "FOR (s:Skill) ON EACH [s.name, s.aliases]",
)


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def run_statement(session, name: str, statement: str) -> None:
    print(f"  -> {name} ... ", end="", flush=True)
    session.run(statement).consume()
    print("ok")


def print_rows(title: str, rows: list[dict], columns: list[str]) -> None:
    print(f"\n--- {title} ---")
    if not rows:
        print("(none found)")
        return
    for r in rows:
        parts = [f"{col}={r[col]}" for col in columns]
        print("  " + " | ".join(parts))


def main() -> int:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        print(f"ERROR: .env not found at {env_path}", file=sys.stderr)
        return 1

    env = load_env(env_path)
    required = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"]
    missing = [k for k in required if k not in env]
    if missing:
        print(f"ERROR: missing keys in .env: {missing}", file=sys.stderr)
        return 1

    driver = GraphDatabase.driver(
        env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])
    )

    try:
        driver.verify_connectivity()
        print(f"Connected to {env['NEO4J_URI']} (database: {env['NEO4J_DATABASE']})\n")

        with driver.session(database=env["NEO4J_DATABASE"]) as session:
            print("Creating uniqueness constraints:")
            for name, statement in CONSTRAINTS:
                run_statement(session, name, statement)

            print("\nCreating fulltext index:")
            run_statement(session, *FULLTEXT_INDEX)

            constraints = session.run(
                "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties"
            ).data()
            print_rows("SHOW CONSTRAINTS", constraints,
                       ["name", "type", "labelsOrTypes", "properties"])

            indexes = session.run(
                "SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties"
            ).data()
            print_rows("SHOW INDEXES", indexes,
                       ["name", "type", "state", "labelsOrTypes", "properties"])

    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()

    print("\nDone. No career nodes or relationships were created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
