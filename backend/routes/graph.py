"""Read-only graph endpoints. Every route here takes the shared read-only
session dependency (backend/deps.py) -- there is no write-capable session
anywhere in this router.
"""
from fastapi import APIRouter, Depends, HTTPException

from api_schemas import GraphOverview, GraphSummary, NodeDetail, NodeRef, RelatedNode
from deps import get_session
from ingest_career import KEY_PROPERTY  # read-only reuse of the existing label/key contract

router = APIRouter(prefix="/api/graph", tags=["graph"])

_ALLOWED_LABELS = set(KEY_PROPERTY)  # Person, Role, Company, Project, Education, Achievement, Skill


def _node_ref(label: str, props: dict) -> NodeRef:
    key_prop = KEY_PROPERTY[label]
    key = props.get(key_prop)
    return NodeRef(id=f"{label}:{key}", label=label, key=str(key), properties=props)


@router.get("/summary", response_model=GraphSummary)
def graph_summary(session=Depends(get_session)) -> GraphSummary:
    label_rows = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count").data()
    rel_rows = session.run("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count").data()
    label_counts = {row["label"]: row["count"] for row in label_rows}
    rel_counts = {row["type"]: row["count"] for row in rel_rows}
    return GraphSummary(
        totalNodes=sum(label_counts.values()),
        totalRelationships=sum(rel_counts.values()),
        labelCounts=label_counts,
        relationshipCounts=rel_counts,
    )


@router.get("/overview", response_model=GraphOverview)
def graph_overview(session=Depends(get_session)) -> GraphOverview:
    person_row = session.run("MATCH (p:Person) RETURN p AS p LIMIT 1").single()
    person = _node_ref("Person", dict(person_row["p"])) if person_row else None

    role_rows = session.run(
        "MATCH (:Person)-[:HAD_ROLE]->(r:Role) OPTIONAL MATCH (r)-[:AT]->(c:Company) "
        "RETURN r AS r, c.name AS company ORDER BY coalesce(r.startDate, '0000')"
    ).data()
    roles = []
    for row in role_rows:
        props = dict(row["r"])
        props["company"] = row["company"]
        roles.append(_node_ref("Role", props))

    project_rows = session.run(
        "MATCH (:Person)-[:BUILT]->(p:Project) RETURN p AS p ORDER BY p.name"
    ).data()
    projects = [_node_ref("Project", dict(row["p"])) for row in project_rows]

    education_rows = session.run("MATCH (:Person)-[:STUDIED_AT]->(e:Education) RETURN e AS e").data()
    education = [_node_ref("Education", dict(row["e"])) for row in education_rows]

    curriculum_rows = session.run(
        "MATCH (p:Project)-[:PART_OF]->(e:Education) RETURN e.educationId AS educationId, p AS p ORDER BY p.name"
    ).data()
    curriculum_projects: dict[str, list[NodeRef]] = {}
    for row in curriculum_rows:
        curriculum_projects.setdefault(row["educationId"], []).append(_node_ref("Project", dict(row["p"])))

    return GraphOverview(
        person=person, roles=roles, projects=projects, education=education,
        curriculumProjects=curriculum_projects,
    )


@router.get("/list/{label}", response_model=list[NodeRef])
def list_nodes(label: str, session=Depends(get_session)) -> list[NodeRef]:
    if label not in _ALLOWED_LABELS:
        raise HTTPException(status_code=404, detail=f"Unknown label {label!r}")
    key_prop = KEY_PROPERTY[label]
    rows = session.run(f"MATCH (n:{label}) RETURN n AS n ORDER BY n.{key_prop}").data()
    return [_node_ref(label, dict(row["n"])) for row in rows]


@router.get("/node/{label}/{key:path}", response_model=NodeDetail)
def node_detail(label: str, key: str, session=Depends(get_session)) -> NodeDetail:
    if label not in _ALLOWED_LABELS:
        raise HTTPException(status_code=404, detail=f"Unknown label {label!r}")
    key_prop = KEY_PROPERTY[label]

    node_row = session.run(
        f"MATCH (n:{label} {{{key_prop}: $key}}) RETURN n AS n", key=key
    ).single()
    if not node_row:
        raise HTTPException(status_code=404, detail=f"{label} {key!r} not found")
    node = _node_ref(label, dict(node_row["n"]))

    out_rows = session.run(
        f"MATCH (n:{label} {{{key_prop}: $key}})-[r]->(m) "
        "RETURN type(r) AS relType, labels(m)[0] AS otherLabel, m AS otherNode",
        key=key,
    ).data()
    in_rows = session.run(
        f"MATCH (n:{label} {{{key_prop}: $key}})<-[r]-(m) "
        "RETURN type(r) AS relType, labels(m)[0] AS otherLabel, m AS otherNode",
        key=key,
    ).data()

    relationships = [
        RelatedNode(direction="out", relationshipType=row["relType"], node=_node_ref(row["otherLabel"], dict(row["otherNode"])))
        for row in out_rows
    ] + [
        RelatedNode(direction="in", relationshipType=row["relType"], node=_node_ref(row["otherLabel"], dict(row["otherNode"])))
        for row in in_rows
    ]
    return NodeDetail(node=node, relationships=relationships)
