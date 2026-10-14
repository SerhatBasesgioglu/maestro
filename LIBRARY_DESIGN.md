# Maestro — Operations Library Design

This document records the library brief and the initial implementation choices.
It is not an agent-rules file. Unimplemented APIs below are proposed interfaces,
not functionality promised by version 0.1.0.

## Purpose and boundaries

Maestro is one reusable Python distribution (`maestro`) with the import
package `maestro`, using a `src/` layout. A script, pipeline, CLI, FastAPI app,
or MCP server may consume it; none is mandatory.

**The library owns:** API/SDK/remoting communication, explicit configuration and
credential interfaces, coherent resource operations, typed library-owned inputs
and results, meaningful exceptions, bounded timeouts, appropriate retries, state
checks, and safe mutation mechanisms. Workflows are added only for concrete,
repeated cross-platform requirements.

Library-owned exceptions share the `MaestroError` base class.

**Consumers own:** command-line/HTTP/MCP/pipeline inputs, configuration and secret
selection, caller authorization and approvals, output and exit-code translation,
Azure DevOps logging/output variables, deployment sequencing, target-level
serialization, and cleanup/restoration policy.

**Non-goals:** a pipeline framework; a mandatory central service; exhaustive API
wrappers; a universal model for unrelated platforms; a giant inherited client;
CLI/FastAPI/MCP dependencies in core; global connections; implicit targets;
hardcoded credentials; and automatic deployment rollback.

## Architecture

```text
CLI / FastAPI / MCP / pipeline / Python script
                         |
                         v
                   Maestro library
                  |                |
                  v                v
          Optional workflows   Individual integrations
                  |                |
                  +--------------->|
                                   v
                        API / SDK / remote transport
                                   |
                                   v
                              Target system
```

Directional dependencies:

```text
consumers -> workflows -> integrations -> shared infrastructure
consumers -------------> integrations
```

An integration never imports another integration to orchestrate a process.
Share genuinely common mechanisms without forcing distinct authentication or
transport semantics together. Kubernetes can use its maintained SDK; a class
named `Client` is not a reason to introduce inheritance.

### Current structure

```text
src/maestro/
  __init__.py                 # No SDK imports or connections
  common/                     # Error/validation/JSON helpers and lazy injectable HTTP
  transports/
    models.py                 # Library-owned scalar/error snapshots
    protocols.py              # Injectable synchronous execution seam
    powershell_remoting.py    # Validated config, ownership, lazy SDK loading
    _pypsrp.py                # Private WSMan/PSRP adapter and deadline boundaries
    ssh.py                    # Explicit host-key/key auth, bounded SSH/rsync processes
  iis/
    client.py                 # Borrowed-transport resource entry point
    application_pools.py      # State/start/stop contracts and polling
    models.py                 # Stable pool states and change results
    errors.py                 # IIS-specific exceptions
    _scripts.py               # Constant source; input is separately bound
  azure_devops/
    client.py, config.py       # Explicit organization/project and PAT/bearer auth
    repositories.py           # Reads, create branch/commit/PR, CAS ref movement
    pipelines.py              # Explicit branch/commit run submission and reads
    models.py, errors.py       # Typed Git/run contracts and business failures
  argocd/
    client.py, config.py       # Explicit server, AppProject, bearer token, namespace
    applications.py           # Read, request sync, deadline-checked health waits
    models.py, errors.py       # Application observations and health-timeout errors
  sonarqube/
    client.py, config.py       # Explicit server and caller-supplied bearer token
    projects.py                # Project reads via the component API
    quality_gates.py           # Project/analysis gate reads with explicit selectors
    models.py, errors.py       # Typed project/gate models and resource errors
  linux/
    client.py                 # Borrowed SSH transport; no deployment service layer
    services.py               # Independent systemd state/start/stop APIs
    files.py                  # Independent legacy single-file rsync upload
    compose.py                # Explicit remote Compose down/up and YAML image edits
    _yaml.py, _scripts.py     # Lazy round-trip YAML and atomic remote-file helper
    models.py, errors.py       # Typed command/file contracts and sanitized failures
  windows/
    client.py                 # Borrowed PowerShell remoting transport
    services.py               # Independent Windows SCM state/start/stop APIs
    _scripts.py               # Constant remote PowerShell source
    models.py, errors.py      # Typed native states/results and sanitized failures
  artifacts/
    filesystem.py             # Single-file staging on existing local/mounted paths
    nexus.py                  # Explicit Nexus raw-hosted streaming API
    transport.py, _httpx.py   # Injectable raw HTTP seam and lazy optional adapter
    _files.py, _validation.py # Bounded staging, hash verification, portable keys
    models.py, errors.py      # Artifact metadata and sanitized local errors
  openbao/
    client.py, config.py      # Explicit HTTPS target and caller-supplied token
    kv_v2.py                  # Read-only KV v2 point reads
    models.py                 # Secret data and KV v2 metadata
tests/
  unit/
  integration/                  # Opt-in IIS mutations and read-only HTTP smoke tests
  packaging/                  # Build/install without optional dependencies
```

