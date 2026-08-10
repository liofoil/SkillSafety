# Vendored OpenClaw adapter

OpenClaw was evaluated through a custom Harbor adapter; it was not included
in the Harbor 0.3.0 wheel. The retained runtime copy had SHA-256:

```text
82d0a0eb8e3f7957c65f2ab4917585b9d866815dc5d18bfc055e33c28038fd97
```

Across 1,853 retained OpenClaw trial records, 1,749 version probes reported
`OpenClaw 2026.4.15 (041266a)`, 104 were unknown, and none reported a
different version. The adapter's installation command fixed
`openclaw@2026.4.15`; its floating `nvm install 22` resolved to Node
v22.22.3 in the retained runtime.

`openclaw.py` preserves the agent interaction while making three
publication/reproduction changes:

1. Harbor loads it through the official `--agent-import-path` interface,
   so no installed-package enum or factory files are modified.
2. Node is pinned to the recovered observed v22.22.3 runtime rather than a
   floating major version.
3. The post-run copy of the complete `~/.openclaw` directory is removed
   because that directory contains the provider credential. The two agent
   turns still write their JSON/text output to Harbor's agent log.

The third change occurs after both agent turns and does not alter the task
workspace or verifier execution.
