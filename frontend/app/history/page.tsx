"use client";

import { useEffect, useMemo, useState } from "react";
import { AppShell, headerFromLive } from "@/components/AppShell";
import { ConsoleBody } from "@/components/ConsoleBody";
import { Panel, StatusBadge, ValueReadout } from "@/components/primitives";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLiveStream } from "@/lib/live";

type Source = "LIVE" | "SYNTHETIC" | "REPLAY";
type Period = "daily" | "weekly" | "monthly" | "custom";

interface HistorySummary {
  dataset: Source;
  window: { period: Period; label: string; start_utc: string; end_utc: string; complete: boolean };
  coverage: { run_count: number; expected_minutes: number; covered_minutes: number; coverage_percent: number; sample_count: number; valid_sample_count: number; valid_sample_percent: number };
  incidents: { distinct_count: number; severity_mix: Record<string, number>; total_duration_s: number; mean_duration_s: number };
  recurrence: { rule_id: string; affected_entity: string; distinct_incidents: number; first_seen_utc: string; last_seen_utc: string }[];
  actions: Record<string, number>;
  limits: { truncated: boolean };
}

interface ReviewResponse {
  analysis_id: string;
  generation_mode: "LLM" | "TEMPLATE" | "REPLAY";
  status: string;
  cached: boolean;
  result: { title: string; findings: string[]; limitations: string[] };
}

function previousMonth(): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1))
    .toISOString()
    .slice(0, 10);
}

function fmtSeconds(value: number): string {
  return value < 60 ? `${value.toFixed(0)} s` : `${(value / 60).toFixed(1)} min`;
}

