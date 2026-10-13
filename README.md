# AgentOps Omnigent Adapter

Markdown 기반 Harness Agent를 Omnigent Web UI에서 만들고, 불러오고, 실행하는 B안 어댑터입니다.

Project 하나를 Agent 원본 폴더 하나로 관리합니다.

```text
<projects-root>/<project-name>/
├── AGENTS.md
├── README.md
├── spec.md
├── skills/
└── agents/
```

사용자는 이 폴더의 Markdown만 편집합니다. 실행에 필요한 Omnigent 설정과 Codex 설정은 `.aops/` 아래에 자동 생성됩니다.

## 1. 설치

먼저 [agentops](https://github.com/jeeHwon/agentops)를 설치하고 `aops configure`로 Databricks Profile, Registry, Warehouse와 MLflow Experiment를 설정합니다.

```bash
git clone https://github.com/jeeHwon/agentops-omnigent.git
cd agentops-omnigent
./install.sh
aops-omni --version
```

설치 과정은 Omnigent 0.17.0과 전용 Codex 0.146.0을 격리된 경로에 설치합니다. 사용자가 평소 실행하는 `codex` 명령과 전역 설정은 변경하지 않습니다.

## 2. Web UI 실행

여러 Agent Project를 둘 상위 폴더를 만들고 서버를 실행합니다.

```bash
mkdir -p ~/agent-projects
aops-omni serve ~/agent-projects --host 127.0.0.1 --port 6767
```

브라우저에서 <http://127.0.0.1:6767>을 엽니다.

왼쪽 Project 영역의 `+` 버튼에서 다음 작업을 선택할 수 있습니다.

- **새 Agent 만들기**: 표준 Markdown 템플릿이 들어 있는 새 로컬 폴더를 만듭니다.
- **Registry에서 불러오기**: 현재 사용자에게 허용된 Agent Release와 UC Skills를 검증하고 로컬 폴더로 복원합니다.

Project를 선택하면 그 아래에서 세션을 만들고 대화할 수 있습니다. 세션의 파일 영역에서 `AGENTS.md`, `README.md`, `spec.md`, `skills/**/*.md`, `agents/**/*.md`를 열어 저장할 수 있습니다.

## 3. 수정 내용 적용

원본 Markdown과 실행용 bundle은 역할이 다릅니다.

```text
사용자가 편집하는 원본 폴더
        ↓ 새 세션 생성 직전 자동 검증·조립
.aops/omnigent 실행 bundle
        ↓
Omnigent Runner → Codex
```

새 세션을 생성할 때마다 원본 Markdown을 검증하고 `.aops/omnigent`를 다시 만듭니다. Harness 지침이나 Skill을 수정한 뒤 새 세션을 열면 변경 내용이 적용됩니다. `.aops/`의 생성 파일은 직접 수정하지 않습니다.

## 4. Registry 등록

개발이 끝난 Agent는 원본 폴더를 새 Release로 등록합니다.

```bash
aops-omni validate ~/agent-projects/<project-name>
aops-omni register ~/agent-projects/<project-name> --version 1.0.0
```

등록 시 Agent Release Manifest, Skill 버전과 체크섬, 변경 불가능한 Release Artifact가 함께 저장됩니다. 운영 Agent는 공통 Skill의 최신본을 자동 추종하지 않고 등록 시점의 Skill 버전을 사용합니다.

## 5. 터미널 사용

Web UI 없이 같은 Agent 계약을 사용할 수도 있습니다.

```bash
# Registry 조회와 로드
aops-omni list
aops-omni load <agent-id> --version <version>

# 로컬 Agent 검증과 실행
aops-omni prepare <agent-folder>
aops-omni validate <agent-folder>
aops-omni run <agent-folder>
```

## 6. MLflow 모니터링

`run`과 `serve`는 Omnigent OTel Span을 AgentOps Collector로 전달합니다.

```text
Omnigent Agent · Tool · Sub-agent Span
        ↓
AgentOps Collector
        ↓
MLflow agent.turn Trace
        ↓
RelevanceToQuery · Safety · Completeness
```

Trace에는 입력, 출력, Token 사용량, Tool, Sub-agent, Agent ID·Release와 Skill 체크섬이 포함됩니다. B안 Trace에는 `agentops.runtime=omnigent`가 기록됩니다.

## 7. 테스트

```bash
PYTHONPATH=../agentops/src:src ../agentops/.venv/bin/pytest -q
node --check omnigent-web/agentops-projects.js
node tests/test_web_ui.mjs
bash -n install.sh scripts/build_omnigent_ui.sh
```
