# Conductor

A reusable, synchronous Python 3.11+ library for internal development, DevOps,
infrastructure, and delivery operations. The library is the product: no CLI,
pipeline runtime, API service, or MCP server is required to use or test it.

**Implemented integrations:** remote IIS application pools, Azure DevOps Git and
pipeline runs, Argo CD applications, and SonarQube projects/quality gates.
Kubernetes and cross-platform workflows remain planned; there are no empty
integration placeholders or mandatory consumer wrappers.

## Install

Publish to your approved private Python feed, then pin the version in consumers:

```powershell
python -m pip install "conductor[iis]==0.1.0"
python -m pip install "conductor[azure-devops,argocd,sonarqube]==0.1.0"
```

The base distribution has no runtime dependencies. IIS remoting uses optional
`pypsrp` 0.9.x. Windows Kerberos uses SSPI and does not require the non-Windows
Kerberos extra. For a separately configured Linux/macOS Kerberos caller, install
`conductor[iis,kerberos]`; native GSSAPI/Kerberos prerequisites are also needed.
Non-Windows live authentication has not been validated for this project.

The independent `azure-devops`, `argocd`, and `sonarqube` extras use HTTPX 0.28.x.
Install only the integrations you need. Importing or constructing any client
does not load its optional HTTP/remoting dependency or open a connection.

No public-registry publication or feed configuration is included. Confirm your
private feed's package-name availability, release ownership, and approval policy
before publishing. Version 0.1.0 is the initial development release.

## HTTP integrations

Clients require explicit HTTPS targets and caller-supplied tokens. Tokens below
are variables supplied by the consumer from its approved secret source, not
credentials discovered by Conductor. **Authorize mutations before invoking them.**
These clients are independent; no IIS connection or deployment workflow is needed.

### Azure DevOps

```python
from conductor.azure_devops import AzureDevOpsClient, AzureDevOpsConfig

with AzureDevOpsClient(
    AzureDevOpsConfig(
        organization_url="https://dev.azure.com/my-organization",
        project="My Project",
        token=ado_token,
        auth="pat",  # Or "bearer" for a caller-supplied access token.
    )
) as ado:
    repository = ado.repositories.get("My Repository")
    branch = ado.repositories.get_branch(repository.id, "main")
    created = ado.repositories.create_branch(
        repository.id,
        "release/example",
        from_commit=branch.commit_id,
    )
    run = ado.pipelines.run(42, branch=created.name, commit=created.commit_id)
```

| Resource method | Contract |
| --- | --- |
| `repositories.get(repository)` | Read one repository by name or ID. |
| `repositories.get_branch(repository, name)` | Read an exact branch and current commit. |
| `repositories.create_branch(..., from_commit=...)` | Create only if absent; never replace an existing branch. |
| `repositories.move_branch_ref(..., expected_commit=..., new_commit=...)` | Compare-and-swap an existing ref; cannot delete it. |
| `repositories.create_commit(..., expected_commit=..., message=..., changes=...)` | Atomically create/push one commit using typed `CommitChange` inputs. |
| `repositories.create_pull_request(..., source_branch=..., target_branch=..., title=...)` | Create a PR; never merge or enable auto-complete. |
| `pipelines.run(pipeline_id, branch=..., commit=...)` | Submit one run, not wait for success; commit pinning is optional. |
| `pipelines.get_run(pipeline_id, run_id)` | Read typed run state/result. |

Commit changes support `GitChangeType.ADD`, `EDIT`, and `DELETE`, with repository
paths such as `/config/settings.json`. Add/edit content uses `GitContentType.RAW_TEXT`
or validated `BASE64`; delete changes cannot supply content. `PipelineVariable`
has an explicit `is_secret` flag; its value is excluded from representations.
Template parameters accept JSON values. No local Git checkout or shell is used.

Git writes need code/branch/PR permissions; ref moves may additionally require
force-push permission. Pipeline reads/submission need their respective permissions.
Branch names may be short or `refs/heads/...`; commits must be non-zero full SHA-1
identifiers. REST API 7.1 is the default; an on-premises collection URL and explicit
`api_version` can be supplied, but compatibility with that server needs validation.

Azure DevOps can reject a ref update inside HTTP 200. Conductor checks the per-ref
result: stale expected commits/locks/name conflicts raise `BranchConflictError`,
permission rejections raise `PermissionDeniedError`, and other rejections raise
`BranchUpdateError` with `update_status`. Writes are not retried or deduplicated;
after an uncertain push, PR creation, or pipeline submission, inspect server state
before attempting again. Server-side branch policies remain authoritative.

### Argo CD

