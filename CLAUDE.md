# CLAUDE.md

## Local LLM router

The `local-llm-router` MCP server sends tasks to local models.

- For boilerplate, small edits, tests and docstrings, call `local_chat` with `task_type: simple`.
- For longer or multi-file work, use `task_type: heavy`.
- Treat the output as a draft. Review it before using it.
- Handle architecture, security and subtle-bug reasoning yourself. Don't send it to the router.
- If `router_status` shows a backend down, do the task yourself.
