// SPDX-License-Identifier: Apache-2.0
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { captureToken } from "./api";
import { App } from "./App";
import { TokenContext } from "./token";
import "./styles.css";

const token = captureToken();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <TokenContext.Provider value={token}>
        <App hasToken={token !== null} />
      </TokenContext.Provider>
    </StrictMode>,
  );
}
