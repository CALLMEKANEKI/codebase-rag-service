from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate

OPENAPI_PATH = Path(__file__).parents[1] / "contracts" / "openapi.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
EXPECTED_OPERATIONS = {
    ("GET", "/v1/health"),
    ("POST", "/v1/indexes"),
    ("GET", "/v1/indexes/{id}"),
    ("DELETE", "/v1/indexes/{id}"),
    ("GET", "/v1/indexes/{id}/files"),
    ("POST", "/v1/query"),
}
EXPECTED_CITATION_FIELDS = {
    "ref_id",
    "file_path",
    "symbol",
    "symbol_kind",
    "start_line",
    "end_line",
    "snippet",
    "score",
}


@pytest.fixture(scope="module")
def openapi_spec():
    with OPENAPI_PATH.open(encoding="utf-8") as contract_file:
        return yaml.safe_load(contract_file)


def test_openapi_yaml_parses(openapi_spec):
    assert isinstance(openapi_spec, dict)


def test_openapi_spec_is_valid(openapi_spec):
    validate(openapi_spec)


def test_openapi_has_exactly_the_expected_operations(openapi_spec):
    operations = {
        (method.upper(), path)
        for path, path_item in openapi_spec["paths"].items()
        for method in path_item
        if method.lower() in HTTP_METHODS
    }

    assert operations == EXPECTED_OPERATIONS


def test_health_endpoint_does_not_require_api_key(openapi_spec):
    assert openapi_spec["paths"]["/v1/health"]["get"]["security"] == []


def test_other_endpoints_require_api_key(openapi_spec):
    global_security = openapi_spec.get("security", [])
    for path, path_item in openapi_spec["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or (
                path == "/v1/health" and method.lower() == "get"
            ):
                continue

            security = operation.get("security", global_security)
            assert any("ApiKeyAuth" in requirement for requirement in security), (
                f"{method.upper()} {path} does not require ApiKeyAuth"
            )


def test_citation_schema_requires_all_expected_fields(openapi_spec):
    required = set(openapi_spec["components"]["schemas"]["Citation"]["required"])

    assert EXPECTED_CITATION_FIELDS <= required
