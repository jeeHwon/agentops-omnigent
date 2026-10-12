from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from databricks.sdk import WorkspaceClient

from codex_agentops.config import Settings
from codex_agentops.manifest import (
    calculate_definition_checksums,
    load_manifest,
    load_skills,
    load_subagents,
    read_release_metadata,
    validate_agent,
)


class OmnigentAdapterError(RuntimeError):
    pass


OMNIGENT_017_CODEX_VERSION = "0.146.0"


@dataclass(frozen=True)
class OmnigentBundle:
    root: Path
    config_path: Path
    codex_home: Path
    agent_id: str
    version: str
    skill_names: tuple[str, ...]
    subagent_names: tuple[str, ...]


class OmnigentBundleBuilder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def prepare(
        self,
        source: str | Path,
        *,
        destination: str | Path | None = None,
    ) -> OmnigentBundle:
        manifest = validate_agent(source)
        skills = load_skills(manifest.root)
        subagents = load_subagents(manifest.root, set(skills))
        release = read_release_metadata(manifest.root) or {}
        version = str(release.get("version") or "working")
        target = Path(destination or manifest.root / ".aops" / "omnigent").expanduser().resolve()
        if target == manifest.root or manifest.root in target.parents and ".aops" not in target.parts:
            raise OmnigentAdapterError("Omnigent bundle must be generated outside the editable Agent source.")
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="aops-omnigent-", dir=target.parent) as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            for name in ("AGENTS.md", "README.md", "spec.md"):
                shutil.copy2(manifest.root / name, staging / name)
            shutil.copytree(manifest.root / "skills", staging / "skills")

            codex_path = compatible_codex_path()
            codex_install_root = _codex_install_root(codex_path)
            root_config = _config(
                name=manifest.agent_id,
                description=manifest.description,
                model=manifest.model,
                profile=self.settings.profile,
                workspace_host=self.settings.host,
                codex_install_root=codex_install_root,
                subagents=sorted(subagents),
            )
            _write_yaml(staging / "config.yaml", root_config)

            # Pin the structured Codex harness to the adapter-owned binary via
            # ``OMNIGENT_CODEX_PATH`` at launch time.  Unlike ``codex-native``,
            # the direct ``codex`` harness consumes App Server events and
            # returns a structured TurnComplete event with response and usage.
            for name, subagent in subagents.items():
                sub_root = staging / "agents" / name
                sub_root.mkdir(parents=True)
                shutil.copy2(subagent.root / "AGENTS.md", sub_root / "AGENTS.md")
                assigned = subagent.skills or tuple(skills)
                for skill_name in assigned:
                    shutil.copytree(skills[skill_name].root, sub_root / "skills" / skill_name)
                _write_yaml(
                    sub_root / "config.yaml",
                    _config(
                        name=name,
                        description=subagent.description,
                        model=manifest.model,
                        profile=self.settings.profile,
                        workspace_host=self.settings.host,
                        codex_install_root=codex_install_root,
                        subagents=[],
                    ),
                )

            checksums = calculate_definition_checksums(manifest.root)
            metadata = {
                "schema_version": 1,
                "runtime": "omnigent",
                "agent_id": manifest.agent_id,
                "release_version": version,
                "harness_sha256": checksums.harness,
                "skills_sha256": checksums.combined_skills,
                "skills": sorted(skills),
                "subagents": sorted(subagents),
                "codex_version": OMNIGENT_017_CODEX_VERSION,
            }
            (staging / "runtime.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)

        # Keep CODEX_HOME outside the uploaded Omnigent agent tarball. Codex
        # creates executable symlinks in this directory and Omnigent correctly
        # rejects tarballs that contain links.
        codex_home = target.parent / f"{target.name}-codex-home"
        if codex_home.exists():
            shutil.rmtree(codex_home)
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(
            "check_for_update_on_startup = false\n",
            encoding="utf-8",
        )

        return OmnigentBundle(
            root=target,
            config_path=target / "config.yaml",
            codex_home=codex_home,
            agent_id=manifest.agent_id,
            version=version,
            skill_names=tuple(sorted(skills)),
            subagent_names=tuple(sorted(subagents)),
        )


def telemetry_environment(bundle: OmnigentBundle, settings: Settings) -> dict[str, str]:
    metadata = json.loads((bundle.root / "runtime.json").read_text(encoding="utf-8"))
    attributes = {
        "agentops.agent_id": bundle.agent_id,
        "agentops.agent_revision": bundle.version,
        "agentops.runtime": "omnigent",
        "agentops.agent_type": "harness",
        "agentops.harness_checksum": metadata["harness_sha256"],
        "agentops.skills_checksum": metadata["skills_sha256"],
        "agentops.skill_names": "|".join(bundle.skill_names),
    }
    existing = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    capture_content = os.environ.get("AOPS_OMNIGENT_CAPTURE_CONTENT", "true").strip().lower()
    if capture_content not in {"true", "false"}:
        raise OmnigentAdapterError("AOPS_OMNIGENT_CAPTURE_CONTENT must be true or false.")
    values = [item for item in existing.split(",") if item.strip()]
    values.extend(f"{key}={value}" for key, value in attributes.items())
    return {
        "DATABRICKS_CONFIG_PROFILE": settings.profile,
        "CODEX_HOME": str(bundle.codex_home),
        # Use an adapter-owned Omnigent server, host and conversation store.
        # A machine-global Omnigent daemon keeps the OTel endpoint from the
        # process that first started it, so reusing it could silently route
        # AgentOps traces to a stale collector port. The dedicated data dir
        # also prevents AgentOps runs from stopping or changing unrelated
        # Omnigent sessions on the same machine.
        "OMNIGENT_DATA_DIR": str(_omnigent_data_dir()),
        "OMNIGENT_CODEX_PATH": compatible_codex_path(),
        "OMNIGENT_TELEMETRY_ENABLED": "true",
        "OMNIGENT_OTEL_CAPTURE_CONTENT": capture_content,
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{settings.otel_port}",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "30",
        "OTEL_BSP_SCHEDULE_DELAY": "500",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_RESOURCE_ATTRIBUTES": ",".join(values),
    }


def _omnigent_data_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home.expanduser() / "agentops-omnigent" / "runtime"


def compatible_codex_path() -> str:
    """Return the Codex binary validated by Omnigent 0.17 brokered auth.

    A separate binary keeps the user's normal Codex installation untouched.
    ``AOPS_OMNIGENT_CODEX_PATH`` remains available for managed environments
    that provision the compatible binary elsewhere.
    """
    override = os.environ.get("AOPS_OMNIGENT_CODEX_PATH", "").strip()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    candidates = [
        override,
        str(data_home / "agentops-omnigent" / "codex" / "node_modules" / ".bin" / "codex"),
        shutil.which("codex") or "",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except OSError:
            continue
        if result.returncode == 0 and OMNIGENT_017_CODEX_VERSION in result.stdout:
            # Preserve npm's executable shim path. Omnigent's native Codex
            # harness expects the CLI launcher, not the resolved JavaScript
            # implementation file behind the shim.
            return str(Path(candidate).expanduser().absolute())
    raise OmnigentAdapterError(
        "Omnigent 0.17용 Codex 0.146.0을 찾을 수 없습니다. "
        "`agentops-omnigent/install.sh`를 실행하세요."
    )


def validate_with_omnigent(bundle: OmnigentBundle) -> dict[str, Any]:
    executable = shutil.which("omnigent")
    if not executable:
        raise OmnigentAdapterError("omnigent CLI가 설치되지 않았습니다.")
    first_line = Path(executable).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    python = first_line.removeprefix("#!").strip()
    if not python or not Path(python).is_file():
        raise OmnigentAdapterError("omnigent CLI의 Python runtime을 확인할 수 없습니다.")
    code = (
        "from pathlib import Path; "
        "from omnigent.spec.parser import parse; "
        "s=parse(Path(__import__('sys').argv[1]), expand_env=False); "
        "print(s.name, len(s.skills), len(s.sub_agents))"
    )
    result = subprocess.run(
        [python, "-c", code, str(bundle.root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise OmnigentAdapterError(result.stderr.strip() or "Omnigent parser validation failed.")
    return {
        "agent_id": bundle.agent_id,
        "version": bundle.version,
        "skills": list(bundle.skill_names),
        "subagents": list(bundle.subagent_names),
        "parser": result.stdout.strip(),
    }


def validate_model_service(
    bundle: OmnigentBundle,
    settings: Settings,
    *,
    workspace: WorkspaceClient | None = None,
) -> str:
    config = yaml.safe_load(bundle.config_path.read_text(encoding="utf-8"))
    model = str(config.get("executor", {}).get("model", "")).strip()
    if not model.startswith("system.ai."):
        return model

    client = workspace or WorkspaceClient(profile=settings.profile)
    services: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        query = {"parent": "schemas/system.ai", "max_results": 1000}
        if page_token:
            query["page_token"] = page_token
        response = client.api_client.do(
            "GET",
            "/api/2.1/unity-catalog/model-services",
            query=query,
        )
        if not isinstance(response, dict):
            raise OmnigentAdapterError("Model Service 목록 응답이 올바르지 않습니다.")
        rows = response.get("model_services", [])
        if isinstance(rows, list):
            services.extend(row for row in rows if isinstance(row, dict))
        next_page = response.get("next_page_token")
        if not isinstance(next_page, str) or not next_page:
            break
        page_token = next_page

    supported = sorted(
        str(row.get("name", "")).removeprefix("model-services/")
        for row in services
        if model_service_supports_codex(row)
    )
    if model not in supported:
        recommendations = ", ".join(
            candidate
            for candidate in supported
            if candidate.startswith("system.ai.gpt-")
        )
        if not recommendations:
            recommendations = ", ".join(supported)
        detail = f" 사용 가능 모델: {recommendations}" if recommendations else ""
        raise OmnigentAdapterError(
            f"Agent 모델을 {settings.profile}에서 Codex로 사용할 수 없습니다: {model}.{detail}"
        )
    return model


def model_service_supports_codex(service: dict[str, Any]) -> bool:
    api_types = service.get("supported_api_types", [])
    return isinstance(api_types, list) and "codex/v1/responses" in api_types


def _config(
    *,
    name: str,
    description: str,
    model: str,
    profile: str,
    workspace_host: str,
    codex_install_root: str,
    subagents: list[str],
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "spec_version": 1,
        "name": name,
        "description": description,
        "executor": {
            "type": "omnigent",
            "model": model,
            "auth": {"type": "databricks", "profile": profile},
            "config": {"harness": "codex"},
        },
        "async": True,
        # Omnigent's signer-backed Databricks Codex route requires both an
        # active OS sandbox and an explicit, exact model egress grant.  The
        # grant is intentionally limited to the workspace Codex Responses
        # endpoint; all other network access remains outside this contract.
        "model_egress": [_model_egress_rule(workspace_host)],
        "os_env": {
            "type": "caller_process",
            "cwd": ".",
            "sandbox": {
                "type": _sandbox_type(),
                # The adapter-owned npm tree lives below ~/.local. Grant only
                # that immutable install tree instead of widening Seatbelt to
                # the user's home or /Users.
                "read_paths": [codex_install_root],
                "write_paths": ["."],
                "allow_network": False,
                "cwd_allow_hidden": [".bin"],
            },
        },
    }
    if subagents:
        config["tools"] = {"agents": subagents}
    return config


def _sandbox_type() -> str:
    # Omnigent resolves ``auto`` to darwin_seatbelt on macOS and
    # linux_bwrap on Linux.  This keeps the generated release portable while
    # satisfying the signer-backed Codex isolation requirement.
    return "auto"


def _model_egress_rule(workspace_host: str) -> str:
    parsed = urlparse(workspace_host)
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443):
        raise OmnigentAdapterError(
            f"Databricks workspace host must be an HTTPS origin: {workspace_host}"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise OmnigentAdapterError(
            f"Databricks workspace host must not contain a path: {workspace_host}"
        )
    return f"POST {parsed.hostname}/ai-gateway/codex/v1/responses"


def _codex_install_root(codex_path: str) -> str:
    path = Path(codex_path).expanduser().absolute()
    for parent in path.parents:
        if parent.name == "node_modules":
            return str(parent.parent)
    return str(path.parent)


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
