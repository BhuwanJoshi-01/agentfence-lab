// SPDX-License-Identifier: Apache-2.0
import { useContext, useEffect, useState } from "react";
import { BOUNDARIES, getJSON, type RunSummary, type RunsPage } from "../api";
import { Outcome, PageTitle, Pending, TableScroll } from "../components/ui";
import { ago, label, shortId, when } from "../format";
import { href } from "../router";
import { TokenContext } from "../token";
import { useApi } from "../useApi";

export function Overview() {
  const first = useApi<RunsPage>("/runs?limit=50");
  const token = useContext(TokenContext);
  const [more, setMore] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  useEffect(() => {
    if (first.state === "ready") {
      setMore([]);
      setCursor(first.data.next_cursor);
    }
  }, [first]);

  async function loadMore() {
    if (!cursor || !token) return;
    setLoadingMore(true);
    try {
      const page = await getJSON<RunsPage>(`/runs?limit=50&cursor=${encodeURIComponent(cursor)}`, token);
      setMore((rows) => [...rows, ...page.runs]);
      setCursor(page.next_cursor);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <>
      <PageTitle title="Runs">Runs</PageTitle>
      <Pending result={first}>
        {(data) => {
          const runs = [...data.runs, ...more];
          if (runs.length === 0) {
            return (
              <p>
                No runs yet. Start one with <code>agentfence test --agent mock --agent-profile safe</code>.
              </p>
            );
          }
          const totals = Object.fromEntries(
            BOUNDARIES.map((b) => [b, runs.reduce((sum, run) => sum + (run.boundary[b] ?? 0), 0)]),
          );
          const attempts = runs.reduce((sum, run) => sum + run.attempts, 0);
          return (
            <>
              <section aria-labelledby="totals">
                <h2 id="totals">Attempts by boundary outcome</h2>
                <ul className="counters">
                  {BOUNDARIES.map((b) => (
                    <li key={b}>
                      <span className="count">{totals[b]}</span> <Outcome value={b} />
                    </li>
                  ))}
                </ul>
                <p className="muted">
                  {attempts} attempt(s) across {runs.length} run(s) shown. These are counts in a small
                  synthetic scenario scope, not a security score. A PASS means no violation was observed
                  under that test, nothing more.
                </p>
              </section>
              <section aria-labelledby="recent">
                <h2 id="recent">Recent runs</h2>
                <TableScroll label="Recent runs">
                  <table>
                    <caption className="sr-only">Saved runs, newest first</caption>
                    <thead>
                      <tr>
                        <th scope="col">Run</th>
                        <th scope="col">Started</th>
                        <th scope="col">Agent</th>
                        <th scope="col">Attempts</th>
                        {BOUNDARIES.map((b) => (
                          <th scope="col" key={b}>
                            {label(b)}
                          </th>
                        ))}
                        <th scope="col">Isolation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((run) => (
                        <tr key={run.run_id}>
                          <th scope="row">
                            <a href={href.run(run.run_id)} aria-label={`Run ${run.run_id}`}>
                              <code>{shortId(run.run_id)}</code>
                            </a>
                          </th>
                          <td>
                            <time dateTime={run.started_at} title={when(run.started_at)}>
                              {ago(run.started_at)}
                            </time>
                            <span className="sub">{when(run.started_at)}</span>
                          </td>
                          <td>mock · {run.agent_profile}</td>
                          <td className="num">{run.attempts}</td>
                          {BOUNDARIES.map((b) => (
                            <td className="num" key={b}>
                              {run.boundary[b] ?? 0}
                            </td>
                          ))}
                          <td>{run.isolation_tier}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableScroll>
                {cursor && (
                  <button type="button" onClick={loadMore} disabled={loadingMore}>
                    {loadingMore ? "Loading…" : "Show older runs"}
                  </button>
                )}
              </section>
            </>
          );
        }}
      </Pending>
    </>
  );
}
