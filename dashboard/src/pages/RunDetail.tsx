// SPDX-License-Identifier: Apache-2.0
import { useMemo, useState } from "react";
import { type Attempt, BOUNDARIES, type Boundary, type RunReport } from "../api";
import { Outcome, PageTitle, Pending, TableScroll } from "../components/ui";
import { label, ratio, shortId, when } from "../format";
import { href } from "../router";
import { useApi } from "../useApi";

function exposure(a: Attempt): string {
  if (a.variant === "benign") return "not applicable";
  if (a.boundary === "NOT_TRIGGERED") return "no";
  if (a.boundary === "INCONCLUSIVE") return "unknown";
  return "yes";
}

function explanation(a: Attempt): string {
  if (a.boundary === "INCONCLUSIVE") {
    return a.coverage_gaps.length
      ? `Missing evidence: ${a.coverage_gaps.join(", ")}.`
      : "Required evidence was missing.";
  }
  if (a.boundary === "NOT_TRIGGERED" && a.variant === "adversarial") {
    return "The agent never reached the untrusted content, so resistance was not tested.";
  }
  return a.limitations.join(" ");
}

export function RunDetail({ id }: { id: string }) {
  const run = useApi<RunReport>(`/runs/${encodeURIComponent(id)}`);
  const attempts = useApi<{ attempts: Attempt[] }>(`/runs/${encodeURIComponent(id)}/attempts`);
  const [outcome, setOutcome] = useState<Boundary | "ALL">("ALL");
  const [query, setQuery] = useState("");

  return (
    <>
      <PageTitle title={`Run ${shortId(id)}`}>
        Run <code>{shortId(id)}</code>
      </PageTitle>
      <Pending result={run}>
        {(r) => (
          <>
            <p className="lede">
              {r.agent.name} · {r.agent.profile} · {r.isolation.tier} · {when(r.started_at)} ·{" "}
              {r.summary.attempts} attempts · seed {r.seed} · repeat {r.repeat}
            </p>
            <div className="notice">
              <strong>Isolation:</strong> {r.isolation.claim}
            </div>
            <section aria-labelledby="metrics">
              <h2 id="metrics">Summary</h2>
              <ul className="counters">
                {BOUNDARIES.map((b) => (
                  <li key={b}>
                    <span className="count">{r.summary.boundary[b]}</span> <Outcome value={b} />
                  </li>
                ))}
              </ul>
              <dl className="facts">
                <dt>Exposure coverage (adversarial)</dt>
                <dd>{ratio(r.summary.exposure_coverage)}</dd>
                <dt>Violations among exposed</dt>
                <dd>{ratio(r.summary.violation_rate)}</dd>
                <dt>Legitimate task completed</dt>
                <dd>{ratio(r.summary.utility_rate)}</dd>
                <dt>Benign controls left incomplete</dt>
                <dd>{ratio(r.summary.benign_incomplete_rate)}</dd>
                <dt>Inconclusive</dt>
                <dd>{ratio(r.summary.inconclusive_rate)}</dd>
              </dl>
              <p className="muted">{r.disclaimer}</p>
              <p>
                <a href={href.compare(r.run_id)}>Compare this run with another</a>
              </p>
            </section>
          </>
        )}
      </Pending>
      <Pending result={attempts}>
        {({ attempts: rows }) => (
          <Matrix rows={rows} outcome={outcome} query={query} onOutcome={setOutcome} onQuery={setQuery} />
        )}
      </Pending>
    </>
  );
}

function Matrix(props: {
  rows: Attempt[];
  outcome: Boundary | "ALL";
  query: string;
  onOutcome: (value: Boundary | "ALL") => void;
  onQuery: (value: string) => void;
}) {
  const { rows, outcome, query } = props;
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(
      (a) =>
        (outcome === "ALL" || a.boundary === outcome) &&
        (needle === "" ||
          a.scenario_id.toLowerCase().includes(needle) ||
          a.finding_code.toLowerCase().includes(needle)),
    );
  }, [rows, outcome, query]);
  return (
    <section aria-labelledby="matrix">
      <h2 id="matrix">Scenario matrix</h2>
      <form className="filters" onSubmit={(e) => e.preventDefault()}>
        <label>
          Boundary outcome{" "}
          <select value={outcome} onChange={(e) => props.onOutcome(e.target.value as Boundary | "ALL")}>
            <option value="ALL">All</option>
            {BOUNDARIES.map((b) => (
              <option key={b} value={b}>
                {label(b)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Scenario or finding code{" "}
          <input type="search" value={query} onChange={(e) => props.onQuery(e.target.value)} />
        </label>
      </form>
      <p className="muted" aria-live="polite">
        Showing {shown.length} of {rows.length} attempts.
      </p>
      <TableScroll label="Scenario matrix">
        <table>
          <caption className="sr-only">Attempts in this run</caption>
          <thead>
            <tr>
              <th scope="col">Scenario</th>
              <th scope="col">Variant</th>
              <th scope="col">Untrusted content reached?</th>
              <th scope="col">Boundary</th>
              <th scope="col">Utility</th>
              <th scope="col">Infrastructure</th>
              <th scope="col">Finding</th>
              <th scope="col">Evidence</th>
              <th scope="col">Notes</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((a) => (
              <tr key={a.attempt_id} className={`row-${a.boundary.toLowerCase()}`}>
                <th scope="row">
                  <a href={href.attempt(a.attempt_id)}>{a.scenario_id}</a>
                  {a.repetition > 0 && <span className="sub">repetition {a.repetition}</span>}
                </th>
                <td>{a.variant}</td>
                <td>{exposure(a)}</td>
                <td>
                  <Outcome value={a.boundary} />
                </td>
                <td>{label(a.utility)}</td>
                <td>{a.infrastructure}</td>
                <td>
                  <code>{a.finding_code}</code>
                </td>
                <td className="num">{a.evidence_ids.length}</td>
                <td>{explanation(a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </section>
  );
}
