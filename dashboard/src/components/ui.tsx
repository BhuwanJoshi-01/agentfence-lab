// SPDX-License-Identifier: Apache-2.0
import { type ReactNode, useEffect, useRef } from "react";
import type { Loaded } from "../useApi";
import { label } from "../format";

/** An outcome as text first; colour only reinforces it (docs/08 section 10). */
export function Outcome({ value }: { value: string }) {
  return <span className={`badge badge-${value.toLowerCase().replaceAll("_", "-")}`}>{label(value)}</span>;
}

const TRUST: Record<string, string> = {
  host_observed: "host observed",
  broker_observed: "broker observed",
  agent_reported: "agent reported",
  derived: "derived",
};

export function TrustTag({ trust }: { trust: string }) {
  return <span className={`trust trust-${trust.replaceAll("_", "-")}`}>{TRUST[trust] ?? label(trust)}</span>;
}

let initialLoad = true;

/**
 * A page's main heading. After in-app navigation it takes focus so screen readers announce
 * the new page; on the first load focus stays at the top, so the first Tab reaches the
 * skip link.
 */
export function PageTitle({ children, title }: { children: ReactNode; title: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = `${title} · AgentFence Lab`;
    if (initialLoad) {
      initialLoad = false;
      return;
    }
    ref.current?.focus();
  }, [title]);
  return (
    <h1 ref={ref} tabIndex={-1}>
      {children}
    </h1>
  );
}

export function Pending<T>({ result, children }: { result: Loaded<T>; children: (data: T) => ReactNode }) {
  if (result.state === "loading") {
    return (
      <p className="muted" role="status">
        Loading…
      </p>
    );
  }
  if (result.state === "error") {
    return (
      <div className="notice notice-error" role="alert">
        <strong>Could not load this.</strong> {result.message}
        {result.status === 401 && " Restart `agentfence ui` and open the new link it prints."}
      </div>
    );
  }
  return <>{children(result.data)}</>;
}

/** Wide tables scroll inside a focusable, labelled region instead of the page. */
export function TableScroll({ label: name, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-scroll" role="region" aria-label={`${name} table`} tabIndex={0}>
      {children}
    </div>
  );
}

export function List({
  items,
  empty = "none",
  prose = false,
}: {
  items: readonly string[];
  empty?: string;
  prose?: boolean;
}) {
  if (items.length === 0) return <span className="muted">{empty}</span>;
  return (
    <ul className="plain">
      {items.map((item) => (
        <li key={item}>{prose ? item : <code>{item}</code>}</li>
      ))}
    </ul>
  );
}
