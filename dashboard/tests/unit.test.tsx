// SPDX-License-Identifier: Apache-2.0
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureToken, forgetToken, getJSON } from "../src/api";
import { App } from "../src/App";
import { Outcome, TrustTag } from "../src/components/ui";
import { ratio } from "../src/format";
import { parseRoute } from "../src/router";
import { TokenContext } from "../src/token";

const TOKEN = "t".repeat(43);

afterEach(() => {
  cleanup();
  forgetToken();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("session token", () => {
  it("is taken from the fragment and removed from the address bar", () => {
    window.location.hash = `#token=${TOKEN}`;
    expect(captureToken()).toBe(TOKEN);
    expect(window.location.hash).toBe("#/");
    expect(window.location.href).not.toContain(TOKEN);
    expect(window.sessionStorage.getItem("agentfence.session")).toBe(TOKEN);
  });

  it("survives a reload in the same tab, and is not invented", () => {
    expect(captureToken()).toBeNull();
    window.sessionStorage.setItem("agentfence.session", TOKEN);
    expect(captureToken()).toBe(TOKEN);
  });

  it.each(["#token=short", "#token=" + "t".repeat(43) + "&x=1", "#/runs/x?token=" + TOKEN])(
    "ignores malformed fragments: %s",
    (hash) => {
      window.location.hash = hash;
      expect(captureToken()).toBeNull();
    },
  );

  it("is sent only as a bearer header to the local API, without credentials or referrer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await getJSON("/health", TOKEN);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/health");
    expect(init.credentials).toBe("omit");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("surfaces the API's error shape", async () => {
    const body = { error: { code: "unauthorized", message: "a valid session token is required" } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 401 })));
    await expect(getJSON("/runs", TOKEN)).rejects.toMatchObject({ status: 401, code: "unauthorized" });
  });
});

describe("routes", () => {
  it.each([
    ["", { page: "overview" }],
    ["#/", { page: "overview" }],
    ["#/runs/abc-123", { page: "run", id: "abc-123" }],
    ["#/attempts/att_1", { page: "attempt", id: "att_1" }],
    ["#/compare?left=a&right=b", { page: "compare", left: "a", right: "b" }],
    ["#/scenarios", { page: "scenarios" }],
    ["#/runs/../../etc", { page: "missing" }],
    ["#/runs/<script>", { page: "missing" }],
  ])("%s", (hash, route) => {
    expect(parseRoute(hash)).toEqual(route);
  });
});

describe("presentation", () => {
  it("states outcomes and trust as text, not only colour", () => {
    render(
      <>
        <Outcome value="NOT_TRIGGERED" />
        <TrustTag trust="agent_reported" />
      </>,
    );
    expect(screen.getByText("NOT TRIGGERED")).toBeTruthy();
    expect(screen.getByText("agent reported")).toBeTruthy();
  });

  it("formats ratios with intervals and refuses to divide by zero", () => {
    expect(ratio({ numerator: 5, denominator: 5, value: 1, wilson_95: [0.5655, 1] })).toBe(
      "5/5 (95% CI 0.57–1.00)",
    );
    expect(ratio({ numerator: 0, denominator: 0, value: null, wilson_95: null })).toBe(
      "not applicable (no cases)",
    );
  });

  it("asks for the terminal link when there is no token", () => {
    render(<App hasToken={false} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Open the link from your terminal");
  });
});

describe("hostile text is rendered inert (AF-0607)", () => {
  const HOSTILE = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';

  beforeEach(() => {
    const scenario = {
      id: "AF-X-001",
      version: "1.0.0",
      variant: "adversarial",
      category: "prompt-injection",
      title: HOSTILE,
      risk_tags: [HOSTILE],
      legitimate_task: HOSTILE,
      attack_surface: { kind: "repository_document", path: HOSTILE },
      boundary: { kind: "write_scope", allow: [HOSTILE], deny: [] },
      observation_channels: [],
      failure_rules: [],
      benign_pair: null,
      sha256: "0".repeat(64),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ scenarios: [scenario] }), { status: 200 })),
    );
    window.location.hash = "#/scenarios";
  });

  it("shows the markup as text and creates no elements from it", async () => {
    const { container } = render(
      <TokenContext.Provider value={TOKEN}>
        <App hasToken />
      </TokenContext.Provider>,
    );
    await waitFor(() => expect(screen.getAllByText(HOSTILE, { exact: false }).length).toBeGreaterThan(0));
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
