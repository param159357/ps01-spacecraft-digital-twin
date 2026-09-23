"use client";

import { useCallback, useEffect, useState } from "react";
import { AppShell, headerFromLive } from "@/components/AppShell";
import { LiveChart } from "@/components/LiveChart";
import { Panel, StatusBadge } from "@/components/primitives";
import { ChannelReadout, useChannelMeta } from "@/components/telemetry";
import { ApiError } from "@/lib/api";
import { useRequireRole } from "@/lib/auth";
import {
  fetchSimulatorState,
  runControl,
  setConditions,
  useLiveStream,
  type RunOperation,
  type SimulatorTruthView,
} from "@/lib/live";

const CHART_CHANNELS = ["battery_soc", "load_power", "battery_temperature"];

function ControlButton({
  label,
  onClick,
  busy,
  tone = "default",
}: {
  label: string;
  onClick: () => void;
  busy: boolean;
  tone?: "default" | "primary" | "danger";
}) {
  const classes = {
    default: "border-edge-strong text-ink-dim hover:border-accent hover:text-accent",
    primary: "border-accent bg-accent/15 text-accent hover:bg-accent/25",
    danger: "border-critical/50 text-critical hover:bg-critical/10",
  }[tone];

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={`rounded border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40 ${classes}`}
    >
      {label}
    </button>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  hint,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  hint?: string;
  onCommit: (value: number) => void;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between">
        <label className="text-xs text-ink-dim">{label}</label>
        <span className="tnum text-xs text-ink">
          {local}
          <span className="ml-1 text-ink-faint">{unit}</span>
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onMouseUp={() => onCommit(local)}
        onTouchEnd={() => onCommit(local)}
        onKeyUp={() => onCommit(local)}
        className="w-full accent-[var(--color-accent)]"
      />
      {hint ? <p className="text-[10px] text-ink-faint">{hint}</p> : null}
    </div>
  );
}

function Toggle({
  label,
  checked,
  hint,
  onChange,
}: {
  label: string;
  checked: boolean;
  hint?: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-[var(--color-accent)]"
      />
      <span>
        <span className="block text-xs text-ink-dim">{label}</span>
        {hint ? <span className="block text-[10px] text-ink-faint">{hint}</span> : null}
      </span>
    </label>
  );
}

