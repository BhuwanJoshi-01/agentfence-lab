// SPDX-License-Identifier: Apache-2.0
import type { Environment } from "../api";
import { Outcome, PageTitle, Pending, TableScroll } from "../components/ui";
import { when } from "../format";
import { useApi } from "../useApi";

export function EnvironmentPage() {
  const result = useApi<Environment>("/environment");
  return (
    <>
      <PageTitle title="Environment">Environment and isolation</PageTitle>
      {result.state === "loading" && (
        <p className="muted">Asking the container engines for their own reports; this can take a few seconds.</p>
      )}
      <Pending result={result}>
        {(env) => (
          <>
            <p className="lede">
              Isolation state: <Outcome value={env.isolation_state} />
            </p>
            {env.blocking_checks.length > 0 && (
              <div className="notice notice-error" role="note">
                Sandbox runs are refused on this host until these pass: {env.blocking_checks.join(", ")}. There
                is no way to proceed anyway.
              </div>
            )}
            <section aria-labelledby="checks">
              <h2 id="checks">Host checks</h2>
              <TableScroll label="Host checks">
                <table>
                  <caption className="sr-only">Read-only host checks from agentfence doctor</caption>
                  <thead>
                    <tr>
                      <th scope="col">Check</th>
                      <th scope="col">Status</th>
                      <th scope="col">Result</th>
                      <th scope="col">Remedy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {env.checks.map((c) => (
                      <tr key={c.id}>
                        <th scope="row">
                          <code>{c.id}</code>
                          {!c.required && <span className="sub">optional</span>}
                        </th>
                        <td>
                          <Outcome value={c.status} />
                        </td>
                        <td>
                          {c.summary}
                          {c.detail && <span className="sub">{c.detail}</span>}
                        </td>
                        <td>{c.remedy}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </section>
            <section aria-labelledby="attestation">
              <h2 id="attestation">Latest container self-test</h2>
              {env.attestation === null ? (
                <p>
                  No self-test recorded. Run <code>agentfence doctor --self-test</code> on a host where the checks
                  above pass.
                </p>
              ) : (
                <>
                  <p>
                    {env.attestation_passed ? "Passed" : "Not passed"} · {when(env.attestation.created_at)} ·{" "}
                    {env.attestation.engine} {env.attestation.engine_version} ·{" "}
                    {env.attestation_verified ? "attestation verifies" : "attestation does NOT verify (edited?)"}
                  </p>
                  <TableScroll label="Self-test checks">
                    <table>
                      <caption className="sr-only">Isolation properties checked inside a container</caption>
                      <thead>
                        <tr>
                          <th scope="col">Property</th>
                          <th scope="col">Status</th>
                          <th scope="col">Expected</th>
                          <th scope="col">Observed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {env.attestation.checks.map((c) => (
                          <tr key={c.id}>
                            <th scope="row">
                              <code>{c.id}</code>
                            </th>
                            <td>
                              <Outcome value={c.status} />
                            </td>
                            <td>{c.expected}</td>
                            <td>
                              <code className="wrap">{c.observed}</code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableScroll>
                </>
              )}
            </section>
          </>
        )}
      </Pending>
    </>
  );
}
