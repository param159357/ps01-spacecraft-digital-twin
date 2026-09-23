"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { StatusBadge } from "@/components/primitives";

interface Statement { statement: string; evidence_ids: string[] }
interface Analysis {
  analysis_id: string;
  status: string;
  generation_mode: "LLM" | "TEMPLATE" | "REPLAY";
  cached: boolean;
  result?: {
    fallback_reason?: string | null;
    evidence_trail: { tool: string }[];
    investigation: {
      observations: Statement[];
      hypotheses: Statement[];
      missing_evidence: string[];
      supported_action_ids: string[];
      next_checks: string[];
    };
  };
}

export function InvestigationPanel({ incidentId }: { incidentId: string }) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.get<Analysis>(`/incidents/${incidentId}/analysis/latest`)
      .then((value) => { if (active) setAnalysis(value); })
      .catch((value) => {
        if (active && (!(value instanceof ApiError) || value.code !== "ANALYSIS_NOT_FOUND")) {
          setError(value instanceof ApiError ? value.message : "Investigation load failed.");
        }
      });
    return () => { active = false; };
  }, [incidentId]);

  useEffect(() => {
    if (!analysis || analysis.status === "COMPLETED" || analysis.status === "DEFERRED") return;
    const timer = window.setInterval(async () => {
      try { setAnalysis(await api.get<Analysis>(`/analyses/${analysis.analysis_id}`)); }
      catch (value) { setError(value instanceof ApiError ? value.message : "Investigation refresh failed."); }
    }, 800);
    return () => window.clearInterval(timer);
  }, [analysis]);

  async function investigate() {
    setBusy(true); setError(null);
    try { setAnalysis(await api.post<Analysis>(`/incidents/${incidentId}/investigate`)); }
    catch (value) { setError(value instanceof ApiError ? value.message : "Investigation failed."); }
    finally { setBusy(false); }
  }

  const result = analysis?.result;
  const content = result?.investigation;
  return <div className="space-y-2 border-t border-edge pt-2 text-xs">
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" disabled={busy} onClick={() => void investigate()} className="rounded border border-accent px-2 py-1 text-accent disabled:opacity-40">
        {busy ? "Requesting…" : "Investigate stored evidence"}
      </button>
      {analysis ? <StatusBadge tone={analysis.status === "COMPLETED" ? "nominal" : "warning"}>{analysis.status}</StatusBadge> : null}
      {analysis ? <StatusBadge tone="accent">{analysis.generation_mode}</StatusBadge> : null}
      {analysis?.cached ? <StatusBadge tone="nominal">Cached revision</StatusBadge> : null}
    </div>
    <p className="text-[10px] text-ink-faint">Read-only analysis. It cannot execute recovery or change thresholds.</p>
    {error ? <p className="text-critical">{error}</p> : null}
    {result?.fallback_reason ? <p className="text-warning">Template fallback: {result.fallback_reason.replaceAll("_", " ").toLowerCase()}.</p> : null}
    {content ? <div className="space-y-2 rounded border border-edge/70 p-2">
      <p className="text-[10px] tracking-wide text-ink-faint uppercase">Observations</p>
      {content.observations.map((item) => <p key={`${item.statement}:${item.evidence_ids.join()}`} className="text-ink-dim">{item.statement} <span className="tnum text-[10px] text-ink-faint">[{item.evidence_ids.join(", ")}]</span></p>)}
      {content.hypotheses.length ? <><p className="text-[10px] tracking-wide text-ink-faint uppercase">Hypotheses</p>{content.hypotheses.map((item) => <p key={item.statement} className="text-ink-dim">{item.statement}</p>)}</> : null}
      <p className="text-[10px] tracking-wide text-ink-faint uppercase">Missing evidence</p>
      <ul className="list-disc pl-4 text-ink-faint">{content.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
      <p className="text-[10px] tracking-wide text-ink-faint uppercase">Next checks</p>
      <ul className="list-disc pl-4 text-ink-dim">{content.next_checks.map((item) => <li key={item}>{item}</li>)}</ul>
      <p className="text-[10px] text-ink-faint">Evidence trail: {result.evidence_trail.map((item) => item.tool).join(" → ")}</p>
    </div> : null}
  </div>;
}
