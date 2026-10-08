# Verification evidence path

The first release claims independent library-only IIS, Windows service, Azure DevOps,
Argo CD, SonarQube, and Linux APIs, explicit remote targets/credentials, typed
results, safe parameter binding, concurrency-aware writes, meaningful failures,
and optional dependencies.
The highest-risk boundary is an uncertain remote mutation: success or failure of
the local request does not alone prove the final remote state, completed pipeline,
or successful deployment. Kubernetes/workflows remain outside this release.

## Recorded Windows/Linux validation — 2026-10-08

- macOS, Python 3.11.15: **664 passed, 5 skipped**, **98%** Python statement/branch
  coverage after the Windows service safety corrections. Four skips are the
  gated live IIS/HTTP tests; one is the Linux-only xattr test. Ruff lint/format,
  strict mypy (56 source files), lock consistency, and diff checks passed.
- Packaging tests built wheel/sdist and installed the wheel without dependencies
  in a clean environment, including Windows public imports and a fake service read.
- Windows focused tests: **115 passed**. PowerShell syntax parsing and compilation
  of the native stop helper passed without invoking the helper. These establish
  syntax, compilation, parameter/response contracts, and offline failure handling,
  not Windows Service Control Manager behavior or Windows PowerShell 5.1 runtime
  compatibility. Python coverage does not measure remote PowerShell or native code.
- No live target was contacted. Windows service identity/rights, exact-name lookup,
  dependency rejection, native stopping, and restoration require separately
  approved non-production Windows checks before deployment.

## Earlier Linux/API validation — 2026-10-08 (before Windows services)

- macOS, Python 3.11.15: **549 passed, 5 skipped**, **98%** Python statement/branch
  coverage. Four skips are the gated live IIS/HTTP tests; one is the Linux-only
  xattr test. Ruff lint/format, strict mypy (50 source files), lock consistency,
  and diff whitespace checks passed.
- Packaging checks built wheel/sdist and installed the wheel without dependencies
  in a clean venv. Linux imports, fake systemd/Compose operations, and actionable
  missing-YAML errors worked without loading ruamel.yaml or starting processes.
  The `linux` extra resolved to ruamel.yaml 0.18.17 in the development environment;
  this is not an isolated Linux-extra artifact installation or live-host claim.
- Captured OpenSSH/rsync argv/environment/input establish argument/host-key policy,
  explicit identity/target, timeout ceilings, sanitized failures, and no replay.
  Actual SSH/rsync launches and socket connections are blocked in the unit suite.
- The file helper executed locally on isolated temporary files: read/atomic
  replacement, owner/mode preservation, stale fingerprints, advisory lock
  contention, and unsafe-file refusal passed. On macOS the unavailable Python
  Linux xattr API is shimmed as an empty attribute set; ACL/SELinux/xattr behavior
  remains unestablished here. The helper is not counted as executed PowerShell,
  systemd, Docker, rsync, or live SSH evidence.
- No live target was contacted or mutated. The Linux changes were checked on
  Python 3.11/macOS only; supported runtime/caller/server combinations require
  separately repeated evidence. No service/deployment orchestration was added.
- This recorded 2026-10-08 run predates the Windows service API. Its counts and
  coverage are prior Linux/API evidence, not verification of Windows services.

## Earlier offline validation — 2026-10-07 (before Linux APIs)

- macOS, Python 3.11.15 and 3.14.6: **407 passed, 4 live tests skipped** on each runtime.
- Python 3.11 statement/branch coverage: **98%**; this measures Python behavior,
  not execution of remote PowerShell, REST server logic, or real TLS/authentication.
- Ruff lint/format checks, strict mypy, and the uv lock consistency check passed.
- Packaging tests built wheel/sdist and installed the wheel without dependencies
  in a clean environment. All public integrations imported and fake operations
  ran without pypsrp, HTTPX, or consumer-framework dependencies. Missing real-I/O
  dependencies raised actionable errors without opening a connection.
- A separate clean environment installed the built `conductor[iis]` wheel offline
  from cached dependencies and imported the pypsrp 0.9.1 adapter successfully,
  without opening a connection or changing root logging handlers.
- A separate clean environment installed the built
  `conductor[azure-devops,argocd,sonarqube]` wheel offline from cached dependencies.
  All clients remained lazy; the HTTPX adapter initialized/closed without sockets,
  pypsrp, or root-logger changes. This establishes artifact/extra isolation, not TLS.
