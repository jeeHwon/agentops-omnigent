from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from codex_agentops.collector import ensure_collector
from codex_agentops.config import ConfigError, load_settings
from codex_agentops.service import AgentOpsService

from . import __version__
from .bundle import (
    OmnigentAdapterError,
    OmnigentBundleBuilder,
    telemetry_environment,
    validate_model_service,
    validate_with_omnigent,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aops-omni", description="Harness Agent의 Omnigent runtime adapter"
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="공통 Agent Registry 목록을 조회합니다").add_argument(
        "--all-versions", action="store_true"
    )
    load = commands.add_parser("load", help="Registry Agent를 로드하고 Omnigent bundle을 생성합니다")
    load.add_argument("agent_id")
    load.add_argument("--version")
    load.add_argument("--path", type=Path)
    prepare = commands.add_parser("prepare", help="로컬 Agent 소스에서 Omnigent bundle을 생성합니다")
    prepare.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    prepare.add_argument("--output", type=Path)
    validate = commands.add_parser("validate", help="생성한 bundle을 공식 Omnigent parser로 검증합니다")
    validate.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    run = commands.add_parser("run", help="같은 Agent를 Omnigent REPL에서 실행합니다")
    run.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    run.add_argument("--prompt")
    run.add_argument("--continue-session", action="store_true")
    serve = commands.add_parser("serve", help="Agent가 등록된 Omnigent Web UI를 실행합니다")
    serve.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=6767)
    register = commands.add_parser("register", help="개선한 Agent를 공통 Registry에 새 버전으로 등록합니다")
    register.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    register.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        service = AgentOpsService(settings)
        builder = OmnigentBundleBuilder(settings)
        if args.command == "list":
            for item in service.list_agents(all_versions=args.all_versions):
                print(
                    f"{item.agent_id}@{item.version}\t{item.name}\t"
                    f"skills={','.join(item.skill_names)}"
                )
            return 0
        if args.command == "load":
            target, item = service.load_agent(
                args.agent_id,
                version=args.version,
                target=args.path,
                runtime="source",
            )
            bundle = builder.prepare(target)
            validate_with_omnigent(bundle)
            print(f"Agent 로드 완료: {item.agent_id}@{item.version}")
            print(f"Source: {target}")
            print(f"Omnigent bundle: {bundle.root}")
            return 0
        if args.command in {"prepare", "validate", "run", "serve"}:
            bundle = builder.prepare(args.path, destination=getattr(args, "output", None))
            result = validate_with_omnigent(bundle)
            if args.command in {"prepare", "validate"}:
                print(f"Omnigent bundle 준비 완료: {bundle.root}")
                print(
                    f"Agent: {result['agent_id']}@{result['version']} "
                    f"skills={len(result['skills'])} subagents={len(result['subagents'])}"
                )
                return 0
            validate_model_service(bundle, settings)
            ensure_collector(settings)
            env = {**os.environ, **telemetry_environment(bundle, settings)}
            if args.command == "run":
                command = ["omnigent", "run", str(bundle.root), "--server", "local"]
                if args.prompt:
                    command.extend(["--prompt", args.prompt])
                if args.continue_session:
                    command.append("--continue")
            else:
                command = [
                    "omnigent",
                    "server",
                    "--agent",
                    str(bundle.root),
                    "--host",
                    args.host,
                    "--port",
                    str(args.port),
                ]
            # Run from the generated bundle so its project-level Omnigent
            # configuration wins over machine-level harness wrappers.
            return subprocess.call(command, env=env, cwd=bundle.root)
        if args.command == "register":
            item = service.register_agent(args.path, args.version)
            print(f"Agent 등록 완료: {item.agent_id}@{item.version}")
            return 0
    except (ConfigError, OmnigentAdapterError, RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
