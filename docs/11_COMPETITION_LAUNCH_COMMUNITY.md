# 11 — Competitive Positioning, Open-Source Growth and Launch

## 1. Existing work: acknowledge competitors explicitly

This is not a wholly unserved problem. Several open-source projects already evaluate agent runtime security:

- **[AgentTrap](https://github.com/zhmzm/AgentTrap)** — dynamic benchmark of third-party skill trust failures with runtime evidence and benign controls.
- **[Agent3σ-Canary / AgentCanary](https://github.com/antgroup/Agent3Sigma-Canary)** — broad security evaluation framework with sandboxed tasks, trajectories, scenario extensibility and analysis dashboards.
- **[Pipelock](https://github.com/pipelab-org/pipelock)** — agent-security runtime control focused on egress or traffic policy enforcement (verify current capabilities/terminology before comparison).
- **[OWASP GenAI Security Project](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)** — threat-taxonomy and mitigation context, not a direct product competitor.

**Implication:** a generic "LLM jailbreak benchmark" or clone dashboard will struggle to differentiate. Position AgentFence Lab as a **small, local, developer-configuration regression tester**, with confidence-calibrated runtime side-effect evidence and reproducible baseline comparison. Be honest that AgentTrap and AgentCanary already pursue runtime realism; your differentiator is UX, installation simplicity, integration fidelity and policy comparison—not claiming the idea of runtime evaluation is new.

## 2. Concrete differentiators to test with users

| Differentiator | What the user sees | How to verify usefulness |
|---|---|---|
| `agentfence doctor` | tells which isolation control is missing | time-to-troubleshoot in user study |
| 5-minute offline demo | mock scenarios run without keys | clean installation test |
| Paired benign controls | detects unhelpful total refusal | observed false-positive accounting |
| Evidence-first verdict | inspector shows actual events | reviewers agree with classifications |
| Baseline compare | regression after config/model update | same-case comparison on repeated attempts |
| Explicit unknowns | `INCONCLUSIVE` rather than fabricated pass | telemetry-loss injected tests |
| Local HTML report | share redacted evidence without backend | recipient can open without connection |

*Do not* claim a five-minute onboarding time unless measured. A "5-minute demo" is a target.

## 3. Primary audience and channels

1. Coding-agent power users who customize configs and approval policies.
2. Maintainers adopting automated PR agents and review bots.
3. DevSecOps teams looking for evidence-based guardrail regression tests.
4. Security researchers evaluating specific agents and adapters.
5. Educators demonstrating trust-boundary failures in synthetic tasks.

Community locations: GitHub Discussions, relevant coding-agent repos' integration areas, OWASP GenAI community channels, Hacker News Show HN and targeted developer/security forums. Follow each community's self-promotion rules. More useful: file high-quality upstream issues or PRs if AgentFence uncovers a reproducible problem under agreed policies.

## 4. Potential launch demo storyboard (90 seconds)

**0–15 sec: user pain** — "We let agents read README files and tool output. Can we prove they honor the scope of a task?"

**15–30 sec: install** — a clean Linux machine, `agentfence doctor` (show real checks). No API token in first demo.

**30–50 sec: scenario** — show harmless test fixture, permitted files, forbidden synthetic marker, and paired benign run.

**50–70 sec: observed result** — show agent actions and a trusted broker denial/allowed-file diff with the report saying "attempted" rather than "stolen".

**70–90 sec: evidence and reproducibility** — drill into timeline, replay the mock scenario, show event IDs, share HTML report. End with "Real agents require controlled auth/egress support; see limitations." 

**Never** present mock-agent output as if Claude/Codex was actually tested. Live-agent demos require separate production-supported security gates.

## 5. README positioning

Proposed opening:

> AgentFence Lab checks whether AI coding agents respect file, network and tool boundaries when they encounter untrusted project content. It runs reproducible synthetic scenarios in a sandbox, observes actual effects and reports PASS, FAIL or INCONCLUSIVE—with evidence. Start offline with a mock agent; supported real agents are added only when their isolation is verified.

A high-performing repository front page should answer:

- What does it do (one sentence)?
- Who is it for (one sentence)?
- What can I run in less than a minute (documented, measured command)?
- Which agents/platforms are actually supported today?
- What does a failed case look like and where is the proof?
- Which permissions, credentials and network paths does it use?
- What is unsafe/unsupported?
- How can I contribute a scenario without causing harm?

## 6. Ethical comparison and claims

No invented ratings, stars, benchmark numbers or outcomes. When comparing external tools, use dated references and exact features confirmed by docs. Avoid implying authorization to test third-party machines. Refrain from vendor "most vulnerable" league tables unless sample sizes, policies, versions, limitations and statistical uncertainty are sound.

**Security language:** use "observed prohibited synthetic file read request" rather than "steals your credentials"; use "blocked local mock sink attempt" rather than "exfiltrated secrets"; use "no violation observed under this test" rather than "secure".

## 7. Issue labels and contributor onboarding

Recommended labels:

- `good first issue` — docs, UI, deterministic mock fixtures;
- `help wanted` — supported distro reproducibility, noncritical reporting;
- `security-review-required` — collector, sandbox, network, scenario payload;
- `adapter:codex`, `adapter:claude`, `adapter:opencode`;
- `scenario`, `scenario:benign-control`, `evidence`, `inconclusive`;
- `regression`, `docs`, `privacy`, `accessibility`.

Contributor UX:

1. `pip install -e '.[dev]'` on Linux.
2. `pytest tests/unit -q` offline, no API keys.
3. Run mock scenario with sample fixture.
4. Pick issue template with acceptance criteria and telemetry requirements.
5. Submit PR including tests and sanitized evidence.

Avoid requiring every new contributor to install a live coding agent or obtain paid API access.

## 8. First 90 days after public launch

| Time | Action | Evidence of progress |
|---|---|---|
| Week 1 | Publish reproducible mock demo and known limitations | 5 external installs verified by voluntary feedback |
| Weeks 2–3 | Close onboarding bugs, improve error messages | fewer setup-related issues |
| Weeks 4–5 | Scenario contribution guide + 2 new audited benign controls | external accepted PRs |
| Weeks 6–8 | Research first live adapter feasibility | documented security decision and test evidence |
| Weeks 9–12 | Offer adapter alpha only if gates pass, else strengthen core | independent reproduction of findings |

Metrics should focus on external contributors, reproducible reports, issue response time and coverage. GitHub stars can be tracked but are not a guarantee or priority above security quality.

## 9. Sustainability

Recommended license: Apache-2.0 after verifying dependencies and fixture licenses. Maintain security policy and coordinated disclosure channel. Release cadence: patches as necessary; minor releases tied to test improvements and clear migration notes. Prefer sponsorship/donations or paid support for enterprise integrations over withholding fundamental security checks behind a closed tier.

## 10. Launch checklist

- [ ] Name and package availability checked (not presumed).
- [ ] Installation from clean Linux works and is documented with actual commands.
- [ ] Screenshot/video uses actual software, not a mocked UI presented as released.
- [ ] Clear platform/adapter support table and limitation warning.
- [ ] Reproducible 5-case mock run with synthetic assets only.
- [ ] Security and privacy docs accessible from README.
- [ ] At least one independent reviewer verifies a FAIL evidence chain.
- [ ] CONTRIBUTING and safe scenario policy ready.
- [ ] `good first issue` backlog and discussion channel ready.
- [ ] Every public claim corresponds to recorded, current evidence.
