"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { Core } from "cytoscape";

const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), { ssr: false });
const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

// The Feature Architect's response is markdown with a fenced code block
// (the actual stub) followed by prose (contract notes). Split them so the
// code renders in Monaco - with real syntax highlighting - instead of a
// flat <pre> block, and the prose stays as plain text underneath.
function splitFeatureStub(markdown: string): { code: string | null; language: string; rest: string } {
  const match = markdown.match(/```([\w+-]*)\n([\s\S]*?)```/);
  if (!match) return { code: null, language: "plaintext", rest: markdown };
  return { code: match[2].trimEnd(), language: match[1] || "plaintext", rest: markdown.slice(match.index! + match[0].length).trim() };
}

function MermaidDiagram({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const id = `mermaid-${useId().replace(/:/g, "")}`;

  useEffect(() => {
    let cancelled = false;
    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, theme: "neutral" });
      const { svg } = await mermaid.render(id, chart);
      if (!cancelled) setSvg(svg);
    });
    return () => {
      cancelled = true;
    };
  }, [chart, id]);

  if (!svg) return <p className="text-sm text-ink-muted">Rendering diagram…</p>;
  return <div dangerouslySetInnerHTML={{ __html: svg }} />;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type JobStatus = {
  job_id: string;
  repo_path: string;
  status: "pending" | "running" | "done" | "failed";
  error: string | null;
  architecture_report: string | null;
  schema_report: string | null;
  impact_report: string | null;
  anti_pattern_report: string | null;
};

type GraphNode = { data: { id: string; label: string; name: string } };
type CytoscapeElements = {
  elements: {
    nodes: GraphNode[];
    edges: { data: { id: string; source: string; target: string; label: string } }[];
  };
};

const NODE_COLORS: Record<string, string> = {
  File: "#8892a6",
  Class: "#2f6fe0",
  Function: "#1e8f79",
  Field: "#8b5e34",
  ExternalSymbol: "#b3bac9",
  ExternalService: "#d1453a",
};

// ---- Small reusable pieces --------------------------------------------

