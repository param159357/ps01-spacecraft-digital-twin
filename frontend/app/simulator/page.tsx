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

// Mirrors the bound the backend enforces on `computer_resets`, so the button can say
// why it has stopped instead of letting the request fail validation.
const MAX_COMPUTER_RESETS = 20;

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
  // The reset injection keeps its own pending flag rather than sharing `busy`, so a
  // click cannot be repeated while the request is in flight and the rest of the panel
  // stays usable.
  const [injectingReset, setInjectingReset] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Starting conditions for the next reset. They are applied when a run is created,
  // never to a run already in progress.
  const [advanced, setAdvanced] = useState({
    initial_soc_percent: 70,
    initial_sim_time_s: 0,
    initial_payload_temperature_c: 25,
    mode: "NOMINAL",
  });

  // Local mirror of the injected conditions so the controls stay responsive.
  const [conditions, setLocalConditions] = useState({
    generation_factor: 1,
    extra_load_w: 0,
    payload_duty_percent: 100,
    heater_stuck_on: false,
    heater_auto: true,
    valve_open: false,
    contact_loss: false,
    branch_stuck_on: false,
    isolator_failed: false,
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
          branch_stuck_on: Boolean(hidden.branch_stuck_on),
          isolator_failed: Boolean(hidden.isolator_failed),
          payload_duty_percent: Number(known.payload_duty_percent ?? 100),
          heater_auto: Boolean(known.heater_auto ?? true),
          valve_open: Boolean(known.valve_open_commanded),
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

  /** Raise the commanded reset total by exactly one.

   * The vehicle records one timestamped event per increment, so this produces one real
   * reset through the ordinary condition pipeline — the same path the presets use. The
   * incident is left entirely to the rule engine: two distinct events inside the
   * configured window open `RESET_RECURRENCE` on their own, and nothing here writes an
   * incident or touches the counter directly.
   *
   * The target is read back from the authoritative simulator state at click time rather
   * than from this component's 2-second mirror, because incrementing a stale value would
   * either skip an event or do nothing at all.
   */
  async function injectComputerReset() {
    setInjectingReset(true);
    setError(null);
    setNotice(null);
    try {
      const state = await fetchSimulatorState();
      const current = Number(state.truth?.hidden_conditions.computer_resets ?? 0);
      if (current >= MAX_COMPUTER_RESETS) {
        setError(
          `The simulator accepts at most ${MAX_COMPUTER_RESETS} resets in one run. Reset the run to inject more.`,
        );
        return;
      }
      const before = Number(state.truth?.resets_applied ?? 0);
      await setConditions({ computer_resets: current + 1 });
      setNotice(
        `Reset ${current + 1} commanded. The vehicle records the event on its next tick` +
          `${running ? "" : " — the run is not advancing, so resume it to see the event"}.` +
          ` ${before} recorded so far.`,
      );
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not inject a computer reset.");
    } finally {
      setInjectingReset(false);
    }
  }

  /** Reset into the starting conditions chosen above, then hold the run paused.

   * Every value here is a validated parameter the backend already accepts on run
   * creation. The operating mode is not a creation parameter — it is a known command —
   * so it is commanded immediately after the new run exists, which is what makes the run
   * begin in that mode.
   */
  async function resetWithAdvanced() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await runControl("RESET", {
        initial_soc_percent: advanced.initial_soc_percent,
        initial_sim_time_s: advanced.initial_sim_time_s,
        initial_payload_temperature_c: advanced.initial_payload_temperature_c,
      });
      if (advanced.mode !== "NOMINAL") await setConditions({ mode: advanced.mode });
      setNotice(
        `New run created and held paused at ${advanced.initial_soc_percent}% SOC, ` +
          `clock ${advanced.initial_sim_time_s} s, payload ` +
          `${advanced.initial_payload_temperature_c} °C, mode ${advanced.mode}. Press Resume to run it.`,
      );
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset with those settings.");
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

  async function loadD02() {
    setBusy(true);
    setError(null);
    try {
      await runControl("RESET", { initial_soc_percent: 24 });
      await setConditions({ extra_load_w: 200, payload_duty_percent: 100 });
      await runControl("START");
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load D02.");
    } finally {
      setBusy(false);
    }
  }

  async function loadD04(isolatorWorks: boolean) {
    setBusy(true);
    setError(null);
    try {
      // Labelled starting conditions, chosen so D04 produces BOTH kinds of evidence
      // quickly: the payload node begins close to its configured limit, and the run
      // begins shortly before a scheduled eclipse with a modest reserve, so the
      // 30-minute projection crosses the reserve boundary. Section 7.1 permits starting
      // near a boundary provided it is visibly labelled; no timestamp is falsified.
      await runControl("RESET", {
        initial_soc_percent: 28,
        initial_sim_time_s: 3000,
        initial_payload_temperature_c: 53,
      });
      await setConditions({ branch_stuck_on: true, isolator_failed: !isolatorWorks });
      await runControl("START");
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load D04.");
    } finally {
      setBusy(false);
    }
  }

  async function loadD03() {
    setBusy(true);
    setError(null);
    try {
      // A low reserve just before a scheduled eclipse, with a heavy unexplained load.
      // Left alone the warning escalates to critical within a couple of minutes, and if
      // it is still ignored the declared load shedding eventually drops the payload —
      // and that operating time is gone for good. Labelled starting condition, not a
      // falsified timestamp.
      await runControl("RESET", { initial_soc_percent: 11, initial_sim_time_s: 3500 });
      await setConditions({ extra_load_w: 300, payload_duty_percent: 100 });
      await runControl("START");
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load D03.");
    } finally {
      setBusy(false);
    }
  }

  async function loadD01() {
    setBusy(true);
    setError(null);
    try {
      await runControl("RESET", { initial_soc_percent: 70, initial_sim_time_s: 3590 });
      await runControl("START");
      await refreshTruth();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load D01.");
    } finally {
      setBusy(false);
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

        {notice ? (
          <p
            role="status"
            className="rounded border border-accent/40 bg-accent/10 px-3 py-2 text-xs text-accent"
          >
            {notice}
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
              <ControlButton
                label="Load D01 eclipse transition"
                onClick={() => void loadD01()}
                busy={busy}
              />
              <ControlButton
                label="Load D02 recovery demo"
                onClick={() => void loadD02()}
                busy={busy}
                tone="primary"
              />
              <ControlButton
                label="Load D03 delayed response"
                onClick={() => void loadD03()}
                busy={busy}
              />
              <ControlButton
                label="Load D04 stuck branch"
                onClick={() => void loadD04(true)}
                busy={busy}
                tone="primary"
              />
              <ControlButton
                label="Load D04 with a failed isolator"
                onClick={() => void loadD04(false)}
                busy={busy}
                tone="danger"
              />
            </div>
            <p className="mt-2 text-[10px] text-ink-faint">
              Each scenario resets to a visibly labelled starting condition — a stated
              state of charge, spacecraft clock or payload temperature. No timestamp is
              falsified to force a dramatic result.
            </p>
            <dl className="mt-4 space-y-1 text-xs">
              <div className="flex justify-between">
                <dt className="text-ink-faint">Run ID</dt>
                <dd className="tnum text-ink">{run?.run_id?.slice(0, 8) ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-faint">Frame</dt>
                {/* The newest frame actually received, not the run metadata's count,
                    which is captured once with the snapshot and then never moves. No
                    valid frame reads as an em dash: frame 0 is a real sequence number
                    and must not stand in for "nothing yet". */}
                <dd className="tnum text-ink">{frame ? frame.frame_seq : "—"}</dd>
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
            <div className="mt-4 border-t border-edge pt-3">
              <div className="flex flex-wrap items-center gap-2">
                <ControlButton
                  label={injectingReset ? "Injecting…" : "Inject computer reset"}
                  onClick={() => void injectComputerReset()}
                  busy={injectingReset || !run?.run_id}
                />
                <span className="tnum text-[10px] text-ink-faint">
                  {truth ? `${truth.resets_applied} recorded` : "no run"}
                  {truth?.reset_events_s?.length
                    ? ` at ${truth.reset_events_s.map((t) => t.toFixed(0)).join(", ")} s`
                    : ""}
                </span>
              </div>
              <p className="mt-2 text-[10px] text-ink-faint">
                One click commands one more reset, and the vehicle records one timestamped
                event for it on its next tick. Two distinct events inside the configured
                window are what open <span className="text-ink-dim">RESET_RECURRENCE</span>
                {" "}on the flight console — the rule does that from observed telemetry, so
                click twice and wait rather than expecting an incident from the first one.
              </p>
            </div>

            <details className="mt-3 border-t border-edge pt-3">
              <summary className="cursor-pointer text-xs text-ink-dim hover:text-accent">
                Advanced reset settings
              </summary>
              <div className="mt-3 space-y-4">
                <p className="text-[10px] text-ink-faint">
                  Starting conditions for a <em>new</em> run. They are applied when the run
                  is created and cannot change a run already in progress, so the button
                  below resets and holds the new run paused. Each range is the bound the
                  backend already validates.
                </p>
                <Slider
                  label="Starting battery SOC"
                  value={advanced.initial_soc_percent}
                  min={5}
                  max={95}
                  step={1}
                  unit="%"
                  onCommit={(v) => setAdvanced((prev) => ({ ...prev, initial_soc_percent: v }))}
                />
                <Slider
                  label="Starting spacecraft clock"
                  value={advanced.initial_sim_time_s}
                  min={0}
                  max={5399}
                  step={10}
                  unit="s"
                  hint="Where in the configured 60 min sunlight / 30 min eclipse cycle the run begins."
                  onCommit={(v) => setAdvanced((prev) => ({ ...prev, initial_sim_time_s: v }))}
                />
                <Slider
                  label="Starting payload temperature"
                  value={advanced.initial_payload_temperature_c}
                  min={-10}
                  max={54}
                  step={1}
                  unit="°C"
                  onCommit={(v) =>
                    setAdvanced((prev) => ({ ...prev, initial_payload_temperature_c: v }))
                  }
                />
                <div className="space-y-1">
                  <label className="block text-xs text-ink-dim" htmlFor="advanced-mode">
                    Operating mode
                  </label>
                  <select
                    id="advanced-mode"
                    value={advanced.mode}
                    onChange={(e) => setAdvanced((prev) => ({ ...prev, mode: e.target.value }))}
                    className="w-full rounded border border-edge-strong bg-panel-raised px-2 py-1.5 text-xs text-ink"
                  >
                    <option value="NOMINAL">NOMINAL</option>
                    <option value="MANEUVER">MANEUVER</option>
                  </select>
                  <p className="text-[10px] text-ink-faint">
                    MANEUVER widens the configured rate and pointing profiles by the
                    vehicle&apos;s maneuver scale. It is a known command, not a fault, and it
                    remains changeable during a run.
                  </p>
                </div>
                <ControlButton
                  label="Reset with these settings"
                  onClick={() => void resetWithAdvanced()}
                  busy={busy}
                  tone="danger"
                />
              </div>
            </details>

            <p className="mt-3 text-[10px] text-ink-faint">
              A reset starts a new run ID and clears chart buffers. Previous runs stay in the
              database.
            </p>
            <p className="mt-2 text-[10px] text-warning">
              D01 starts 10 simulated seconds before the configured eclipse to verify the
              scheduled transition without waiting an hour. 
              D02 is a labelled fictional starting condition: 24% observed SOC, 200 W extra
              load, payload at 100%. It is not an orbital calculation.
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
                label="Optional branch stuck on"
                checked={conditions.branch_stuck_on}
                hint="Branch OPTIONAL_A draws its rated power regardless of command. Produces both power and thermal evidence."
                onChange={(v) => void commit({ branch_stuck_on: v })}
              />
              <Toggle
                label="Isolator does not respond"
                checked={conditions.isolator_failed}
                hint="An approved isolation command still applies, but nothing changes — the operator sees a failed recovery."
                onChange={(v) => void commit({ isolator_failed: v })}
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
                label="Cold-gas valve open"
                checked={conditions.valve_open}
                onChange={(v) => void commit({ valve_open: v })}
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
