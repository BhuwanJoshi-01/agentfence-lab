// SPDX-License-Identifier: Apache-2.0
// Every JavaScript package that ships inside the built dashboard (non-dev entries in
// package-lock.json) must carry an Apache-2.0-compatible license (ADR-014). Dev tooling
// does not ship and is not checked here.
import { readFileSync } from "node:fs";

const ALLOWED = new Set(["MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "0BSD"]);
const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url), "utf-8"));
const shipped = Object.entries(lock.packages).filter(([path, meta]) => path !== "" && !meta.dev);
const bad = shipped.filter(([, meta]) => !ALLOWED.has(meta.license));
for (const [path, meta] of shipped) console.log(`${meta.license ?? "UNKNOWN"}\t${path.replace("node_modules/", "")}@${meta.version}`);
if (shipped.length === 0 || bad.length > 0) {
  console.error(`license check failed: ${bad.map(([p, m]) => `${p} (${m.license})`).join(", ") || "nothing found"}`);
  process.exit(1);
}