Create resource/config/model/error modules only when functionality exists. Future
modules may be `kubernetes` and `workflows`.
There are intentionally no empty integration placeholders or consumer wrappers.

## Public API

Resource-oriented methods, explicit identifiers, typed parameters/results,
documented state changes, permissions, timeouts, exceptions, and ownership:

```python
# Implemented:
iis.application_pools.get_state(name)
iis.application_pools.stop(name)
iis.application_pools.start(name)

ado.repositories.get(...)
ado.repositories.get_branch(...)
ado.repositories.create_branch(...)
ado.repositories.move_branch_ref(..., expected_commit=..., new_commit=...)
ado.repositories.create_commit(...)
ado.repositories.create_pull_request(...)
ado.pipelines.run(...)
ado.pipelines.get_run(...)
argocd.applications.get(...)
argocd.applications.sync(...)
argocd.applications.wait_for_health(...)
sonar.projects.get(...)
sonar.quality_gates.get_status(...)
linux.services.get_state(...)
linux.services.start(...)
linux.services.stop(...)
linux.files.upload(...)
linux.compose.down(...)
linux.compose.up(...)
linux.compose.set_image(...)
windows.services.get_state(...)
windows.services.start(...)
windows.services.stop(...)
filesystem.publish(source, key, expected_sha256=None, overwrite=False)
filesystem.retrieve(key, destination, expected_sha256, overwrite=False)
nexus.publish(source, key, expected_sha256=None, timeout=None)
nexus.retrieve(key, destination, expected_sha256, overwrite=False, timeout=None)
openbao.kv_v2.read(mount, path, version=None, timeout=None)

# Proposed, not implemented:
k8s.workloads.get_deployment(...)
k8s.workloads.wait_for_rollout(...)
```

Avoid ambiguous `update_branch()`: commits, ref movement, and pull-request merges
have distinct safety contracts. Azure DevOps ref updates check the
expected previous commit. Do not expose SDK response objects as the public
compatibility promise.

**Chosen contract:** Python 3.11+, synchronous APIs only. Async consumers offload
blocking work. The normal IIS deployment topology is a Windows domain-connected
agent using Kerberos and trusted HTTPS; explicit WinRM HTTP/5985 is also supported
for approved use. Specific identity, DNS/SPNs, certificate trust, permissions,
server versions, and endpoint restrictions still require live checks.

## Configuration and safety

- Consumers supply explicit targets and credentials. Core methods do not parse
  arguments or discover pipeline variables. Platform credentials are not universal.
- OpenBao receives only a caller-supplied Vault token. Login, token TTL/rotation,
  environment selection, and any secret-to-configuration mutation remain consumer
  responsibilities.
- Keep credential values out of logs, representations, and operational errors.
  Maestro drops raw remote error text and invocation details. Third-party
  wire logging and application traceback-local collection remain caller risks.
