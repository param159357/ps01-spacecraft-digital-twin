"use client";

// Small shared presentation pieces. Status is always colour *and* text, so the
// display stays readable without relying on colour perception (section 5).

import type { ReactNode } from "react";

export type StatusTone = "nominal" | "warning" | "critical" | "unavailable" | "accent";

const TONE_CLASS: Record<StatusTone, string> = {
  nominal: "border-nominal/40 bg-nominal/10 text-nominal",
  warning: "border-warning/40 bg-warning/10 text-warning",
  critical: "border-critical/40 bg-critical/10 text-critical",
  unavailable: "border-unavailable/40 bg-unavailable/10 text-unavailable",
  accent: "border-accent/40 bg-accent/10 text-accent",
};

export function StatusBadge({
  tone,
  children,
  title,
}: {
  tone: StatusTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium tracking-wide uppercase ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`flex flex-col rounded-md border border-edge bg-panel ${className}`}
      aria-label={title}
    >
      <header className="flex items-start justify-between gap-3 border-b border-edge px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold tracking-wide text-ink uppercase">
            {title}
          </h2>
          {subtitle ? <p className="mt-0.5 text-xs text-ink-faint">{subtitle}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      <div className="flex-1 px-4 py-3">{children}</div>
    </section>
  );
}

/**
 * Honest placeholder for a capability that is contracted but not yet built. It names
 * the phase instead of showing an empty chart that implies working coverage.
 */
export function PendingPanel({
  title,
  phase,
  what,
  children,
}: {
  title: string;
  phase: number;
  what: string;
  children?: ReactNode;
}) {
  return (
    <Panel
      title={title}
      actions={<StatusBadge tone="unavailable">Phase {phase}</StatusBadge>}
    >
      <p className="text-sm text-ink-dim">{what}</p>
      <p className="mt-2 text-xs text-ink-faint">
        No data is shown here because none exists yet. This panel stays empty rather than
        displaying placeholder values.
      </p>
      {children}
    </Panel>
  );
}

export function ValueReadout({
  label,
  value,
  unit,
  tone = "unavailable",
  note,
}: {
  label: string;
  value: string | number | null;
  unit?: string;
  tone?: StatusTone;
  note?: string;
}) {
  const unavailable = value === null || value === undefined || value === "";
  const toneText: Record<StatusTone, string> = {
    nominal: "text-nominal",
    warning: "text-warning",
    critical: "text-critical",
    unavailable: "text-unavailable",
    accent: "text-accent",
  };
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs tracking-wide text-ink-faint uppercase">{label}</span>
      <span className={`tnum text-xl ${unavailable ? "text-unavailable" : toneText[tone]}`}>
        {/* Unavailable is shown as an em dash, never as zero. */}
        {unavailable ? "—" : value}
        {!unavailable && unit ? (
          <span className="ml-1 text-sm text-ink-faint">{unit}</span>
        ) : null}
      </span>
      {note ? <span className="text-xs text-ink-faint">{note}</span> : null}
    </div>
  );
}
