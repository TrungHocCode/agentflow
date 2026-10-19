# AgentFlow Subagent Roster

Project-scoped subagents for `pi-subagents`. Agent definitions live in `.pi/agents/`, model routing in `.pi/settings.json`.

Delegation is not automatic: a subagent runs only when you ask for it.

## Roster

| Lane | Agent | Model / thinking | Use it when |
| --- | --- | --- | --- |
| Recon | `recon` | gpt-6-luna / low | You need a cheap, fast map of the code before deciding anything. |
| Design | `plan-architect` | deepseek-v4-pro / high | The change touches architecture, `State`, module boundaries, or an API/DB contract. |
| Backend write | `py-writer` | kimi-k2.7-code / high | Implementing under `backend/app/**`. |
| Frontend write | `frontend-writer` | gpt-6-luna / high | Implementing under `frontend/**`. |
| Tests | `test-writer` | deepseek-v4.1-flash / high | New coverage, or repairing a failing test. |
| Mechanical | `fix-runner` | gpt-6-luna / low | The change is fully specified: rename, lint fix, enumerated reviewer list. |
| Correctness | `review-correctness` | qwen3.8-max / high | Any diff. Answers "is it right, does it regress". |
| Contracts | `review-contract` | deepseek-v4-pro / high | Any diff touching `execution/` or a module boundary. |
| Security | `review-security` | glm-5.3 / high | Any diff touching tools, crawlers, HTTP, files, auth, or secrets. |
| Tests review | `review-tests` | gpt-6-luna / medium | Any diff that claims test coverage. |
| Performance | `review-performance` | kimi-k2.7-code / medium | Any diff adding an LLM call, a loop, or a query. |
| UI | `review-ui` | gpt-6-luna / medium | Any diff under `frontend/**`. |

## Why these models

- `kimi-k2.7-code` — code-specialized, 262K context. Owns multi-file implementation where the whole seam must fit in one head.
- `deepseek-v4-pro` — top reasoning tier at moderate cost. Reserved for design and contract judgement, where being wrong is expensive and being slow is not.
- `qwen3.8-max` — strongest adversarial reading. Owns correctness review, where a missed P0 is the only real failure mode.
- `glm-5.3` — best at "this looks fine but is actually an injection path". Owns security.
- `gpt-6-luna` — fast, cheap, 1M context, strong instruction-following at low thinking. The workhorse for recon, mechanical edits, frontend, and hygiene reviews.
- `deepseek-v4.1-flash` — cheapest competent tier. Owns test authoring, which is high-volume and well-bounded.

Rule: capability tiers for well-scoped work, reasoning tiers for judgement work.

## Standard flows

New backend feature:

```
recon → plan-architect → py-writer → test-writer
      → review-correctness + review-contract + review-tests → fix-runner
```

Risky tool or network change, add `review-security`.
Change that adds an LLM call or a loop, add `review-performance`.

Frontend change:

```
recon → frontend-writer → review-ui → review-correctness
```

Fully specified cleanup:

```
fix-runner → review-correctness
```

## Guardrails baked into the prompts

- Writers never edit `workspace_data/`, `.venv/`, `node_modules/`, `dist/`, `docs/` (gitignored), and never commit.
- `test-writer` is the only lane allowed to edit `tests/**`; it never weakens an assertion to reach green.
- Reviewers are read-only and must cite `path:line` with proof. They name the test command instead of running the suite.
- `py-writer` and `frontend-writer` escalate unapproved product, architecture, or scope decisions through `contact_supervisor` instead of deciding.
- Every lane knows the project's real constraints: two-phase execution, `State` reducers, registry pattern, `<tool_output>` wrapping, `workspace_data/` sandbox, and the unit suite's hard network block.