- HTTP/API clients require TLS verification. WinRM accepts explicitly selected
  HTTPS or HTTP/5985; HTTPS can use a caller-supplied trusted CA bundle, while
  `ca_bundle` is rejected for HTTP. With pypsrp 0.9.1, HTTP uses
  `encryption="auto"` WSMan message wrapping, not TLS or certificate-backed server
  identity. No TLS verification bypass is provided for HTTPS clients.
- Use normal named library logging without root-logger configuration or handlers.
- Validate finite positive request/command/poll/cleanup budgets. Deadline checks
  are cooperative at SDK boundaries, not guaranteed hard cancellation.
- Do not blindly replay writes or retry authentication/permission failures. The
  remoting transport disables HTTP request replays and invalidates failed sessions.
  Separately, the shared HTTPX transport for API clients does not follow redirects,
  retry, or use environment proxies.
- Prefer idempotent state-setting methods, recheck state remotely, and use
  concurrency controls where a platform supports them. IIS has no pool-state CAS;
  consumers must serialize deployments and handle uncertain mutation outcomes.
- Explicit previews can describe mutations but do not replace authorization. No
  preview interface is introduced for IIS state-setting; Argo CD exposes its
  explicit server-side dry-run flag and disables pruning by default.

### HTTP integration boundaries

HTTP integrations share mechanics through composition, not a universal inherited
client. Configurations, authentication headers, resource operations, and typed
models remain platform-specific. HTTPX loads on the first real request; injected
library-owned transport protocols support operation without any optional SDK.
Clients own default transports, borrow injected ones, and do not discover secrets.

Azure DevOps uses REST 7.1 by default. Ref creation uses the zero old commit; ref
movement and pushes require an explicit expected commit. HTTP 200 ref updates
are checked for business failures. Pipeline submission is not completion, and PR
creation is not merging. No automatic deduplication or conflict resolution exists.

Argo CD uses explicit AppProject/optional application namespace targeting. Sync
is one request; health waits only observe state. Requiring an immutable observed
revision reduces stale-health ambiguity, but does not identify a unique sync
operation. Explicit multi-source revision vectors are not implemented.

SonarQube supports read-only project and gate observations. Gate selectors cannot
mix project and analysis IDs or branch and PR. Completed analysis IDs, not Compute
Engine task IDs, avoid relying on the latest concurrently changing project gate.
Scanner execution, CE waiting, and release approval are outside this slice.

OpenBao supports explicit, read-only KV v2 point reads. It sends the caller token in
`X-Vault-Token`, optionally sends an explicit namespace, and returns validated secret
data plus version metadata with sensitive values hidden from representations. An
omitted version means the server's latest version. A 404 means the requested value is
absent or unreadable; it does not distinguish absence, deletion, destruction, or
other unreadability. No login, token renewal/rotation, write, delete, list, environment
discovery, or deployment/workflow operation is provided.

All integrations retain HTTP error categories and reject malformed schemas. HTTP
timeouts bound I/O phases, not guaranteed elapsed time. The Argo health wait checks
a single monotonic deadline before/after requests and bounds polling sleeps.

## Independent Linux APIs

Linux resources share an injectable SSH command/file protocol, not a business
service layer. Each systemd command, rsync upload, Compose down/up, or YAML image
edit is independently callable. No API chains stop/copy/start or down/edit/up,
restores state, approves mutations, or introduces container readiness policy.

The default transport runs caller-installed OpenSSH/rsync on POSIX callers, with
explicit keys/known-hosts, no discovery or write replays, and finite process
budgets. Compose targets an explicit remote Unix Docker socket and one remote
file/project. Image edits parse with optional caller-side ruamel.yaml and use a
remote Python 3 standard-library helper for fingerprint checks, advisory locking,
metadata-preserving atomic replacement. External writers must still coordinate;
offline success does not establish host trust, authentication, target permissions,
Linux metadata handling, actual transfers, or Docker/systemd behavior.

## Independent Windows service API

