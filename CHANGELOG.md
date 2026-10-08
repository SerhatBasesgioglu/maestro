# Changelog

## 0.1.0 — Unreleased

- Initial synchronous Python 3.11+ library with a dependency-free base package.
- Optional HTTPS/Kerberos PowerShell remoting with explicit connection ownership,
  trusted CA support, deadline checks, and no automatic HTTP request replays.
- Remote IIS application-pool state/start/stop, typed states/results, bounded
  polling, deliberate transition handling, and distinguishable failures.
- Fake-transport/SDK unit tests, isolated packaging tests, and opt-in disposable
  remote-pool integration tests.
- Library design, consumer boundaries, release policy, and verification limits.
- Independent Azure DevOps, Argo CD, and SonarQube clients with lazy optional
  HTTPX extras, explicit targets/tokens, TLS verification, and typed results.
- Azure DevOps repository/branch reads, compare-and-swap branch creation/movement,
  atomic commit pushes, pull-request creation, and pipeline run submission/reads.
- Argo CD application reads, explicit non-pruning sync requests/dry runs, and
  health/sync/revision polling under one cooperative deadline.
- SonarQube project and quality-gate reads with unambiguous project/analysis and
  branch/PR selection; no scanner execution or automatic release approval.
- Shared injectable HTTP transport, sanitized error categories, no redirects or
  write replays, dependency-free fake operations, and opt-in read-only API smoke tests.
- Independent Linux systemd state/start/stop, legacy single-file rsync upload, and
  Docker Compose v2 down/detached-up APIs over explicit key-based OpenSSH.
- Remote Compose YAML image edits with lazy `linux`/ruamel.yaml extra, expected-image
  checks, advisory locking, fingerprints, and metadata-preserving atomic replacement.
  No deployment/service layer, restart orchestration, directory mirroring, or volume
  deletion is introduced; live Linux targets remain unvalidated.
- Independent Windows service state/read and start/stop APIs over the existing
  PowerShell remoting transport, with typed native states/results, exact installed
  service-name binding, and distinct management errors. Start uses
  `ServiceController.Start()`; stop uses a constant Add-Type C# helper calling
  native SCM `ControlService(SERVICE_CONTROL_STOP)`, which does not force-stop
  dependent services. The optional `windows` extra selects the same `pypsrp`
  dependency as `iis`; no service sequencing, readiness/stability polling, or
  restoration is added. The target requires a compatible full-language Windows
  PowerShell 5.1+ endpoint.

Live Windows/domain authentication, IIS compatibility, and Windows service
management must be verified on an approved non-production target before deployment.
HTTP server versions, authentication, permissions, and mutation semantics also
require live validation. Kubernetes, workflows, CLI, API/MCP servers, and consumer
pipelines remain unimplemented.
