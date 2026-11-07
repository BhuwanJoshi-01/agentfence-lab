// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";

export type Route =
  | { page: "overview" }
  | { page: "run"; id: string }
  | { page: "attempt"; id: string }
  | { page: "compare"; left: string; right: string }
  | { page: "scenarios" }
  | { page: "environment" }
  | { page: "about" }
  | { page: "missing" };

const ID = "[A-Za-z0-9._:-]{1,256}";

export function parseRoute(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?", 2);
  const params = new URLSearchParams(query);
  let match: RegExpExecArray | null;
  if (path === "" || path === "/") return { page: "overview" };
  if ((match = new RegExp(`^/runs/(${ID})$`).exec(path))) return { page: "run", id: match[1]! };
  if ((match = new RegExp(`^/attempts/(${ID})$`).exec(path))) {
    return { page: "attempt", id: match[1]! };
  }
  if (path === "/compare") {
    return { page: "compare", left: params.get("left") ?? "", right: params.get("right") ?? "" };
  }
  if (path === "/scenarios") return { page: "scenarios" };
  if (path === "/environment") return { page: "environment" };
  if (path === "/about") return { page: "about" };
  return { page: "missing" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}

export const href = {
  overview: "#/",
  run: (id: string) => `#/runs/${encodeURIComponent(id)}`,
  attempt: (id: string) => `#/attempts/${encodeURIComponent(id)}`,
  compare: (left = "", right = "") =>
    `#/compare?${new URLSearchParams({ left, right }).toString()}`,
  scenarios: "#/scenarios",
  environment: "#/environment",
  about: "#/about",
};
