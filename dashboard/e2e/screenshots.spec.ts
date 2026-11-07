// SPDX-License-Identifier: Apache-2.0
// Regenerates the README screenshots from the real dashboard (mock runs, labelled as such):
//   AGENTFENCE_SCREENSHOTS=1 npx playwright test screenshots --project desktop
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const base = () => process.env.AGENTFENCE_UI_BASE ?? "";
const token = () => process.env.AGENTFENCE_UI_TOKEN ?? "";
const out = (name: string) => fileURLToPath(new URL(`../../docs/images/${name}`, import.meta.url));

test.skip(!process.env.AGENTFENCE_SCREENSHOTS, "set AGENTFENCE_SCREENSHOTS=1 to regenerate");

test("README screenshots", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.goto(`${base()}/#token=${token()}`);
  await expect(page.locator(".counters")).toBeVisible();
  await page.screenshot({ path: out("dashboard-runs.png") });
  const runs = await (
    await page.request.get(`${base()}/api/v1/runs`, { headers: { Authorization: `Bearer ${token()}` } })
  ).json();
  const unsafe = runs.runs.find((r: { agent_profile: string }) => r.agent_profile === "unsafe").run_id;
  await page.goto(`${base()}/#/runs/${unsafe}`);
  await expect(page.getByRole("region", { name: "Scenario matrix table", exact: true })).toBeVisible();
  await page.screenshot({ path: out("dashboard-run.png"), fullPage: true });
  await page.getByRole("link", { name: "AF-CRED-001", exact: true }).click();
  await expect(page.locator(".timeline li").first()).toBeVisible();
  await page.screenshot({ path: out("dashboard-attempt.png"), fullPage: true });
});
