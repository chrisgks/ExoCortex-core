# Reviewer Decision Rules

- If a claim is not backed by file evidence, downgrade it to an open question.
- If a subsystem cannot be verified, report the blocker explicitly instead of implying confidence.
- Prefer a smaller set of confirmed high-impact findings over a long list of speculative nits.
- Re-check the affected path after any substantive fix before clearing a finding.
- Treat documentation mismatches as real findings when the docs define runtime behavior or operator expectations.
