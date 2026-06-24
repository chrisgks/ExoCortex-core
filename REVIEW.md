# ExoCortex Review Contract

Use this file as the project-specific review contract when auditing ExoCortex.

## Scope

Review the whole repository as one system, not as disconnected folders.

Key surfaces:

- markdown control-plane files that define behavior and operator expectations
- wrapper runtime in `tools/wrappers/`
- session workers and automation scripts in `tools/workers/` and `tools/automations/`
- Mission Control backend in `tools/mission-control/backend/`
- Mission Control frontend in `tools/mission-control/frontend/`
- tests in `tests/`
- skill, agent, template, domain, journal, raw, and wiki structures when they affect runtime behavior, discoverability, or durable state

## Priority Risks

Prioritize findings that could cause:

- wrong context or agent resolution
- broken wrapper execution or CLI delegation
- missing, corrupt, or misleading session artifacts
- accidental writes to the wrong place in the markdown control plane
- mismatch between documented ExoCortex behavior and actual implementation
- backend and frontend contract drift
- false confidence from shallow or incomplete tests
- automation behavior that is unsafe, noisy, or hard to recover from

## Required Review Process

1. Read `README.md` and `AGENTS.md` first.
2. Read relevant local contracts such as `AGENT.md`, `WORKFLOWS.md`, and `SKILLS.md` when they shape runtime behavior.
3. Map the major subsystems before issuing conclusions.
4. Trace end-to-end flows, especially:
   - context discovery to harness launch
   - session capture to worker processing
   - durable artifact creation in journal and markdown surfaces
   - backend API behavior to frontend consumption
5. Run relevant tests, builds, lint, or direct verification commands where available.
6. If something cannot be verified, state the exact blocker.

## Findings Standard

- findings first, ordered by severity
- file references with line numbers for every finding
- explain the failure mode, not just the code smell
- separate confirmed issues from open questions
- treat contract mismatches as real findings when the contract defines product behavior

## Done Condition

The review is complete only when:

- high-risk subsystems have been checked
- attempted verification steps are reported
- unresolved uncertainty is listed under open questions or coverage gaps
- the final output includes a short release-readiness assessment
