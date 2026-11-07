// SPDX-License-Identifier: Apache-2.0
import type { Scenario } from "../api";
import { List, PageTitle, Pending } from "../components/ui";
import { label } from "../format";
import { useApi } from "../useApi";

export function Scenarios() {
  const result = useApi<{ scenarios: Scenario[] }>("/scenarios");
  return (
    <>
      <PageTitle title="Scenarios">Scenario catalog</PageTitle>
      <p className="muted">
        Bundled synthetic scenarios. Every secret is an <code>AF_FAKE_</code> marker and no real endpoint
        exists. Text below is the manifest's own, shown as plain text.
      </p>
      <Pending result={result}>
        {({ scenarios }) => (
          <div className="cards">
            {scenarios.map((s) => (
              <article key={s.id} className="card" aria-labelledby={`sc-${s.id}`}>
                <h2 id={`sc-${s.id}`}>
                  {s.id} <span className="muted">v{s.version}</span>
                </h2>
                <p>
                  <strong>{s.title}</strong> · {s.variant} · {label(s.category)}
                </p>
                <dl className="facts">
                  <dt>Legitimate task</dt>
                  <dd>{s.legitimate_task}</dd>
                  <dt>Untrusted surface</dt>
                  <dd>
                    {s.attack_surface ? (
                      <>
                        {label(s.attack_surface.kind)} <code>{s.attack_surface.path}</code>
                      </>
                    ) : (
                      "none (benign control)"
                    )}
                  </dd>
                  <dt>Boundary ({label(s.boundary.kind)})</dt>
                  <dd>
                    allow <List items={s.boundary.allow} /> deny <List items={s.boundary.deny} />
                  </dd>
                  <dt>Fails on</dt>
                  <dd>
                    <List items={s.failure_rules.map(label)} empty="nothing (benign control)" prose />
                  </dd>
                  <dt>Required collectors</dt>
                  <dd>
                    <List items={s.observation_channels} />
                  </dd>
                  <dt>Risk tags</dt>
                  <dd>{s.risk_tags.join(", ") || "none"}</dd>
                  <dt>Paired with</dt>
                  <dd>{s.benign_pair ?? "—"}</dd>
                </dl>
              </article>
            ))}
          </div>
        )}
      </Pending>
    </>
  );
}
