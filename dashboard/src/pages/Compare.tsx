// SPDX-License-Identifier: Apache-2.0
import type { Comparison, RunsPage } from "../api";
import { Outcome, PageTitle, Pending, TableScroll } from "../components/ui";
import { shortId, when } from "../format";
import { href } from "../router";
import { useApi } from "../useApi";

const CHANGE_NOTE: Record<string, string> = {
  REGRESSED: "boundary got worse",
  IMPROVED: "boundary got better",
  CHANGED: "something other than the boundary changed",
  INCOMPARABLE: "not comparable; no delta is claimed",
};

export function Compare({ left, right }: { left: string; right: string }) {
  const runs = useApi<RunsPage>("/runs?limit=200");
  const ready = left !== "" && right !== "" && left !== right;
  const comparison = useApi<Comparison>(
    ready ? `/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}` : null,
  );
  function pick(side: "left" | "right", value: string) {
    window.location.hash = href.compare(side === "left" ? value : left, side === "right" ? value : right);
  }
  return (
    <>
      <PageTitle title="Compare runs">Compare two runs</PageTitle>
      <Pending result={runs}>
        {({ runs: rows }) => (
          <form className="filters" onSubmit={(e) => e.preventDefault()}>
            {(["left", "right"] as const).map((side) => (
              <label key={side}>
                {side === "left" ? "Baseline" : "Candidate"}{" "}
                <select value={side === "left" ? left : right} onChange={(e) => pick(side, e.target.value)}>
                  <option value="">Choose a run</option>
                  {rows.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {shortId(r.run_id)} · {r.agent_profile} · {when(r.started_at)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </form>
        )}
      </Pending>
      {!ready && <p className="muted">Choose two different runs.</p>}
      {ready && (
        <Pending result={comparison}>
          {(c) => (
            <section aria-labelledby="pairs">
              {c.run_level_reasons.length > 0 && (
                <div className="notice notice-warn" role="note">
                  <strong>These runs are not comparable as a whole:</strong> {c.run_level_reasons.join("; ")}
                </div>
              )}
              <h2 id="pairs">Scenario by scenario</h2>
              <ul className="counters">
                {Object.entries(c.counts).map(([change, count]) => (
                  <li key={change}>
                    <span className="count">{count}</span> <Outcome value={change} />
                  </li>
                ))}
              </ul>
              <TableScroll label="Comparison">
                <table>
                  <caption className="sr-only">Baseline and candidate outcomes per scenario</caption>
                  <thead>
                    <tr>
                      <th scope="col">Scenario</th>
                      <th scope="col">Change</th>
                      <th scope="col">Baseline (boundary/utility)</th>
                      <th scope="col">Candidate (boundary/utility)</th>
                      <th scope="col">Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.pairs.map((p) => (
                      <tr key={`${p.scenario_id}-${p.repetition}`}>
                        <th scope="row">
                          {p.scenario_id}
                          {p.repetition > 0 && <span className="sub">repetition {p.repetition}</span>}
                        </th>
                        <td>
                          <Outcome value={p.change} />
                        </td>
                        <td>{p.baseline ?? "absent"}</td>
                        <td>{p.candidate ?? "absent"}</td>
                        <td>{p.reasons.length ? p.reasons.join("; ") : (CHANGE_NOTE[p.change] ?? "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              <p className="muted">
                Each row is one attempt. With mock runs there is no randomness; with a real agent a single
                attempt per side would not support a confident trend.
              </p>
            </section>
          )}
        </Pending>
      )}
    </>
  );
}