- HTTP tests captured library-owned requests and exercised installed HTTPX 0.28.1
  using MockTransport. Assertions cover exact target/path/query/body/authentication,
  redirect refusal, TLS configuration, timeout ceilings, exception sanitization,
  owned/borrowed lifecycle, and request counts. Unit tests block socket connections.
- Live-test gate checks confirmed that IIS opt-in does not enable HTTP tests, and
  HTTP opt-in without explicit target configuration/non-production confirmation
  fails before making requests.
- No live integration target was contacted or mutated. Windows/Kerberos/IIS,
  API server versions, RBAC/token scopes, certificate trust, mutation concurrency,
  and restoration remain live-validation work. The Windows service API was not
  present then and is not covered by these earlier results.

These results establish the offline contracts below, not production readiness.

## Claim owners and minimum evidence

| Claim | Evidence owner | Minimum evidence | Limitation |
| --- | --- | --- | --- |
| Resource methods honor state contracts, transitions, deadlines, and avoid replaying writes | Unit suite: `test_application_pools.py` | Controlled transport responses and clock, captured operations/parameters | Does not execute Windows commands |
| Configuration, transport lifetime, failure invalidation, and secret-safe errors are correct | Unit suite: `test_remoting.py` | Fake owned sessions, invalid inputs, representation/log/traceback checks | Does not establish real authentication |
| SDK requests bind parameters, disable retries, map failures, and check budgets | Unit suite: `test_pypsrp_adapter.py` | Installed pypsrp, intercepted WSMan requests and fake pipeline/runspace objects | Does not establish protocol behavior on a server |
| HTTP target/authentication/encoding, TLS configuration, sanitized failures, lifecycle, and no replay are correct | Unit suite: `test_http.py` | Real HTTPX with MockTransport, controlled exceptions, captured headers/body/query/timeouts, dependency-free fakes | MockTransport does not perform a TLS handshake, DNS, or server authentication |
| Azure DevOps operations send explicit branch/commit inputs and reject failed or mismatched results | Unit suite: `test_azure_devops.py` | Captured CAS ref/push payloads, HTTP-200 conflicts/permission/policy failures, typed PR/run schemas | Does not prove server-side CAS/policies or pipeline execution |
| Argo CD targets an explicit project/namespace and health waits check state/revision/deadlines without sync/replay | Unit suite: `test_argocd.py` | Fake application observations and monotonic clock, pending/running operations, late response, bounded sleeps | A state observation cannot identify completion of a particular sync transaction |
| SonarQube gate selection is unambiguous and malformed results never become passing gates | Unit suite: `test_sonarqube.py` | Exact project/analysis/branch/PR selectors, typed statuses/conditions, invalid schema cases | Does not prove completed CE processing, server edition support, or release policy |
| SSH/rsync use explicit host/key/trust, safe quoting, finite process budgets, and no replay | Unit suite: `test_ssh.py` | Captured Popen argv/environment/input, fake exit/timeout/interruption cases, local process-group cleanup | Does not authenticate or verify a real server; POSIX adapter only |
| Linux resource methods remain independent, typed, bounded, and fail without workflow/replay | Unit suite: `test_linux.py` | Fake SSH commands/uploads, exact systemd/Compose targets, YAML edits, expected-image checks, shared edit clock | Does not run systemd, Docker, sudo, or rsync |
| Windows service calls honor native-state transitions, exact literal name binding, and distinct errors without polling or write replay | Unit suite: `tests/unit/test_windows_services.py` | Fake PowerShell calls and results across states, scalar parameter capture, error mapping and sanitized failures | Does not execute Windows PowerShell or establish actual SCM semantics |
| Remote file edits refuse unsafe layouts/conflicts and replace without partial in-place writes | Unit suite: `test_compose_file_helper.py` | Actual helper execution on temporary files, fingerprint conflicts, lock contention, metadata and unsafe-file cases | Non-Linux POSIX tests shim absent xattr APIs; Linux ACL/SELinux/xattrs and unrelated concurrent writers require separate evidence |
| Distribution builds and all base imports/fake operations need no SDK or target | Packaging suite: `tests/packaging/test_distribution.py` | Offline wheel/sdist build and no-dependency install in an isolated venv, including Windows public imports and a fake read | Extra resolution/platform authentication require separate checks |
| Actual Kerberos, IIS management, and initial-state restoration work | Target owner: opt-in integration suite | Approved non-production HTTPS endpoint and confirmed disposable pool | Agent loss/transport failures can defeat restoration |
| Windows remoting, service permissions, and native service/dependency behavior work on a target | Windows target owner | Approved non-production target on Windows PowerShell 5.1+, trusted HTTPS/authentication, and a disposable service | No live Windows service target has been contacted; fakes and packaging checks are not live evidence |
| HTTP clients can read actual API targets | Target owners: `test_http_clients.py` | Explicit secret config file, non-production confirmation, approved repo/application/project | Read-only smoke checks do not exercise CAS pushes, PR/pipeline creation, sync completion, or negative TLS/RBAC cases |
| Certificates, denied access, endpoint restrictions, and server versions meet deployment policy | Deployment/security owners | Positive and negative live checks with approved identities/certificates | Not established by a mocked request or a happy-path pool test |

