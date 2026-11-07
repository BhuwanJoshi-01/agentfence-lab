// SPDX-License-Identifier: Apache-2.0
// Records the README demo (AF-0702) from real output: a terminal view of an actual
// `agentfence test` run captured now, then the real dashboard on the same runs.
//   AGENTFENCE_DEMO=1 npx playwright test demo --project desktop && scripts/make-demo-gif.sh
// It shows the mock agent, labelled as such; it is never presented as a real agent.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";

const base = () => process.env.AGENTFENCE_UI_BASE ?? "";
const token = () => process.env.AGENTFENCE_UI_TOKEN ?? "";
const bin = () =>
  process.env.AGENTFENCE_BIN ?? fileURLToPath(new URL("../../.venv/bin/agentfence", import.meta.url));

test.skip(!process.env.AGENTFENCE_DEMO, "set AGENTFENCE_DEMO=1 to record the demo");

function realOutput(): string {
  const { state } = JSON.parse(readFileSync(join(tmpdir(), "agentfence-e2e.json"), "utf-8")) as {
    state: string;
  };
  const env = { ...process.env, AGENTFENCE_STATE_DIR: state, COLUMNS: "100" };
  try {
    execFileSync(bin(), ["test", "--agent", "mock", "--agent-profile", "unsafe"], { env, encoding: "utf-8" });
  } catch (error) {
    return (error as { stdout: string }).stdout; // exit 1: boundaries crossed, as expected
  }
  throw new Error("the unsafe mock run was expected to exit 1");
}

async function caption(page: Page, text: string) {
  await page.evaluate((t) => {
    let el = document.getElementById("demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-caption";
      el.setAttribute(
        "style",
        "position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#111;color:#fff;" +
          "padding:8px 16px;border-radius:8px;font:600 18px system-ui;z-index:99;max-width:90%;",
      );
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}

test("demo", async ({ browser }) => {
  test.setTimeout(180_000);
  const output = realOutput()
    .split("\n")
    .filter((line) => !line.startsWith("Run ID:"))
    .join("\n");
  const context = await browser.newContext({
    viewport: { width: 1180, height: 720 },
    recordVideo: { dir: fileURLToPath(new URL("../test-results/demo-video", import.meta.url)), size: { width: 1180, height: 720 } },
  });
  const page = await context.newPage();

  // 1. The real CLI output, typed out line by line (no styling of a real terminal is claimed).
  await page.setContent(
    `<body style="margin:0;background:#0f1115;color:#d7dae0;font:15px ui-monospace,monospace">` +
      `<pre id="t" style="padding:24px;white-space:pre-wrap"></pre></body>`,
  );
  const lines = [`$ agentfence test --agent mock --agent-profile unsafe`, ...output.split("\n")];
  for (const line of lines) {
    await page.evaluate((l) => {
      document.getElementById("t")!.textContent += `${l}\n`;
    }, line);
    await page.waitForTimeout(line.startsWith("$") ? 1200 : 180);
  }
  await page.waitForTimeout(3500);

  // 2. The dashboard on the same runs.
  await page.goto(`${base()}/#token=${token()}`);
  await caption(page, "agentfence ui: read-only, local, mock agent results");
  await page.waitForTimeout(3500);
  await page.getByRole("link", { name: /^Run / }).first().click();
  await caption(page, "Each attempt: boundary, utility and the evidence behind it");
  await page.waitForTimeout(3000);
  await page.getByLabel("Boundary outcome").selectOption("FAIL");
  await page.waitForTimeout(2500);
  await page.getByRole("link", { name: "AF-CRED-001", exact: true }).click();
  await caption(page, "Trusted broker denied the read: an attempt, not completed access");
  await expect(page.getByText("prohibited attempt (blocked)").first()).toBeVisible();
  await page.waitForTimeout(4500);
  await page.goto(`${base()}/#/compare`);
  const runs = await (
    await page.request.get(`${base()}/api/v1/runs`, { headers: { Authorization: `Bearer ${token()}` } })
  ).json();
  const id = (profile: string) =>
    runs.runs.find((r: { agent_profile: string }) => r.agent_profile === profile).run_id as string;
  await page.getByLabel("Baseline").selectOption(id("safe"));
  await page.getByLabel("Candidate").selectOption(id("unsafe"));
  await caption(page, "Compare runs: regressions per scenario, never a single score");
  await page.waitForTimeout(4000);
  await page.goto(`${base()}/#/environment`);
  await expect(page.getByRole("heading", { name: "Host checks" })).toBeVisible({ timeout: 60_000 });
  await caption(page, "Host checks and the container self-test: fail closed");
  await page.waitForTimeout(4000);
  await context.close();
});
