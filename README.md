# Maestro

A reusable, synchronous Python 3.11+ library for internal development, DevOps,
infrastructure, and delivery operations. The library is the product: no CLI,
pipeline runtime, API service, or MCP server is required to use or test it.

**Implemented integrations:** remote IIS application pools and Windows services,
filesystem/Nexus artifact storage, Azure DevOps Git and pipeline runs, Argo CD
applications, SonarQube projects/quality gates, read-only OpenBao KV v2 access, and
Linux systemd/rsync/Docker Compose APIs over SSH.
Kubernetes and cross-platform workflows remain planned; there are no empty
integration placeholders or mandatory consumer wrappers.

## Install

The declared initial version, 0.1.0, has not been published. The planned release
channel is downloadable wheel and source-distribution assets on GitHub Releases,
not installation from a Python package index. The release workflow is not active
until it is merged to the default branch. See [RELEASING.md](RELEASING.md) for the
release status and installation guidance; until an asset is available, use the
development setup below.

The base distribution has no runtime dependencies. IIS remoting and Windows
service management use optional `pypsrp` 0.9.x, selected by the `iis` and `windows`
extras respectively. Windows Kerberos uses SSPI and does not require the non-Windows
Kerberos extra. For a separately configured Linux/macOS Kerberos caller, install
`maestro[iis,kerberos]`; native GSSAPI/Kerberos prerequisites are also needed.
Non-Windows live authentication has not been validated for this project.

The independent `azure-devops`, `argocd`, `sonarqube`, `nexus`, and `openbao` extras
use HTTPX 0.28.x. Install only the integrations you need. Importing or constructing
any client does not load its optional HTTP/remoting dependency or open a connection.

Linux SSH/rsync commands use installed system tools rather than a Python SSH SDK.
The optional `linux` extra supplies ruamel.yaml 0.18.x **only for Compose image-file
edits**. Importing Linux clients does not load YAML libraries or start processes.

## HTTP integrations

Clients require explicit HTTPS targets and caller-supplied tokens. Tokens below
are variables supplied by the consumer from its approved secret source, not
credentials discovered by Maestro. **Authorize mutations before invoking them.**
These clients are independent; no IIS connection or deployment workflow is needed.

### Azure DevOps

```python
from maestro.azure_devops import AzureDevOpsClient, AzureDevOpsConfig

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

Azure DevOps can reject a ref update inside HTTP 200. Maestro checks the per-ref
result: stale expected commits/locks/name conflicts raise `BranchConflictError`,
permission rejections raise `PermissionDeniedError`, and other rejections raise
`BranchUpdateError` with `update_status`. Writes are not retried or deduplicated;
after an uncertain push, PR creation, or pipeline submission, inspect server state
before attempting again. Server-side branch policies remain authoritative.

### Argo CD

```python
from maestro.argocd import ArgoCdClient, ArgoCdConfig

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
from maestro.sonarqube import SonarQubeClient, SonarQubeConfig, QualityGateStatus

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

### OpenBao KV v2 read-only

`maestro[openbao]` supplies the default HTTPX 0.28.x adapter. The token and any
namespace/CA settings come explicitly from the consumer; Maestro does not read
environment variables, log in, renew or rotate tokens, or change consumer
configuration:

```python
from maestro.openbao import OpenBaoClient, OpenBaoConfig

bao_token = ...  # Supplied by the consumer from an approved secret source.
config = OpenBaoConfig(
    server_url="https://openbao.example.net/platform",
    token=bao_token,
    namespace="team/app",  # Optional X-Vault-Namespace.
    ca_bundle="/etc/maestro/openbao-ca.pem",  # Optional trusted PEM CA.
)

with OpenBaoClient(config) as bao:
    secret = bao.kv_v2.read("secret", "apps/widget", version=2)
    widget_password = secret.data["password"]
    # Pass the value to its intended consumer; do not print or log it.
```

| Read | Contract |
| --- | --- |
| `kv_v2.read(mount, path, version=None, timeout=None)` | One KV v2 point read returning `KvV2Secret(data, metadata)`. An omitted version asks OpenBao for its latest version; a supplied version must be positive. |

