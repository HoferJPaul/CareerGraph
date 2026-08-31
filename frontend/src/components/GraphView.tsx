import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "../api/client";
import type { GraphOverview, NodeDetail, NodeRef } from "../types/career";

const LABEL_COLOR: Record<string, string> = {
  Person: "#0d8a82",
  Role: "#2f5fa8",
  Project: "#b5750f",
  Skill: "#6b46c1",
  Achievement: "#1d8a5f",
  Company: "#8a949a",
  Education: "#c0392b",
};

const NODE_WIDTH = 154;
const EXPAND_LIMIT = 10;

function labelText(label: string, props: Record<string, unknown>): string {
  return (
    (props.title as string) ??
    (props.name as string) ??
    (props.displayName as string) ??
    (props.institution as string) ??
    (props.description as string | undefined)?.slice(0, 46) ??
    label
  );
}

function makeNode(ref: NodeRef, x: number, y: number): Node {
  return {
    id: ref.id,
    position: { x, y },
    data: { ref, label: labelText(ref.label, ref.properties) },
    style: {
      border: `2px solid ${LABEL_COLOR[ref.label] ?? "#999"}`,
      borderRadius: 10,
      padding: "7px 9px",
      background: "white",
      width: NODE_WIDTH,
      textAlign: "center" as const,
      fontSize: 12,
      lineHeight: 1.3,
      display: "-webkit-box",
      WebkitLineClamp: 2,
      WebkitBoxOrient: "vertical" as const,
      overflow: "hidden",
    },
  };
}

/** Regular grid, centered horizontally on originX, growing downward (rowGap > 0)
 * or upward (rowGap < 0) from originY. Guarantees no overlap by construction --
 * unlike an arc, spacing never shrinks as node count grows within a row. */
function gridPositions(originX: number, originY: number, count: number, columns: number, colGap: number, rowGap: number) {
  const positions: { x: number; y: number }[] = [];
  for (let i = 0; i < count; i++) {
    const row = Math.floor(i / columns);
    const itemsInRow = Math.min(columns, count - row * columns);
    const rowWidth = (itemsInRow - 1) * colGap;
    const col = i % columns;
    positions.push({
      x: originX - rowWidth / 2 + col * colGap,
      y: originY + row * rowGap,
    });
  }
  return positions;
}

function buildOverviewGraph(overview: GraphOverview) {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const centerX = 520;
  const personY = 360;

  if (overview.person) {
    nodes.push(makeNode(overview.person, centerX, personY));
  }

  const rolePositions = gridPositions(centerX, personY - 160, overview.roles.length, 4, 175, -110);
  overview.roles.forEach((r, i) => {
    nodes.push(makeNode(r, rolePositions[i].x, rolePositions[i].y));
    edges.push({ id: `p-${r.id}`, source: overview.person!.id, target: r.id, label: "HAD_ROLE" });
  });

  // Curriculum projects (PART_OF an Education) are nested under their Education
  // node instead of the flat Person-anchored grid -- a visualization-hierarchy
  // choice only. The underlying Person-[:BUILT]->Project edge still exists in
  // Neo4j and remains queryable (e.g. via node-detail click); it's just not
  // drawn in this overview layout for curriculum projects.
  const curriculumProjectIds = new Set(
    Object.values(overview.curriculumProjects).flat().map((p) => p.id),
  );
  const standaloneProjects = overview.projects.filter((p) => !curriculumProjectIds.has(p.id));

  const projectPositions = gridPositions(centerX, personY + 150, standaloneProjects.length, 5, 175, 100);
  standaloneProjects.forEach((p, i) => {
    nodes.push(makeNode(p, projectPositions[i].x, projectPositions[i].y));
    edges.push({ id: `p-${p.id}`, source: overview.person!.id, target: p.id, label: "BUILT" });
  });

  const eduPositions = gridPositions(centerX - 430, personY - 40, overview.education.length, 1, 0, 90);
  overview.education.forEach((e, i) => {
    nodes.push(makeNode(e, eduPositions[i].x, eduPositions[i].y));
    edges.push({ id: `p-${e.id}`, source: overview.person!.id, target: e.id, label: "STUDIED_AT" });

    const curriculumProjects = overview.curriculumProjects[e.key] ?? [];
    if (curriculumProjects.length === 0) return;
    const origin = eduPositions[i];
    const clusterPositions = gridPositions(origin.x, origin.y + 140, curriculumProjects.length, 4, 165, 105);
    curriculumProjects.forEach((p, j) => {
      nodes.push(makeNode(p, clusterPositions[j].x, clusterPositions[j].y));
      edges.push({ id: `edu-${p.id}`, source: e.id, target: p.id, label: "PART_OF" });
    });
  });

  return { nodes, edges };
}

function FitOnChange({ trigger }: { trigger: number }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const id = window.requestAnimationFrame(() => {
      fitView({ padding: 0.18, duration: 300, maxZoom: 1.05 });
    });
    return () => window.cancelAnimationFrame(id);
  }, [trigger, fitView]);
  return null;
}

