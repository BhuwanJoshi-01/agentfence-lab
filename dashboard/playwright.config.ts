// SPDX-License-Identifier: Apache-2.0
import { defineConfig, devices } from "@playwright/test";

// The suite starts a real `agentfence ui` (see e2e/global-setup.ts); set AGENTFENCE_BIN to
// use an `agentfence` other than ../.venv/bin/agentfence.
export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  workers: 1,
  reporter: [["list"]],
  use: { trace: "off" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
