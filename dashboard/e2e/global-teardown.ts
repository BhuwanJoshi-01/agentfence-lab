// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default async function globalTeardown(): Promise<void> {
  const file = join(tmpdir(), "agentfence-e2e.json");
  if (!existsSync(file)) return;
  const { pid, state } = JSON.parse(readFileSync(file, "utf-8")) as { pid: number; state: string };
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    // already gone
  }
  if (process.env.AGENTFENCE_E2E_KEEP) {
    console.log(`kept e2e state and server log in ${state}`);
  } else {
    rmSync(state, { recursive: true, force: true });
  }
  rmSync(file, { force: true });
}
