from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from codex_agentops.config import load_settings

from .bundle import OmnigentBundleBuilder
from .projects import AgentProjectManager, create_agentops_router


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--projects-root", required=True, type=Path)
    parser.add_argument("--agent-source", action="append", default=[], type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=6767, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "::1"}:
        raise ValueError("Agent 원문 관리 서버는 로컬 루프백에서만 실행할 수 있습니다.")
    settings = load_settings()

    # Omnigent reads this constant when omnigent.server.app is imported.
    web_dist = os.environ.get("AOPS_OMNIGENT_WEB_DIST", "").strip()
    if web_dist:
        os.environ["OMNIGENT_WEB_UI_DIST"] = web_dist

    import omnigent.server.app as app_module
    from omnigent.cli import _preregister_agent, cli

    original_create_app = app_module.create_app

    def create_app_with_agentops(*positional: Any, **kwargs: Any):
        agent_store = kwargs["agent_store"]
        artifact_store = kwargs["artifact_store"]
        agent_cache = kwargs["agent_cache"]

        def registrar(bundle: Path) -> str:
            registered = _preregister_agent(bundle, agent_store, artifact_store, agent_cache)
            if registered is None:
                raise RuntimeError(f"Omnigent Agent 이름이 없습니다: {bundle}")
            return registered

        manager = AgentProjectManager(
            args.projects_root,
            settings,
            registrar,
            builder=OmnigentBundleBuilder(settings),
        )
        router = create_agentops_router(
            manager,
            project_store=kwargs["project_store"],
            auth_provider=kwargs.get("auth_provider"),
        )
        extra = list(kwargs.get("extra_routers") or [])
        extra.append((router, "/v1/agentops", ["agentops"]))
        kwargs["extra_routers"] = extra
        app = original_create_app(*positional, **kwargs)

        @app.middleware("http")
        async def refresh_agent_before_session(request, call_next):
            if request.method == "POST" and request.url.path.rstrip("/") == "/v1/sessions":
                from omnigent.server.routes._auth_helpers import require_user
                from fastapi.responses import JSONResponse

                try:
                    user_id = require_user(request, kwargs.get("auth_provider"))
                except Exception:
                    return JSONResponse(status_code=401, content={"detail": "Authentication required"})
                try:
                    payload = json.loads((await request.body()) or b"{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = {}
                project_id = payload.get("project_id") if isinstance(payload, dict) else None
                if isinstance(project_id, str) and project_id:
                    project = await asyncio.to_thread(
                        kwargs["project_store"].get,
                        project_id,
                        user_id=user_id,
                    )
                    source = (project.config or {}).get("agentops_source") if project else None
                    if isinstance(source, str) and source:
                        if user_id not in {None, "local"} or (
                            request.client is None
                            or request.client.host not in {"127.0.0.1", "::1"}
                        ):
                            return JSONResponse(status_code=403, content={"detail": "로컬 소스 실행 권한이 없습니다."})
                        try:
                            root = manager.project_path(Path(source).name)
                            if str(root) != source:
                                return JSONResponse(status_code=403, content={"detail": "Agent 소스 경로가 유효하지 않습니다."})
                            await asyncio.to_thread(manager.prepare, root)
                        except Exception as exc:
                            return JSONResponse(
                                status_code=400,
                                content={"detail": f"Agent runtime 준비 실패: {exc}"},
                            )
            return await call_next(request)

        return app

    app_module.create_app = create_app_with_agentops
    command = [
        "server",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-open",
    ]
    for source in args.agent_source:
        command.extend(["--agent", str(source)])
    try:
        cli.main(args=command, prog_name="aops-omni", standalone_mode=False)
    finally:
        app_module.create_app = original_create_app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
