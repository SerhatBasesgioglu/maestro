import pytest

from conductor.iis import ApplicationPoolState, IisClient
from conductor.transports import PowerShellRemoting

pytestmark = pytest.mark.integration


@pytest.fixture
def remote_pool(request):
    """Keep restoration in teardown, so pytest retains both test and cleanup errors."""
    endpoint = request.config.getoption("--iis-endpoint")
    pool = request.config.getoption("--iis-pool")
    confirmation = request.config.getoption("--iis-confirm-pool")
    if not endpoint or not pool or pool != confirmation:
        pytest.fail(
            "Live tests require an explicit endpoint, pool, and matching --iis-confirm-pool."
        )

    with PowerShellRemoting(
        endpoint=endpoint,
        auth="kerberos",
        ca_bundle=request.config.getoption("--iis-ca-bundle"),
    ) as transport:
        iis = IisClient(transport=transport)
        initial = iis.application_pools.get_state(pool)
        if initial not in (ApplicationPoolState.STARTED, ApplicationPoolState.STOPPED):
            pytest.skip(
                "Disposable pool must initially be in a stable state; no mutation attempted."
            )
        try:
            yield iis, pool
        finally:
            # An uncertain transport failure invalidates the session. Reopen
            # explicitly to read current state and attempt the approved cleanup.
            transport.open()
            if initial is ApplicationPoolState.STARTED:
                iis.application_pools.start(pool)
            else:
                iis.application_pools.stop(pool)


def test_disposable_remote_pool_and_restore_initial_state(remote_pool):
    """Destructive opt-in smoke test; the caller approves the non-production target."""
    iis, pool = remote_pool
    started = iis.application_pools.start(pool)
    assert started.final_state is ApplicationPoolState.STARTED
    assert not iis.application_pools.start(pool).changed
    stopped = iis.application_pools.stop(pool)
    assert stopped.final_state is ApplicationPoolState.STOPPED
    assert not iis.application_pools.stop(pool).changed
