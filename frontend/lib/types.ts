// Types mirroring the backend contracts in backend/app/contracts.
// Kept hand-written and small: the API is the authority, this file only describes it.

export type Role =
  | "SIMULATOR"
  | "MASTER"
  | "POWER"
  | "THERMAL_PROPULSION"
  | "FLIGHT_SYSTEMS";

export type QualityState = "VALID" | "MISSING" | "STALE" | "INVALID" | "SUSPECT";

export type SourceKind = "LIVE" | "SYNTHETIC" | "REPLAY";

export type RunStatus = "PAUSED" | "RUNNING" | "STOPPED";

export type Severity = "INFO" | "WARNING" | "CRITICAL" | "UNKNOWN";

export type PredictionCoverage = "MODELLED" | "PROFILE" | "DERIVED" | "RULE_ONLY";

export interface SessionUser {
  username: string;
  role: Role;
  home_route: string;
  visible_groups: string[];
  can_execute_commands: boolean;
  can_control_simulator: boolean;
}

export interface ChannelInfo {
  id: string;
  label: string;
  unit: string;
  groups: string[];
  type: "float" | "boolean" | "count";
  provenance: "SENSOR" | "DERIVED" | "GROUND";
  description: string;
  operating_min: number | null;
  operating_max: number | null;
  display_min: number | null;
  display_max: number | null;
  prediction: PredictionCoverage;
  rule_refs: string[];
}

export interface ChannelsResponse {
  config_version: string;
  freshness: { warning_after_s: number; critical_after_s: number };
  channels: ChannelInfo[];
}

export interface RuleInfo {
  id: string;
  family: string;
  subsystem: string;
  severity_default: Severity;
  implemented: boolean;
  planned_phase: number | null;
  description: string;
}

export interface ActionInfo {
  id: string;
  label: string;
  subsystem: string;
  enabled: boolean;
  planned_phase: number | null;
  operation: string;
  prerequisites: string[];
  trade_off: string;
}

export interface CoverageResponse {
  config_version: string;
  model_version: string;
  vehicle_name: string;
  rules: RuleInfo[];
  actions: ActionInfo[];
  executable_by_this_role: boolean;
}

export const ROLE_LABEL: Record<Role, string> = {
  SIMULATOR: "Simulator console",
  MASTER: "Master console",
  POWER: "Power console",
  THERMAL_PROPULSION: "Thermal / propulsion console",
  FLIGHT_SYSTEMS: "Flight systems console",
};

// Units are rendered from the channel dictionary, never hard-coded per component.
export function formatUnit(unit: string): string {
  if (unit === "degC") return "°C";
  if (unit === "bar_absolute") return "bar a";
  if (unit === "boolean") return "";
  return unit;
}
