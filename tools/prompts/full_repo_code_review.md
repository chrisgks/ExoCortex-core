Use the `code-review` skill as the default review workflow.

Treat `REVIEW.md` as the ExoCortex-specific review contract.

Perform a full-repository code review of this project as a principal engineer doing a pre-release audit.

Requirements:

1. Read `README.md`, `wiki/00_meta/Operating Contract.md`, and `REVIEW.md` first.
2. Review the whole repository, not just recently changed files.
3. Run relevant tests, lint, build, or direct verification commands where available.
4. Continue the review until the reviewer workflow stop condition is met.
5. Return findings first, ordered by severity, with file references and line numbers.

Do not lead with a summary. Do not pad with praise. Keep open questions and coverage gaps separate from confirmed issues.
