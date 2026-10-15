from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_agentops.config import Settings
from codex_agentops.manifest import create_agent, load_manifest

from agentops_omnigent.bundle import OmnigentBundle
from agentops_omnigent.projects import (
    AgentProjectError,
    AgentProjectManager,
    project_validation_response,
    source_operator_allowed,
)


class _Builder:
    def prepare(self, source):
        root = Path(source).resolve()
        manifest = load_manifest(root)
        target = root / ".aops" / "omnigent"
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.yaml").write_text("spec_version: 1\n", encoding="utf-8")
        codex_home = root / ".aops" / "omnigent-codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        return OmnigentBundle(
            root=target,
            config_path=target / "config.yaml",
            codex_home=codex_home,
            agent_id=manifest.agent_id,
            version="working",
            skill_names=("request-structurer",),
            subagent_names=("validator",),
        )


class _Service:
    def __init__(self):
        self.items = [
            SimpleNamespace(
                agent_id="registry-agent",
                version="1.2.0",
                name="Registry Agent",
                description="Registry sample",
                skill_names=("one", "two"),
            )
        ]

    def list_agents(self, *, all_versions):
        assert all_versions is True
        return self.items

    def load_agent(self, agent_id, *, version, target, runtime):
        assert runtime == "source"
        create_agent(agent_id, target)
        return Path(target), self.items[0]

    def register_agent(self, path, version):
        manifest = load_manifest(path)
        return SimpleNamespace(
            agent_id=manifest.agent_id,
            version=version,
            name=manifest.name,
            skill_names=("request-structurer",),
        )


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agentops_omnigent.projects.validate_with_omnigent",
        lambda bundle: {"agent_id": bundle.agent_id},
    )
    return AgentProjectManager(
        tmp_path / "projects",
        Settings(profile="test", host="https://example.com"),
        lambda bundle: f"omni-{bundle.parent.parent.name}",
        service=_Service(),
        builder=_Builder(),
    )


def test_create_project_generates_editable_source_and_hidden_runtime(manager):
    prepared = manager.create("문서 검토")

    assert prepared.source.name == "문서 검토"
    assert prepared.agent_id.startswith("agent-")
    assert (prepared.source / "AGENTS.md").is_file()
    assert (prepared.source / "README.md").is_file()
    assert (prepared.source / "SPEC.md").is_file()
    assert (prepared.source / "skills/request-structurer/SKILL.md").is_file()
    assert (prepared.source / "agents/validator/AGENTS.md").is_file()
    assert prepared.bundle.root == prepared.source / ".aops/omnigent"


def test_registry_release_loads_as_local_project(manager):
    prepared = manager.load("registry-agent", version="1.2.0", name="검토 Agent")

    assert prepared.source.name == "검토 Agent"
    assert prepared.agent_id == "registry-agent"
    assert manager.list_registry()[0]["skills"] == ["one", "two"]


def test_ui_can_validate_and_register_project(manager):
    prepared = manager.create("safe-agent", "safe-agent")
    assert project_validation_response(prepared) == {
        "valid": True,
        "agent_id": "safe-agent",
        "agent_version": "1.0.0",
        "version": "working",
        "source": str(manager.projects_root / "safe-agent"),
        "omnigent_agent_id": "omni-safe-agent",
        "skills": ["request-structurer"],
        "subagents": ["validator"],
    }

    registered = manager.register("safe-agent", "1.0.0")
    assert registered == {
        "agent_id": "safe-agent",
        "version": "1.0.0",
        "name": "Safe Agent",
        "skills": ["request-structurer"],
    }


def test_markdown_api_exposes_only_editable_definition_files(manager):
    prepared = manager.create("safe-agent", "safe-agent")
    generated = prepared.source / ".aops/omnigent/AGENTS.md"
    generated.write_text("generated", encoding="utf-8")
    executable = prepared.source / "skills/request-structurer/tool.py"
    executable.write_text("print('no')", encoding="utf-8")

    files = manager.markdown_files("safe-agent")
    assert "AGENTS.md" in files
    assert "skills/request-structurer/SKILL.md" in files
    assert ".aops/omnigent/AGENTS.md" not in files
    assert "skills/request-structurer/tool.py" not in files

    manager.write_markdown("safe-agent", "AGENTS.md", "# 변경")
    assert manager.read_markdown("safe-agent", "AGENTS.md") == "# 변경"
    with pytest.raises(AgentProjectError, match="편집 가능한"):
        manager.read_markdown("safe-agent", ".aops/omnigent/AGENTS.md")
    with pytest.raises(AgentProjectError, match="편집 가능한"):
        manager.read_markdown("safe-agent", "skills/request-structurer/tool.py")


def test_project_cannot_escape_projects_root(manager, tmp_path):
    outside = tmp_path / "outside"
    create_agent("outside", outside)
    with pytest.raises(AgentProjectError, match="projects root 밖"):
        manager.prepare(outside)


def test_source_routes_are_local_only_and_sensitive_project_is_not_prepared(manager):
    assert source_operator_allowed("local", "127.0.0.1") is True
    assert source_operator_allowed(None, "::1") is True
    assert source_operator_allowed("alice@example.com", "127.0.0.1") is False
    assert source_operator_allowed(None, "198.51.100.2") is False
    source = create_agent("hr-agent", manager.projects_root / "hr")
    spec = source / "SPEC.md"
    spec.write_text(spec.read_text().replace("data_sensitivity: 내부", "data_sensitivity: 개인정보"))
    from codex_agentops.manifest import ManifestError

    with pytest.raises(ManifestError, match="대외비/개인정보"):
        manager.prepare(source)


def test_failed_create_removes_partial_folder(manager, monkeypatch):
    def fail(_source):
        raise RuntimeError("invalid")

    monkeypatch.setattr(manager, "prepare", fail)
    with pytest.raises(RuntimeError, match="invalid"):
        manager.create("broken-agent", "broken-agent")
    assert not (manager.projects_root / "broken-agent").exists()
