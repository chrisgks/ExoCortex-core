# Security Policy

## Scope

ExoCortex is a local-first toolchain. The highest-priority security and privacy issues are:

- accidental publication of private user data
- incorrect path handling that writes outside the intended repo
- wrapper behavior that leaks sensitive context into logs or prompts
- automation that mutates durable state unsafely

## Reporting

If you discover a security or privacy issue, do not open a public issue with sensitive details.

Report it privately to the project maintainers through the channel listed in the repository metadata or contact method used for releases.

## Disclosure Expectations

- Provide a clear description of the issue.
- Include reproduction steps when safe.
- Say whether the issue affects the public core, private-instance workflows, or both.
- Allow time for a fix before broad disclosure when the issue could expose user data.
