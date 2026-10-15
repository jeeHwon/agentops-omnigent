# AgentOps Omnigent Adapter

Markdown 기반 Skill-based Agent를 Omnigent Web UI에서 만들고, 불러오고, 실행하는 B안 어댑터입니다.

Project 하나를 Agent 원본 폴더 하나로 관리합니다.

```text
<projects-root>/<project-name>/
├── AGENTS.md
├── README.md
├── SPEC.md
├── skills/
└── agents/
```

사용자는 이 폴더의 Markdown만 편집합니다. 실행에 필요한 Omnigent 설정과 Codex 설정은 `.aops/` 아래에 자동 생성됩니다.

**보안 경계:** 이 어댑터는 원문을 로컬에 복사합니다. `대외비`/`개인정보` 또는 `강화` Agent는 로컬 실행·로드·등록을 거부하며, 원문 관리 API는 루프백 단일 사용자 전용입니다. HR처럼 실행만 허용하고 원문 비공개가 필요한 용도는 현재 방식으로 제공하지 않으며 후속 구현은 사용자 의사결정까지 보류합니다. `SPEC.md`의 `uc_permissions`는 접근제어가 아닙니다. 최신 필드·Registry 계약은 A의 `docs/SPEC_CONTRACT.md`를 참고하세요.

**LLM 경로:** B는 Omnigent의 Databricks profile 인증 브로커와 기존 Codex 전용 모델 egress를 사용합니다. 전역 Codex 공급자 설정을 상속하지 않으며, 이 작업에서 추가 연결은 변경하지 않습니다.

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

Project를 선택하면 그 아래에서 세션을 만들고 대화할 수 있습니다. 세션의 파일 영역에서 `AGENTS.md`, `README.md`, `SPEC.md`, `skills/**/*.md`, `agents/**/*.md`를 열어 저장할 수 있습니다.

Project 영역의 **검증·등록** 버튼에서는 현재 Agent를 검증하고 새 버전을 입력해 Registry에 등록할 수 있습니다. 따라서 Web UI 안에서 다음 개발 주기를 완료할 수 있습니다.

```text
Agent 생성 또는 Registry에서 불러오기
        ↓
Markdown 편집
        ↓
새 세션에서 대화 테스트
        ↓
검증·등록 → 새 Release 버전 입력
        ↓
UC Skills + Release Artifact + Agent Registry 등록
```

## 3. 수정 내용 적용

원본 Markdown과 실행용 bundle은 역할이 다릅니다.

```text
사용자가 편집하는 원본 폴더
        ↓ 새 세션 생성 직전 자동 검증·조립
.aops/omnigent 실행 bundle
        ↓
Omnigent Runner → Codex
```

새 세션을 생성할 때마다 원본 Markdown을 검증하고 `.aops/omnigent`를 다시 만듭니다. Skill-based Agent 지침이나 Skill을 수정한 뒤 새 세션을 열면 변경 내용이 적용됩니다. `.aops/`의 생성 파일은 직접 수정하지 않습니다.

## 4. Registry 등록

개발이 끝난 Agent는 Project 영역의 **검증·등록** 버튼을 눌러 새 Release로 등록합니다. 검증 결과에서 Agent ID, 현재 Release, Skill과 Sub-agent 수를 확인한 뒤 새 버전을 입력합니다.

동일한 작업을 터미널에서 수행할 수도 있습니다.

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

Trace에는 **관측된 경우에 한해** Token 사용량·Tool span, Agent ID, Release와 Skill 체크섬이 포함됩니다. Prompt/Tool 입출력 본문은 기본적으로 **수집하지 않습니다** (`AOPS_OMNIGENT_CAPTURE_CONTENT=true`로 명시적 활성화). 과거 실제 부모·자식 세션의 관측 토큰은 Omnigent 로컬 DB와 MLflow가 일치했지만, 추가 재시험에서는 **첫 Turn도 재개 Turn도 사용량이 누락**됐습니다. 실제 Sub-agent는 부모와 분리된 최상위 Trace로 저장됩니다. 자식 session ID가 본문과 분리된 OTel 속성으로 제공되지 않는 한 계층을 보장할 수 없습니다. 새 Trace의 `agentops.subagent_span_coverage=unverified`로 이 위험을 나타내며, 사용량의 요청별 100% 커버리지·비용·Tool 내부 호출 자동 추적도 보장하지 않습니다. B안 Trace에는 `agentops.runtime=omnigent`가 기록됩니다. 상태별 검증 범위는 A 저장소의 `docs/MONITORING_STATUS.md`를 참고하십시오.

현재 코드는 프롬프트와 응답이 모두 수집된 Trace만 평가하도록 scorer 필터를 생성합니다(`agentops.evaluation_inputs_available=true`). **기존 공유 MLflow 실험의 scorer 필터가 같다는 뜻은 아닙니다.** 정책 불일치 시 자동 변경하지 않고 중지합니다. 기본 비수집 모드의 Token·지연 지표와 업무 품질 지표를 혼동하지 마십시오.

Web UI의 체크섬은 현재 로컬 Project의 runtime metadata를 조회한 **최선 노력** 값입니다. Project ID 중복·동시 재조립 시 누락 또는 시점 불일치가 가능하며, 실행별 불변 snapshot을 위한 별도 세션 계측이 필요합니다.

## 7. 테스트

```bash
PYTHONPATH=../agentops/src:src ../agentops/.venv/bin/pytest -q
node --check omnigent-web/agentops-projects.js
node tests/test_web_ui.mjs
bash -n install.sh scripts/build_omnigent_ui.sh
```