Windows service management shares the existing injectable PowerShell remoting
protocol and caller-owned `PowerShellRemoting` lifecycle; it is not a new transport
or a workflow. `get_state(name, timeout=None)` reads a typed native service state.
`start` and `stop` each issue one remote operation and return
`ServiceChangeResult(name, initial_state, final_state, changed)`. No polling,
deployment/service sequencing, restoration, or health policy is included.

The API uses the exact installed service name (not display name), literal service
lookup, and separately bound scalar parameters. The remote helper constructs
`ServiceController` with its default constructor and sets `ServiceName` explicitly;
names are limited to 256 UTF-16 code units and reject unpaired surrogates. It
reports the seven native states—`Stopped`, `StartPending`, `StopPending`, `Running`,
`ContinuePending`, `PausePending`, and `Paused`—plus `Unknown`. Start is a no-op
from `Running`, calls `ServiceController.Start()` only from `Stopped`, and rejects
paused/pending/unknown states. Stop is a no-op from `Stopped` and dispatches only
from `Running` or `Paused`; a constant C# helper loaded with `Add-Type` calls
`OpenSCManager`, `OpenService`, and
`ControlService(SERVICE_CONTROL_STOP)`, then closes its handles. An active
dependent service is not stopped or cascaded; SCM error 1051 is reported as a
dependency failure. Pending/unknown states are rejected, and no resume operation
exists. The SCM may start ordinary dependencies during start, but Maestro never
enables disabled services. The observed final state may be transitional and is not
a health guarantee.

Missing, denied, active-dependent, disabled, native-command, and protocol failures
remain distinguishable without exposing raw remote messages. As with IIS, remote
timeouts are cooperative and a lost response does not prove a mutation was not
applied. `ControlService` may block while the service handles its control; the
transport deadline is not hard cancellation. One remote mutation call and no
library polling do not imply that the native call cannot wait. A Windows PowerShell
5.1+ full-language endpoint supporting `Add-Type`, .NET, and native SCM invocation,
trusted HTTPS or explicitly selected HTTP/5985 with the existing authentication
options, and suitable service management rights are prerequisites. HTTPS certificate
verification is unchanged; HTTP message wrapping does not provide TLS server
identity. Constrained Language Mode/JEA is not compatible by endpoint selection
alone.

## Independent artifact storage APIs

Artifact operations store and retrieve one opaque file at a time; they do not deploy
to a target, unpack archives, mirror directories, or orchestrate a build/release.
Callers record the returned `ArtifactInfo(key, size, sha256)` in trusted release
metadata, then retrieve on the deployment agent using that expected digest. Copying
the retrieved local file to a Windows/Linux target is a separate target-specific
operation.

`FilesystemArtifacts(root)` works only with an already accessible caller path,
including an existing SMB UNC/mapped path or mounted NFS path. It does not mount a
share or discover authentication. The root, key parents, and local destination/
staging parents must exist before each call; their creation is caller-owned. Keys are portable ASCII
relative paths; traversal, reserved Windows names, and symlink or junction paths
are rejected. Reads/writes are bounded to 1 MiB chunks; digest checks complete
before promotion. Mounted operations may block; there is no hard operation timeout.
The default commit is no-clobber and fails closed when
the filesystem cannot provide the needed primitive. `overwrite=True` explicitly
uses `os.replace`. Existing file mode/ACL is not preserved on replacement; process
permissions, inherited ACLs, and umask remain caller policy. Local temporary-file
tests do not establish SMB/NFS atomicity, durability, or hostile-directory-race
safety.

