"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), { ssr: false });

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  async function submitRepo() {
    setSubmitError(null);
    setJob(null);
    setGraph(null);
    setFeatureStub(null);

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
            placeholder="Absolute path to a local repo, e.g. /home/you/project"
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
              {featureStub && (
                <pre className="whitespace-pre-wrap text-sm bg-zinc-100 dark:bg-zinc-900 rounded p-3 overflow-x-auto">
                  {featureStub}
                </pre>
              )}
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
