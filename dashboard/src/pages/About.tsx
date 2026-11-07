// SPDX-License-Identifier: Apache-2.0
import { PageTitle } from "../components/ui";

export function About() {
  return (
    <>
      <PageTitle title="About">About, limitations and privacy</PageTitle>
      <section aria-labelledby="what">
        <h2 id="what">What this shows</h2>
        <p>
          AgentFence Lab runs paired synthetic scenarios and reports, per attempt, whether a boundary held (
          <code>PASS</code>), was crossed (<code>FAIL</code>), could not be judged for lack of evidence (
          <code>INCONCLUSIVE</code>) or was never put to the test (<code>NOT_TRIGGERED</code>). Every verdict
          cites the trusted events behind it.
        </p>
      </section>
      <section aria-labelledby="limits">
        <h2 id="limits">Limitations</h2>
        <ul>
          <li>Only a deterministic mock agent exists. It runs in-process; no scenario runs in a container yet.</li>
          <li>Five scenario families. A PASS means no violation was observed under that test, not that anything is secure.</li>
          <li>The evidence hash chain detects corruption and casual tampering, not someone who controls this machine.</li>
        </ul>
      </section>
      <section aria-labelledby="privacy">
        <h2 id="privacy">Privacy</h2>
        <ul>
          <li>Everything is served from this machine. No analytics, fonts, CDNs or external requests.</li>
          <li>The dashboard is read-only: nothing can be started, changed or deleted from it.</li>
          <li>
            The session token lives in this tab only and is never sent anywhere except to the local API. Close
            the tab or stop <code>agentfence ui</code> to end the session.
          </li>
        </ul>
      </section>
    </>
  );
}
