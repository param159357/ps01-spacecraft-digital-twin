// Live telemetry types and the SSE subscription hook.
//
// Two failures must look different on screen and are tracked separately here:
//   * `connected` false  -> this browser lost the backend (network, laptop asleep)
//   * `dataAgeS` growing -> the app is fine, the spacecraft stopped sending
// A heartbeat only proves the first. It never means fresh spacecraft data arrived.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { QualityState, RunStatus, SourceKind } from "./types";

export interface ChannelQualityInfo {
  state: QualityState;
  reason: string | null;
  source_time_utc: string | null;
  last_valid_value: number | boolean | null;
}

export interface FrameContext {
  vehicle_config_version: string;
  model_version: string;
  run_id: string;
  source: SourceKind;
  run_status: RunStatus;
  mode: string;
  illumination: "SUNLIGHT" | "ECLIPSE";
  contact_expected: boolean;
  control_revision: number;
  eclipse_next_change_s: number | null;
  known_commands: Record<string, unknown>;
}

export interface Frame {
  run_id: string;
  frame_seq: number;
  tick_seq: number;
  sim_time_s: number;
  received_at_utc: string;
  values: Record<string, number | boolean | null>;
  quality: Record<string, ChannelQualityInfo>;
  context: FrameContext;
  engineering: Record<string, number | boolean | string | null>;
}

export interface RunMeta {
  run_id: string | null;
  status: RunStatus | null;
  source: SourceKind | null;
  control_revision: number;
  frame_seq: number;
  sim_time_s: number | null;
  sim_epoch_utc: string | null;
  config_version: string;
  model_version: string;
  storage_error: string | null;
  tick_seconds: number;
}

export interface LiveState {
  run: RunMeta | null;
  frame: Frame | null;
  /** Backend stream connection, not spacecraft data freshness. */
  connected: boolean;
  /** Seconds since this browser last received a frame, from its own clock. */
  dataAgeS: number | null;
  lastEventAt: number | null;
  error: string | null;
}

export interface HistoryPoint {
  t: number;
  [channel: string]: number | null;
}

const MAX_HISTORY_POINTS = 900;

/**
 * Subscribe to /api/stream.
 *
 * The server sends a snapshot first, under the same lock that registers the
 * subscription, so nothing can slip through the gap between the two. A reset arrives as
 * a run_changed event and clears the chart buffer, because points from a previous run
 * must never be drawn on the new one's chart.
 */
