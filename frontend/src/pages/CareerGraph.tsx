import { useEffect, useState } from "react";
import { api } from "../api/client";
import MetricCard from "../components/MetricCard";
import GraphView from "../components/GraphView";
import type { GraphSummary, NodeRef } from "../types/career";

function useNodeList(label: string) {
  const [nodes, setNodes] = useState<NodeRef[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.listNodes(label).then((result) => {
      if (!cancelled) setNodes(result);
    });
    return () => {
      cancelled = true;
    };
  }, [label]);
  return nodes;
}

function Scrollable({ children, maxHeight }: { children: React.ReactNode; maxHeight: number }) {
  return <div style={{ maxHeight, overflowY: "auto" }}>{children}</div>;
}

function Loading() {
  return <span className="skeleton-value">···</span>;
}

function LoadingRow({ colSpan }: { colSpan: number }) {
  return (
    <tr>
      <td colSpan={colSpan} className="loading-text">
        Loading…
      </td>
    </tr>
  );
}

export default function CareerGraphPage() {
  const [summary, setSummary] = useState<GraphSummary | null>(null);
  const roles = useNodeList("Role");
  const projects = useNodeList("Project");
  const skills = useNodeList("Skill");
  const achievements = useNodeList("Achievement");

  useEffect(() => {
    api.getGraphSummary().then(setSummary);
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>CareerGraph</h1>
        <p>A live, read-only view of Paul's professional-evidence graph in Neo4j Aura.</p>
      </div>

      <div className="grid grid-metrics">
        <MetricCard label="Total nodes" value={summary?.totalNodes ?? <Loading />} />
        <MetricCard label="Total relationships" value={summary?.totalRelationships ?? <Loading />} />
        <MetricCard label="Roles" value={summary?.labelCounts.Role ?? <Loading />} />
        <MetricCard label="Projects" value={summary?.labelCounts.Project ?? <Loading />} />
        <MetricCard label="Skills" value={summary?.labelCounts.Skill ?? <Loading />} />
        <MetricCard label="Achievements" value={summary?.labelCounts.Achievement ?? <Loading />} />
        <MetricCard label="Companies" value={summary?.labelCounts.Company ?? <Loading />} />
        <MetricCard label="Education" value={summary?.labelCounts.Education ?? <Loading />} />
      </div>

      <div className="section">
        <div className="section-title">
          <h2>Evidence graph</h2>
        </div>
        <GraphView />
      </div>

      <div className="section">
        <div className="section-title">
          <h2>Roles</h2>
          <span className="count">{roles?.length ?? <Loading />}</span>
        </div>
        <div className="card">
          <table>
            <colgroup>
              <col style={{ width: "30%" }} />
              <col style={{ width: "24%" }} />
              <col style={{ width: "22%" }} />
              <col style={{ width: "24%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>Title</th>
                <th>Company</th>
                <th>Location</th>
                <th>Dates</th>
              </tr>
            </thead>
            <tbody>
              {!roles && <LoadingRow colSpan={4} />}
              {roles?.map((r) => (
                <tr key={r.id}>
                  <td>{String(r.properties.title ?? "")}</td>
                  <td>{String(r.properties.company ?? "")}</td>
                  <td>{String(r.properties.location ?? "")}</td>
                  <td>
                    {String(r.properties.startDate ?? "")} – {String(r.properties.endDate ?? "present")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section">
        <div className="section-title">
          <h2>Projects</h2>
          <span className="count">{projects?.length ?? <Loading />}</span>
        </div>
        <div className="card">
          <Scrollable maxHeight={360}>
            <table>
              <colgroup>
                <col style={{ width: "44%" }} />
                <col style={{ width: "24%" }} />
                <col style={{ width: "32%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Domain</th>
                </tr>
              </thead>
              <tbody>
                {!projects && <LoadingRow colSpan={3} />}
                {projects?.map((p) => (
                  <tr key={p.id}>
                    <td>{String(p.properties.name ?? "")}</td>
                    <td>{String(p.properties.type ?? "")}</td>
                    <td>{String(p.properties.domain ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scrollable>
        </div>
      </div>

      <div className="grid grid-2 section">
        <div>
          <div className="section-title">
            <h2>Skills</h2>
            <span className="count">{skills?.length ?? <Loading />}</span>
          </div>
          <div className="card">
            <Scrollable maxHeight={340}>
              <table>
                <colgroup>
                  <col style={{ width: "65%" }} />
                  <col style={{ width: "35%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>Skill</th>
                    <th>Category</th>
                  </tr>
                </thead>
                <tbody>
                  {!skills && <LoadingRow colSpan={2} />}
                  {skills?.map((s) => (
                    <tr key={s.id}>
                      <td>{String(s.properties.displayName ?? s.key)}</td>
                      <td>{String(s.properties.category ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scrollable>
          </div>
        </div>
        <div>
          <div className="section-title">
            <h2>Achievements</h2>
            <span className="count">{achievements?.length ?? <Loading />}</span>
          </div>
          <div className="card">
            <Scrollable maxHeight={340}>
              <table>
                <tbody>
                  {!achievements && <LoadingRow colSpan={1} />}
                  {achievements?.map((a) => (
                    <tr key={a.id}>
                      <td>{String(a.properties.description ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scrollable>
          </div>
        </div>
      </div>
    </div>
  );
}