```python
from conductor.argocd import ArgoCdClient, ArgoCdConfig

with ArgoCdClient(
    ArgoCdConfig(
        server_url="https://argocd.company.local",
        project="delivery",
        token=argocd_token,
        # application_namespace="team-apps",  # If explicitly needed.
    )
) as argo:
    application = argo.applications.get("my-app")
    requested = argo.applications.sync("my-app", revision=approved_commit)
    observed = argo.applications.wait_for_health(
        "my-app",
        revision=approved_commit,
        timeout=300,
    )
```

The explicit AppProject is included in requests and checked in results, preserving
the server's denied-vs-missing semantics. Read and sync need the corresponding
Argo CD RBAC permissions. `application_namespace` selects Applications outside
the normal control-plane namespace; it is not the workload destination namespace.

`sync()` requests one operation and returns an `Application` observation; it does
not promise completion. `prune=False` is the default; enabling it may delete
managed resources. `dry_run=True` explicitly requests a server preview. Specifying
single-source `revision` is supported; explicit multi-source revision vectors are not.

`wait_for_health()` performs reads only. By default it requires `HEALTHY`, `SYNCED`,
and no pending/running/terminating operation, under one monotonic polling budget.
`require_synced=False` is an explicit health-only policy. An optional exact observed
revision helps avoid accepting an older healthy deployment; use an immutable commit,
not a moving branch name. A single-revision wait rejects multi-source observations.
This is not a transaction tying a sync to its completion, nor an endpoint-health
probe. The returned `operation_phase` is an observation, not a success guarantee
for your particular request. Timeout raises `ApplicationHealthTimeoutError` with
the last observed application; authentication/transport failures propagate immediately.

### SonarQube

```python
from conductor.sonarqube import SonarQubeClient, SonarQubeConfig, QualityGateStatus

with SonarQubeClient(
    SonarQubeConfig(
        server_url="https://sonarqube.company.local",
        token=sonarqube_token,
    )
) as sonar:
    project = sonar.projects.get("org:project")
    gate = sonar.quality_gates.get_status(project.key, branch="main")
    # For a release, prefer an exact completed analysis supplied by the consumer:
    exact_gate = sonar.quality_gates.get_status(analysis_id=completed_analysis_id)
    passed = exact_gate.status is QualityGateStatus.OK
```

`projects.get()` uses the component API and rejects non-project components.
`quality_gates.get_status()` requires exactly one project key or completed analysis
ID. A project query can select a branch or pull request, but not both; these selectors
require a supporting server edition. An analysis ID is **not** a Compute Engine task
ID. Project queries observe the current gate and can race with concurrent analysis.

These are read-only APIs, not scanner/CE execution or release approval. Gate results
include typed `OK`, `WARN`, `ERROR`, and `NONE` states, conditions, and
`ignored_conditions`; consumers decide policy. `NONE` does not mean passed.
Use a bearer token with the permissions accepted by the target endpoint (project
Browse for reads; the gate endpoint also permits its documented administrative or
analysis permissions). SonarQube Web API V2 migration and server editions/versions
must be verified against your target.

### Shared HTTP contracts

- Default request timeout: 30s; per-call `timeout` can tighten but not extend it.
  HTTPX bounds connect/read/write/pool waits, **not total wall-clock elapsed time**.
  DNS, response trickling, and an in-flight request can extend elapsed time.
- TLS verification is mandatory; supply `ca_bundle` for an explicit PEM CA file.
  Deployment base paths are retained. Environment proxies, redirects, automatic
  retries, and implicit credential discovery are disabled by the default transport.
- HTTP 401/403/404 map to `AuthenticationError`, `PermissionDeniedError`, and
  `ResourceNotFoundError`; 409/412 map to `ConflictError`, and 429 to `RateLimitError`.
  Other non-success responses raise `HttpResponseError` with `status_code`, not a
  body or URL. Malformed JSON/schema raises `ProtocolError`.
- `TlsError`, `TransportError`, and `TransportTimeoutError` distinguish communication
  failures. A dispatched mutation may already have completed; failure is not proof
  of rollback, cancellation, or permission to replay it.
- Use `with` or `close()` for owned HTTP connections. An injected
  `conductor.common.http.HttpTransport` is borrowed and never closed by the client;
  its implementer owns TLS, timeouts, retries, and sanitized errors. The
  `HttpResponse` model supports dependency-free fake transports.
- Instances are not thread-safe. Closed clients cannot be reused. Tokens, commit
  content, and pipeline variable values are excluded from relevant representations;
  external error bodies/messages are not chained into library errors. Avoid wire
  logging and traceback-local dumps in credential-bearing consumers.

## Remote IIS usage

```python
from conductor.iis import IisClient
from conductor.transports import PowerShellRemoting

with PowerShellRemoting(
    endpoint="https://iis02.company.local:5986/wsman",
    auth="kerberos",
    # ca_bundle="company-ca.pem",  # Optional trusted internal CA bundle.
) as transport:
    iis = IisClient(transport=transport, timeout=60, poll_interval=1)
    state = iis.application_pools.get_state("MyAppPool")
    stopped = iis.application_pools.stop("MyAppPool")
    started = iis.application_pools.start("MyAppPool", timeout=45)
```

