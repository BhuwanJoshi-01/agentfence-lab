// SPDX-License-Identifier: Apache-2.0
import type { Ratio } from "./api";

export function ratio(r: Ratio | undefined): string {
  if (!r || r.denominator === 0) return "not applicable (no cases)";
  const ci = r.wilson_95 ? ` (95% CI ${r.wilson_95[0].toFixed(2)}–${r.wilson_95[1].toFixed(2)})` : "";
  return `${r.numerator}/${r.denominator}${ci}`;
}

export function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

export function ago(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function shortId(id: string): string {
  return id.length > 13 ? `${id.slice(0, 8)}…` : id;
}

export function label(value: string): string {
  return value.replaceAll("_", " ");
}
