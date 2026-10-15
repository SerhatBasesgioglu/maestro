# Changelog

## 0.1.0 — Unreleased

- Initial synchronous Python 3.11+ library with a dependency-free base package.
- Optional PowerShell remoting over HTTPS or explicit HTTP/5985, retaining
  Kerberos/Negotiate/NTLM authentication; HTTPS-only CA support and pypsrp 0.9.1
  message wrapping over HTTP (not TLS), with explicit ownership, deadline checks,
  and no automatic scheme fallback or HTTP request replays.
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
- Single-file artifact staging/retrieval on an explicitly accessible filesystem,
  with bounded streaming, SHA-256 verification before local promotion, and
  fail-closed no-overwrite behavior by default. SMB/NFS are used only through
  existing caller-mounted/access paths; no mount/auth discovery or directory
  mirroring is added.
- Optional `nexus` extra for raw hosted Nexus PUT/GET with explicit HTTPS, Basic
  authentication, a lazy injectable streaming transport, trusted-digest retrieval,
  and no write replays. Repository redeploy policy remains authoritative; artifact
  storage does not copy to deployment targets or orchestrate releases.
- Optional `openbao` HTTPX extra for explicit, read-only OpenBao KV v2 point reads.
  Caller-supplied Vault tokens/namespaces, lazy default transport, injectable borrowed
  HTTP transport, and secret-safe typed results; no login, token lifecycle,
  environment discovery, writes, or deployment workflow.
- Distribution and import namespace is `maestro`, renamed from the prior `conductor`
  namespace.
- Prepare a manually triggered GitHub Release workflow for the declared version,
  with distribution assets and checksums; it is not active until merged to the
  default branch.

Live Windows/domain authentication, IIS compatibility, and Windows service
management must be verified on an approved non-production target before deployment.
Nexus server/authentication/repository policy and mounted SMB/NFS behavior also
require separately approved target validation.
HTTP server versions, authentication, permissions, and mutation semantics also
require live validation. Kubernetes, CLI, API/MCP servers, and product-level
consumer/deployment workflows remain unimplemented.