function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg className={`spin ${className}`} width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function Button({
  children,
  onClick,
  disabled,
  loading,
  variant = "primary",
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
  variant?: "primary" | "secondary";
  type?: "button" | "submit";
}) {
  const base =
    "inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface";
  const variants =
    variant === "primary"
      ? "bg-accent text-accent-ink hover:brightness-110 active:brightness-95"
      : "bg-surface-2 text-ink border border-border hover:bg-border/60";
  return (
    <button type={type} className={`${base} ${variants}`} onClick={onClick} disabled={disabled || loading}>
      {loading && <Spinner />}
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-ink-muted uppercase tracking-wide">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "border border-border bg-surface text-ink placeholder:text-ink-muted/70 rounded-lg px-3.5 py-2.5 text-sm outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20";

const STATUS_STYLES: Record<JobStatus["status"], { label: string; soft: string; solid: string; pulse?: boolean }> = {
  pending: { label: "Pending", soft: "bg-warning-soft text-warning", solid: "bg-warning", pulse: true },
  running: { label: "Running", soft: "bg-info-soft text-info", solid: "bg-info", pulse: true },
  done: { label: "Done", soft: "bg-success-soft text-success", solid: "bg-success" },
  failed: { label: "Failed", soft: "bg-danger-soft text-danger", solid: "bg-danger" },
};

function StatusPill({ status }: { status: JobStatus["status"] }) {
  const s = STATUS_STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${s.soft}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.solid} ${s.pulse ? "pulse" : ""}`} />
      {s.label}
    </span>
  );
}

function Card({ title, icon, children }: { title: string; icon?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-6">
      <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
        {icon && <span aria-hidden="true">{icon}</span>}
        {title}
      </h2>
      {children}
    </section>
  );
}

// ---- Page ---------------------------------------------------------------

export default function Home() {
  const [repoPath, setRepoPath] = useState("");
  const [proposedEdit, setProposedEdit] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [graph, setGraph] = useState<CytoscapeElements | null>(null);
  const [featureRequest, setFeatureRequest] = useState("");
  const [featureStub, setFeatureStub] = useState<string | null>(null);
  const [featureLoading, setFeatureLoading] = useState(false);
  const [featureError, setFeatureError] = useState<string | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [selectedFunction, setSelectedFunction] = useState("");
  const [sequenceDiagram, setSequenceDiagram] = useState<string | null>(null);
  const [sequenceLoading, setSequenceLoading] = useState(false);
  const [logs, setLogs] = useState<{ message: string; ts: string }[]>([]);
  const [nodeType, setNodeType] = useState("All");
  const [graphSearch, setGraphSearch] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode["data"] | null>(null);
  const graphRef = useRef<Core | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const logsEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      eventSourceRef.current?.close();
    };
  }, []);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ block: "end" });
  }, [logs]);

  function streamLogs(jobId: string) {
    eventSourceRef.current?.close();
    const es = new EventSource(`${API_URL}/repos/${jobId}/events`);
    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      setLogs((prev) => [...prev, data]);
    };
    es.onerror = () => es.close();
    eventSourceRef.current = es;
  }

  async function submitRepo() {
    setSubmitting(true);
    setSubmitError(null);
    setJob(null);
    setGraph(null);
    setFeatureStub(null);
    setSequenceDiagram(null);
    setLogs([]);
    setSelectedNode(null);
    setImpactError(null);

    try {
      const res = await fetch(`${API_URL}/repos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_path: repoPath }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Request failed: ${res.status}`);
      }
      const { job_id } = await res.json();
      setJob({
        job_id,
        repo_path: repoPath,
        status: "pending",
        error: null,
        architecture_report: null,
        schema_report: null,
        impact_report: null,
        anti_pattern_report: null,
      });
      pollJob(job_id);
      streamLogs(job_id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function pollJob(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const res = await fetch(`${API_URL}/repos/${jobId}`);
      const data: JobStatus = await res.json();
      setJob(data);
      if (data.status === "done" || data.status === "failed") {
        clearInterval(pollRef.current!);
        if (data.status === "done") loadGraph(jobId);
      }
    }, 1500);
  }

  async function loadGraph(jobId: string) {
    const res = await fetch(`${API_URL}/repos/${jobId}/graph`);
    setGraph(await res.json());
  }

  async function loadSequenceDiagram() {
    if (!job || !selectedFunction) return;
    setSequenceLoading(true);
    setSequenceDiagram(null);
    try {
      const res = await fetch(`${API_URL}/repos/${job.job_id}/sequence/${encodeURIComponent(selectedFunction)}`);
      const data = await res.json();
      setSequenceDiagram(data.mermaid ?? null);
    } finally {
      setSequenceLoading(false);
    }
  }

  async function submitFeatureRequest() {
    if (!job) return;
    setFeatureLoading(true);
    setFeatureStub(null);
    setFeatureError(null);
    try {
      const res = await fetch(`${API_URL}/repos/${job.job_id}/feature`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feature_request: featureRequest }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail ?? `Feature request failed (${res.status})`);
      setFeatureStub(data.feature_stub ?? data.detail ?? "No response");
    } catch (err) {
      setFeatureError(err instanceof Error ? err.message : String(err));
    } finally {
      setFeatureLoading(false);
    }
  }

  async function submitImpactAnalysis() {
    if (!job || !proposedEdit.trim()) return;
    setImpactLoading(true);
    setImpactError(null);
    try {
      const res = await fetch(`${API_URL}/repos/${job.job_id}/impact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposed_edit: proposedEdit }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Could not start impact analysis");
      }
      for (let attempt = 0; attempt < 160; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const latest = await fetch(`${API_URL}/repos/${job.job_id}`).then((response) => response.json() as Promise<JobStatus>);
        setJob(latest);
        if (latest.impact_report) return;
        if (latest.error) throw new Error(latest.error);
      }
      throw new Error("Impact analysis is taking longer than expected. Please try again shortly.");
    } catch (err) {
      setImpactError(err instanceof Error ? err.message : String(err));
    } finally {
      setImpactLoading(false);
    }
  }

  const busy = job?.status === "pending" || job?.status === "running";

  const filteredGraph = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const query = graphSearch.trim().toLowerCase();
    const nodes = graph.elements.nodes.filter(
      (node) =>
        (nodeType === "All" || node.data.label === nodeType) &&
        (!query || node.data.name.toLowerCase().includes(query)),
    );
    const visibleIds = new Set(nodes.map((node) => node.data.id));
    return {
      nodes,
      edges: graph.elements.edges.filter((edge) => visibleIds.has(edge.data.source) && visibleIds.has(edge.data.target)),
    };
  }, [graph, graphSearch, nodeType]);

  const cyElements = [
    ...filteredGraph.nodes.map((n) => ({ data: { ...n.data, color: NODE_COLORS[n.data.label] ?? "#8892a6" } })),
    ...filteredGraph.edges,
  ];

  function focusNode() {
    const cy = graphRef.current;
    if (!cy || !selectedNode) return;
    const node = cy.getElementById(selectedNode.id);
    cy.elements().removeClass("is-focus is-neighbor");
    node.addClass("is-focus");
    node.neighborhood().addClass("is-neighbor");
    cy.animate({ fit: { eles: node.closedNeighborhood(), padding: 80 }, duration: 350 });
  }

  function resetGraphView() {
    const cy = graphRef.current;
    if (!cy) return;
    cy.elements().removeClass("is-focus is-neighbor");
    cy.fit(undefined, 52);
  }

  return (
    <div className="min-h-screen bg-bg text-ink">
      <main className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-7 sm:px-6 sm:py-10">
        <header className="flex flex-col gap-3 rounded-2xl border border-border bg-surface px-5 py-5 shadow-sm sm:flex-row sm:items-center sm:justify-between sm:px-7">
          <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-ink">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle cx="6" cy="6" r="3" fill="currentColor" />
                <circle cx="18" cy="6" r="3" fill="currentColor" />
                <circle cx="12" cy="18" r="3" fill="currentColor" />
                <path d="M8.5 7.5 11 16M15.5 7.5 13 16M9 6h6" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </div>
            <h1 className="text-xl font-semibold tracking-tight">CodeGrapher</h1>
          </div>
          <p className="text-sm text-ink-muted">Explore a repository’s architecture, risk, and code relationships.</p>
          </div>
          <span className="w-fit rounded-full bg-accent-soft px-3 py-1.5 text-xs font-semibold text-accent">Repository intelligence</span>
        </header>

        <Card title="Submit a repo" icon="📦">
          <div className="grid gap-4">
            <Field label="Repository">
              <input
                className={inputClass}
                placeholder="Git URL or an absolute local path"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                disabled={submitting || busy}
              />
            </Field>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={submitRepo} disabled={!repoPath || submitting || busy} loading={submitting}>
              {submitting ? "Submitting…" : "Ingest repo"}
            </Button>
            {job && (
              <div className="flex items-center gap-2 text-sm text-ink-muted">
                <span className="font-mono text-xs">{job.job_id.slice(0, 8)}</span>
                <StatusPill status={job.status} />
              </div>
            )}
          </div>

          {submitError && (
            <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{submitError}</p>
          )}
          {job?.error && (
            <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{job.error}</p>
          )}

          {logs.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                {busy && <span className="h-1.5 w-1.5 rounded-full bg-info pulse" />}
                {busy ? "Live progress" : "Progress log"}
              </div>
              <div className="max-h-56 overflow-y-auto rounded-lg bg-[#0d1017] p-3 font-mono text-[12px] leading-relaxed text-zinc-300">
                {logs.map((log, i) => (
                  <div key={i} className="flex gap-2">
                    <span className="shrink-0 text-zinc-500">{new Date(log.ts).toLocaleTimeString()}</span>
                    <span>{log.message}</span>
                  </div>
                ))}
                <div ref={logsEndRef} />
              </div>
            </div>
          )}
        </Card>

        {job?.status === "done" && (
          <>
            <section className="flex flex-col gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Analysis overview</p>
              <h2 className="text-2xl font-semibold tracking-tight">Understand the system before you change it.</h2>
              <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">Key findings are organized into focused briefs. Open only the detail you need, then use the interactive map to trace the code behind it.</p>
            </section>
            <Card title="Assess a proposed change" icon="💥">
              <p className="-mt-1 text-sm leading-relaxed text-ink-muted">The repository is ready. Describe the change you’re considering and we’ll trace its downstream impact.</p>
              <div className="flex flex-col gap-3 sm:flex-row">
                <input className={`${inputClass} flex-1`} placeholder="e.g. Change how handle.py routes a user message" value={proposedEdit} onChange={(e) => setProposedEdit(e.target.value)} disabled={impactLoading} />
                <Button onClick={submitImpactAnalysis} disabled={!proposedEdit.trim() || impactLoading} loading={impactLoading}>{impactLoading ? "Tracing impact…" : "Run impact analysis"}</Button>
              </div>
              {impactError && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{impactError}</p>}
              {job.impact_report && <ReportCard title="Latest blast radius" icon="💥" text={job.impact_report} />}
            </Card>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <ReportCard title="Architecture" icon="🗺️" text={job.architecture_report} />
              <ReportCard title="Schema" icon="🗄️" text={job.schema_report} />
              <ReportCard title="Anti-Patterns" icon="⚠️" text={job.anti_pattern_report} />
            </div>

            <Card title="Repository map" icon="🕸️">
              <div className="flex flex-col justify-between gap-3 border-b border-border pb-4 lg:flex-row lg:items-end">
                <div>
                  <p className="text-sm font-medium text-ink">{graph?.elements.nodes.length ?? 0} symbols, {graph?.elements.edges.length ?? 0} relationships</p>
                  <p className="mt-1 text-xs text-ink-muted">Select a node to isolate its direct neighborhood and inspect its connections.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <input className={`${inputClass} w-52 py-2`} value={graphSearch} onChange={(e) => setGraphSearch(e.target.value)} placeholder="Find a symbol…" />
                  <select className={`${inputClass} py-2`} value={nodeType} onChange={(e) => setNodeType(e.target.value)}>
                    <option value="All">All types</option>
                    {Object.keys(NODE_COLORS).map((label) => <option key={label}>{label}</option>)}
                  </select>
                  <Button variant="secondary" onClick={resetGraphView}>Reset view</Button>
                </div>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                {Object.entries(NODE_COLORS).map(([label, color]) => (
                  <span key={label} className="flex items-center gap-1.5 text-xs text-ink-muted">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
                    {label}
                  </span>
                ))}
              </div>
              {graph && (
                <div className="grid overflow-hidden rounded-xl border border-border bg-[#0d1117] lg:grid-cols-[minmax(0,1fr)_260px]">
                  <CytoscapeComponent
                    elements={cyElements}
                    cy={(cy: Core) => { graphRef.current = cy; cy.on("tap", "node", (event) => { const data = event.target.data(); setSelectedNode(data); }); }}
                    style={{ width: "100%", height: "780px" }}
                    layout={{ name: "cose", animate: false, padding: 100, idealEdgeLength: 190, nodeRepulsion: 28000, gravity: 0.18, numIter: 2500 }}
                    stylesheet={[
                      {
                        selector: "node",
                        style: {
                          "background-color": "data(color)",
                          label: "",
                          "font-size": "10px",
                          color: "#cbd5e1",
                          "text-valign": "bottom",
                          "text-margin-y": 7,
                          "text-outline-color": "#0d1117",
                          "text-outline-width": 2,
                          width: 18,
                          height: 18,
                        },
                      },
                      {
                        selector: "edge",
                        style: {
                          width: 1.15,
                          opacity: 0.35,
                          "line-color": "#718096",
                          "target-arrow-color": "#718096",
                          "target-arrow-shape": "triangle",
                          "curve-style": "bezier",
                          label: "data(label)",
                          "font-size": "0px",
                        },
                      },
                      { selector: "node.is-neighbor", style: { opacity: 1, label: "data(name)" } },
                      { selector: "edge.is-neighbor", style: { opacity: 0.85, width: 2, "line-color": "#d4af37", "target-arrow-color": "#d4af37" } },
                      { selector: "node.is-focus", style: { width: 28, height: 28, "border-width": 3, "border-color": "#f7d774", "font-size": "12px" } },
                    ]}
                  />
                  <aside className="flex min-h-[220px] flex-col border-t border-white/10 bg-[#121822] p-5 text-slate-200 lg:border-l lg:border-t-0">
                    {selectedNode ? <>
                      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Selected symbol</p>
                      <span className="mt-3 w-fit rounded-full px-2 py-1 text-xs font-medium" style={{ backgroundColor: `${NODE_COLORS[selectedNode.label]}33`, color: NODE_COLORS[selectedNode.label] }}>{selectedNode.label}</span>
                      <h3 className="mt-3 break-words text-base font-semibold text-white">{selectedNode.name}</h3>
                      <p className="mt-2 break-all font-mono text-[11px] leading-relaxed text-slate-500">{selectedNode.id}</p>
                      <div className="mt-auto pt-5"><Button variant="secondary" onClick={focusNode}>Focus connections</Button></div>
                    </> : <>
                      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Explore the map</p>
                      <h3 className="mt-3 text-base font-semibold text-white">Choose a symbol</h3>
                      <p className="mt-2 text-sm leading-relaxed text-slate-400">Click any node to reveal its type and focus its direct relationships.</p>
                    </>}
                  </aside>
                </div>
              )}
            </Card>

            <Card title="Call sequence diagram" icon="🔀">
              <div className="flex flex-col gap-3 sm:flex-row">
                <select
                  className={`${inputClass} flex-1`}
                  value={selectedFunction}
                  onChange={(e) => setSelectedFunction(e.target.value)}
                >
                  <option value="">Select a function…</option>
                  {graph?.elements.nodes
                    .filter((n) => n.data.label === "Function")
                    .map((n) => (
                      <option key={n.data.id} value={n.data.name}>
                        {n.data.name}
                      </option>
                    ))}
                </select>
                <Button
                  variant="secondary"
                  onClick={loadSequenceDiagram}
                  disabled={!selectedFunction || sequenceLoading}
                  loading={sequenceLoading}
                >
                  {sequenceLoading ? "Loading…" : "Show diagram"}
                </Button>
              </div>
              {sequenceDiagram && (
                <div className="overflow-x-auto rounded-lg border border-border bg-white p-3">
                  <MermaidDiagram chart={sequenceDiagram} />
                </div>
              )}
            </Card>

            <Card title="Request a feature" icon="✨">
              <Field label="Describe the feature">
                <textarea
                  className={inputClass}
                  rows={3}
                  placeholder="e.g. Add a refund_order feature…"
                  value={featureRequest}
                  onChange={(e) => setFeatureRequest(e.target.value)}
                />
              </Field>
              <Button
                onClick={submitFeatureRequest}
                disabled={!featureRequest || featureLoading}
                loading={featureLoading}
              >
                {featureLoading ? "Generating…" : "Generate stub"}
              </Button>
              {featureError && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{featureError}</p>}
              {featureStub &&
                (() => {
                  const { code, language, rest } = splitFeatureStub(featureStub);
                  return (
                    <div className="flex flex-col gap-3">
                      {code && (
                        <div className="overflow-hidden rounded-lg border border-border">
                          <MonacoEditor
                            height="320px"
                            defaultLanguage={language === "ts" ? "typescript" : language}
                            value={code}
                            theme="vs-dark"
                            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
                          />
                        </div>
                      )}
                      <pre className="whitespace-pre-wrap rounded-lg bg-surface-2 p-3.5 text-sm text-ink">
                        {rest}
                      </pre>
                    </div>
                  );
                })()}
            </Card>
          </>
        )}
      </main>
    </div>
  );
}

