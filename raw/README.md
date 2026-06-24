# Raw Sources

`raw/` is the immutable source layer for the wiki.

- Put unprocessed files in `raw/inbox/`.
- Categorize mixed inbox files before ingesting them further.
- Put curated source files in `raw/sources/`.
- Put local images or attachments in `raw/assets/`.

The agent may read from these files, register them, and cite them. It should not rewrite their contents unless explicitly asked.

The default workflow is markdown-only. The LLM should read raw files, categorize them when necessary, and then create or update markdown pages in `wiki/` directly.
