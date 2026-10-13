from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from codex_agentops.config import Settings
from codex_agentops.release import build_release, write_release_metadata

from agentops_omnigent.bundle import (
    OmnigentAdapterError,
    OmnigentBundleBuilder,
    compatible_codex_path,
    telemetry_environment,
    validate_model_service,
)


SAMPLE = Path(__file__).parents[2] / "agentops" / "samples" / "release-agent"


def test_bundle_converts_common_source_to_direct_omnigent(tmp_path, monkeypatch):
    import shutil

    source = tmp_path / "source"
    shutil.copytree(SAMPLE, source)
    write_release_metadata(build_release(source, "1.0.0", "main.agentops"))
    settings = Settings(
        profile="test-profile",
        host="https://example.cloud.databricks.com",
        otel_port=4319,
    )
    bundle = OmnigentBundleBuilder(settings).prepare(source, destination=tmp_path / "bundle")
    config = yaml.safe_load(bundle.config_path.read_text(encoding="utf-8"))

    assert config["executor"]["config"]["harness"] == "codex"
    assert config["executor"]["auth"] == {"type": "databricks", "profile": "test-profile"}
    sandbox = config["os_env"]["sandbox"]
    assert sandbox["type"] == "auto"
    assert sandbox["read_paths"][0].endswith("agentops-omnigent/codex")
    assert sandbox["write_paths"] == ["."]
    assert sandbox["allow_network"] is False
    assert sandbox["cwd_allow_hidden"] == [".bin"]
    assert config["model_egress"] == [
        "POST example.cloud.databricks.com/ai-gateway/codex/v1/responses"
    ]
    assert config["tools"]["agents"] == ["validator"]
    assert not (bundle.root / ".omnigent/config.yaml").exists()
    assert bundle.codex_home.parent == bundle.root.parent
    assert not bundle.codex_home.is_relative_to(bundle.root)
    assert (bundle.codex_home / "config.toml").is_file()
    assert (bundle.root / "skills/release-summary/SKILL.md").is_file()
    assert (bundle.root / "agents/validator/config.yaml").is_file()
    assert (bundle.root / "agents/validator/skills/release-validation/SKILL.md").is_file()
    metadata = json.loads((bundle.root / "runtime.json").read_text(encoding="utf-8"))
    assert metadata["release_version"] == "1.0.0"
    assert metadata["codex_version"] == "0.146.0"
    assert "source" not in metadata
    assert "codex_path" not in metadata

    monkeypatch.setenv("AOPS_OMNIGENT_CODEX_PATH", compatible_codex_path())
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    env = telemetry_environment(bundle, settings)
    assert env["OMNIGENT_TELEMETRY_ENABLED"] == "true"
    assert env["OMNIGENT_OTEL_CAPTURE_CONTENT"] == "true"
    assert env["OMNIGENT_CODEX_PATH"]
    assert env["CODEX_HOME"] == str(bundle.codex_home)
    assert env["OMNIGENT_DATA_DIR"] == str(tmp_path / "data/agentops-omnigent/runtime")
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert "agentops.agent_revision=1.0.0" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "agentops.service_criticality=low" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "agentops.data_sensitivity=internal" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "agentops.risk_level=low" in env["OTEL_RESOURCE_ATTRIBUTES"]


class _FakeApiClient:
    def __init__(self, model: str) -> None:
        self.model = model

    def do(self, method, path, *, query):
        assert method == "GET"
        assert path == "/api/2.1/unity-catalog/model-services"
        assert query["parent"] == "schemas/system.ai"
        return {
            "model_services": [
                {
                    "name": f"model-services/{self.model}",
                    "supported_api_types": ["codex/v1/responses"],
                }
            ]
        }


class _FakeWorkspace:
    def __init__(self, model: str) -> None:
        self.api_client = _FakeApiClient(model)


def test_model_service_preflight_accepts_codex_model(tmp_path):
    import shutil

    source = tmp_path / "source"
    shutil.copytree(SAMPLE, source)
    settings = Settings(profile="test-profile", host="https://example.cloud.databricks.com")
    bundle = OmnigentBundleBuilder(settings).prepare(source, destination=tmp_path / "bundle")

    assert validate_model_service(
        bundle,
        settings,
        workspace=_FakeWorkspace("system.ai.gpt-5-6-sol"),
    ) == "system.ai.gpt-5-6-sol"


def test_model_service_preflight_rejects_missing_model(tmp_path):
    import shutil

    source = tmp_path / "source"
    shutil.copytree(SAMPLE, source)
    settings = Settings(profile="test-profile", host="https://example.cloud.databricks.com")
    bundle = OmnigentBundleBuilder(settings).prepare(source, destination=tmp_path / "bundle")

    with pytest.raises(OmnigentAdapterError, match="Codex로 사용할 수 없습니다"):
        validate_model_service(
            bundle,
            settings,
            workspace=_FakeWorkspace("system.ai.gpt-5-5"),
        )
