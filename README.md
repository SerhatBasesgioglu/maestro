# Conductor

A reusable, synchronous Python 3.11+ library for internal development, DevOps,
infrastructure, and delivery operations. The library is the product: no CLI,
pipeline runtime, API service, or MCP server is required to use or test it.

**Implemented integrations:** remote IIS application pools and Windows services,
Azure DevOps Git and pipeline runs, Argo CD applications, SonarQube projects/quality
gates, and Linux systemd/rsync/Docker Compose APIs over SSH.
Kubernetes and cross-platform workflows remain planned; there are no empty
integration placeholders or mandatory consumer wrappers.

## Install

Publish to your approved private Python feed, then pin the version in consumers:

```powershell
python -m pip install "conductor[iis]==0.1.0"
python -m pip install "conductor[windows]==0.1.0"
python -m pip install "conductor[azure-devops,argocd,sonarqube]==0.1.0"
python -m pip install "conductor[linux]==0.1.0"
```

The base distribution has no runtime dependencies. IIS remoting and Windows
service management use optional `pypsrp` 0.9.x, selected by the `iis` and `windows`
extras respectively. Windows Kerberos uses SSPI and does not require the non-Windows
Kerberos extra. For a separately configured Linux/macOS Kerberos caller, install
`conductor[iis,kerberos]`; native GSSAPI/Kerberos prerequisites are also needed.
Non-Windows live authentication has not been validated for this project.

The independent `azure-devops`, `argocd`, and `sonarqube` extras use HTTPX 0.28.x.
Install only the integrations you need. Importing or constructing any client
does not load its optional HTTP/remoting dependency or open a connection.

Linux SSH/rsync commands use installed system tools rather than a Python SSH SDK.
The optional `linux` extra supplies ruamel.yaml 0.18.x **only for Compose image-file
edits**. Importing Linux clients does not load YAML libraries or start processes.

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

## Remote Linux APIs

These are independent APIs, **not a deployment/service workflow**. Legacy file
uploads do not stop/start systemd services; Compose operations never invoke rsync.
Consumers decide sequencing, authorization, restoration, and target serialization.

```python
from conductor.linux import ComposeProject, LinuxClient
from conductor.transports import OpenSshTransport, SshConfig

ssh = OpenSshTransport(
    SshConfig(
        host="linux-test.company.local",
        username="deploy",
        identity_file="/secure/conductor/id_ed25519",
        known_hosts="/secure/conductor/known_hosts",
        command_timeout=120,
    )
)
linux = LinuxClient(transport=ssh, timeout=120)
# sudo=True explicitly requests sudo -n for systemd/Compose, never for rsync.
```

| API | Contract |
| --- | --- |
| `linux.services.get_state("legacy.service")` | Read typed systemd active state; missing units raise `ServiceNotFoundError`. |
| `linux.services.stop("legacy.service")` / `.start(...)` | Dispatch one blocking `systemctl` command; success is not app health, and no restoration/polling is added. |
| `linux.files.upload("app.jar", "/opt/legacy/app.jar", dry_run=False)` | Rsync one local regular file over the same verified SSH target; no directory mirroring, deletion, source removal, or sudo. |
| `linux.compose.down(project)` | Stop/remove this project's containers/networks; no volume/image deletion or orphan-removal option is requested. |
| `linux.compose.up(project)` | Detached `up --no-build`; no readiness guarantee. |
| `linux.compose.set_image(project, "api", "registry/app:v2", expected_image="registry/app:v1")` | Edit one service's remote YAML image only; does not pull, restart, run Compose, or upload with rsync. |

Construct an explicit project for each Compose call:

```python
project = ComposeProject(
    compose_file="/opt/container-app/compose.yaml",
    project_name="container-app",
    # env_file="/opt/container-app/deploy.env",  # Explicit when needed.
)
```

### Linux prerequisites and limits

- The real process adapter requires a **POSIX caller** with trusted OpenSSH, and
  patched rsync **3.x on both ends** for uploads. The bundled macOS rsync 2.x is
  unsupported; configure `rsync_executable` to an installed current version.
  No rsync daemon or remote-to-remote/local fallback is used.
- Supply a DNS/IPv4 host, account, non-interactive private-key file, and an
  independently verified known-hosts file. Keep credentials outside the repository.
  SSH configuration, password/agent authentication, proxies, forwarding, automatic
  host-key enrollment, and command retries are disabled. IPv6/jump hosts and
  encrypted-key prompting are not supported by this initial adapter.
- The target needs systemd for service calls, writable destination directories for
  uploads, and Docker Compose **v2** for container calls. Compose uses an explicit
  remote Unix Docker socket, file, project, and project directory. Implicit `.env`
  loading is disabled; supply `env_file` when needed. Other Compose YAML includes,
  interpolation, hooks, mounts, and Docker registry/config behavior remain native
  target behavior: approve the file and target environment before invoking it.
- `sudo=True` uses non-interactive `sudo -n`; it neither configures permissions nor
  makes arbitrary SSH/Compose source a sandbox. Rsync still runs as the SSH user.
- Image edits additionally require `conductor[linux]` on the caller and Python 3
  on the remote Linux target. The file must be UTF-8, regular, single-linked, at
  most 1 MiB, without special permission bits. Final symlinks and whole-service
  anchors/aliases are refused. Comments/quotes are preserved; formatting may change.