This **changes remote pool state**. Caller authorization and deployment approval
must happen before these calls. No methods execute IIS commands locally or fall
back to the agent machine. A URL without an explicit port uses HTTPS port 443;
include `:5986` for a normal WinRM HTTPS endpoint.

### Required environment

- Machine 1: Windows domain-connected agent, Python, and Conductor. Kerberos must
  work for the **actual agent service identity**, not just an interactive user.
- Machine 2: Windows IIS server, Windows PowerShell with `WebAdministration` and
  the IIS provider, and an enabled remoting endpoint.
- Trusted HTTPS certificate, correct DNS/SPN configuration, and restricted network
  access from approved agents. Supply a trusted PEM CA bundle if necessary;
  certificate verification cannot be disabled through this API.
- The caller identity needs remoting access and permission to read/manage the
  intended pools. Python and Conductor are **not** installed on machine 2.

Kerberos is explicit by default. `auth="negotiate"` may fall back to NTLM;
`auth="ntlm"` requires a caller-supplied username and password. Obtain credentials
in the consumer from an approved secret source. Credential delegation is disabled,
proxies are bypassed, and no third-machine/double-hop access is assumed.

`configuration_name` selects the remote PowerShell endpoint. **Selecting a JEA
endpoint does not by itself make these operations JEA-compatible:** the current
adapter submits scripts, whereas typical JEA endpoints use `NoLanguage` and
allow-listed commands. A constrained endpoint needs separately reviewed server
functions and a cmdlet-based adapter; do not weaken JEA to accommodate these scripts.

### IIS method contracts

| Method | Result and behavior |
| --- | --- |
| `get_state(name)` | `ApplicationPoolState`: `STARTED`, `STOPPED`, `STARTING`, `STOPPING`, or `UNKNOWN`. No mutation. |
| `start(name)` | `ApplicationPoolChangeResult`; waits until `STARTED`, no-op if already started. |
| `stop(name)` | `ApplicationPoolChangeResult`; waits until `STOPPED`, no-op if already stopped. |

Change results have `name`, `initial_state`, `final_state`, and `changed`.
`changed` records whether this call's remote command reported issuing a native
start/stop, not whether an unrelated caller changed the pool. Results do not
indicate application health or deployment success.

Start/stop wait through either transitional state before deciding whether to
mutate, and refuse to mutate `UNKNOWN`. A mutation rechecks state remotely and
issues a native command only from the opposite stable state. There is at most
one mutation request per call; concurrent interference may cause a timeout rather
than repeated writes. This is not a distributed lock: serialize deployments
targeting the same pool in your consumer.

Names are separately bound PowerShell parameters. Empty names, control characters,
path separators, and wildcard characters are rejected. This intentionally limits
some unusual IIS names rather than risking provider-path traversal or multi-pool
selection. Spaces, Unicode, apostrophes, and other literal names are supported.

### Errors

IIS exceptions are available from `conductor.iis`; shared categories are in
`conductor.common.errors`:

- `ApplicationPoolNotFoundError`: the named remote pool is absent.
- `IisPermissionDeniedError` / shared `PermissionDeniedError`: denied resource or
  remoting access.
- `AuthenticationError`, `TlsError`, `TransportError`: authentication,
  certificate/TLS, or communication/protocol failure.
- `TransportTimeoutError`: remoting command/connection timed out; an already
  dispatched write may have completed.
- `ApplicationPoolTimeoutError`: polling could not confirm the target state;
  attributes include `name`, `desired_state`, `last_state`, and `timeout`.
- `ApplicationPoolStateError`: unknown state, so no further mutation is attempted.
- `IisCommandError`: other management failures, including missing IIS components.
- `IisProtocolError`: invalid/unexpected remote result.
- `ConfigurationError`, `MissingDependencyError`: invalid configuration or a
  missing optional dependency. Invalid pool names raise `ValueError`.

Authentication, denied access, and failed writes are not retried. A transport
failure invalidates its session; reopening is explicit and does not replay an
operation. Check the remote state and apply consumer policy before deciding what
to do after an uncertain mutation.

### Timeouts and ownership

Defaults: connection 5s, HTTP read 10s, remote command/open 30s, cleanup 5s,
total pool operation 60s, poll interval 1s. All are finite and positive. Per-call
transport timeouts can tighten but cannot extend its configured command ceiling.
Pool operations share a monotonic deadline across reads, mutation, and polling.