function ReportCard({ title, icon, text }: { title: string; icon: string; text: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const lines = (text ?? "No findings were returned.").split("\n").filter(Boolean);
  const preview = expanded ? lines : lines.slice(0, 7);

  return (
    <div className="flex min-h-64 flex-col rounded-2xl border border-border bg-surface p-5 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <span aria-hidden="true">{icon}</span>{title}
        </h3>
        <span className="rounded-full bg-surface-2 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">Brief</span>
      </div>
      <div className="mt-4 flex flex-col gap-2.5 text-sm leading-6 text-ink-muted">
        {preview.map((line, index) => {
          const clean = line.replace(/^\*\s+/, "").replace(/^[-•]\s+/, "");
          if (line.startsWith("## ")) return <h4 key={index} className="mt-1 text-base font-semibold text-ink">{line.slice(3)}</h4>;
          if (line.startsWith("### ")) return <h5 key={index} className="mt-1 text-sm font-semibold text-ink">{line.slice(4)}</h5>;
          if (/^\*\s+|^[-•]\s+/.test(line)) return <p key={index} className="border-l-2 border-accent/50 pl-3">{clean}</p>;
          return <p key={index}>{line}</p>;
        })}
      </div>
      {lines.length > 7 && <button onClick={() => setExpanded(!expanded)} className="mt-auto pt-4 text-left text-sm font-semibold text-accent hover:underline">{expanded ? "Show less" : `Read full brief (${lines.length} lines)`}</button>}
    </div>
  );
}
