# {{DOMAIN_TITLE}} Projects

Create one subfolder per serious {{DOMAIN_NAME}} project.

Each project starts with its static-identity files and grows the rest lazily — a
contract file exists only once it has real content to hold:

- `README.md` (static identity, created with the project)
- `AGENT.md` (static identity, created with the project)
- `STATE.md` (live state — add it once there is current work to track)
- `MEMORY.md` (accumulated judgment — add it once there is a durable lesson)
- `DECISION RULES.md` (accumulated judgment — add it once a stable rule earns a place)
- optional `SKILLS.md` (pointers to reusable, invocable capabilities)
- optional `wiki/`
- optional `artifacts/`
- optional local `journal/`

A reusable sequence graduates to a **skill**, not a `WORKFLOWS.md` file — that
contract is retired.
