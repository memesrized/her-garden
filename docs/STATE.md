# Project state

Small single-household Python MCP service backed by PostgreSQL. Approved scope is in
`plant_memory_mcp_spec.md`; implementation adds authenticated HTTPS, location creation,
retry safety, corrections, Compose deployment and CI/CD for `master`.

| Area | File | Notes |
|---|---|---|
| Data flow | [system/data_flow.md](state/system/data_flow.md) | Modules and transaction boundaries |
| Decisions | [architecture/decisions.md](state/architecture/decisions.md) | Simplicity and tradeoffs |
| Operations | [system/operations.md](state/system/operations.md) | Deployment, backups, authentication |
| Remaining work | [progress/known_issues.md](state/progress/known_issues.md) | Limits and verification status |
| Changes | [changes/README.md](state/changes/README.md) | Active changelog |

Read this index first, then only the relevant detail file, then the active changelog.
Changelogs are append-only; add newer entries at the top. Rotate to the next numbered file
after 250 lines or 15 KB and update the active pointer. Never rewrite old entries.