`NexusRawArtifacts` uses an explicit `NexusRawConfig` and raw hosted repository path
(`/repository/{repository}/{key}`), with direct PUT/GET and preserved base-URL path
prefixes. The optional `nexus` extra supplies lazy HTTPX 0.28.x; the public narrow
`ArtifactHttpTransport`/`ArtifactHttpResponse` seam is injectable, and the default
`HttpxArtifactTransport` is owned while injected transports are borrowed. Credentials
are explicitly supplied as a username/password pair sent with HTTP Basic over
verified HTTPS; no bearer token or environment discovery is used. Publish snapshots
and hashes to private disk before one non-replayed PUT; HTTP streams use bounded 1 MiB
chunks, while upload staging requires artifact-sized scratch. Its returned digest is
local metadata, not a server checksum. A failed PUT may have completed. Nexus redeploy
policy remains authoritative: immutable keys require a repository configured to
Disable redeploy, and no client publish-side overwrite or HEAD/CAS guarantee exists.
Retrieval accepts only an exact 200 raw identity response, requires a trusted expected
digest, and verifies a local temporary file before promotion. HTTP phase timeouts are
not hard elapsed-time or cancellation guarantees.

`ArtifactIntegrityError` and `ArtifactFileError` distinguish digest and local-file
failures. Nexus additionally uses shared authentication, permission, conflict,
transport/timeout, and HTTP status errors; payloads and credentials do not appear in
errors or representations. Live mounted-share and Nexus server behavior remains a
separate target-owner validation boundary.

## Initial use case: remote IIS

```text
Machine 1: on-premises Windows pipeline agent
    installs/calls Maestro
    -> PowerShell Remoting / WinRM over HTTPS with Kerberos, or explicit HTTP/5985
       with WSMan message wrapping and an existing supported authentication mode
Machine 2: Windows IIS server
    executes IIS commands; no Python or Maestro installation required
```

Machine 2 needs IIS management components, `WebAdministration`, a configured
remoting endpoint, restricted network access, and a suitably authorized identity.
Maestro never invokes local PowerShell as a remote fallback.

### Pool contracts

- `get_state` returns a documented enum, including transitional and unknown states.
- `stop` is successful if already stopped; `start` if already started.
- Start/stop wait for confirmation under one total polling budget; transitions are
  deliberately observed rather than treated as stable states.
- Names are parameters, not source text; literal resource lookup precedes native
  `Get-WebAppPoolState`, `Stop-WebAppPool`, or `Start-WebAppPool` operations.
- Missing pool, denied access, authentication, TLS/transport, unexpected response,
  unsupported/unknown state, and state timeout failures remain distinguishable.
- Remote script mutations recheck stable state. Only one mutation request is
  dispatched per call. This limits write replay; it does not prevent races.
- The caller owns transport lifetime; IisClient does not close borrowed resources.

### Remoting security

Prefer Kerberos for the confirmed domain topology; integrated authentication
still depends on the actual agent service identity and correct setup. Secrets,
if used, come from the consumer. No wildcard trusted hosts, HTTPS
certificate-verification bypass, or credential delegation is provided. HTTP/5985
is explicit rather than a fallback from HTTPS; it uses pypsrp 0.9.1 message
wrapping but is not TLS and does not provide TLS certificate-backed server identity.
`ca_bundle` applies only to HTTPS. Existing Kerberos, Negotiate, and NTLM
authentication remain available; NTLM requires caller-supplied username/password.

Consider JEA with approved pool names and operations, but do not equate selecting
an endpoint with JEA compatibility. Standard NoLanguage JEA endpoints cannot
execute the current scripts. A later cmdlet-only transport/server contract needs
separate review. Access to a third machine is a separate double-hop problem.

## Pipeline consumption

Consumer scripts live in the consuming repository and install a pinned package
version from the approved feed. A CLI remains optional. They may read environment
variables and emit Azure DevOps logging commands; the core library may not.

Before stopping an initially STARTED pool, emit/store restoration intent so a
partially completed stop can still trigger cleanup. Never start an initially
stopped pool by accident, and explicitly decide how to handle transitions.

An Azure DevOps cleanup step can use:

```yaml
condition: and(always(), eq(variables['IIS_RESTORE_REQUIRED'], 'true'))
```

Restoration remains consumer policy. Deployment failure should trigger a cleanup
attempt, but agent/server loss can prevent it. Surface cleanup errors without
losing the original deployment failure. Restoring a running pool is not a health
check, rollback, or concurrency-control mechanism.

