"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), { ssr: false });
const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

// The Feature Architect's response is markdown with a fenced code block
// (the actual stub) followed by prose (contract notes). Split them so the
// code renders in Monaco - with real syntax highlighting - instead of a
// flat <pre> block, and the prose stays as plain text underneath.
function splitFeatureStub(markdown: string): { code: string | null; rest: string } {
  const match = markdown.match(/```(?:python)?\n([\s\S]*?)```/);
  if (!match) return { code: null, rest: markdown };
  return { code: match[1].trimEnd(), rest: markdown.slice(match.index! + match[0].length).trim() };
}

function MermaidDiagram({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const idRef = useRef(`mermaid-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    let cancelled = false;
    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, theme: "neutral" });
      const { svg } = await mermaid.render(idRef.current, chart);
      if (!cancelled) setSvg(svg);
    });
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (!svg) return <p className="text-sm text-ink-muted">Rendering diagram…</p>;
  // eslint-disable-next-line react/no-danger
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

type CytoscapeElements = {
  elements: {
    nodes: { data: { id: string; label: string; name: string } }[];
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
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-6 shadow-sm">
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
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [selectedFunction, setSelectedFunction] = useState("");
  const [sequenceDiagram, setSequenceDiagram] = useState<string | null>(null);
  const [sequenceLoading, setSequenceLoading] = useState(false);
  const [logs, setLogs] = useState<{ message: string; ts: string }[]>([]);
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

    try {
      const res = await fetch(`${API_URL}/repos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_path: repoPath,
          ...(proposedEdit ? { proposed_edit: proposedEdit } : {}),
        }),
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
    try {
      const res = await fetch(`${API_URL}/repos/${job.job_id}/feature`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feature_request: featureRequest }),
      });
      const data = await res.json();
      setFeatureStub(data.feature_stub ?? data.detail ?? "No response");
    } finally {
      setFeatureLoading(false);
    }
  }

  const busy = job?.status === "pending" || job?.status === "running";

  const cyElements = graph
    ? [
        ...graph.elements.nodes.map((n) => ({
          data: { ...n.data, color: NODE_COLORS[n.data.label] ?? "#8892a6" },
        })),
        ...graph.elements.edges,
      ]
    : [];

  return (
    <div className="min-h-screen bg-bg text-ink">
      <main className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-12">
        <header className="flex flex-col gap-1.5">
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
          <p className="text-sm text-ink-muted">Turn any repository into a queryable knowledge graph.</p>
        </header>

        <Card title="Submit a repo" icon="📦">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Repository">
              <input
                className={inputClass}
                placeholder="Git URL or an absolute local path"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                disabled={submitting || busy}
              />
            </Field>
            <Field label="Proposed edit (optional)">
              <input
                className={inputClass}
                placeholder="What change should Impact Analysis evaluate?"
                value={proposedEdit}
                onChange={(e) => setProposedEdit(e.target.value)}
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
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <ReportCard title="Architecture" icon="🗺️" text={job.architecture_report} />
              <ReportCard title="Schema" icon="🗄️" text={job.schema_report} />
              <ReportCard title="Impact / Blast Radius" icon="💥" text={job.impact_report} />
              <ReportCard title="Anti-Patterns" icon="⚠️" text={job.anti_pattern_report} />
            </div>

            <Card title={`Graph — ${graph?.elements.nodes.length ?? 0} nodes`} icon="🕸️">
              <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                {Object.entries(NODE_COLORS).map(([label, color]) => (
                  <span key={label} className="flex items-center gap-1.5 text-xs text-ink-muted">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
                    {label}
                  </span>
                ))}
              </div>
              {graph && (
                <div className="overflow-hidden rounded-lg border border-border">
                  <CytoscapeComponent
                    elements={cyElements}
                    style={{ width: "100%", height: "480px" }}
                    layout={{ name: "cose", animate: false }}
                    stylesheet={[
                      {
                        selector: "node",
                        style: {
                          "background-color": "data(color)",
                          label: "data(name)",
                          "font-size": "8px",
                          color: "#888",
                          "text-valign": "bottom",
                          width: 16,
                          height: 16,
                        },
                      },
                      {
                        selector: "edge",
                        style: {
                          width: 1,
                          "line-color": "#c7cddb",
                          "target-arrow-color": "#c7cddb",
                          "target-arrow-shape": "triangle",
                          "curve-style": "bezier",
                          label: "data(label)",
                          "font-size": "6px",
                        },
                      },
                    ]}
                  />
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
              {featureStub &&
                (() => {
                  const { code, rest } = splitFeatureStub(featureStub);
                  return (
                    <div className="flex flex-col gap-3">
                      {code && (
                        <div className="overflow-hidden rounded-lg border border-border">
                          <MonacoEditor
                            height="320px"
                            defaultLanguage="python"
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
  return (
    <div className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-5 shadow-sm">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <span aria-hidden="true">{icon}</span>
        {title}
      </h3>
      <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-ink-muted">
        {text}
      </pre>
    </div>
  );
}
