import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("remote IIS integration (explicit opt-in)")
    group.addoption("--run-iis-integration", action="store_true", default=False)
    group.addoption("--iis-endpoint", default=None, help="Approved non-production HTTPS endpoint")
    group.addoption("--iis-pool", default=None, help="Disposable pool to start/stop")
    group.addoption(
        "--iis-confirm-pool", default=None, help="Repeat the pool name to approve mutation"
    )
    group.addoption("--iis-ca-bundle", default=None, help="Optional trusted PEM CA bundle")
    http = parser.getgroup("HTTP integration (read-only, explicit opt-in)")
    http.addoption("--run-http-integration", action="store_true", default=False)
    http.addoption(
        "--http-integration-config", default=None, help="Caller-supplied secret JSON config file"
    )
    http.addoption("--http-confirm-nonproduction", action="store_true", default=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" not in item.keywords:
            continue
        if "http_integration" in item.keywords:
            if not config.getoption("--run-http-integration"):
                item.add_marker(
                    pytest.mark.skip(
                        reason="HTTP live tests require explicit non-production opt-in."
                    )
                )
        elif not config.getoption("--run-iis-integration"):
            item.add_marker(
                pytest.mark.skip(
                    reason="Live IIS tests require explicit opt-in and a disposable pool."
                )
            )
