// SPDX-License-Identifier: Apache-2.0
// The dashboard against a real `agentfence ui` (AF-0602..AF-0607). axe is a baseline, not
// proof of accessibility (docs/08 section 10).
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const base = () => process.env.AGENTFENCE_UI_BASE ?? "";
const token = () => process.env.AGENTFENCE_UI_TOKEN ?? "";
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

type Run = { run_id: string; agent_profile: string };

async function runs(page: Page): Promise<Record<string, string>> {
  const response = await page.request.get(`${base()}/api/v1/runs`, {
    headers: { Authorization: `Bearer ${token()}` },
  });
  const body = (await response.json()) as { runs: Run[] };
  return Object.fromEntries(body.runs.map((r) => [r.agent_profile, r.run_id]));
}

const problems: string[] = [];

test.beforeEach(async ({ page }) => {
  problems.length = 0;
  page.on("console", (msg) => msg.type() === "error" && problems.push(msg.text()));
  page.on("pageerror", (err) => problems.push(err.message));
  await page.goto(`${base()}/#token=${token()}`);
  await expect(page).toHaveURL(`${base()}/#/`);
});

test.afterEach(() => {
  expect(problems, "console errors or CSP violations").toEqual([]);
});

async function axe(page: Page) {
  // WCAG 2.2 AA plus axe's best practices (unique landmarks, heading order, ...).
  const result = await new AxeBuilder({ page }).withTags([...WCAG, "best-practice"]).analyze();
  expect(result.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}

test("the token leaves the address bar and the overview keeps outcomes apart", async ({ page }) => {
  expect(page.url()).not.toContain(token());
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  for (const outcome of ["FAIL", "PASS", "INCONCLUSIVE", "NOT TRIGGERED"]) {
    await expect(page.locator(".counters").getByText(outcome, { exact: true })).toBeVisible();
  }
  await expect(page.getByText("not a security score")).toBeVisible();
  await axe(page);
});

test("run detail: matrix, filters and explanations", async ({ page }) => {
  const ids = await runs(page);
  await page.goto(`${base()}/#/runs/${ids.unsafe}`);
  const matrix = page.getByRole("region", { name: "Scenario matrix table", exact: true });
  await expect(matrix.locator("tbody tr")).toHaveCount(10);
  await page.getByLabel("Boundary outcome").selectOption("FAIL");
  await expect(matrix.locator("tbody tr")).toHaveCount(5);
  await expect(page.getByText("Showing 5 of 10 attempts.")).toBeVisible();
  await page.getByLabel("Boundary outcome").selectOption("ALL");
  await page.getByLabel("Scenario or finding code").fill("AF-CRED");
  await expect(matrix.locator("tbody tr")).toHaveCount(2);
  await axe(page);

  await page.goto(`${base()}/#/runs/${ids["no-telemetry"]}`);
  await expect(page.getByText("Missing evidence:").first()).toBeVisible();
});

test("attempt detail: timeline with trust tags and attempted vs completed", async ({ page }) => {
  const ids = await runs(page);
  await page.goto(`${base()}/#/runs/${ids.unsafe}`);
  await page.getByRole("link", { name: "AF-CRED-001", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("AF-CRED-001");
  await expect(page.locator(".timeline li").first()).toBeVisible();
  await expect(page.locator(".trust").first()).toBeVisible();
  await expect(page.getByText("prohibited attempt (blocked)").first()).toBeVisible();
  await axe(page);
});

test("compare: regressions per scenario, and nothing claimed for one run", async ({ page }) => {
  const ids = await runs(page);
  await page.goto(`${base()}/#/compare`);
  await expect(page.getByText("Choose two different runs.")).toBeVisible();
  await page.getByLabel("Baseline").selectOption(ids.safe!);
  await page.getByLabel("Candidate").selectOption(ids.unsafe!);
  const table = page.getByRole("region", { name: "Comparison table", exact: true });
  await expect(table.getByText("REGRESSED")).toHaveCount(5);
  await axe(page);
});

test("scenario catalog, environment and about", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto(`${base()}/#/scenarios`);
  await expect(page.locator("article.card")).toHaveCount(10);
  await axe(page);
  await page.goto(`${base()}/#/environment`);
  // The route runs the container engines' own info queries (each bounded at 15 s).
  await expect(page.getByRole("heading", { name: "Host checks" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Latest container self-test" })).toBeVisible();
  await axe(page);
  await page.goto(`${base()}/#/about`);
  await expect(page.getByRole("heading", { name: "Limitations", exact: true })).toBeVisible();
  await axe(page);
});

test("navigation moves focus to the new page's heading", async ({ page }) => {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Scenarios" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Scenario catalog" })).toBeFocused();
});

test("keyboard: the first Tab reaches the skip link, which moves to the content", async ({ page }) => {
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#content$/);
});

test("no page scrolls sideways", async ({ page }) => {
  const ids = await runs(page);
  for (const path of ["/", `/runs/${ids.unsafe}`, "/scenarios", "/environment", "/compare"]) {
    await page.goto(`${base()}/#${path}`);
    await page.locator("h1").waitFor();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});

test.describe("without a valid session", () => {
  test("a fresh tab without the token is asked for the terminal link", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto(`${base()}/`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Open the link from your terminal");
    await page.close();
  });

  test("a wrong token gets a clear error, not data", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto(`${base()}/#token=${"x".repeat(43)}`);
    await expect(page.getByRole("alert")).toContainText("session token");
    await page.close();
  });
});