function DetailPanel({ detail, truncatedCount, onClose }: { detail: NodeDetail; truncatedCount: number; onClose: () => void }) {
  return (
    <div style={{ width: 300, borderLeft: "1px solid var(--color-border)", padding: 16, height: 560, overflowY: "auto", flexShrink: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="badge badge-neutral">{detail.node.label}</span>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </div>
      <h3 style={{ fontSize: "0.95rem", marginTop: 10 }}>{labelText(detail.node.label, detail.node.properties)}</h3>
      {(detail.node.properties.description as string | undefined) && (
        <p style={{ fontSize: "0.82rem" }}>{detail.node.properties.description as string}</p>
      )}
      <dl style={{ fontSize: "0.78rem" }}>
        {Object.entries(detail.node.properties)
          .filter(([k]) => k !== "description")
          .map(([k, v]) => (
            <div key={k} style={{ marginBottom: 6 }}>
              <dt style={{ color: "var(--color-text-faint)" }}>{k}</dt>
              <dd style={{ margin: 0, overflowWrap: "break-word" }}>{String(v)}</dd>
            </div>
          ))}
      </dl>
      <h4 style={{ fontSize: "0.82rem", marginTop: 14 }}>
        Relationships ({detail.relationships.length})
      </h4>
      <ul style={{ fontSize: "0.78rem", paddingLeft: 16, margin: 0 }}>
        {detail.relationships.slice(0, 30).map((r, i) => (
          <li key={i} style={{ marginBottom: 3 }}>
            {r.direction === "out" ? r.relationshipType : `${r.relationshipType} (in)`} → {labelText(r.node.label, r.node.properties)}
          </li>
        ))}
      </ul>
      {truncatedCount > 0 && (
        <p style={{ fontSize: "0.74rem", color: "var(--color-text-faint)", marginTop: 10 }}>
          Showing {EXPAND_LIMIT} of {detail.relationships.length} connections on the canvas to keep the graph readable.
        </p>
      )}
    </div>
  );
}

function GraphCanvas() {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [truncatedCount, setTruncatedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [fitTrigger, setFitTrigger] = useState(0);
  const [overview, setOverview] = useState<GraphOverview | null>(null);

  const loadOverview = useCallback(() => {
    setLoading(true);
    setDetail(null);
    setExpanded(new Set());
    api.getGraphOverview().then((result) => {
      setOverview(result);
      const { nodes: n, edges: e } = buildOverviewGraph(result);
      setNodes(n);
      setEdges(e);
      setLoading(false);
      setFitTrigger((t) => t + 1);
    });
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      const ref = node.data.ref as NodeRef;
      const [label, key] = ref.id.split(/:(.+)/);
      api.getNodeDetail(label, key).then((detailResult) => {
        setDetail(detailResult);
        setTruncatedCount(Math.max(0, detailResult.relationships.length - EXPAND_LIMIT));

        if (expanded.has(ref.id)) return;
        setExpanded((prev) => new Set(prev).add(ref.id));

        setNodes((currentNodes) => {
          const existingIds = new Set(currentNodes.map((n) => n.id));
          const toAdd = detailResult.relationships
            .map((rel) => rel.node)
            .filter((n) => !existingIds.has(n.id))
            .slice(0, EXPAND_LIMIT);

          if (toAdd.length === 0) return currentNodes;

          const origin = currentNodes.find((n) => n.id === ref.id)!.position;
          const direction = origin.y < 360 ? -1 : 1; // expand upward from upper nodes, downward from lower ones
          const spread = gridPositions(origin.x, origin.y + direction * 150, toAdd.length, 4, 168, direction * 95);
          const newNodes = toAdd.map((n, i) => makeNode(n, spread[i].x, spread[i].y));
          return [...currentNodes, ...newNodes];
        });

        setEdges((currentEdges) => {
          const existingIds = new Set(currentEdges.map((e) => e.id));
          const capped = detailResult.relationships.slice(0, EXPAND_LIMIT);
          const newEdges = capped
            .filter((rel) => !existingIds.has(`${ref.id}-${rel.node.id}-${rel.relationshipType}`))
            .map((rel) => ({
              id: `${ref.id}-${rel.node.id}-${rel.relationshipType}`,
              source: rel.direction === "out" ? ref.id : rel.node.id,
              target: rel.direction === "out" ? rel.node.id : ref.id,
              label: rel.relationshipType,
              style: { stroke: "#cfd8db" },
              labelStyle: { fontSize: 9, fill: "#8a9a9e" },
            }));
          return [...currentEdges, ...newEdges];
        });

        setFitTrigger((t) => t + 1);
      });
    },
    [expanded],
  );

  const legend = useMemo(
    () =>
      Object.entries(LABEL_COLOR).map(([label, color]) => (
        <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 5, marginRight: 14, fontSize: 12 }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: color, display: "inline-block" }} />
          {label}
        </span>
      )),
    [],
  );

  const isExpandedView = expanded.size > 0;

  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--color-border)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>{legend}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12, color: "var(--color-text-faint)" }}>
            Click a node to expand its connections
          </span>
          {isExpandedView && (
            <button className="btn btn-ghost" onClick={loadOverview} style={{ fontSize: 12, padding: "5px 10px" }}>
              Reset view
            </button>
          )}
        </div>
      </div>
      <div style={{ display: "flex" }}>
        <div style={{ height: 560, flex: 1, position: "relative" }}>
          {loading || !overview ? (
            <div className="empty-state" style={{ margin: 40 }}>
              Loading graph…
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodeClick={onNodeClick}
              minZoom={0.4}
              maxZoom={1.4}
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls showInteractive={false} />
              <FitOnChange trigger={fitTrigger} />
            </ReactFlow>
          )}
        </div>
        {detail && <DetailPanel detail={detail} truncatedCount={truncatedCount} onClose={() => setDetail(null)} />}
      </div>
    </div>
  );
}

export default function GraphView() {
  return (
    <ReactFlowProvider>
      <GraphCanvas />
    </ReactFlowProvider>
  );
}
