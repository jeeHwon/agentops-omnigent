from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from codex_agentops.config import Settings
from codex_agentops.manifest import (
    AGENT_ID_RE, create_agent, load_manifest, require_source_distribution_allowed,
)
from codex_agentops.service import AgentOpsService

from .bundle import OmnigentBundle, OmnigentBundleBuilder, validate_with_omnigent


RuntimeRegistrar = Callable[[Path], str]
_PROJECT_NAME_RE = re.compile(r"^[^/\\\x00]{1,80}$")


class AgentProjectError(RuntimeError):
    pass


def source_operator_allowed(user_id: str | None, remote_host: str | None) -> bool:
    return user_id in {None, "local"} and remote_host in {"127.0.0.1", "::1"}


@dataclass(frozen=True)
class PreparedProject:
    name: str
    source: Path
    agent_id: str
    version: str
    omnigent_agent_id: str
    bundle: OmnigentBundle


class NewProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    agent_id: str | None = None


class LoadProjectRequest(BaseModel):
    agent_id: str
    version: str | None = None
    name: str | None = Field(default=None, max_length=80)


class RegisterProjectRequest(BaseModel):
    version: str


class SaveMarkdownRequest(BaseModel):
    content: str


def project_validation_response(prepared: PreparedProject) -> dict[str, Any]:
    """Return the validation summary consumed by the Omnigent project UI."""

    return {
        "valid": True,
        "agent_id": prepared.agent_id,
        "agent_version": load_manifest(prepared.source).agent_version,
        "version": prepared.version,
        "source": str(prepared.source),
        "omnigent_agent_id": prepared.omnigent_agent_id,
        "skills": list(prepared.bundle.skill_names),
        "subagents": list(prepared.bundle.subagent_names),
    }