The client issues `GET {server-prefix}/v1/{mount}/data/{path}`. Nested mount and
secret paths retain their slash-separated components while each component is URL
encoded. The response envelope is validated before returning `secret.data` and
`secret.metadata` (`version`, `created_time`, `deletion_time`, `destroyed`, and
optional `custom_metadata`). Secret data and custom metadata are hidden from model
representations. `X-Vault-Token` is the explicit caller token; an explicitly
configured namespace adds `X-Vault-Namespace`.

The API is read-only: there is no login, token renewal/rotation, write, delete, list,
environment loading, discovery, or deployment workflow. HTTP 403 means access is
denied. A 404 reports an absent or unreadable version; it does not promise to
distinguish absent, deleted, destroyed, or otherwise unreadable data. Caller-owned
token TTL/rotation policy remains outside Maestro. HTTP phase timeouts do not
promise a hard elapsed-time deadline or cancellation. No live OpenBao authentication,
TLS, namespace, permission, or server compatibility is claimed.
See the [OpenBao KV v2 API](https://openbao.org/docs/api/secret/kv/kv-v2/) for the
server endpoint contract.

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
  `maestro.common.http.HttpTransport` is borrowed and never closed by the client;
  its implementer owns TLS, timeouts, retries, and sanitized errors. The
  `HttpResponse` model supports dependency-free fake transports.
- Instances are not thread-safe. Closed clients cannot be reused. Tokens, commit
  content, pipeline variable values, and OpenBao secret/custom-metadata values are
  excluded from relevant representations;
  external error bodies/messages are not chained into library errors. Avoid wire
  logging and traceback-local dumps in credential-bearing consumers.

## Remote Linux APIs

These are independent APIs, **not a deployment/service workflow**. Legacy file
uploads do not stop/start systemd services; Compose operations never invoke rsync.
Consumers decide sequencing, authorization, restoration, and target serialization.

```python
from maestro.linux import ComposeProject, LinuxClient
from maestro.transports import OpenSshTransport, SshConfig

ssh = OpenSshTransport(
    SshConfig(
        host="linux-test.company.local",
        username="deploy",
        identity_file="/secure/maestro/id_ed25519",
        known_hosts="/secure/maestro/known_hosts",
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
- Image edits additionally require `maestro[linux]` on the caller and Python 3
  on the remote Linux target. The file must be UTF-8, regular, single-linked, at
  most 1 MiB, without special permission bits. Final symlinks and whole-service
  anchors/aliases are refused. Comments/quotes are preserved; formatting may change.
- Image edits use a fingerprint, a persistent sibling `.maestro.lock` advisory
  lock, and same-directory atomic replacement. Owner/mode/xattrs are preserved or
  the edit fails; inode/mtime change. Cooperating Maestro writers are serialized,
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
from maestro.iis import IisClient
from maestro.transports import PowerShellRemoting

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
back to the agent machine. Endpoints explicitly select HTTPS or HTTP: an omitted
HTTPS port defaults to 443, while an omitted HTTP port defaults to 5985. Use an
explicit `:5986` for a normal WinRM HTTPS endpoint; HTTP/5985 is also supported,
for example `http://iis02.company.local:5985/wsman`. Maestro does not retry an
endpoint using another scheme.

For an HTTP/5985 proof of concept, this read-only call uses caller-supplied NTLM
credentials; populate the variables from an approved secret source:

```python
from maestro.iis import IisClient
from maestro.transports import PowerShellRemoting

with PowerShellRemoting(
    endpoint="http://iis02.company.local:5985/wsman",
    auth="ntlm",
    username=winrm_username,
    password=winrm_password,
) as transport:
    iis = IisClient(transport=transport)
    print(iis.application_pools.get_state("ConductorDisposable"))
```

The transport uses pypsrp 0.9.1 `encryption="auto"`, which applies WSMan message
wrapping over HTTP; this is not TLS and provides no TLS certificate-backed server
identity. HTTP needs no TLS certificate, and `ca_bundle` is HTTPS-only and rejected
for HTTP. This read-only example does not replace the disposable-pool confirmation
required for live mutations.

### Required environment

- Machine 1: Windows domain-connected agent, Python, and Maestro. For Kerberos,
  it must work for the **actual agent service identity**, not just an interactive
  user.
- Machine 2: Windows IIS server, Windows PowerShell with `WebAdministration` and
  the IIS provider, and an enabled remoting endpoint.
- Correct DNS/SPN configuration and restricted network access from approved agents.
  For HTTPS, use a trusted certificate and supply a PEM CA bundle if necessary;
  certificate verification cannot be disabled. HTTP has no TLS certificate or
  certificate-backed server identity; use it only where explicitly approved.
- The caller identity needs remoting access and permission to read/manage the
  intended pools. Python and Maestro are **not** installed on machine 2.

Kerberos is the default. Existing `auth="negotiate"` may fall back to NTLM;
`auth="ntlm"` requires a caller-supplied username and password. Obtain credentials
in the consumer from an approved secret source. No Basic or anonymous mode is
provided. Credential delegation is disabled, proxies are bypassed, and no
third-machine/double-hop access is assumed.

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

IIS exceptions are available from `maestro.iis`; shared categories are in
`maestro.common.errors`. Project-owned exceptions derive from `MaestroError` in
that module, the library's base exception:

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

Maestro never configures the root logger or prints credentials. Credentials,
remote result payloads, and remote error identifiers are excluded from relevant
representations; external exception messages/bodies are not chained into its
errors. **Do not enable pypsrp wire-level DEBUG logging or traceback-local dumps
in credential-bearing processes**: those third-party/application facilities can
expose sensitive data.

## Remote Windows services

Windows service management is an independent API over the existing caller-owned
`PowerShellRemoting` transport; it adds no transport or deployment workflow. Reuse
the same endpoint, authentication, and transport-lifecycle rules described above
for IIS, including its HTTPS/HTTP-specific requirements:

```python
from maestro.transports import PowerShellRemoting
from maestro.windows import WindowsClient

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
are not made compatible merely by selecting their endpoint. Maestro and Python
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
ordinary dependencies, but Maestro never enables a disabled service. Stop never
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
`ServiceCommandError`, and `ServiceProtocolError` from `maestro.windows`.
Authentication and access denial use the shared `AuthenticationError` and
`PermissionDeniedError` from `maestro.common.errors`. Raw remote error text is
not exposed in these errors.
No live Windows service target has been validated for this release; see
[VERIFICATION.md](VERIFICATION.md) for the offline claim owner and live boundary.

## Artifact staging and delivery

Artifact storage is independent of deployment. Build an archive, publish that one
file to an already accessible filesystem or Nexus raw hosted repository, and record
the returned `ArtifactInfo.sha256` in trusted release metadata. A deployment agent
retrieves the file with that expected digest, then uses a separate target-specific
copy/deployment operation. Maestro does not copy directly from storage to a
Windows/Linux target, unpack archives, mirror directories, or orchestrate a release.

| Method | Contract |
| --- | --- |
| `FilesystemArtifacts(root).publish(source, key, expected_sha256=None, overwrite=False)` | Stage one file under an existing root and return `ArtifactInfo(key, size, sha256)`. |
| `FilesystemArtifacts(root).retrieve(key, destination, expected_sha256, overwrite=False)` | Verify one stored file against the required digest before local promotion. |
| `NexusRawArtifacts(config).publish(source, key, expected_sha256=None, timeout=None)` | Snapshot/hash one file and issue one raw-repository PUT; returns local digest metadata. |
| `NexusRawArtifacts(config).retrieve(key, destination, expected_sha256, overwrite=False, timeout=None)` | Stream one raw response, verify the required digest, then promote locally. |

### Existing filesystem share

`FilesystemArtifacts` uses a path the caller can already access: for example an
existing SMB UNC/mapped path on Windows or an already-mounted NFS path. It does not
mount shares, discover them, or discover credentials. Root, key-parent, and
destination-parent directories must exist; the caller creates them:

```python
from pathlib import Path
from maestro.artifacts import FilesystemArtifacts

root = Path("/mnt/approved-artifacts")  # Existing accessible mount/root.
(root / "releases/1.2.0").mkdir(parents=True, exist_ok=True)
key = "releases/1.2.0/widget.zip"

# Build/store agent:
stored = FilesystemArtifacts(root).publish("dist/widget.zip", key)
release_sha256 = stored.sha256  # Persist in trusted release metadata.

# Separately, on an agent that can access the same share:
root = Path("/mnt/approved-artifacts")  # Its existing path to that share.
key = trusted_release_record.key
release_sha256 = trusted_release_record.sha256  # Supplied by the consumer.
destination = Path("incoming/widget.zip")
destination.parent.mkdir(parents=True, exist_ok=True)
FilesystemArtifacts(root).retrieve(
    key,
    destination,
    expected_sha256=release_sha256,
)
# Copy/deploy destination to the target using a separate target-specific operation.
```

Filesystem reads and writes use bounded 1 MiB chunks. Publish optionally checks a
caller-known digest; retrieval requires the digest from trusted release metadata. Data is staged
to a temporary file alongside its destination and the digest is verified before
promotion. The default no-overwrite commit uses a native no-clobber operation and
fails closed if the share cannot support it; `overwrite=True` explicitly uses
`os.replace`. Errors before promotion leave the prior destination intact. Replacement
uses native permissions, ACL inheritance, and umask; the old file's mode/ACL is not
preserved. There is no universal SMB/NFS server-side atomicity or crash-durability
guarantee, and mounted filesystem calls may block without a hard operation timeout.
Use trusted caller-controlled roots/parents: this is not a sandbox against hostile
directory races. Only one file (including an archive as opaque bytes) is handled per
call. Keys are portable ASCII relative paths; traversal, reserved Windows names, and
symlink/junction paths are rejected.

### Nexus raw hosted repository

Install the optional `nexus` extra for the default HTTPX 0.28.x transport. The
configuration timeout defaults to 30 seconds; per-call timeouts may be supplied.
Supply the repository endpoint and credentials explicitly from the consumer's
approved secret source:

```python
from pathlib import Path
from maestro.artifacts import NexusRawArtifacts, NexusRawConfig

config = NexusRawConfig(
    base_url="https://nexus.example.net/nexus",
    repository="raw-release",
    username=nexus_username,
    password=nexus_password,
    ca_bundle="/etc/maestro/nexus-ca.pem",  # Omit when system trust is sufficient.
)
key = "releases/1.2.0/widget.zip"

# Build/store agent:
with NexusRawArtifacts(config) as raw:
    stored = raw.publish("dist/widget.zip", key)
release_sha256 = stored.sha256  # Persist with trusted release metadata.

# Separately, on the deployment agent, construct config from its explicit
# endpoint/CA settings and approved credentials, then load trusted release metadata:
config = NexusRawConfig(
    base_url="https://nexus.example.net/nexus",
    repository="raw-release",
    username=nexus_username,
    password=nexus_password,
    ca_bundle="/etc/maestro/nexus-ca.pem",
)
key = trusted_release_record.key
release_sha256 = trusted_release_record.sha256  # Supplied by the consumer.
destination = Path("incoming/widget.zip")
destination.parent.mkdir(parents=True, exist_ok=True)
with NexusRawArtifacts(config) as raw:
    raw.retrieve(key, destination, expected_sha256=release_sha256)
# Transfer/deploy destination separately; raw storage does not target the host.
```

This is a direct raw-hosted `PUT`/`GET` at
`/repository/{repository}/{key}`; any context prefix in `base_url` is retained.
Authentication is HTTP Basic using the configured username/password pair, not a
bearer token. Nexus user-token `tokenname`/`passcode` credentials are used as the
Basic username/password pair, not as a bearer token. HTTPS certificate verification
is mandatory; `ca_bundle` supplies a trusted PEM CA file. There is no implicit
environment credential discovery. The `NexusRawArtifacts` default transport is lazy
and owned (use `with` or `close()`); an injected `ArtifactHttpTransport` is borrowed.
The public transport seam is
`ArtifactHttpTransport`/`ArtifactHttpResponse`, with `HttpxArtifactTransport` as the
optional default implementation.

Nexus upload/download streams use bounded 1 MiB chunks. Publish snapshots and hashes
the local file into private disk scratch before one non-replayed `PUT`; this needs
scratch space for an artifact-sized snapshot. The returned digest is local metadata,
not a Nexus checksum receipt. An uncertain PUT may have completed, so inspect
repository state before retrying. Nexus repository
policy remains authoritative: configure a raw hosted repository to **Disable
redeploy** when immutable release keys are required. `Allow redeploy` permits a
later PUT to replace the existing key; there is no publish-side overwrite flag or
HEAD-based compare-and-swap guarantee. The `overwrite` option applies only to the
local retrieve destination. Retrieval accepts only an exact HTTP 200 raw
identity stream (no redirect, partial response, or compression), hashes while
streaming to a local temporary file, and promotes only after the required digest
matches. HTTP request-phase timeouts do not guarantee total elapsed time or cancel
an in-flight server operation.

Filesystem failures include `ArtifactFileError`, `ArtifactIntegrityError`, and the
shared `PermissionDeniedError` for native access denial. Nexus also maps shared
`AuthenticationError`, `PermissionDeniedError`, `ConflictError`, `TransportError`/
`TransportTimeoutError`, and `HttpResponseError`/`RateLimitError` categories from
`maestro.common.errors`.
Error text and representations do not expose artifact payloads or credentials.
No live SMB/NFS share or Nexus server has been validated; see
[VERIFICATION.md](VERIFICATION.md) for evidence owners and boundaries.

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
uv sync --extra iis --extra windows --extra nexus --extra openbao \
  --extra azure-devops --extra argocd --extra sonarqube --extra linux
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=maestro
uv run python -m build --no-isolation
```

Unit tests use fake transports and fake SDK objects. Packaging tests build a
wheel and sdist and install the wheel **without dependencies** in a clean venv;
Windows, artifact, and OpenBao public imports/fake operations are checked without
pypsrp or HTTPX.
Unit tests block network connections; HTTP adapter tests use real HTTPX with
`MockTransport`. No CLI, pipeline service, Windows host, mounted SMB/NFS share, or
network target is required.

### Hosted CI

Both GitHub Actions and Azure Pipelines are configured for offline checks on Ubuntu
24.04 with Python 3.11 and 3.14. Each uses uv 0.12.15 and a locked environment with the
`iis`, `windows`, `nexus`, `openbao`, `azure-devops`, `argocd`, `sonarqube`, and
`linux` extras; native Kerberos is not installed. CI runs Ruff lint and format
checks, mypy, the offline unit/adapter/packaging tests with a 95% coverage floor
and builds a wheel and sdist. JUnit XML
is written to `test-results/junit.xml`, coverage XML to `coverage.xml`, and the
built distributions to `dist/`.

These outputs are internal CI run artifacts, not Python-feed publication. Ordinary
CI needs no secrets, live-test flags, AWS access, publishing, or deployment
permissions; AWS and live SSH/WinRM tests remain separate approved manual future
work. The workflows do not change the library's runtime feature limits. No hosted
run is claimed here.

GitHub CI runs on pushes to `main` and pull requests targeting `main`. Initial
activation requires committing and pushing the workflow and namespace changes on a
feature branch and opening a PR to `main`; a push to that feature branch alone does
not trigger either event. Manual `workflow_dispatch` runs are available only after
the workflow is present on the default branch. Local or uncommitted workflow files
are not active. After the first runs, use the actual emitted check names if
configuring required checks on `main`; the branch is not claimed to be protected
already.

To connect Azure Pipelines, use Azure DevOps Services and its hosted Ubuntu 24.04
agents. Open **Pipelines > New pipeline > GitHub**, then have a repository
owner/admin authorize the Azure Pipelines GitHub App or service connection for only
`SerhatBasesgioglu/maestro` and this pipeline. Select the existing
`.azure-pipelines/ci.yml` from the feature branch or, after merge, from the default
branch. An Azure DevOps organization/project and this repository authorization are
required; registration is pending, and no Azure hosted run is claimed. No Git mirror
is needed. Azure YAML PR triggers work with GitHub. If the YAML is instead used
with Azure Repos, configure PR build validation as a branch policy.

Live IIS tests are opt-in, use Kerberos, and require an explicitly confirmed disposable
pool on a non-production target. The HTTPS example is:

```text
uv run pytest tests/integration --run-iis-integration \
  --iis-endpoint https://iis-test.company.local:5986/wsman \
  --iis-pool ConductorDisposable \
  --iis-confirm-pool ConductorDisposable
```

For HTTP, use `--iis-endpoint http://iis-test.company.local:5985/wsman` instead and
omit `--iis-ca-bundle`; the same opt-in and exact pool confirmation still apply.
`--iis-ca-bundle` is only for HTTPS. Tests attempt to restore the initial stable
state. Live authentication, permissions, HTTPS certificate trust (or the lack of
TLS server identity over HTTP), server compatibility, and cancellation behavior
still require this environment; offline test success is not evidence of those
claims. See [VERIFICATION.md](VERIFICATION.md) for the evidence boundaries.

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

## Versioning and releases

Version 0.1.0 is currently declared but unpublished. See
[RELEASING.md](RELEASING.md) for the version policy, GitHub Release procedure, and
changelog updates. GitHub Release assets are not a Python package-index
publication.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
