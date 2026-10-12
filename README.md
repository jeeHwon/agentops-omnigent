# AgentOps Omnigent Adapter

공통 Harness Agent Release를 Omnigent에서 직접 개발하고 실행하는 B안 어댑터입니다. MCP 서버를 거치지 않으며 `agentops`와 같은 Agent Registry, UC Skills, Release Artifact와 MLflow `agent.turn` 계약을 사용합니다.

## 전제 조건

1. `agentops`를 설치하고 `aops configure`를 완료합니다.
2. `npm`과 `uv`를 준비합니다.
3. 이 저장소의 어댑터를 설치합니다.

```bash
cd agentops-omnigent
./install.sh
aops-omni --version
```

설치 스크립트는 같은 상위 디렉터리에 `agentops` 저장소가 있으면 그 소스를 사용하고, 없으면 공개 GitHub 저장소에서 core 패키지를 설치합니다. Omnigent 0.17.0, 공식 `databricks`·`tracing` extra와 호환 Codex 0.146.0을 어댑터 전용 경로에 설치하며 기존 시스템 `codex` 명령과 버전은 바꾸지 않습니다. 생성된 bundle은 프로젝트 수준 Omnigent 설정으로 이 전용 Codex를 고정하므로 사용자 전역의 harness wrapper 설정과 충돌하지 않습니다.

각 bundle은 업로드 대상 밖에 생성된 깨끗한 전용 `CODEX_HOME`에서 실행됩니다. 전역 Codex의 Plugin, Hook, model catalog는 B안 세션에 섞이지 않으며 Agent bundle의 Markdown, Skill과 Sub-agent만 Omnigent가 주입합니다.

어댑터는 전용 `OMNIGENT_DATA_DIR`도 사용합니다. AgentOps용 Server·Host·Runner와 세션 DB가 일반 Omnigent 실행과 분리되므로, 기존 daemon의 OTel 포트나 다른 프로젝트 세션과 충돌하지 않습니다.

Omnigent는 구조화된 Direct `codex` harness로 실행합니다. macOS에서는 Seatbelt, Linux에서는 bubblewrap를 자동 선택하고, 파일 읽기는 어댑터 전용 Codex 설치 트리, 쓰기는 Agent 작업 디렉터리로 제한합니다. 네트워크는 닫은 상태에서 선택한 Databricks Workspace의 Codex Responses 경로만 `model_egress`로 허용합니다.

## Registry Agent 사용

```bash
aops-omni list
aops-omni load <agent-id> --version <version>
cd <agent-id>
aops-omni run .
```

`load`는 공통 Release를 현재 사용자의 UC Skill 권한으로 검증하고 소스 폴더를 복원한 뒤 `.aops/omnigent`에 공식 Omnigent bundle을 생성합니다.

## 로컬 Agent 사용

```bash
aops-omni prepare <agent-folder>
aops-omni validate <agent-folder>
aops-omni run <agent-folder>
```

Web UI가 필요하면 다음과 같이 실행합니다.

```bash
aops-omni serve <agent-folder> --host 127.0.0.1 --port 6767
```

Omnigent 화면에서 같은 Agent와 대화하고 세션을 관리할 수 있습니다. 개발이 끝난 소스는 공통 Registry에 새 버전으로 등록합니다.

```bash
aops-omni register <agent-folder> --version 1.0.1
```

## 생성되는 Omnigent bundle

```text
.aops/omnigent/
├── config.yaml
├── AGENTS.md
├── README.md
├── spec.md
├── runtime.json
├── skills/
│   └── <skill>/SKILL.md
└── agents/
    └── <subagent>/
        ├── config.yaml
        ├── AGENTS.md
        └── skills/
```

- Root `config.yaml`은 Direct Codex harness, Databricks Profile, 격리 정책과 Sub-agent 목록을 정의합니다.
- 공통 `skills/`는 Omnigent 공식 Skill discovery 구조를 그대로 사용합니다.
- 공통 `agents/<name>/AGENTS.md`는 Omnigent Sub-agent의 `config.yaml`과 함께 조립됩니다.
- 생성물은 직접 수정하지 않고 원본 Agent Markdown을 수정한 뒤 다시 `prepare`합니다.

## MLflow Trace

`run`과 `serve`는 Omnigent native OTel을 자동 활성화합니다.
`run`은 사용자 전역 Omnigent Server 설정에 영향받지 않도록 로컬 Server를 명시적으로 사용합니다.

```text
Omnigent agent:* / tool:* spans
  → OTLP/HTTP protobuf
  → AgentOps local collector
  → 공통 MLflow agent.turn Trace
  → RelevanceToQuery · Safety · Completeness
```

Collector는 Omnigent가 여러 OTLP request로 나누어 전송한 Span을 OTel trace ID로 조립하고, exporter 재전송을 Span ID로 제거합니다. `sys_session_send`의 conversation ID를 이용해 validator 같은 Sub-agent를 부모 Turn 아래에 결합하므로 한 사용자 Turn은 하나의 MLflow `agent.turn`이 됩니다.

Trace에는 입력·출력 원문, 정확한 전체 Token, Tool, Sub-agent, Agent ID·Release와 Skill 체크섬이 포함됩니다. A안과 B안의 Trace tag `agentops.runtime` 값은 각각 `codex`, `omnigent`입니다.

기본값은 입력·출력 원문 기록입니다. 구조 검증이나 민감정보가 포함된 로컬 테스트에서는 해당 실행에만 `AOPS_OMNIGENT_CAPTURE_CONTENT=false`를 지정해 본문 캡처를 끌 수 있습니다.

## A안과 B안의 차이

| 항목 | A안 Codex Adapter | B안 Omnigent Adapter |
|---|---|---|
| 실행 UI | 기존 Codex CLI 또는 자체 Web UI | Omnigent REPL·Web UI |
| 런타임 생성 | `.agents/`, `.codex/` | Omnigent `config.yaml` bundle |
| 세션 관리 | Codex 세션 | Omnigent Server·Host·Runner 세션 |
| Trace 원천 | Codex Hook + Codex OTel | Omnigent native OTel |
| 공통 요소 | Agent 폴더, UC Skills, Registry, Release, MLflow Trace와 Scorer | 동일 |

## 테스트

```bash
PYTHONPATH=../agentops/src:src ../agentops/.venv/bin/pytest -q
```