No CLI, API, MCP, pipeline runtime, or production target is needed for the offline
path. A fake transport is the durable affordance for controlling and observing
state transitions. A fake SDK boundary and HTTPX MockTransport test adapter behavior
and wire request construction without a server. A clean venv establishes
import/dependency isolation more directly than the development environment.
Live checks are a stronger evidence path but require target-owner approval, real
service identities/versions, and disposable mutations; they are not substituted
with mocks or performed against illustrative/default targets.

## Offline checks

```text
uv sync --extra iis --extra windows --extra azure-devops --extra argocd --extra sonarqube --extra linux
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov=conductor
uv run python -m build --no-isolation
```

The packaging test builds using locally installed build tools (`--no-isolation`)
and installs its wheel with `--no-index --no-deps`. It checks public imports and a
fake-transport operations in the clean environment, not just archive contents.
All four live tests are skipped by default, with separate IIS/HTTP opt-in gates.

Before release, resolve/install the requested extras from the built wheel in
separate environments and run the offline suite on supported Python versions and Windows.
Installing the Linux/macOS `kerberos` extra additionally needs native libraries.
Repeat evidence when the relevant code, dependency version, target policy, or
runtime changes; do not treat old offline results as live environment evidence.

## Linux verification boundary

Linux verification uses the existing flat unit-test structure and no live-target
configuration. Unit tests block socket connections and actual ssh/rsync subprocess
launches; the file helper runs only against pytest-owned temporary files with the
local Python interpreter. This durable fake transport/process seam is the chosen
offline evidence path, stronger than inspecting command strings alone but weaker
than approved target tests. No deployment/service workflow has been added.

Live Linux validation needs an approved non-production host, independently trusted
known-hosts entry and identity, systemd unit/Compose project/disposable destination,
explicit sudo policy if needed, patched rsync 3.x at both ends, Compose v2, and
remote Python 3 for image edits. Target owners must verify host-key/auth negative
cases, permissions, Linux metadata/ACL/SELinux preservation, transfers, Compose
behavior, uncertain mutations, and coordination with external file writers. No
default host, implicit credential, or destructive smoke test is inferred.

## Windows service verification boundary

The offline owner for state/mutation rules and PowerShell parameter/error behavior
is `tests/unit/test_windows_services.py`; packaging coverage in
`tests/packaging/test_distribution.py` checks the installed public imports and a
fake service read without pypsrp. Those checks do not execute PowerShell or the
Windows Service Control Manager; in particular they do not execute the Add-Type
C# helper, .NET `ServiceController`, or SCM P/Invoke calls. The dated
549-passed/5-skipped, 98%-coverage result above predates this API and is not
Windows service verification.

No live Windows service target has been contacted. Before deployment, a target owner
must separately approve a non-production Windows PowerShell 5.1+ target, establish
the caller's real authentication, trusted HTTPS, endpoint policy, and management
rights, and verify service-name targeting, disabled/active-dependent behavior,
state transitions, native stop handling, and uncertain mutation outcomes against a
disposable service. The endpoint must support full-language execution, `Add-Type`,
.NET, and native SCM invocation; constrained language/JEA is not compatible by
selection alone. `ControlService` may block while a service handles its control,
and cooperative transport timeouts do not guarantee remote cancellation. There is
no inferred endpoint or default live target.

### Windows implementation references (not execution evidence)

