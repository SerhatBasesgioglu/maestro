"""Pipeline run submission/read APIs, independent of pipeline runtime and logging commands."""

from collections.abc import Mapping

from maestro.azure_devops._validation import branch_ref, commit_id, positive_id
from maestro.azure_devops.config import AzureDevOpsConfig
from maestro.azure_devops.models import (
    PipelineRun,
    PipelineRunResult,
    PipelineRunState,
    PipelineVariable,
)
from maestro.common._json import int_value, object_value, required_object, string_value
from maestro.common._validation import non_empty, path_segment
from maestro.common.errors import ConfigurationError, ProtocolError
from maestro.common.http import JsonValue, _JsonConnection


class Pipelines:
    def __init__(self, connection: _JsonConnection, config: AzureDevOpsConfig) -> None:
        self._connection = connection
        self._prefix = f"{path_segment(config.project, 'project')}/_apis/pipelines"
        self._params = {"api-version": config.api_version}

    def run(
        self,
        pipeline_id: int,
        *,
        branch: str,
        commit: str | None = None,
        template_parameters: Mapping[str, JsonValue] | None = None,
        variables: Mapping[str, PipelineVariable] | None = None,
        timeout: float | None = None,
    ) -> PipelineRun:
        """Queue one run with an explicit self-repository branch and optional pinned commit.

        Requires run/queue permission. Returns submission state, not completion;
        no automatic retry, YAML preview, deployment sequencing, or pipeline output.
        Variable secret flags are explicit and values are excluded from repr.
        """
        positive_id(pipeline_id, "pipeline_id")
        repository: dict[str, JsonValue] = {"refName": branch_ref(branch)}
        if commit is not None:
            repository["version"] = commit_id(commit)
        body: dict[str, JsonValue] = {"resources": {"repositories": {"self": repository}}}
        if template_parameters is not None:
            for name in template_parameters:
                non_empty(name, "template parameter name")
            body["templateParameters"] = dict(template_parameters)
        if variables is not None:
            encoded: dict[str, JsonValue] = {}
            for name, variable in variables.items():
                non_empty(name, "variable name")
                if not isinstance(variable, PipelineVariable):
                    raise ConfigurationError(
                        "variables must contain typed PipelineVariable values."
                    )
                encoded[name] = {"value": variable.value, "isSecret": variable.is_secret}
            body["variables"] = encoded
        return _decode_run(
            self._connection.request(
                "POST",
                f"{self._prefix}/{pipeline_id}/runs",
                params=self._params,
                json_body=body,
                timeout=timeout,
            ),
            pipeline_id,
        )

    def get_run(
        self, pipeline_id: int, run_id: int, *, timeout: float | None = None
    ) -> PipelineRun:
        """Read an explicitly identified run; requires pipeline-read permission."""
        positive_id(pipeline_id, "pipeline_id")
        positive_id(run_id, "run_id")
        result = _decode_run(
            self._connection.request(
                "GET",
                f"{self._prefix}/{pipeline_id}/runs/{run_id}",
                params=self._params,
                timeout=timeout,
            ),
            pipeline_id,
        )
        if result.id != run_id:
            raise ProtocolError("Azure DevOps returned a different run than requested.")
        return result


def _decode_run(value: JsonValue, pipeline_id: int) -> PipelineRun:
    data = object_value(value)
    returned_pipeline = int_value(required_object(data, "pipeline").get("id"))
    if returned_pipeline != pipeline_id:
        raise ProtocolError("Azure DevOps returned a different pipeline than requested.")
    try:
        state = PipelineRunState(string_value(data.get("state")))
        result = (
            PipelineRunResult(string_value(data["result"]))
            if data.get("result") is not None
            else None
        )
    except ValueError:
        raise ProtocolError("Azure DevOps returned an unknown pipeline run state/result.") from None
    return PipelineRun(int_value(data.get("id")), returned_pipeline, state, result)
