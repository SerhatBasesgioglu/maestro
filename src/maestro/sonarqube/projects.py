"""Project reads using the maintained component endpoint, without project mutation."""

from maestro.common._json import object_value, required_object, string_value
from maestro.common._validation import non_empty
from maestro.common.errors import ProtocolError
from maestro.common.http import _JsonConnection
from maestro.sonarqube.errors import SonarQubeError
from maestro.sonarqube.models import Project


class Projects:
    def __init__(self, connection: _JsonConnection) -> None:
        self._connection = connection

    def get(self, key: str, *, timeout: float | None = None) -> Project:
        """Read a project key with Browse permission; never treat files/portfolios as projects."""
        non_empty(key, "project key")
        data = object_value(
            self._connection.request(
                "GET", "api/components/show", params={"component": key}, timeout=timeout
            )
        )
        component = required_object(data, "component")
        if string_value(component.get("qualifier")) != "TRK":
            raise SonarQubeError("The requested SonarQube component is not a project.")
        returned_key = string_value(component.get("key"))
        if returned_key != key:
            raise ProtocolError("SonarQube returned a different project than requested.")
        return Project(returned_key, string_value(component.get("name")))
