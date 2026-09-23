"use client";

// Compact 2D spacecraft diagram — CLAUDE.md section 5.
//
// Deliberately schematic: blocks and power flow, no 3D, no animation, no decorative
// starfield. Each block shows one key value in its real unit, and status is carried by
// text as well as colour so it never depends on colour perception alone.
//
// A block is only "nominal" when its evidence is actually valid. A channel whose quality
// is not VALID renders as unavailable rather than green.

import type { Frame } from "@/lib/live";
import { formatUnit, type ChannelInfo } from "@/lib/types";

type BlockStatus = "nominal" | "warning" | "critical" | "unavailable";

const FILL: Record<BlockStatus, string> = {
  nominal: "var(--color-nominal)",
  warning: "var(--color-warning)",
  critical: "var(--color-critical)",
  unavailable: "var(--color-unavailable)",
};

interface BlockSpec {
  id: string;
  label: string;
  channelId: string;
  digits: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

// Layout is fixed so values never jump position between frames.
const BLOCKS: BlockSpec[] = [
  { id: "array", label: "Solar array", channelId: "solar_current", digits: 2, x: 16, y: 96, w: 104, h: 56 },
  { id: "bus", label: "Regulated bus", channelId: "bus_voltage", digits: 2, x: 168, y: 96, w: 104, h: 56 },
  { id: "battery", label: "Battery", channelId: "battery_soc", digits: 1, x: 168, y: 18, w: 104, h: 56 },
  { id: "payload", label: "Payload", channelId: "payload_temperature", digits: 1, x: 320, y: 18, w: 104, h: 56 },
  { id: "computer", label: "Computer", channelId: "computer_temperature", digits: 1, x: 320, y: 96, w: 104, h: 56 },
  { id: "thermal", label: "Heater", channelId: "heater_power", digits: 1, x: 320, y: 174, w: 104, h: 56 },
  { id: "propulsion", label: "Cold gas", channelId: "tank_pressure", digits: 1, x: 168, y: 174, w: 104, h: 56 },
  { id: "flight", label: "Wheel", channelId: "wheel_speed", digits: 0, x: 16, y: 174, w: 104, h: 56 },
];

// Power flows from the array into the bus, and from the bus outward to the loads.
const LINKS: [string, string][] = [
  ["array", "bus"],
  ["bus", "battery"],
  ["bus", "payload"],
  ["bus", "computer"],
  ["bus", "thermal"],
  ["bus", "propulsion"],
  ["bus", "flight"],
];

function statusFor(info: ChannelInfo | undefined, frame: Frame | null, channelId: string): BlockStatus {
  if (!frame) return "unavailable";
  const quality = frame.quality[channelId];
  const value = frame.values[channelId];
  if (quality?.state !== "VALID" || typeof value !== "number") return "unavailable";
  if (!info) return "nominal";

  const { operating_min: lo, operating_max: hi } = info;
  if ((lo !== null && value < lo) || (hi !== null && value > hi)) return "critical";

  // Margin is a fraction of the operating band width, not of the limit's magnitude, so
  // a channel with a narrow high band (a 24-29 V battery) does not sit permanently amber.
  const margin =
    lo !== null && hi !== null ? (hi - lo) * 0.05 : 0.05 * Math.abs(hi ?? lo ?? 0);
  if (margin > 0) {
    if (hi !== null && value > hi - margin) return "warning";
    if (lo !== null && value < lo + margin) return "warning";
  }
  return "nominal";
}

function centre(block: BlockSpec): [number, number] {
  return [block.x + block.w / 2, block.y + block.h / 2];
}

export function SubsystemDiagram({
  frame,
  meta,
}: {
  frame: Frame | null;
  meta: Record<string, ChannelInfo>;
}) {
  const byId = Object.fromEntries(BLOCKS.map((b) => [b.id, b]));

  return (
    <div>
      {/* Capped width keeps this compact on a 1920-wide master screen instead of
          letting a fixed aspect ratio swallow half the viewport. */}
      <svg
        viewBox="0 0 440 248"
        className="mx-auto h-auto w-full max-w-[560px]"
        role="img"
        aria-label="Schematic of spacecraft subsystems with one key value each"
      >
        <title>Spacecraft subsystem schematic</title>

        {LINKS.map(([from, to]) => {
          const [x1, y1] = centre(byId[from]);
          const [x2, y2] = centre(byId[to]);
          return (
            <line
              key={`${from}-${to}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke="var(--color-edge-strong)"
              strokeWidth={1.25}
            />
          );
        })}

        {BLOCKS.map((block) => {
          const info = meta[block.channelId];
          const status = statusFor(info, frame, block.channelId);
          const value = frame?.values[block.channelId];
          const usable = status !== "unavailable" && typeof value === "number";

          return (
            <g key={block.id}>
              <rect
                x={block.x}
                y={block.y}
                width={block.w}
                height={block.h}
                rx={4}
                fill="var(--color-panel-raised)"
                stroke={FILL[status]}
                strokeWidth={1.5}
              />
              {/* Status marker carries a shape as well as colour. */}
              <circle cx={block.x + 10} cy={block.y + 11} r={3.5} fill={FILL[status]} />
              <text
                x={block.x + 20}
                y={block.y + 15}
                fill="var(--color-ink-dim)"
                fontSize="8.5"
                letterSpacing="0.4"
              >
                {block.label.toUpperCase()}
              </text>
              <text
                x={block.x + 10}
                y={block.y + 36}
                fill={usable ? "var(--color-ink)" : "var(--color-unavailable)"}
                fontSize="15"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {usable ? (value as number).toFixed(block.digits) : "—"}
              </text>
              {usable && info ? (
                <text
                  x={block.x + 10}
                  y={block.y + 49}
                  fill="var(--color-ink-faint)"
                  fontSize="8"
                >
                  {formatUnit(info.unit)} · {status}
                </text>
              ) : (
                <text
                  x={block.x + 10}
                  y={block.y + 49}
                  fill="var(--color-unavailable)"
                  fontSize="8"
                >
                  no valid reading
                </text>
              )}
            </g>
          );
        })}
      </svg>

      <p className="mt-2 text-[10px] text-ink-faint">
        Schematic only — block positions are a layout convenience, not a physical
        arrangement. Lines show the power boundary from array to bus to loads. Each block
        reports one channel in its own unit; a block greys out when that reading is not
        valid rather than showing a stale number as healthy.
      </p>
    </div>
  );
}
