# Yogurt Social Injector

This native OpenClaw plugin registers `before_prompt_build`. For each eligible
user turn it calls the local Python bridge, receives `context_block`, and returns
it as `prependSystemContext`.

The plugin never rewrites `event.prompt`. If the Python process, SQLite database,
or social layer fails, the hook returns no modification and OpenClaw continues
its normal model flow.

Use the repository-level installer:

```bash
bash scripts/install-openclaw-adapter
```

See `docs/OPENCLAW_ADAPTER.md` for configuration, tests, user mapping, and rollback.
