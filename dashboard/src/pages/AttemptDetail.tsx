// SPDX-License-Identifier: Apache-2.0
import { useContext, useEffect, useState } from "react";
import { type Attempt, type EventsPage, getJSON, type ObservedEvent } from "../api";
import { List, Outcome, PageTitle, Pending, TrustTag } from "../components/ui";
import { label, when } from "../format";
import { href } from "../router";
import { TokenContext } from "../token";
import { useApi } from "../useApi";

const MAX_PAGES = 20;

function role(event: ObservedEvent, attempt: Attempt): string | null {
  if (attempt.completed_effects.includes(event.event_id)) return "completed effect";
  if (attempt.prohibited_attempts.includes(event.event_id)) return "prohibited attempt (blocked)";
  if (attempt.utility_evidence.includes(event.event_id)) return "utility evidence";
  if (attempt.evidence_ids.includes(event.event_id)) return "cited evidence";
  return null;
}

function useEvents(id: string, token: string | null) {
  const [events, setEvents] = useState<ObservedEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!token) return;
    let current = true;
    (async () => {
      const all: ObservedEvent[] = [];
      let after = -1;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const data = await getJSON<EventsPage>(
          `/attempts/${encodeURIComponent(id)}/events?limit=200&after=${after}`,
          token,
        );
        all.push(...data.events);
        if (data.next_after === null) break;
        after = data.next_after;
      }
      if (current) setEvents(all);
    })().catch((e: unknown) => current && setError((e as Error).message));
    return () => {
      current = false;
    };
  }, [id, token]);
  return { events, error };
}

export function AttemptDetail({ id }: { id: string }) {
  const attempt = useApi<Attempt>(`/attempts/${encodeURIComponent(id)}`);
  const token = useContext(TokenContext);
  const { events, error } = useEvents(id, token);
  return (
    <Pending result={attempt}>
      {(a) => (
        <>
          <PageTitle title={`${a.scenario_id} attempt`}>
            {a.scenario_id} <span className="muted">· {a.variant}</span>
          </PageTitle>
          <p>
            <a href={href.run(a.run_id ?? "")}>Back to the run</a>
          </p>
          <div className="split">
            <section aria-labelledby="timeline">
              <h2 id="timeline">Timeline</h2>
              {error && (
                <div className="notice notice-error" role="alert">
                  Could not load events: {error}
                </div>
              )}
              {events === null && !error && <p role="status">Loading events…</p>}
              {events && (
                <ol className="timeline">
                  {events.map((e) => {
                    const why = role(e, a);
                    return (
                      <li key={e.event_id} id={e.event_id} className={why ? "cited" : undefined}>
                        <div className="when">
                          <time dateTime={e.timestamp}>{when(e.timestamp)}</time> · #{e.sequence}
                        </div>
                        <div>
                          <strong>{label(e.kind)}</strong> <TrustTag trust={e.source_trust} />
                          {e.decision !== "NONE" && <span className="decision">{label(e.decision)}</span>}
                        </div>
                        <div>
                          <code>{e.target}</code>
                          {why && <span className="why">{why}</span>}
                        </div>
                        {Object.keys(e.details).length > 0 && (
                          <dl className="details">
                            {Object.entries(e.details).map(([k, v]) => (
                              <div key={k}>
                                <dt>{k}</dt>
                                <dd>{String(v)}</dd>
                              </div>
                            ))}
                          </dl>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
            <section aria-labelledby="inspector">
              <h2 id="inspector">Evidence</h2>
              <dl className="facts">
                <dt>Boundary</dt>
                <dd>
                  <Outcome value={a.boundary} />
                </dd>
                <dt>Utility</dt>
                <dd>{label(a.utility)}</dd>
                <dt>Infrastructure</dt>
                <dd>{a.infrastructure}</dd>
                <dt>Finding</dt>
                <dd>
                  <code>{a.finding_code}</code> · confidence {a.confidence}
                </dd>
                <dt>Prohibited attempts</dt>
                <dd>
                  <List items={a.prohibited_attempts} />
                </dd>
                <dt>Completed effects</dt>
                <dd>
                  <List items={a.completed_effects} />
                </dd>
                <dt>Evidence cited</dt>
                <dd>
                  <List items={a.evidence_ids} />
                </dd>
                <dt>Utility evidence</dt>
                <dd>
                  <List items={a.utility_evidence} />
                </dd>
                <dt>Coverage gaps</dt>
                <dd>
                  <List items={a.coverage_gaps} />
                </dd>
                <dt>Limitations</dt>
                <dd>
                  <List items={a.limitations} prose />
                </dd>
                <dt>Scenario version</dt>
                <dd>{a.scenario_version}</dd>
                <dt>Evaluator</dt>
                <dd>{a.evaluator_version}</dd>
                <dt>Fixture digest</dt>
                <dd>
                  <code className="wrap">{a.fixture_digest}</code>
                </dd>
                <dt>Impact scope</dt>
                <dd>{a.impact_scope}</dd>
              </dl>
              <p className="muted">
                An attempt that was blocked is reported as an attempt, never as completed access. Evidence
                from the agent itself would be marked “agent reported” and never proves anything.
              </p>
            </section>
          </div>
        </>
      )}
    </Pending>
  );
}
