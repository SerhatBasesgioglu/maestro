# Conductor — Operations Library Design

This document records the library brief and the initial implementation choices.
It is not an agent-rules file. Unimplemented APIs below are proposed interfaces,
not functionality promised by version 0.1.0.

## Purpose and boundaries

Conductor is one reusable Python distribution (`conductor`) with the import
package `conductor`, using a `src/` layout. A script, pipeline, CLI, FastAPI app,
or MCP server may consume it; none is mandatory.

**The library owns:** API/SDK/remoting communication, explicit configuration and
credential interfaces, coherent resource operations, typed library-owned inputs
and results, meaningful exceptions, bounded timeouts, appropriate retries, state
checks, and safe mutation mechanisms. Workflows are added only for concrete,
repeated cross-platform requirements.

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
                   Conductor library
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
src/conductor/
  __init__.py                 # No SDK imports or connections
   common/                     # Error/validation/JSON helpers and lazy injectable HTTP
  transports/
    models.py                 # Library-owned scalar/error snapshots
    protocols.py              # Injectable synchronous execution seam
    powershell_remoting.py    # Validated config, ownership, lazy SDK loading
    _pypsrp.py                # Private WSMan/PSRP adapter and deadline boundaries
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
tests/
  unit/
   integration/                # Opt-in IIS mutations and read-only HTTP smoke tests
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

# Proposed, not implemented:
k8s.workloads.get_deployment(...)
k8s.workloads.wait_for_rollout(...)
```

Avoid ambiguous `update_branch()`: commits, ref movement, and pull-request merges
have distinct safety contracts. Azure DevOps ref updates check the
expected previous commit. Do not expose SDK response objects as the public
compatibility promise.

**Chosen contract:** Python 3.11+, synchronous APIs only. Async consumers offload
blocking work. IIS target deployment setup: a Windows domain-connected agent using
Kerberos and trusted HTTPS. Specific identity, DNS/SPNs, certificate trust,
permissions, server versions, and endpoint restrictions still require live checks.

## Configuration and safety

- Consumers supply explicit targets and credentials. Core methods do not parse
  arguments or discover pipeline variables. Platform credentials are not universal.
- Keep credential values out of logs, representations, and operational errors.
  Conductor drops raw remote error text and invocation details. Third-party
  wire logging and application traceback-local collection remain caller risks.
- Verify TLS; allow a caller-supplied trusted CA bundle, never an insecure bypass.
- Use normal named library logging without root-logger configuration or handlers.
- Validate finite positive request/command/poll/cleanup budgets. Deadline checks
  are cooperative at SDK boundaries, not guaranteed hard cancellation.
- Do not blindly replay writes or retry authentication/permission failures. The
  remoting transport disables HTTP replays and invalidates failed sessions. The
  shared HTTP transport does not follow redirects, retry, or use environment proxies.
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

All integrations retain HTTP error categories and reject malformed schemas. HTTP
timeouts bound I/O phases, not guaranteed elapsed time. The Argo health wait checks
a single monotonic deadline before/after requests and bounds polling sleeps.

## Initial use case: remote IIS

```text
Machine 1: on-premises Windows pipeline agent
    installs/calls Conductor
    -> PowerShell Remoting / WinRM over trusted HTTPS with Kerberos
Machine 2: Windows IIS server
    executes IIS commands; no Python or Conductor installation required
```

Machine 2 needs IIS management components, `WebAdministration`, a configured
remoting endpoint, restricted network access, and a suitably authorized identity.
Conductor never invokes local PowerShell as a remote fallback.

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
if used, come from the consumer. No wildcard trusted hosts, unencrypted auth,
TLS-verification bypass, or credential delegation is provided.

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
Implemented extras: `conductor[iis]`, non-Windows Kerberos support via
`conductor[iis,kerberos]`, and the independent `azure-devops`, `argocd`, and
`sonarqube` extras using HTTPX 0.28.x. Imports do not connect to targets, require
local IIS, or eagerly import optional dependencies.

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
a clean environment. Adapter tests use the installed optional SDK but intercept
remote calls. Live tests are opt-in, require an explicit HTTPS endpoint and
confirmed disposable pool, and attempt restoration of its initial stable state.
Separate explicitly approved HTTP live smoke tests perform reads only. Real
HTTPX request construction is tested with MockTransport and sockets are blocked
in unit tests; base-wheel fake operations run without any integration SDK.

Offline tests cannot establish real Kerberos, certificate trust, permissions,
IIS/API server compatibility, JEA/RBAC restrictions, mutation/concurrency behavior,
or reliable remote cancellation.
See VERIFICATION.md for the claim owners and evidence boundaries.

## Sequence and remaining decisions

1. **Chosen:** Python 3.11+ and synchronous APIs.
2. **Chosen topology:** Windows domain agent, Kerberos, trusted HTTPS. Verify the
   actual agent identity, network, DNS/SPNs, trust, endpoint, and permissions.
3. **Implemented offline:** minimal remoting and IIS pool get/start/stop APIs.
4. Verify a disposable non-production pool, then add consumer-owned pipeline scripts.
5. **Implemented offline:** Azure DevOps Git/CAS ref updates and pipeline runs,
   Argo CD application reads/sync/health waits, and SonarQube projects/gates.
6. Validate approved HTTP targets and disposable mutations; add Kubernetes only
   when a concrete consumer requirement is authorized.
7. Add workflows after repeated requirements establish a useful contract.
8. Add independent CLI/API/MCP wrappers only if needed.

Still required: supported live server/caller combinations, agent service identity,
certificate trust and SPN setup, JEA choice/server contract, private package feed,
release ownership, licensing, and approvals for destructive operations.