export default function HistoryPage() {
  const { user, loading } = useAuth();
  const live = useLiveStream();
  const [source, setSource] = useState<Source>("SYNTHETIC");
  const [period, setPeriod] = useState<Period>("monthly");
  const [start, setStart] = useState(previousMonth);
  const [end, setEnd] = useState("");
  const [summary, setSummary] = useState<HistorySummary | null>(null);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && !user) window.location.assign("/login/");
  }, [user, loading]);

  const query = useMemo(() => {
    const params = new URLSearchParams({ source, period, start });
    if (period === "custom" && end) params.set("end", end);
    return params.toString();
  }, [source, period, start, end]);

  async function loadSummary() {
    setBusy(true);
    setError(null);
    setReview(null);
    try {
      setSummary(await api.get<HistorySummary>(`/history/summary?${query}`));
    } catch (value) {
      setSummary(null);
      setError(value instanceof ApiError ? value.message : "History request failed.");
    } finally {
      setBusy(false);
    }
  }

  async function generateReview() {
    setBusy(true);
    setError(null);
    try {
      let next = await api.post<ReviewResponse>("/history/review", {
        source, period, start, end: period === "custom" && end ? end : null,
      });
      setReview(next);
      for (let attempt = 0; attempt < 30 && next.status !== "COMPLETED"; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
        next = await api.get<ReviewResponse>(`/analyses/${next.analysis_id}`);
        setReview(next);
      }
    } catch (value) {
      setError(value instanceof ApiError ? value.message : "Review generation failed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (user && user.role !== "SIMULATOR") void loadSummary();
    // Selection changes deliberately refresh the selected evidence window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, query]);

  if (loading || !user) return null;

  return (
    <AppShell role={user.role} runState={headerFromLive(live)}>
      <ConsoleBody
        headline="History and recurrence"
        summary="Facts computed from stored observations. Every view names one dataset and one bounded UTC window; synthetic and live evidence are never combined."
        pending={[]}
      >
        <Panel title="Evidence selection" subtitle="Completed UTC periods only">
          <div className="flex flex-wrap items-end gap-3 text-xs">
            <label className="grid gap-1 text-ink-faint">Dataset
              <select className="rounded border border-edge bg-void px-3 py-2 text-ink" value={source} onChange={(event) => setSource(event.target.value as Source)}>
                <option value="SYNTHETIC">Synthetic demonstration</option>
                <option value="LIVE">Live</option><option value="REPLAY">Replay</option>
              </select>
            </label>
            <label className="grid gap-1 text-ink-faint">Period
              <select className="rounded border border-edge bg-void px-3 py-2 text-ink" value={period} onChange={(event) => setPeriod(event.target.value as Period)}>
                <option value="daily">Completed day</option><option value="weekly">Seven days</option>
                <option value="monthly">Completed month</option><option value="custom">Custom</option>
              </select>
            </label>
            <label className="grid gap-1 text-ink-faint">Start date (UTC)
              <input type="date" className="rounded border border-edge bg-void px-3 py-2 text-ink" value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            {period === "custom" ? <label className="grid gap-1 text-ink-faint">End date, exclusive (UTC)
              <input type="date" className="rounded border border-edge bg-void px-3 py-2 text-ink" value={end} onChange={(event) => setEnd(event.target.value)} />
            </label> : null}
            <button className="rounded border border-accent/50 bg-accent/10 px-3 py-2 font-medium text-accent disabled:opacity-50" disabled={busy} onClick={() => void loadSummary()}>Refresh evidence</button>
          </div>
          {error ? <p className="mt-3 text-sm text-critical">{error}</p> : null}
        </Panel>

        {summary ? <>
          <Panel title={summary.window.label} subtitle={`${summary.window.start_utc} — ${summary.window.end_utc}`} actions={<StatusBadge tone="accent">{summary.dataset}</StatusBadge>}>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <ValueReadout label="Distinct incidents" value={summary.incidents.distinct_count} tone={summary.incidents.distinct_count ? "warning" : "nominal"} note="One lifecycle counts once" />
              <ValueReadout label="Minute coverage" value={summary.coverage.coverage_percent.toFixed(2)} unit="%" tone={summary.coverage.coverage_percent === 100 ? "nominal" : "warning"} note={`${summary.coverage.covered_minutes} / ${summary.coverage.expected_minutes} minutes`} />
              <ValueReadout label="Valid samples" value={summary.coverage.valid_sample_percent.toFixed(2)} unit="%" tone="nominal" note={`${summary.coverage.run_count} source run(s)`} />
              <ValueReadout label="Mean duration" value={fmtSeconds(summary.incidents.mean_duration_s)} tone="accent" />
            </div>
            {summary.limits.truncated ? <p className="mt-3 text-xs text-warning">The bounded query reached its row cap.</p> : null}
          </Panel>

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Recurrence evidence" subtitle="Distinct incident IDs by rule and entity">
              {summary.recurrence.length ? <div className="overflow-auto"><table className="w-full text-left text-xs">
                <thead className="text-ink-faint"><tr><th className="py-1">Rule / entity</th><th>Occurrences</th><th>Observed span</th></tr></thead>
                <tbody>{summary.recurrence.map((item) => <tr key={`${item.rule_id}:${item.affected_entity}`} className="border-t border-edge/60">
                  <td className="py-2"><span className="tnum text-ink">{item.rule_id}</span><span className="block text-ink-faint">{item.affected_entity}</span></td>
                  <td className="tnum text-warning">{item.distinct_incidents}</td><td className="text-ink-faint">{item.first_seen_utc.slice(0, 10)} — {item.last_seen_utc.slice(0, 10)}</td>
                </tr>)}</tbody>
              </table></div> : <p className="text-sm text-ink-faint">No recurring rule/entity pair in this window.</p>}
            </Panel>
            <Panel title="Commanded versus measured" subtitle="Classified from stored command and outcome events">
              <dl className="grid grid-cols-2 gap-2 text-xs">{Object.entries(summary.actions).map(([key, value]) => <div key={key} className="rounded border border-edge/60 p-2">
                <dt className="text-ink-faint">{key.replaceAll("_", " ")}</dt><dd className="tnum mt-1 text-lg text-ink">{value}</dd>
              </div>)}</dl>
            </Panel>
          </div>

          <Panel title="Bounded period review" subtitle="Queued separately from telemetry and cached by dataset and exact window" actions={<button className="rounded border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent disabled:opacity-50" disabled={busy} onClick={() => void generateReview()}>Generate review</button>}>
            {review ? <div className="space-y-2 text-sm text-ink-dim">
              <div className="flex flex-wrap gap-2"><StatusBadge tone="accent">{review.generation_mode}</StatusBadge><StatusBadge tone={review.status === "COMPLETED" ? "nominal" : "warning"}>{review.status}</StatusBadge>{review.cached ? <StatusBadge tone="nominal">Cached</StatusBadge> : null}</div>
              {review.status === "COMPLETED" ? <>
                <h3 className="font-semibold text-ink">{review.result.title}</h3>
                <ul className="list-disc space-y-1 pl-5">{review.result.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul>
                <p className="text-xs text-ink-faint">{review.result.limitations.join(" ")}</p>
              </> : <p className="text-ink-faint">Review queued; deterministic evidence remains available above.</p>}
            </div> : <p className="text-sm text-ink-faint">Generate a citable template report for the selected evidence window.</p>}
          </Panel>
        </> : null}
      </ConsoleBody>
    </AppShell>
  );
}