## Packaging and compatibility

One versioned distribution with optional dependencies; the base package has none.
Implemented extras: `maestro[iis]`, `maestro[windows]` (the same optional
pypsrp dependency as IIS), `maestro[nexus]` and `maestro[openbao]` (HTTPX
0.28.x), non-Windows Kerberos support via `maestro[iis,kerberos]`, and the
independent `azure-devops`, `argocd`, and `sonarqube` extras using HTTPX 0.28.x. The
`linux` extra adds ruamel.yaml only for Compose image edits; SSH/rsync use installed
POSIX system tools. Imports do not connect to targets, require local IIS/Windows
APIs, or eagerly import optional dependencies.

Use an approved private feed, pin consumer versions, and record public changes in
release notes. Follow Semantic Versioning; before 1.0, minor versions may change
the API incompatibly. Split distributions only when dependencies, ownership, or
release cadence justify it. Feed, ownership, licensing, and publication approvals
are organizational decisions, not assumed by this implementation.

## Verification

Unit tests replace transports/SDK objects and check parameter binding, response
conversion, exception mapping, already-satisfied states, transitions, timeouts,
write replay avoidance, ownership, and sensitive-data handling. No live target is
needed to test public library APIs.

Packaging tests build a wheel/sdist and install without optional dependencies in
a clean environment, including Windows public imports and a fake service read,
plus artifact filesystem/Nexus and OpenBao imports with no-SDK fake operations.
Adapter tests use the installed optional SDK but intercept remote calls. Live tests
are opt-in, require an explicit HTTPS or HTTP/5985 endpoint and confirmed disposable
pool, and attempt restoration of its initial stable state. `ca_bundle` is HTTPS-only.
Separate explicitly approved HTTP live smoke tests perform reads only. Real HTTPX
request construction is tested with MockTransport and sockets are blocked in unit
tests; base-wheel fake operations run
without any integration SDK. The Windows SCM behavior and real remoting/authentication
still require a separately approved target; no live Windows service target is claimed.

Offline tests cannot establish real Kerberos, HTTPS certificate trust, HTTP server
identity, permissions, IIS/Windows service/API server compatibility, JEA/RBAC
restrictions, mounted SMB/NFS semantics, Nexus repository policy, OpenBao
authentication/TLS/namespace/RBAC semantics, mutation/concurrency behavior, or
reliable remote cancellation.
See VERIFICATION.md for the claim owners and evidence boundaries.

## Sequence and remaining decisions

1. **Chosen:** Python 3.11+ and synchronous APIs.
2. **Chosen default topology:** Windows domain agent, Kerberos, and trusted HTTPS;
   explicitly selected HTTP/5985 is also supported for approved use, with message
   wrapping but no TLS certificate-backed server identity. Verify the actual agent
   identity, network, DNS/SPNs, trust where HTTPS is used, endpoint, and permissions.
3. **Implemented offline:** minimal remoting and IIS pool get/start/stop APIs;
    independent Windows service state/start/stop APIs.
4. Verify a disposable non-production pool, then add consumer-owned pipeline scripts.
5. **Implemented offline:** Azure DevOps Git/CAS ref updates and pipeline runs,
   Argo CD application reads/sync/health waits, and SonarQube projects/gates.
6. Validate approved HTTP targets and disposable mutations; add Kubernetes only
   when a concrete consumer requirement is authorized. **Implemented offline:**
    independent Linux systemd, rsync, and Compose APIs, plus independent filesystem
    and Nexus artifact storage; OpenBao KV v2 read-only API; validate approved SSH,
    mounted-share, Nexus, and OpenBao targets separately.
7. Add workflows after repeated requirements establish a useful contract.
8. Add independent CLI/API/MCP wrappers only if needed.

Still required: supported live server/caller combinations, agent service identity,
certificate trust and SPN setup, JEA choice/server contract, private package feed,
release ownership, licensing, and approvals for destructive operations.
