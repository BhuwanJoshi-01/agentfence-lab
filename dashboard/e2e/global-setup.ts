// SPDX-License-Identifier: Apache-2.0
// Seed a private state directory with real runs, start `agentfence ui` on a free loopback
// port, and hand the tests the session URL it prints.
import { execFileSync, spawn } from "node:child_process";
import { appendFileSync, chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => (typeof address === "object" && address ? done(address.port) : fail()));
    });
  });
}

export default async function globalSetup(): Promise<void> {
  const bin =
    process.env.AGENTFENCE_BIN ?? fileURLToPath(new URL("../../.venv/bin/agentfence", import.meta.url));
  const state = mkdtempSync(join(tmpdir(), "agentfence-e2e-"));
  chmodSync(state, 0o700);
  const env = { ...process.env, AGENTFENCE_STATE_DIR: state };
  for (const profile of ["safe", "unsafe", "no-telemetry"]) {
    try {
      execFileSync(bin, ["test", "--agent", "mock", "--agent-profile", profile], { env, stdio: "ignore" });
    } catch {
      // exit 1 (boundary failure) is the expected result for the unsafe profile
    }
  }
  const port = await freePort();
  const child = spawn(bin, ["ui", "--port", String(port)], { env, stdio: ["ignore", "ignore", "pipe"] });
  const url = await new Promise<string>((done, fail) => {
    let seen = "";
    const timer = setTimeout(() => fail(new Error(`agentfence ui did not start: ${seen}`)), 20000);
    child.stderr.on("data", (chunk: Buffer) => {
      appendFileSync(join(state, "ui.log"), chunk);
      seen += chunk.toString();
      const match = /(http:\/\/127\.0\.0\.1:\d+\/#token=[A-Za-z0-9_-]+)/.exec(seen);
      if (match?.[1]) {
        clearTimeout(timer);
        done(match[1]);
      }
    });
  });
  for (let i = 0; i < 50; i += 1) {
    const ok = await fetch(`http://127.0.0.1:${port}/`).then((r) => r.ok).catch(() => false);
    if (ok) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  const [base, token] = url.split("/#token=") as [string, string];
  process.env.AGENTFENCE_UI_BASE = base;
  process.env.AGENTFENCE_UI_TOKEN = token;
  writeFileSync(join(tmpdir(), "agentfence-e2e.json"), JSON.stringify({ pid: child.pid, state }));
}
