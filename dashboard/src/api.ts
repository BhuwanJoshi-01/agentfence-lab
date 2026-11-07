// SPDX-License-Identifier: Apache-2.0
// Typed client for the read-only local API (docs/reference/local-api.md).

export type Boundary = "PASS" | "FAIL" | "INCONCLUSIVE" | "NOT_TRIGGERED";
export const BOUNDARIES: readonly Boundary[] = ["FAIL", "PASS", "INCONCLUSIVE", "NOT_TRIGGERED"];

export interface RunSummary {
  run_id: string;
  started_at: string;
  status: string;
  scenario_profile: string;
  agent_profile: string;
  attempts: number;
  boundary: Partial<Record<Boundary, number>>;
  seed: number;
  repeat: number;
  tool_version: string;
  isolation_tier: string;
}

export interface RunsPage {
  runs: RunSummary[];
  next_cursor: string | null;
}

export interface Ratio {
  numerator: number;
  denominator: number;
  value: number | null;
  wilson_95: [number, number] | null;
}

export interface RunReport {
  run_id: string;
  started_at: string;
  ended_at: string;
  tool_version: string;
  scenario_profile: string;
  seed: number;
  repeat: number;
  disclaimer: string;
  agent: { name: string; version: string; profile: string };
  isolation: { tier: string; claim: string };
  summary: {
    attempts: number;
    adversarial: number;
    benign: number;
    boundary: Record<Boundary, number>;
    exposure_coverage: Ratio;
    violation_rate: Ratio;
    utility_rate: Ratio;
    benign_incomplete_rate: Ratio;
    inconclusive_rate: Ratio;
    no_exposure_rate: Ratio;
  };
}

export interface Attempt {
  attempt_id: string;
  run_id?: string;
  scenario_id: string;
  scenario_version: string;
  category: string;
  variant: "adversarial" | "benign";
  repetition: number;
  boundary: Boundary;
  utility: string;
  infrastructure: string;
  finding_code: string;
  confidence: string;
  evaluator_version: string;
  evidence_ids: string[];
  prohibited_attempts: string[];
  completed_effects: string[];
  utility_evidence: string[];
  coverage_gaps: string[];
  limitations: string[];
  fixture_digest: string;
  final_status: string;
  impact_scope: string;
  event_count: number;
}

export interface ObservedEvent {
  event_id: string;
  sequence: number;
  timestamp: string;
  kind: string;
  source_component: string;
  source_trust: string;
  target: string;
  decision: string;
  details: Record<string, string | number | boolean | null>;
  content_hash: string | null;
}

export interface EventsPage {
  attempt_id: string;
  events: ObservedEvent[];
  next_after: number | null;
}

export interface Scenario {
  id: string;
  version: string;
  variant: "adversarial" | "benign";
  category: string;
  title: string;
  risk_tags: string[];
  legitimate_task: string;
  attack_surface: { kind: string; path: string } | null;
  boundary: { kind: string; allow: string[]; deny: string[] };
  observation_channels: string[];
  failure_rules: string[];
  benign_pair: string | null;
  sha256: string;
}

export interface Comparison {
  baseline_run: string;
  candidate_run: string;
  run_level_reasons: string[];
  counts: Record<string, number>;
  pairs: {
    scenario_id: string;
    variant: string;
    repetition: number;
    change: string;
    baseline: string | null;
    candidate: string | null;
    reasons: string[];
  }[];
}

export interface Check {
  id: string;
  status: "PASS" | "FAIL" | "UNKNOWN";
  required: boolean;
  summary: string;
  detail: string;
  remedy: string;
}

export interface Attestation {
  attestation_id: string;
  created_at: string;
  engine: string;
  engine_version: string;
  image: string;
  checks: { id: string; status: "PASS" | "FAIL" | "UNKNOWN"; expected: string; observed: string }[];
}

export interface Environment {
  isolation_state: string;
  checks: Check[];
  blocking_checks: string[];
  attestation: Attestation | null;
  attestation_verified: boolean;
  attestation_passed: boolean;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const STORAGE_KEY = "agentfence.session";
const TOKEN = /^#token=([A-Za-z0-9_-]{32,128})$/;

function sessionStore(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

let memoryToken: string | null = null;

/**
 * Take the session token from the URL fragment (`#token=...`), keep it for this tab only,
 * and remove it from the address bar and history. The fragment is never sent to a server
 * or in a Referer header.
 */
export function captureToken(): string | null {
  const match = TOKEN.exec(window.location.hash);
  const store = sessionStore();
  if (match?.[1]) {
    memoryToken = match[1];
    try {
      store?.setItem(STORAGE_KEY, match[1]);
    } catch {
      // storage disabled: keep the token in memory for this page only
    }
    window.history.replaceState(null, "", `${window.location.pathname}#/`);
  }
  if (memoryToken) return memoryToken;
  try {
    memoryToken = store?.getItem(STORAGE_KEY) ?? null;
  } catch {
    memoryToken = null;
  }
  return memoryToken;
}

export function forgetToken(): void {
  memoryToken = null;
  try {
    sessionStore()?.removeItem(STORAGE_KEY);
  } catch {
    // nothing to forget
  }
}

export async function getJSON<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "error",
      error?.message ?? `request failed (${response.status})`,
    );
  }
  return body as T;
}