- [.NET Framework `ServiceController.ServiceName`](https://learn.microsoft.com/en-us/dotnet/api/system.serviceprocess.servicecontroller.servicename?view=netframework-4.8.1)
  and the [Framework reference source](https://referencesource.microsoft.com/#System.ServiceProcess/ServiceController.cs)
  for its exact-name property behavior.
- Win32 [`OpenSCManagerW`](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-openscmanagerw),
  [`OpenServiceW`](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-openservicew),
  and [`ControlService`](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-controlservice)
  document the native SCM calls used for a literal stop request.

These references describe API contracts only. They do not show that the C# helper,
PowerShell endpoint, or native SCM behavior was executed or validated on Windows.

## Opt-in IIS smoke test

```text
uv run pytest tests/integration --run-iis-integration \
  --iis-endpoint https://iis-test.company.local:5986/wsman \
  --iis-pool ConductorDisposable \
  --iis-confirm-pool ConductorDisposable \
  --iis-ca-bundle company-ca.pem
```

Run under the intended Windows agent service identity. The CA option is optional
when the default trust store is sufficient. The pool-confirmation value must
match the pool exactly, and the target must be explicitly approved as
non-production. The test exercises start/stop/idempotency, then attempts initial
stable-state restoration even if assertions fail. It refuses to mutate a pool
whose initial state is transitional or unknown.

Separately verify unauthorized identities are denied, an untrusted certificate
fails, allowed operations/pool names match endpoint policy, DNS/SPNs are correct,
and the intended Windows/IIS versions work. No destructive check is run against
an inferred/default target. Selecting a JEA endpoint is not a compatibility test.

## Opt-in HTTP read-only smoke checks

See [README.md](README.md#develop-and-verify) for the secret JSON config schema:

```text
uv run pytest tests/integration/test_http_clients.py --run-http-integration \
  --http-confirm-nonproduction --http-integration-config /secure/http-integration.local.json
```

The explicit file selects each target/identity. Omitted sections are skipped;
missing confirmation/configuration fails before any request. No credentials are
read implicitly from pipeline/environment state. Never commit this file or enable
pytest traceback-local dumps when running it.

The smoke checks read one repository, one application, and one project/gate. They
do not exercise any mutation. Before deployment, target owners must separately
approve and verify CAS conflicts against disposable Git branches, commit/PR/run
creation, non-pruning Argo sync and observed revision/health, edition-specific
Sonar selectors, and denied identities/untrusted certificates. There is no claim
of live compatibility based on recorded offline results.

## SDK research basis

The adapter targets pypsrp 0.9.x, initially researched against 0.9.1:

- [Project usage/API documentation](https://github.com/jborean93/pypsrp/blob/v0.9.1/README.md)
- [PowerShell/runspace lifecycle and polling](https://github.com/jborean93/pypsrp/blob/v0.9.1/src/pypsrp/powershell.py)
- [WSMan/HTTP timeouts, TLS and retries](https://github.com/jborean93/pypsrp/blob/v0.9.1/src/pypsrp/wsman.py)
- [Error-record properties](https://github.com/jborean93/pypsrp/blob/v0.9.1/src/pypsrp/complex_objects.py)

`PowerShell.invoke()` waits through operation-timeout responses. WSMan receives
normally retry read timeouts. The private adapter instead uses begin/poll,
deadline-checked WSMan calls, and no automatic HTTP retries. Underlying requests
timeouts are per connect/read, not a hard elapsed-time limit against every DNS,
authentication, streaming, or operating-system behavior. Cleanup uses a separate
budget, and cancellation/outcome certainty remains a live-system limitation.

### HTTP/API research basis

- Azure DevOps REST 7.1: [ref updates](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/refs/update-refs?view=azure-devops-rest-7.1),
  [commit pushes](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pushes/create?view=azure-devops-rest-7.1),
  [pull requests](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1),
  and [pipeline runs](https://learn.microsoft.com/en-us/rest/api/azure/devops/pipelines/runs/run-pipeline?view=azure-devops-rest-7.1).
- Argo CD: [bearer authentication and AppProject security semantics](https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/)
  and [v3.5.4 Swagger schema](https://github.com/argoproj/argo-cd/blob/v3.5.4/assets/swagger.json).
- SonarQube: [Web API authentication/version migration](https://docs.sonarsource.com/sonarqube-server/extension-guide/web-api/)
  and [published endpoint schemas](https://next.sonarqube.com/sonarqube/api/webservices/list)
  for `api/components/show` and `api/qualitygates/project_status`.
- HTTPX 0.28.1: [transports/retries/MockTransport](https://www.python-httpx.org/advanced/transports/),
  [TLS](https://www.python-httpx.org/advanced/ssl/), and
  [I/O phase timeout semantics](https://www.python-httpx.org/advanced/timeouts/).

Research fixes request contracts, not a supported live-server matrix. HTTP timeouts
bound individual I/O phases; they do not guarantee a total request duration or
hard cancellation. The Argo health loop checks elapsed budgets around requests,
not inside an in-flight synchronous HTTP call. Tokens are consumer-supplied and
not automatically refreshed. Current server/API schemas must be rechecked for
the organization's supported editions, versions, scopes, and security policy.