class AgentProjectManager:
    """Own editable Agent sources and assemble their disposable Omnigent bundles."""

    def __init__(
        self,
        projects_root: str | Path,
        settings: Settings,
        registrar: RuntimeRegistrar,
        *,
        service: AgentOpsService | None = None,
        builder: OmnigentBundleBuilder | None = None,
    ) -> None:
        self.projects_root = Path(projects_root).expanduser().resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.service = service or AgentOpsService(settings)
        self.builder = builder or OmnigentBundleBuilder(settings)
        self.registrar = registrar

    def list_local(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for child in sorted(self.projects_root.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_symlink() or not child.is_dir():
                continue
            if not (child / "AGENTS.md").is_file() or not (child / "SPEC.md").is_file():
                continue
            try:
                manifest = load_manifest(child)
            except Exception:
                continue
            projects.append(
                {
                    "name": child.name,
                    "agent_id": manifest.agent_id,
                    "description": manifest.description,
                    "source": str(child),
                }
            )
        return projects

    def list_registry(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": item.agent_id,
                "version": item.version,
                "name": item.name,
                "description": item.description,
                "skills": list(item.skill_names),
            }
            for item in self.service.list_agents(all_versions=True)
        ]

    def create(self, name: str, agent_id: str | None = None) -> PreparedProject:
        project_name = _validate_project_name(name)
        resolved_agent_id = _normalize_agent_id(agent_id, project_name)
        target = self._new_target(project_name)
        try:
            create_agent(resolved_agent_id, target)
            return self.prepare(target)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            raise

    def load(
        self,
        agent_id: str,
        *,
        version: str | None = None,
        name: str | None = None,
    ) -> PreparedProject:
        project_name = _validate_project_name(name or agent_id)
        target = self._new_target(project_name)
        try:
            self.service.load_agent(
                agent_id,
                version=version,
                target=target,
                runtime="source",
            )
            return self.prepare(target)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            raise

    def prepare(self, source: str | Path) -> PreparedProject:
        root = self._existing_source(source)
        manifest = load_manifest(root)
        require_source_distribution_allowed(manifest.data_sensitivity, manifest.security_level)
        bundle = self.builder.prepare(root)
        validate_with_omnigent(bundle)
        omnigent_agent_id = self.registrar(bundle.root)
        if not omnigent_agent_id:
            raise AgentProjectError("Omnigent runtime Agent를 등록하지 못했습니다.")
        return PreparedProject(
            name=root.name,
            source=root,
            agent_id=bundle.agent_id,
            version=bundle.version,
            omnigent_agent_id=omnigent_agent_id,
            bundle=bundle,
        )

    def register(self, project_name: str, version: str) -> dict[str, Any]:
        root = self.project_path(project_name)
        item = self.service.register_agent(root, version)
        return {
            "agent_id": item.agent_id,
            "version": item.version,
            "name": item.name,
            "skills": list(item.skill_names),
        }

    def project_path(self, name: str) -> Path:
        project_name = _validate_project_name(name)
        return self._existing_source(self.projects_root / project_name)

    def markdown_files(self, project_name: str) -> list[str]:
        root = self.project_path(project_name)
        paths: list[str] = []
        for path in root.rglob("*.md"):
            if path.is_symlink() or ".aops" in path.parts:
                continue
            relative = path.relative_to(root)
            if _allowed_markdown(relative):
                paths.append(relative.as_posix())
        return sorted(paths)

    def read_markdown(self, project_name: str, relative: str) -> str:
        path = self._markdown_path(project_name, relative)
        if not path.is_file():
            raise AgentProjectError(f"Markdown 파일을 찾을 수 없습니다: {relative}")
        return path.read_text(encoding="utf-8")

    def write_markdown(self, project_name: str, relative: str, content: str) -> None:
        path = self._markdown_path(project_name, relative)
        if not path.is_file():
            raise AgentProjectError(f"Markdown 파일을 찾을 수 없습니다: {relative}")
        path.write_text(content, encoding="utf-8")

    def _new_target(self, project_name: str) -> Path:
        target = (self.projects_root / project_name).resolve()
        if target.parent != self.projects_root:
            raise AgentProjectError("Agent Project는 projects root 바로 아래에 생성해야 합니다.")
        if target.exists():
            raise AgentProjectError(f"같은 이름의 로컬 Agent Project가 이미 있습니다: {project_name}")
        return target

    def _existing_source(self, source: str | Path) -> Path:
        root = Path(source).expanduser().resolve()
        if root == self.projects_root or self.projects_root not in root.parents:
            raise AgentProjectError("Agent Project가 허용된 projects root 밖에 있습니다.")
        if root.is_symlink() or not root.is_dir():
            raise AgentProjectError(f"Agent Project 폴더를 찾을 수 없습니다: {root}")
        return root

    def _markdown_path(self, project_name: str, relative: str) -> Path:
        root = self.project_path(project_name)
        candidate = Path(relative)
        if candidate.is_absolute() or not _allowed_markdown(candidate):
            raise AgentProjectError("편집 가능한 표준 Markdown 경로가 아닙니다.")
        path = (root / candidate).resolve()
        if root not in path.parents or path.is_symlink():
            raise AgentProjectError("Agent Project 밖의 파일에는 접근할 수 없습니다.")
        current = root
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                raise AgentProjectError("Symbolic link를 통한 파일 접근은 허용하지 않습니다.")
        return path


def create_agentops_router(
    manager: AgentProjectManager,
    *,
    project_store: Any,
    auth_provider: Any = None,
) -> APIRouter:
    """Create local project-management routes mounted below ``/v1/agentops``."""

    from omnigent.server.routes._auth_helpers import require_user
    from omnigent.server.routes.sessions import announce_projects_changed

    router = APIRouter()

    def require_source_operator(request: Request) -> str | None:
        user_id = require_user(request, auth_provider)
        if not source_operator_allowed(user_id, request.client.host if request.client else None):
            raise HTTPException(
                status_code=403,
                detail="Agent 원문 관리 API는 로컬 단일 사용자에게만 허용됩니다.",
            )
        return user_id

    def response(prepared: PreparedProject, project: Any) -> dict[str, Any]:
        return {
            "project": {
                "id": project.id,
                "name": project.name,
                "config": project.config,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            },
            "agent": {
                "agent_id": prepared.agent_id,
                "version": prepared.version,
                "source": str(prepared.source),
                "omnigent_agent_id": prepared.omnigent_agent_id,
            },
        }

    def project_config(prepared: PreparedProject) -> dict[str, Any]:
        return {
            "workspace": str(prepared.source),
            "agent_id": prepared.omnigent_agent_id,
            "use_worktree": False,
            "agentops_source": str(prepared.source),
            "agentops_agent_id": prepared.agent_id,
            "agentops_release": prepared.version,
        }

    async def create_omnigent_project(
        request: Request,
        name: str,
        operation: Callable[[], PreparedProject],
    ) -> dict[str, Any]:
        user_id = require_source_operator(request)
        prepared = await asyncio.to_thread(operation)
        try:
            project = await asyncio.to_thread(
                project_store.create,
                uuid.uuid4().hex,
                name,
                user_id,
                project_config(prepared),
            )
        except Exception:
            shutil.rmtree(prepared.source, ignore_errors=True)
            raise
        announce_projects_changed(user_id)
        return response(prepared, project)

    @router.get("/projects")
    async def list_local_projects(request: Request) -> dict[str, Any]:
        require_source_operator(request)
        return {"data": await asyncio.to_thread(manager.list_local)}

    @router.get("/registry")
    async def list_registry(request: Request) -> dict[str, Any]:
        require_source_operator(request)
        return {"data": await asyncio.to_thread(manager.list_registry)}

    @router.post("/projects")
    async def create_project(request: Request, body: NewProjectRequest) -> dict[str, Any]:
        require_source_operator(request)
        try:
            return await create_omnigent_project(
                request,
                _validate_project_name(body.name),
                lambda: manager.create(body.name, body.agent_id),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/projects/load")
    async def load_project(request: Request, body: LoadProjectRequest) -> dict[str, Any]:
        require_source_operator(request)
        name = _validate_project_name(body.name or body.agent_id)
        try:
            return await create_omnigent_project(
                request,
                name,
                lambda: manager.load(
                    body.agent_id,
                    version=body.version,
                    name=name,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/projects/{project_name}/prepare")
    async def prepare_project(request: Request, project_name: str) -> dict[str, Any]:
        require_source_operator(request)
        try:
            prepared = await asyncio.to_thread(manager.prepare, manager.project_path(project_name))
            return project_validation_response(prepared)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/projects/{project_name}/register")
    async def register_project(
        request: Request,
        project_name: str,
        body: RegisterProjectRequest,
    ) -> dict[str, Any]:
        require_source_operator(request)
        try:
            return await asyncio.to_thread(manager.register, project_name, body.version)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/projects/{project_name}/files")
    async def list_files(request: Request, project_name: str) -> dict[str, Any]:
        require_source_operator(request)
        try:
            return {"data": await asyncio.to_thread(manager.markdown_files, project_name)}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/projects/{project_name}/files/{relative:path}")
    async def read_file(request: Request, project_name: str, relative: str) -> dict[str, str]:
        require_source_operator(request)
        try:
            return {
                "path": relative,
                "content": await asyncio.to_thread(manager.read_markdown, project_name, relative),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/projects/{project_name}/files/{relative:path}")
    async def write_file(
        request: Request,
        project_name: str,
        relative: str,
        body: SaveMarkdownRequest,
    ) -> dict[str, Any]:
        require_source_operator(request)
        try:
            await asyncio.to_thread(manager.write_markdown, project_name, relative, body.content)
            return {"path": relative, "saved": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def _validate_project_name(name: str) -> str:
    value = name.strip()
    if value in {"", ".", ".."} or not _PROJECT_NAME_RE.fullmatch(value):
        raise AgentProjectError("Project 이름은 /와 \\를 제외한 1~80자로 입력하세요.")
    return value


def _normalize_agent_id(value: str | None, project_name: str) -> str:
    if value is not None:
        candidate = value.strip()
        if not AGENT_ID_RE.fullmatch(candidate) or "--" in candidate:
            raise AgentProjectError("Agent ID는 영문 소문자 kebab-case로 입력하세요.")
        return candidate
    candidate = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
    if candidate and len(candidate) <= 64 and AGENT_ID_RE.fullmatch(candidate):
        return candidate
    return f"agent-{uuid.uuid4().hex[:8]}"


def _allowed_markdown(relative: Path) -> bool:
    parts = relative.parts
    if not parts or any(part in {"", ".", "..", ".aops"} for part in parts):
        return False
    if relative.suffix.lower() != ".md":
        return False
    if len(parts) == 1:
        return parts[0] in {"AGENTS.md", "README.md", "SPEC.md"}
    return parts[0] in {"skills", "agents"}
