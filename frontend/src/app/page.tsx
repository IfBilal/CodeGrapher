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

  if (!svg) return <p className="text-sm text-zinc-500">Rendering diagram...</p>;
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
  File: "#6b7280",
  Class: "#2563eb",
  Function: "#16a34a",
  Field: "#9333ea",
  ExternalSymbol: "#9ca3af",
  ExternalService: "#dc2626",
};

export default function Home() {
  const [repoPath, setRepoPath] = useState("");
  const [proposedEdit, setProposedEdit] = useState("");
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

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      eventSourceRef.current?.close();
    };
  }, []);

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
    setSubmitError(null);
    setJob(null);
    setGraph(null);
    setFeatureStub(null);
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
      pollJob(job_id);
      streamLogs(job_id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
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

  const cyElements = graph
    ? [
        ...graph.elements.nodes.map((n) => ({
          data: { ...n.data, color: NODE_COLORS[n.data.label] ?? "#6b7280" },
        })),
        ...graph.elements.edges,
      ]
    : [];

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black text-black dark:text-zinc-50 font-sans">
      <main className="max-w-5xl mx-auto py-12 px-6 flex flex-col gap-8">
        <h1 className="text-2xl font-semibold">CodeGrapher</h1>

        <section className="flex flex-col gap-3 border border-zinc-200 dark:border-zinc-800 rounded-lg p-5">
          <h2 className="font-medium">Submit a repo</h2>
          <input
            className="border border-zinc-300 dark:border-zinc-700 bg-transparent rounded px-3 py-2"
            placeholder="Git URL (https://github.com/...) or an absolute local path"
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
          />
          <input
            className="border border-zinc-300 dark:border-zinc-700 bg-transparent rounded px-3 py-2"
            placeholder="Proposed edit to analyze (optional)"
            value={proposedEdit}
            onChange={(e) => setProposedEdit(e.target.value)}
          />
          <button
            className="self-start bg-black text-white dark:bg-white dark:text-black rounded px-4 py-2 disabled:opacity-50"
            onClick={submitRepo}
            disabled={!repoPath || job?.status === "pending" || job?.status === "running"}
          >
            Ingest repo
          </button>
          {submitError && <p className="text-red-600 text-sm">{submitError}</p>}
          {job && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              Job {job.job_id.slice(0, 8)} — status: <strong>{job.status}</strong>
              {job.error && ` — ${job.error}`}
            </p>
          )}
          {logs.length > 0 && (
            <div className="text-xs font-mono bg-zinc-900 text-zinc-200 rounded p-3 max-h-48 overflow-y-auto flex flex-col gap-1">
              {logs.map((log, i) => (
                <div key={i}>
                  <span className="text-zinc-500">{new Date(log.ts).toLocaleTimeString()}</span> {log.message}
                </div>
              ))}
            </div>
          )}
        </section>

        {job?.status === "done" && (
          <>
            <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <ReportCard title="Architecture" text={job.architecture_report} />
              <ReportCard title="Schema" text={job.schema_report} />
              <ReportCard title="Impact / Blast Radius" text={job.impact_report} />
              <ReportCard title="Anti-Patterns" text={job.anti_pattern_report} />
            </section>

            <section className="border border-zinc-200 dark:border-zinc-800 rounded-lg p-5">
              <h2 className="font-medium mb-3">Graph ({graph?.elements.nodes.length ?? 0} nodes)</h2>
              {graph && (
                <CytoscapeComponent
                  elements={cyElements}
                  style={{ width: "100%", height: "500px" }}
                  layout={{ name: "cose", animate: false }}
                  stylesheet={[
                    {
                      selector: "node",
                      style: {
                        "background-color": "data(color)",
                        label: "data(name)",
                        "font-size": "8px",
                        color: "#666",
                        "text-valign": "bottom",
                        width: 16,
                        height: 16,
                      },
                    },
                    {
                      selector: "edge",
                      style: {
                        width: 1,
                        "line-color": "#ccc",
                        "target-arrow-color": "#ccc",
                        "target-arrow-shape": "triangle",
                        "curve-style": "bezier",
                        label: "data(label)",
                        "font-size": "6px",
                      },
                    },
                  ]}
                />
              )}
            </section>

            <section className="flex flex-col gap-3 border border-zinc-200 dark:border-zinc-800 rounded-lg p-5">
              <h2 className="font-medium">Call sequence diagram</h2>
              <div className="flex gap-2">
                <select
                  className="border border-zinc-300 dark:border-zinc-700 bg-transparent rounded px-3 py-2 flex-1"
                  value={selectedFunction}
                  onChange={(e) => setSelectedFunction(e.target.value)}
                >
                  <option value="">Select a function...</option>
                  {graph?.elements.nodes
                    .filter((n) => n.data.label === "Function")
                    .map((n) => (
                      <option key={n.data.id} value={n.data.name}>
                        {n.data.name}
                      </option>
                    ))}
                </select>
                <button
                  className="bg-black text-white dark:bg-white dark:text-black rounded px-4 py-2 disabled:opacity-50"
                  onClick={loadSequenceDiagram}
                  disabled={!selectedFunction || sequenceLoading}
                >
                  {sequenceLoading ? "Loading..." : "Show diagram"}
                </button>
              </div>
              {sequenceDiagram && (
                <div className="bg-white dark:bg-zinc-900 rounded p-3 overflow-x-auto">
                  <MermaidDiagram chart={sequenceDiagram} />
                </div>
              )}
            </section>

            <section className="flex flex-col gap-3 border border-zinc-200 dark:border-zinc-800 rounded-lg p-5">
              <h2 className="font-medium">Request a feature</h2>
              <textarea
                className="border border-zinc-300 dark:border-zinc-700 bg-transparent rounded px-3 py-2"
                rows={3}
                placeholder="e.g. Add a refund_order feature..."
                value={featureRequest}
                onChange={(e) => setFeatureRequest(e.target.value)}
              />
              <button
                className="self-start bg-black text-white dark:bg-white dark:text-black rounded px-4 py-2 disabled:opacity-50"
                onClick={submitFeatureRequest}
                disabled={!featureRequest || featureLoading}
              >
                {featureLoading ? "Generating..." : "Generate stub"}
              </button>
              {featureStub &&
                (() => {
                  const { code, rest } = splitFeatureStub(featureStub);
                  return (
                    <>
                      {code && (
                        <div className="border border-zinc-300 dark:border-zinc-700 rounded overflow-hidden">
                          <MonacoEditor
                            height="320px"
                            defaultLanguage="python"
                            value={code}
                            theme="vs-dark"
                            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
                          />
                        </div>
                      )}
                      <pre className="whitespace-pre-wrap text-sm bg-zinc-100 dark:bg-zinc-900 rounded p-3 overflow-x-auto">
                        {rest}
                      </pre>
                    </>
                  );
                })()}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function ReportCard({ title, text }: { title: string; text: string | null }) {
  return (
    <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg p-4">
      <h3 className="font-medium mb-2">{title}</h3>
      <pre className="whitespace-pre-wrap text-xs text-zinc-700 dark:text-zinc-300 max-h-64 overflow-y-auto">
        {text}
      </pre>
    </div>
  );
}
