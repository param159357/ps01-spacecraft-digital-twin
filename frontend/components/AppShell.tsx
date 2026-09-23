"use client";

// Persistent mission-control header plus the console body.
//
// The header holds the fields section 5 requires in a fixed position: run ID, data
// source, simulated clock, connection/freshness, illumination and mode. Until Phase 2
// provides a run, every one of them reads "no active run" rather than a plausible
// invented value.

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { freshnessTone, type LiveState } from "@/lib/live";
import { ROLE_LABEL, type Role } from "@/lib/types";
import { StatusBadge } from "./primitives";

export interface RunHeaderState {
  runId: string | null;
  source: "LIVE" | "SYNTHETIC" | "REPLAY" | null;
  runStatus: "RUNNING" | "PAUSED" | "STOPPED" | null;
  simTimeS: number | null;
  illumination: "SUNLIGHT" | "ECLIPSE" | null;
  mode: string | null;
  connected: boolean;
  telemetryAgeS: number | null;
}

export const NO_RUN: RunHeaderState = {
  runId: null,
  source: null,
  runStatus: null,
  simTimeS: null,
  illumination: null,
  mode: null,
  connected: false,
  telemetryAgeS: null,
};

/** Derive the header from a live subscription. */
export function headerFromLive(live: LiveState): RunHeaderState {
  return {
    runId: live.run?.run_id ?? null,
    source: live.run?.source ?? null,
    runStatus: live.run?.status ?? null,
    simTimeS: live.frame?.sim_time_s ?? live.run?.sim_time_s ?? null,
    illumination: live.frame?.context.illumination ?? null,
    mode: live.frame?.context.mode ?? null,
    connected: live.connected,
    telemetryAgeS: live.dataAgeS,
  };
}

function formatSimClock(seconds: number | null): string {
  if (seconds === null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function HeaderField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] tracking-widest text-ink-faint uppercase">{label}</span>
      <span className="text-xs text-ink">{children}</span>
    </div>
  );
}

function RunHeader({ state }: { state: RunHeaderState }) {
  const { runId, source, runStatus, simTimeS, illumination, mode, connected } = state;

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
      <HeaderField label="Run">
        <span className="tnum">{runId ? runId.slice(0, 8) : "—"}</span>
      </HeaderField>

      <HeaderField label="Source">
        {source ? (
          <StatusBadge tone={source === "LIVE" ? "accent" : "warning"}>{source}</StatusBadge>
        ) : (
          <StatusBadge tone="unavailable">No run</StatusBadge>
        )}
      </HeaderField>

      <HeaderField label="State">
        {runStatus ? (
          <StatusBadge tone={runStatus === "RUNNING" ? "nominal" : "warning"}>
            {runStatus}
          </StatusBadge>
        ) : (
          <StatusBadge tone="unavailable">{"—"}</StatusBadge>
        )}
      </HeaderField>

      <HeaderField label="Spacecraft clock">
        <span className="tnum">{formatSimClock(simTimeS)}</span>
      </HeaderField>

      <HeaderField label="Illumination">
        {illumination ? (
          <StatusBadge tone={illumination === "SUNLIGHT" ? "nominal" : "warning"}>
            {illumination}
          </StatusBadge>
        ) : (
          <StatusBadge tone="unavailable">Unknown</StatusBadge>
        )}
      </HeaderField>

      <HeaderField label="Mode">{mode ?? "—"}</HeaderField>

      {/* App connection and spacecraft data freshness are different facts, so they get
          two separate fields. A heartbeat proves only the first. */}
      <HeaderField label="App link">
        <StatusBadge tone={connected ? "nominal" : "critical"}>
          {connected ? "Connected" : "Disconnected"}
        </StatusBadge>
      </HeaderField>

      <HeaderField label="Data age">
        {state.telemetryAgeS === null ? (
          <StatusBadge tone="unavailable">No data</StatusBadge>
        ) : (
          <StatusBadge tone={freshnessTone(state.telemetryAgeS)}>
            <span className="tnum">{state.telemetryAgeS.toFixed(1)} s</span>
          </StatusBadge>
        )}
      </HeaderField>
    </div>
  );
}

export function AppShell({
  role,
  runState = NO_RUN,
  children,
}: {
  role: Role;
  runState?: RunHeaderState;
  children: ReactNode;
}) {
  const { user, logout } = useAuth();
  const [utc, setUtc] = useState<string>("");

  useEffect(() => {
    const tick = () => setUtc(new Date().toISOString().slice(11, 19) + "Z");
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="min-h-screen bg-void">
      <header className="sticky top-0 z-20 border-b border-edge bg-deck/95 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-2">
          <div className="flex items-baseline gap-3">
            <span className="text-sm font-bold tracking-[0.2em] text-accent">PS01</span>
            <span className="text-sm text-ink">{ROLE_LABEL[role]}</span>
          </div>

          <RunHeader state={runState} />

          <div className="flex items-center gap-3">
            <div className="flex flex-col items-end">
              <span className="text-[10px] tracking-widest text-ink-faint uppercase">
                Ground UTC
              </span>
              <span className="tnum text-xs text-ink">{utc || "—"}</span>
            </div>
            <div className="flex flex-col items-end">
              <span className="text-[10px] tracking-widest text-ink-faint uppercase">
                Signed in
              </span>
              <span className="text-xs text-ink">{user?.username ?? "—"}</span>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded border border-edge-strong px-3 py-1.5 text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1880px] px-4 py-4">{children}</main>

      <footer className="border-t border-edge px-4 py-3 text-[11px] text-ink-faint">
        Fictional demonstration vehicle. Mission-control inspired interface; not an official
        NASA or ISRO system and not certified for flight use.
      </footer>
    </div>
  );
}
