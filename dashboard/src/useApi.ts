// SPDX-License-Identifier: Apache-2.0
import { useContext, useEffect, useState } from "react";
import { getJSON } from "./api";
import { TokenContext } from "./token";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "error"; message: string; status: number }
  | { state: "ready"; data: T };

/** GET an API path; `null` skips the request. Stale responses are ignored. */
export function useApi<T>(path: string | null): Loaded<T> {
  const token = useContext(TokenContext);
  const [result, setResult] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    if (path === null || token === null) return;
    let current = true;
    setResult({ state: "loading" });
    getJSON<T>(path, token)
      .then((data) => current && setResult({ state: "ready", data }))
      .catch((error: unknown) => {
        if (!current) return;
        const status = (error as { status?: number }).status ?? 0;
        setResult({ state: "error", message: (error as Error).message, status });
      });
    return () => {
      current = false;
    };
  }, [path, token]);
  return result;
}