Remoting checks deadlines before each WSMan request and each command poll; SDK
HTTP request retries are disabled. These are **cooperative timeouts**, not a
hard real-time cancellation guarantee. An in-flight request, authentication/DNS
processing, or a peer trickling response bytes can delay a deadline check.
Pipeline/session cleanup has its own finite budget and may add time. A timeout
or connection loss does not prove that the remote command was cancelled.

Transport construction performs no network I/O and does not import pypsrp.
Use its context manager or explicitly call `open()` and `close()`. IIS clients
borrow the transport; they never open or close it. Sessions are reused while
healthy, and instances are not thread-safe. Normal cleanup errors are surfaced;
if an operation is already failing, a cleanup error is logged without replacing
the original error. The transport does not restore application-pool state.

Conductor never configures the root logger or prints credentials. Credentials,
remote result payloads, and remote error identifiers are excluded from relevant
representations; external exception messages/bodies are not chained into its
errors. **Do not enable pypsrp wire-level DEBUG logging or traceback-local dumps
in credential-bearing processes**: those third-party/application facilities can
expose sensitive data.

## Consumers stay thin

Consumers select targets and credentials, authorize mutations, decide deployment
sequencing/restoration, and translate results into output. Environment variables,
Azure DevOps logging commands, HTTP responses, CLI parsing, and MCP arguments
belong there, not in the library.

A pipeline should record restoration intent **before** stopping an initially
running pool. Preserve initially stopped pools, handle transitions deliberately,
serialize shared targets, and attempt cleanup after failure without obscuring the
original deployment error. Agent loss still prevents guaranteed restoration.
See [LIBRARY_DESIGN.md](LIBRARY_DESIGN.md) for the design and roadmap.

Async consumers should offload these synchronous calls to a worker thread rather
than block their event loop. No async API is maintained in this initial version.

## Develop and verify

With [uv](https://docs.astral.sh/uv/) installed:

```text
uv sync --extra iis --extra azure-devops --extra argocd --extra sonarqube
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=conductor
uv run python -m build --no-isolation
```

Unit tests use fake transports and fake SDK objects. Packaging tests build a
wheel and sdist and install the wheel **without dependencies** in a clean venv.
Unit tests block network connections; HTTP adapter tests use real HTTPX with
`MockTransport`. No CLI, pipeline service, Windows host, or network target is required.

Live IIS tests are opt-in, use Kerberos, and require an explicitly confirmed disposable
pool on a non-production target:

```text
uv run pytest tests/integration --run-iis-integration \
  --iis-endpoint https://iis-test.company.local:5986/wsman \
  --iis-pool ConductorDisposable \
  --iis-confirm-pool ConductorDisposable
```

Add `--iis-ca-bundle company-ca.pem` for an internal CA. Tests attempt to restore
the initial stable state. Live authentication, permissions, certificate trust,
server compatibility, and cancellation behavior still require this environment;
offline test success is not evidence of those claims. See
[VERIFICATION.md](VERIFICATION.md) for the evidence boundaries.

Read-only live checks for the three HTTP clients are separately opt-in:

```text
uv run pytest tests/integration/test_http_clients.py --run-http-integration \
  --http-confirm-nonproduction --http-integration-config /secure/http-integration.local.json
```

The caller creates this secret JSON file outside the repository; the indicated
basename is also ignored by Git. Omitted integration sections are skipped:

```json
{
  "azure_devops": {
    "config": {
      "organization_url": "https://dev.azure.com/approved-test-organization",
      "project": "Test Project",
      "token": "REPLACE_FROM_APPROVED_SECRET_SOURCE"
    },
    "repository": "DisposableRepository"
  },
  "argocd": {
    "config": {
      "server_url": "https://argocd-test.company.local",
      "project": "test-delivery",
      "token": "REPLACE_FROM_APPROVED_SECRET_SOURCE"
    },
    "application": "test-app"
  },
  "sonarqube": {
    "config": {
      "server_url": "https://sonarqube-test.company.local",
      "token": "REPLACE_FROM_APPROVED_SECRET_SOURCE"
    },
    "project_key": "test:project"
  }
}
```

These illustrative targets are not approvals. The smoke tests do not create
branches/commits/PRs, submit pipelines, sync applications, or change projects.
Live mutation/concurrency, negative authentication/TLS/RBAC, server versions, and
restoration require separately approved disposable resources. Do not use pytest
`--showlocals` or share a secret-bearing config when reporting test failures.

## Compatibility and releases

Python 3.11+; a synchronous public API; one distribution with independently
importable integrations. Use Semantic Versioning: before 1.0, incompatible API
changes require a minor version bump; patch versions are compatible fixes.
After 1.0, incompatible API changes require a major version. Document public
changes in [CHANGELOG.md](CHANGELOG.md), build/test before publishing, and pin
released versions in consumers. The approved private registry and release
ownership remain to be supplied by your organization.