export function useLiveStream(chartChannels: string[] = []): LiveState & {
  history: HistoryPoint[];
  reconnect: () => void;
} {
  const [state, setState] = useState<LiveState>({
    run: null,
    frame: null,
    connected: false,
    dataAgeS: null,
    lastEventAt: null,
    error: null,
  });
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [nonce, setNonce] = useState(0);

  const runIdRef = useRef<string | null>(null);
  const frameSeqRef = useRef<number>(-1);
  const lastFrameClockRef = useRef<number | null>(null);
  const channelsKey = chartChannels.join(",");

  const reconnect = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const channels = channelsKey ? channelsKey.split(",") : [];
    const source = new EventSource("/api/stream", { withCredentials: true });
    let closed = false;

    const applyFrame = (frame: Frame) => {
      // Discard anything from a superseded run or an out-of-order frame.
      if (runIdRef.current && frame.run_id !== runIdRef.current) return;
      if (frame.frame_seq <= frameSeqRef.current) return;
      frameSeqRef.current = frame.frame_seq;
      lastFrameClockRef.current = performance.now();

      setState((prev) => ({
        ...prev,
        frame,
        connected: true,
        dataAgeS: 0,
        lastEventAt: Date.now(),
        error: null,
      }));

      if (channels.length) {
        setHistory((prev) => {
          const point: HistoryPoint = { t: Math.round(frame.sim_time_s) };
          for (const id of channels) {
            const q = frame.quality[id];
            const v = frame.values[id];
            // Only plot values the server says are usable; never plot through a gap.
            point[id] = q?.state === "VALID" && typeof v === "number" ? v : null;
          }
          const next = [...prev, point];
          return next.length > MAX_HISTORY_POINTS ? next.slice(-MAX_HISTORY_POINTS) : next;
        });
      }
    };

    const resetTo = (runId: string | null) => {
      runIdRef.current = runId;
      frameSeqRef.current = -1;
      lastFrameClockRef.current = null;
      setHistory([]);
    };

    source.addEventListener("snapshot", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as {
        run: RunMeta;
        frame: Frame | null;
      };
      resetTo(payload.run.run_id);
      setState((prev) => ({
        ...prev,
        run: payload.run,
        connected: true,
        lastEventAt: Date.now(),
        error: null,
      }));
      if (payload.frame) applyFrame(payload.frame);
    });

    source.addEventListener("telemetry", (event) => {
      applyFrame(JSON.parse((event as MessageEvent).data) as Frame);
    });

    source.addEventListener("run_changed", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as Partial<RunMeta> & {
        reason?: string;
        storage_error?: string;
      };
      if (payload.reason === "resubscribe_required") {
        source.close();
        if (!closed) setNonce((n) => n + 1);
        return;
      }
      if (payload.run_id && payload.run_id !== runIdRef.current) {
        // A reset started a new run: clear the chart buffer rather than drawing the
        // new run's points onto the old run's series.
        resetTo(payload.run_id);
        setState((prev) => ({ ...prev, frame: null }));
      }
      setState((prev) => ({
        ...prev,
        run: prev.run ? { ...prev.run, ...payload } : (payload as RunMeta),
        lastEventAt: Date.now(),
      }));
    });

    source.addEventListener("heartbeat", () => {
      // Confirms the backend connection only. Data freshness is unchanged.
      setState((prev) => ({ ...prev, connected: true, lastEventAt: Date.now() }));
    });

    source.onerror = () => {
      setState((prev) => ({ ...prev, connected: false }));
    };

    source.onopen = () => {
      setState((prev) => ({ ...prev, connected: true, error: null }));
    };

    // Data age is measured on this browser's own monotonic clock, never by mixing it
    // with a simulated-time value.
    const ageTimer = window.setInterval(() => {
      setState((prev) => {
        if (lastFrameClockRef.current === null) return prev;
        const age = (performance.now() - lastFrameClockRef.current) / 1000;
        return { ...prev, dataAgeS: age };
      });
    }, 500);

    return () => {
      closed = true;
      window.clearInterval(ageTimer);
      source.close();
    };
  }, [channelsKey, nonce]);

  return { ...state, history, reconnect };
}

/** Freshness judgement, using the thresholds the backend publishes. */
export function freshnessTone(
  ageS: number | null,
  warningAfterS = 3,
  criticalAfterS = 10,
): "nominal" | "warning" | "critical" | "unavailable" {
  if (ageS === null) return "unavailable";
  if (ageS >= criticalAfterS) return "critical";
  if (ageS >= warningAfterS) return "warning";
  return "nominal";
}

// --- simulator control -------------------------------------------------------

export type RunOperation = "START" | "PAUSE" | "RESET";

export interface SimulatorConditions {
  generation_factor: number;
  extra_load_w: number;
  heater_stuck_on: boolean;
  contact_loss: boolean;
  branch_stuck_on: boolean;
  isolator_failed: boolean;
  payload_duty_percent: number;
  heater_auto: boolean;
  mode: string;
}

export interface SimulatorTruthView {
  tick_seq: number;
  sim_time_s: number;
  energy_wh: number;
  temperatures_c: Record<string, number>;
  gas_mass_kg: number;
  heater_on: boolean;
  valve_open: boolean;
  hidden_conditions: Record<string, number | boolean>;
  known_commands: Record<string, unknown>;
}

export function runControl(
  operation: RunOperation,
  // Bounded, visibly labelled starting conditions for the demonstration scenarios.
  options: {
    seed?: number;
    initial_soc_percent?: number;
    initial_sim_time_s?: number;
    initial_payload_temperature_c?: number;
  } = {},
) {
  return api.post<RunMeta>("/simulator/run", { operation, ...options });
}

export function setConditions(update: Partial<SimulatorConditions>) {
  return api.post<{ control_revision: number }>("/simulator/conditions", update);
}

export function fetchSimulatorState() {
  return api.get<{ run: RunMeta; truth: SimulatorTruthView | null }>("/simulator/state");
}
