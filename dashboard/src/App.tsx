// SPDX-License-Identifier: Apache-2.0
import { href, type Route, useRoute } from "./router";
import { About } from "./pages/About";
import { AttemptDetail } from "./pages/AttemptDetail";
import { Compare } from "./pages/Compare";
import { EnvironmentPage } from "./pages/Environment";
import { Overview } from "./pages/Overview";
import { RunDetail } from "./pages/RunDetail";
import { Scenarios } from "./pages/Scenarios";
import { PageTitle } from "./components/ui";

const NAV: { label: string; href: string; pages: Route["page"][] }[] = [
  { label: "Runs", href: href.overview, pages: ["overview", "run", "attempt"] },
  { label: "Compare", href: href.compare(), pages: ["compare"] },
  { label: "Scenarios", href: href.scenarios, pages: ["scenarios"] },
  { label: "Environment", href: href.environment, pages: ["environment"] },
  { label: "About", href: href.about, pages: ["about"] },
];

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case "overview":
      return <Overview />;
    case "run":
      return <RunDetail id={route.id} />;
    case "attempt":
      return <AttemptDetail id={route.id} />;
    case "compare":
      return <Compare left={route.left} right={route.right} />;
    case "scenarios":
      return <Scenarios />;
    case "environment":
      return <EnvironmentPage />;
    case "about":
      return <About />;
    case "missing":
      return (
        <>
          <PageTitle title="Not found">Page not found</PageTitle>
          <p>
            <a href={href.overview}>Back to runs</a>
          </p>
        </>
      );
  }
}

export function App({ hasToken }: { hasToken: boolean }) {
  const route = useRoute();
  return (
    <>
      <a className="skip" href="#content">
        Skip to content
      </a>
      <header className="top">
        <a className="brand" href={href.overview}>
          AgentFence Lab
        </a>
        <span className="tag">read-only</span>
        <nav aria-label="Main">
          <ul>
            {NAV.map((item) => (
              <li key={item.label}>
                <a href={item.href} aria-current={item.pages.includes(route.page) ? "page" : undefined}>
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="content">
        {hasToken ? (
          <Page route={route} />
        ) : (
          <>
            <PageTitle title="Session needed">Open the link from your terminal</PageTitle>
            <p>
              This dashboard only shows data to the browser tab opened from the link that{" "}
              <code>agentfence ui</code> prints. That link carries a one-session token after{" "}
              <code>#</code>, which never leaves your machine.
            </p>
          </>
        )}
      </main>
      <footer className="foot">
        <p>
          Local and read-only. Mock results test the AgentFence pipeline, not any real coding agent.
        </p>
      </footer>
    </>
  );
}
