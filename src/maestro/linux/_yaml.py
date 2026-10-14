"""Lazy optional round-trip Compose YAML editing; no remote execution or workflows."""

import io
import re
import warnings

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from maestro.common.errors import ConflictError, ResourceNotFoundError
from maestro.linux.errors import ComposeFileError


def edit_image(
    content: str, service: str, image: str, expected_image: str | None
) -> tuple[bytes, str | None, bool]:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False
    try:
        # Parser warnings can include snippets of secret-bearing YAML. Treat
        # questionable documents as sanitized failures instead of emitting them.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            document = yaml.load(content)
        if not isinstance(document, CommentedMap):
            raise ComposeFileError("Compose YAML must contain a services mapping.")
        services = document.get("services")
        if not isinstance(services, CommentedMap):
            raise ComposeFileError("Compose YAML must contain a services mapping.")
        if service not in services:
            raise ResourceNotFoundError("The requested Compose service is absent from the file.")
        definition = services[service]
        if not isinstance(definition, CommentedMap) or definition.anchor.value is not None:
            raise ComposeFileError(
                "Image editing requires an unaliased, unanchored service mapping."
            )
        previous = definition.get("image")
        if previous is not None and not isinstance(previous, str):
            raise ComposeFileError("The existing Compose image must be a string.")
        if expected_image is not None and previous != expected_image:
            raise ConflictError("The existing Compose image does not match expected_image.")
        if previous == image:
            return content.encode("utf-8"), previous, False
        definition["image"] = image
        output = io.StringIO()
        # Preserve the existing mapping indentation when it can be inferred.
        indentation = re.search(r"\n( +)[^ #\n][^\n]*:", content)
        if indentation:
            yaml.indent(mapping=len(indentation[1]), sequence=len(indentation[1]) + 2, offset=2)
        yaml.dump(document, output)
        return output.getvalue().encode("utf-8"), previous, True
    except (YAMLError, ValueError, TypeError, Warning):
        raise ComposeFileError("Compose YAML could not be safely parsed or edited.") from None