- Image edits use a fingerprint, a persistent sibling `.conductor.lock` advisory
  lock, and same-directory atomic replacement. Owner/mode/xattrs are preserved or
  the edit fails; inode/mtime change. Cooperating Conductor writers are serialized,
  but **external editors must also coordinate**; a fingerprint is not a distributed
  lock or a guaranteed CAS against unrelated writers. This supports one YAML file,
  not an effective multi-file/include/interpolated Compose model.
- Timeouts cap each local SSH/rsync process; image reads/edits share one monotonic
  budget. Process startup and cleanup may add elapsed time. Timeout/interruption
  kills the local process group, **not necessarily the remote command**. A failed
  call may already have changed service/container/file state; inspect before replay.
- `LinuxCommandError` and `RsyncError` retain only exit status. Shared
  `TransportError` covers SSH's ambiguous status 255 (including host-key/auth errors).
  Raw stdout/stderr/file contents are omitted from error text and result reprs.
  Do not expose process arguments, wire diagnostics, or traceback-local dumps.

Offline tests capture subprocess arguments and fake remote responses; the helper
is also exercised against isolated temporary files. No live Linux target has been
contacted. Host-key/authentication, sudo rules, Linux metadata/ACL/SELinux handling,
Docker/systemd behavior, and actual transfers still need approved live validation.

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

## Remote Windows services

Windows service management is an independent API over the existing caller-owned
`PowerShellRemoting` transport; it adds no transport or deployment workflow. Reuse
the same trusted HTTPS, authentication, and transport-lifecycle rules described
above for IIS:

```python
from conductor.transports import PowerShellRemoting
from conductor.windows import WindowsClient

with PowerShellRemoting(
    endpoint="https://win02.company.local:5986/wsman",
    auth="kerberos",
) as transport:
    windows = WindowsClient(transport=transport, timeout=60)
    state = windows.services.get_state("ConductorDisposable")
    stopped = windows.services.stop("ConductorDisposable")
```

Use the exact installed service name, not its display name. The remote helper
constructs `ServiceController` with its default constructor, then sets
`ServiceName` explicitly, avoiding display-name fallback; the name is also passed
as a separately bound scalar parameter. Names are limited to at most 256 UTF-16
code units; unpaired surrogates are rejected. The target needs Windows
PowerShell 5.1 or later and an authorized, full-language remoting endpoint that
supports `Add-Type`, .NET, and native SCM calls. Constrained Language Mode and JEA
are not made compatible merely by selecting their endpoint. Conductor and Python
remain on the caller.

| Method | Contract |
| --- | --- |
| `services.get_state(name, timeout=None)` | Read a `ServiceState`; no mutation. |
| `services.start(name, timeout=None)` / `services.stop(name, timeout=None)` | One remote operation returning `ServiceChangeResult(name, initial_state, final_state, changed)`. |

The native states are `Stopped`, `StartPending`, `StopPending`, `Running`,
`ContinuePending`, `PausePending`, and `Paused`, plus `Unknown`. Starting is a no-op
when already `Running`; it dispatches only from `Stopped` and rejects paused,
pending, or unknown states (there is no resume operation). Stopping is a no-op
when already `Stopped`; it dispatches from `Running` or `Paused` and rejects
pending or unknown states. A start can cause the Service Control Manager to start
ordinary dependencies, but Conductor never enables a disabled service. Stop never
forces or cascades a stop to active dependent services.

Start uses `ServiceController.Start()`; stop uses a constant C# helper loaded with
`Add-Type` to call `OpenSCManager`, `OpenService`, and
`ControlService(SERVICE_CONTROL_STOP)`. The helper closes its SCM/service handles.
An active dependent service is left running and the native SCM rejection
(`ERROR_DEPENDENT_SERVICES_RUNNING`, 1051) is reported as a dependency error.

These calls do not poll for stability, sequence deployment work, restore a prior
state, or promise service/application health. `final_state` is the final observation
and may still be transitional. A reported success is not a readiness guarantee.
The existing remoting transport uses cooperative timeouts and does not replay
requests; a timeout or connection loss cannot prove that an issued command did not
complete. The native stop call may block while the service handles the control;
the cooperative deadline is not hard cancellation. No library polling does not
mean the native call cannot wait. Inspect target state before deciding whether to
try again.

Missing services, invalid states, active dependents, disabled services, native
command failures, and protocol failures map to `ServiceNotFoundError`,
`ServiceStateError`, `ServiceDependencyError`, `ServiceDisabledError`,
`ServiceCommandError`, and `ServiceProtocolError` from `conductor.windows`.
Authentication and access denial use the shared `AuthenticationError` and
`PermissionDeniedError` from `conductor.common.errors`. Raw remote error text is
not exposed in these errors.
No live Windows service target has been validated for this release; see
[VERIFICATION.md](VERIFICATION.md) for the offline claim owner and live boundary.

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
uv sync --extra iis --extra windows --extra azure-devops --extra argocd --extra sonarqube --extra linux
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=conductor
uv run python -m build --no-isolation
```

Unit tests use fake transports and fake SDK objects. Packaging tests build a
wheel and sdist and install the wheel **without dependencies** in a clean venv;
the Windows API's public import and fake operation are checked without pypsrp.
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
