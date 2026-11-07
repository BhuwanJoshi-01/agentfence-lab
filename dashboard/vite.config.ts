// SPDX-License-Identifier: Apache-2.0
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The build goes into the Python package, so `pip install` needs no Node toolchain.
// CI rebuilds it and fails if the committed files differ.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/agentfence/api/static",
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.tsx"],
  },
});
