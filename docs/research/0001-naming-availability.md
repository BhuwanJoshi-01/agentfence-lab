# Name availability: "AgentFence" (AF-0001)

- **Checked:** 2026-11-07, against PyPI's JSON API and GitHub's repository search API
- **Question:** can this project publish as "AgentFence Lab" without being confused with
  another project?
- **Answer:** not without real confusion. The name is already used in the same problem
  space. Choosing the name is a maintainer decision, tracked as a follow-up issue. This note
  only records the facts.

## Package names (PyPI)

| Name | Status on 2026-11-07 |
|---|---|
| `agentfence` | **taken**: "A Python library for testing and identifying security vulnerabilities in AI agents", MIT, homepage `https://agentfence.ai`, repository `https://github.com/agentfence/agentfence`, one release (0.1.0) |
| `agentfence-lab` | free; this project's distribution name in `pyproject.toml` |
| `agentfencelab`, `agent-fence` | free |

PyPI normalizes names, so `agentfence-lab` and `agentfence_lab` are the same name. Neither
collides with `agentfence` as a package name, but people searching for either will find
both.

## GitHub repositories named "agentfence"

The repository search for `agentfence in:name` returned 53 repositories. Those with a
description:

| Repository | Created | License | Self-description |
|---|---|---|---|
| [haggaishachar/agentfence](https://github.com/haggaishachar/agentfence) | 2025-03-06 | MIT | "AgentFence is an open-source platform for automatically testing AI agent security. It identifies vulnerabilities such as prompt injection, secret leakage, and system instruction exposure." Homepage `https://agentfence.ai`, the same as the PyPI package. |
| [dgenio/agentfence](https://github.com/dgenio/agentfence) | 2026-05-18 | Apache-2.0 | "Policy firewall for AI agents and MCP tools. ..." |
| [balyakin/agentfence](https://github.com/balyakin/agentfence) | 2026-06-30 | MIT | "Go CLI for running AI coding agents in temporary shadow workspaces" |
| [SushantGautam/AgentFence](https://github.com/SushantGautam/AgentFence) | 2026-09-29 | MIT | "guardrails for AI coding agents on shared Linux hosts ..." |
| [kramit1288/agentfence](https://github.com/kramit1288/agentfence) | 2026-03-12 | not stated | "Open-source gateway for securing, governing, and auditing MCP tool access ..." |

## What this means

- **The closest overlap is the oldest.** `haggaishachar/agentfence` and the PyPI
  `agentfence` package both describe automated security testing of AI agents, the problem
  this project works on, and they predate this repository by about 20 months. They share
  the `agentfence.ai` domain.
- **Four more projects** use the name for agent guardrails, firewalls or sandboxes, three of
  them for coding agents.
- **"Lab" is not enough to tell them apart** in search results, in package installs
  (`pip install agentfence` would install the other project) or in conversation.
- **Trademark status was not checked.** That needs a proper search in the jurisdictions
  that matter, which a repository search cannot replace. docs/12 already lists the name as
  provisional.

## Recommendation

Decide on a name **before** `v0.1.0` (AF-0707) and before any launch post (docs/11 section
10). A rename is cheap now: the CLI, the import package, the distribution name, the
`io.agentfence.*` labels, the `AF_FAKE_` marker prefix and the docs all change in one pull
request. It gets expensive after users have reports, labels and configs on disk.

Suggested checks for any candidate: PyPI JSON API (404 means free), GitHub repository
search, a domain lookup, a trademark search, and a search for the name plus "AI agent".
Keep `AF_FAKE_` as the synthetic-marker prefix either way; it is a format, not branding.
