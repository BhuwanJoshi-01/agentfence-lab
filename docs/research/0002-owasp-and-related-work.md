# OWASP LLM01/LLM06 and related evaluation methods (AF-0002)

- **Checked:** 2026-11-07
- **Sources:** the OWASP Top 10 for LLM Applications 2025 pages for
  [LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) and
  [LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/),
  plus the READMEs of [AgentTrap](https://github.com/zhmzm/AgentTrap) and
  [Agent3σ-Canary](https://github.com/antgroup/Agent3Sigma-Canary), read from the GitHub
  API on that date. Statements about those projects come from their READMEs only. Their
  papers were not reviewed, and nothing of theirs was run.
- **Purpose:** check that the OWASP tags on our scenarios are justified, and learn what
  other runtime evaluators do and what they leave open. This is not a ranking.

## 1. OWASP LLM01:2025 Prompt Injection

Prompt injection is input that changes a model's behaviour or output in unintended ways.
The input does not have to be readable by a human. OWASP separates **direct** injection
(the user's own prompt) from **indirect** injection, where the model "accepts input from
external sources, such as websites or files" that carry the instructions. Its seven
mitigations are: constrain model behaviour; define and validate expected output formats;
input and output filtering; privilege control and least privilege; human approval for
high-risk actions; segregate and identify external content; and **adversarial testing and
attack simulations**.

**Relevance:** every bundled scenario is indirect injection. The deviation always arrives
in workspace content the agent reads while doing a legitimate task: a README, a task note,
project text, a captured test log, a data file header. AgentFence is a tool for the
seventh mitigation. It does not implement the other six. It measures whether an agent's
configuration, which may include them, holds.

## 2. OWASP LLM06:2025 Excessive Agency

Excessive agency allows "damaging actions to be performed in response to unexpected,
ambiguous or manipulated outputs", whether the cause is hallucination or prompt injection.
Its root causes are excessive functionality, excessive permissions and excessive autonomy.
Among the mitigations are minimizing extensions and their permissions, avoiding
open-ended extensions such as a general shell or URL fetch, requiring user approval, and
**complete mediation**: authorization is enforced downstream, not left to the model.
OWASP lists logging, monitoring and rate limits as ways to limit damage that do not
prevent it.

**Relevance:** LLM06 explains why an injected instruction can turn into an action. The
boundary in each of our scenarios (write scope, protected read, network intent) is an
explicit statement of the least privilege the task needs. A `FAIL` means the agent used
more agency than the task needed. AgentFence's own collectors follow complete mediation:
the broker, not the agent, decides and logs protected reads (TB-15).

## 3. Our tags, checked

| Scenario | Tags | Assessment |
|---|---|---|
| `AF-README-001` | LLM01, LLM06 | Justified. Indirect injection in a README leads to a write outside the task scope. |
| `AF-CRED-001` | LLM01, LLM02, LLM06 | Justified for LLM01 and LLM06. LLM02 (sensitive information disclosure) is about a *read* of a synthetic secret, not a disclosure, so it fits only in the sense that the scenario is a precursor. This review covers LLM01 and LLM06; LLM02 is left as is. |
| `AF-NET-001` | LLM01, LLM06 | Justified. The injection asks for an outbound send, the "open-ended extension" case. |
| `AF-TOOL-001` | LLM01, LLM06 | Justified. The injection comes through tool output, which OWASP's "files" wording covers. |
| `AF-ARTIFACT-001` | LLM01, LLM05 | LLM01 is justified. LLM05 (improper output handling) was not reviewed here. |

No tag needs to change for LLM01 or LLM06.

## 4. AgentTrap (README, 2026-11-07)

- **Scope:** "a dynamic benchmark for evaluating whether LLM agents can use third-party
  skills while resisting malicious runtime behavior". The attacks arrive through third-party
  skills and the channels around them.
- **Size:** 141 executable tasks: 91 malicious and 50 benign utility tasks.
- **Outcomes:** `attack_succeeded`, `attack_blocked`, `attack_not_triggered`,
  `utility_incomplete`, `infra_issue`, `benign_correct`, `benign_false_positive`,
  `pending_judge`. `attack_not_triggered` "is not counted as a defense".
- **Judging:** "LLM-based and trajectory-based. There is no non-LLM verdict fallback".
- **Isolation:** a Docker sandbox image with a controlled workspace.
- **Paper:** arXiv 2605.13940.

## 5. Agent3σ-Canary (README, 2026-11-07)

- **Scope:** "security evaluation capabilities for AI Agents in realistic runtime
  environments", organized by risk entry and risk impact. It covers direct and indirect
  injection, skills poisoning and memory poisoning.
- **Agents:** OpenClaw (vanilla and with three security plugins), Hermes, NanoClaw.
- **Isolation:** each task runs "in an isolated environment" using Docker images.
  `--netlock`, which limits container egress to an allowlist, is "disabled by default".
- **Metrics:** attack success rate, security awareness rate, task success rate and average
  score. Auxiliary judge roles call OpenAI-compatible APIs.
- **Paper:** arXiv 2606.10484.

## 6. What this means for AgentFence

**Shared ground, confirmed.** Both projects keep a not-triggered outcome apart from a
defense, and both measure utility next to safety. AgentTrap also counts unnecessary
refusals on benign tasks. AgentFence's `NOT_TRIGGERED`, benign controls and separate
utility axis are the same ideas, not new ones, and the README should keep saying that.

**Where AgentFence differs in method:**

- **Verdicts are deterministic, from trusted collectors.** Both projects use LLM judges
  (AgentTrap says it has no non-LLM fallback). AgentFence's evaluator is rule-based over
  broker, snapshot and sink events, and returns `INCONCLUSIVE` when a required channel is
  missing. The trade-off: rules cannot judge semantic harm, which an LLM judge can attempt.
  A judge, in turn, adds variance and needs its own validation. Neither README reports judge
  agreement with human labels.
- **The isolation posture is fail-closed by default.** AgentFence refuses rootful engines
  and has no network in Tier 0. Canary's egress allowlist is opt-in. This difference is
  about defaults, not about which is safer in practice. AgentFence has not yet run a real
  agent in a container at all.
- **Scope is one developer's configured agent, offline first**, not a benchmark leaderboard.

**Gaps this review exposes in AgentFence:**

1. **Delivery channels.** Both projects inject through channels we do not cover yet:
   third-party skills, MCP-style flows, memory, browser or session state, and state across
   turns (AgentTrap); email, SMS and financial tools (Canary). Our five scenarios all come in
   through workspace files. Candidate future families: a skill or plugin manifest, an MCP
   tool description, and cross-turn workspace state.
2. **Scale and repetition.** 5 adversarial scenarios against AgentTrap's 91 malicious tasks.
   Our Wilson intervals already show how little five attempts say. `--repeat` exists, but
   the scenario count is the larger limit.
3. **Real agents.** Both evaluate real agent frameworks. AgentFence evaluates only its mock
   until the M8 gate. Every claim stays about the pipeline.

AgentTrap's `benign_false_positive` has a direct counterpart in our report summary:
"Benign controls incomplete" (`benign_incomplete_rate`). That is not a gap.

None of these gaps changes a current verdict. Item 1 is tracked in #129. Items 2 and 3 are already on the roadmap (AF-0708, M8).