export default function SimulatorConsole() {
  const { user, checking } = useRequireRole("SIMULATOR");
  const live = useLiveStream(CHART_CHANNELS);
  const { meta } = useChannelMeta();

  const [truth, setTruth] = useState<SimulatorTruthView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Local mirror of the injected conditions so the controls stay responsive.
  const [conditions, setLocalConditions] = useState({
    generation_factor: 1,
    extra_load_w: 0,
    payload_duty_percent: 100,
    heater_stuck_on: false,
    heater_auto: true,
    contact_loss: false,
  });

  const refreshTruth = useCallback(async () => {
    try {
      const state = await fetchSimulatorState();
      setTruth(state.truth);
      if (state.truth) {
        const hidden = state.truth.hidden_conditions;
        const known = state.truth.known_commands;
        setLocalConditions({
          generation_factor: Number(hidden.generation_factor ?? 1),
          extra_load_w: Number(hidden.extra_load_w ?? 0),
          heater_stuck_on: Boolean(hidden.heater_stuck_on),
          contact_loss: Boolean(hidden.contact_loss),
          payload_duty_percent: Number(known.payload_duty_percent ?? 100),
          heater_auto: Boolean(known.heater_auto ?? true),
        });
      }
    } catch {
      /* the header already reports connection state */
    }
  }, []);

  useEffect(() => {
    void refreshTruth();
    const id = window.setInterval(() => void refreshTruth(), 2000);
    return () => window.clearInterval(id);
  }, [refreshTruth]);

  async function operate(operation: RunOperation) {
    setBusy(true);
    setError(null);
    try {
      await runControl(operation);
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Run control failed.");
    } finally {
      setBusy(false);
    }
  }

  async function commit(update: Record<string, number | boolean>) {
    setError(null);
    setLocalConditions((prev) => ({ ...prev, ...update }));
    try {
      await setConditions(update);
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not apply the condition.");
      await refreshTruth();
    }
  }

  if (checking || !user) return null;

  const { run, frame, history } = live;
  const running = run?.status === "RUNNING";

  return (
    <AppShell role="SIMULATOR" runState={headerFromLive(live)}>
      <div className="space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-ink">Simulator console</h1>
          <p className="mt-0.5 max-w-4xl text-sm text-ink-dim">
            The run lives in the backend. Closing this browser does not stop it, and every
            other console observes the same sequence. Hidden conditions below are visible
            only here.
          </p>
        </div>

        {error ? (
          <p role="alert" className="rounded border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-critical">
            {error}
          </p>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Run control"
            subtitle="Backend-owned run"
            actions={
              <StatusBadge tone={running ? "nominal" : "warning"}>
                {run?.status ?? "NO RUN"}
              </StatusBadge>
            }
          >
            <div className="flex flex-wrap gap-2">
              <ControlButton
                label={run?.run_id ? "Resume" : "Start run"}
                onClick={() => void operate("START")}
                busy={busy || running}
                tone="primary"
              />
              <ControlButton label="Pause" onClick={() => void operate("PAUSE")} busy={busy || !running} />
              <ControlButton label="Reset" onClick={() => void operate("RESET")} busy={busy} tone="danger" />
            </div>
            <dl className="mt-4 space-y-1 text-xs">
              <div className="flex justify-between">
                <dt className="text-ink-faint">Run ID</dt>
                <dd className="tnum text-ink">{run?.run_id?.slice(0, 8) ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-faint">Frame</dt>
                <dd className="tnum text-ink">{run?.frame_seq ?? 0}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-faint">Tick interval</dt>
                <dd className="tnum text-ink">{run?.tick_seconds ?? "—"} s</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-faint">Control revision</dt>
                <dd className="tnum text-ink">{run?.control_revision ?? 0}</dd>
              </div>
            </dl>
            <p className="mt-3 text-[10px] text-ink-faint">
              A reset starts a new run ID and clears chart buffers. Previous runs stay in the
              database.
            </p>
          </Panel>

          <Panel title="Hidden conditions" subtitle="Simulator-only. Never sent to an operator.">
            <div className="space-y-4">
              <Slider
                label="Generation factor"
                value={conditions.generation_factor}
                min={0}
                max={1}
                step={0.05}
                unit=""
                hint="Panel output multiplier. Operators must estimate this from observations."
                onCommit={(v) => void commit({ generation_factor: v })}
              />
              <Slider
                label="Unexplained extra load"
                value={conditions.extra_load_w}
                min={0}
                max={300}
                step={5}
                unit="W"
                hint="An uncontrolled fault load. It cannot be shed by the load-shedding policy."
                onCommit={(v) => void commit({ extra_load_w: v })}
              />
              <Toggle
                label="Heater stuck on"
                checked={conditions.heater_stuck_on}
                hint="The actuator ignores its command. Observed state will differ from commanded."
                onChange={(v) => void commit({ heater_stuck_on: v })}
              />
              <Toggle
                label="Loss of contact"
                checked={conditions.contact_loss}
                hint="The vehicle keeps running; nothing reaches the ground. Consoles go stale, not disconnected."
                onChange={(v) => void commit({ contact_loss: v })}
              />
            </div>
          </Panel>

          <Panel title="Commanded state" subtitle="Known to operators as context">
            <div className="space-y-4">
              <Slider
                label="Payload duty"
                value={conditions.payload_duty_percent}
                min={0}
                max={100}
                step={50}
                unit="%"
                hint="A legitimate command. Operators can see it, which is what makes a mismatch detectable."
                onCommit={(v) => void commit({ payload_duty_percent: v })}
              />
              <Toggle
                label="Heater thermostat enabled"
                checked={conditions.heater_auto}
                hint="Commanded thermostat operation. Disabling it commands the heater off."
                onChange={(v) => void commit({ heater_auto: v })}
              />
            </div>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Observed telemetry"
            subtitle="Exactly what the operator consoles receive"
            className="xl:col-span-2"
          >
            <div className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <ChannelReadout channelId="battery_soc" frame={frame} meta={meta} digits={1} compact />
              <ChannelReadout channelId="load_power" frame={frame} meta={meta} digits={1} compact />
              <ChannelReadout channelId="heater_power" frame={frame} meta={meta} digits={1} compact />
              <ChannelReadout
                channelId="battery_temperature"
                frame={frame}
                meta={meta}
                digits={1}
                compact
              />
            </div>
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "battery_soc", axis: "left" },
                { channelId: "load_power", axis: "right" },
              ]}
              height={200}
            />
          </Panel>

          <Panel
            title="Presenter truth"
            subtitle="Exact state, no sensor noise"
            actions={<StatusBadge tone="warning">Hidden</StatusBadge>}
          >
            {truth ? (
              <div className="space-y-3 text-xs">
                <div className="flex justify-between">
                  <span className="text-ink-faint">Stored energy</span>
                  <span className="tnum text-ink">{truth.energy_wh.toFixed(2)} Wh</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-faint">Heater actually on</span>
                  <span className="tnum text-ink">{truth.heater_on ? "YES" : "NO"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-faint">Gas mass</span>
                  <span className="tnum text-ink">{truth.gas_mass_kg.toFixed(4)} kg</span>
                </div>
                <div>
                  <p className="mb-1 text-ink-faint">Node temperatures (true)</p>
                  <ul className="space-y-0.5">
                    {Object.entries(truth.temperatures_c).map(([node, value]) => (
                      <li key={node} className="flex justify-between">
                        <span className="tnum text-ink-dim">{node}</span>
                        <span className="tnum text-ink">{value.toFixed(2)} {"°C"}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <p className="text-[10px] text-ink-faint">
                  Compare against the observed values on the left: the difference is seeded
                  sensor noise, and it is the only thing operators ever see.
                </p>
              </div>
            ) : (
              <p className="text-xs text-ink-faint">No run is active.</p>
            )}
          </Panel>
        </div>
      </div>
    </AppShell>
  );
}
