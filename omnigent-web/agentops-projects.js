const BASE = String(window.__OMNIGENT_BASE_PATH__ || "").replace(/\/$/, "");

const nativeFetch = window.fetch.bind(window);

function agentMarkdownPath(path, type = "file") {
  const parts = String(path).replace(/^\/+/, "").split("/").filter(Boolean);
  if (parts.length === 1) {
    return ["AGENTS.md", "README.md", "spec.md"].includes(parts[0])
      || (["skills", "agents"].includes(parts[0]) && type === "directory");
  }
  if (!["skills", "agents"].includes(parts[0])) return false;
  return type === "directory" || parts.at(-1).toLowerCase().endsWith(".md");
}

function filesystemRequest(url) {
  const match = url.pathname.match(/\/v1\/sessions\/([^/]+)\/resources\/environments\/[^/]+\/filesystem(?:\/(.*))?$/);
  if (!match) return null;
  try {
    return {
      sessionId: decodeURIComponent(match[1]),
      relative: decodeURIComponent(match[2] || "").replace(/^\/+|\/+$/g, ""),
    };
  }
  catch { return null; }
}

const sessionProjectCache = new Map();

async function nativeJson(path, options = {}) {
  const response = await nativeFetch(`${BASE}${path}`, {
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) return { response, body: null };
  return { response, body: await response.clone().json() };
}

function projectApiPath(projectName, relative = "") {
  const encoded = String(projectName).split("/").map(encodeURIComponent).join("/");
  const suffix = String(relative).split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return `/v1/agentops/projects/${encoded}/files${suffix ? `/${suffix}` : ""}`;
}

async function agentProjectForSession(sessionId) {
  if (!sessionProjectCache.has(sessionId)) {
    sessionProjectCache.set(sessionId, (async () => {
      const sessionResult = await nativeJson(
        `/v1/sessions/${encodeURIComponent(sessionId)}?include_items=false&include_liveness=false&include_usage=false`,
      );
      const projectId = sessionResult.body?.project_id;
      if (!projectId) return null;
      const projectResult = await nativeJson(`/v1/projects/${encodeURIComponent(projectId)}`);
      const project = projectResult.body;
      if (!project?.config?.agentops_source || !project?.name) return null;
      return {
        id: project.id,
        name: project.name,
        source: project.config.agentops_source,
        agentId: project.config.agentops_agent_id || null,
        release: project.config.agentops_release || null,
      };
    })());
  }
  return sessionProjectCache.get(sessionId);
}

async function activeAgentProject() {
  const location = new URL(window.location.href);
  const sessionMatch = location.pathname.match(/\/c\/([^/?]+)/);
  if (sessionMatch) {
    const project = await agentProjectForSession(decodeURIComponent(sessionMatch[1]));
    if (project) return project;
  }

  const requestedName = location.searchParams.get("project");
  if (!requestedName) return null;
  const projectsResult = await nativeJson("/v1/projects");
  const project = projectsResult.body?.data?.find((item) => item.name === requestedName);
  if (!project?.config?.agentops_source) return null;
  return {
    id: project.id,
    name: project.name,
    source: project.config.agentops_source,
    agentId: project.config.agentops_agent_id || null,
    release: project.config.agentops_release || null,
  };
}

function nextReleaseVersion(version) {
  const match = String(version || "").match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!match) return "";
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`;
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function directoryListing(files, relative) {
  const prefix = relative ? `${relative}/` : "";
  const entries = new Map();
  for (const file of files) {
    if (!file.startsWith(prefix)) continue;
    const remainder = file.slice(prefix.length);
    if (!remainder) continue;
    const [name, ...tail] = remainder.split("/");
    const path = prefix + name;
    const type = tail.length ? "directory" : "file";
    const existing = entries.get(path);
    if (!existing || type === "directory") {
      entries.set(path, {
        id: path,
        object: "session.environment.filesystem.entry",
        name,
        path,
        type,
        bytes: null,
        modified_at: null,
      });
    }
  }
  const data = [...entries.values()].sort((left, right) => (
    left.name === right.name ? 0 : left.name < right.name ? -1 : 1
  ));
  return {
    object: "list",
    data,
    first_id: data[0]?.id || null,
    last_id: data.at(-1)?.id || null,
    has_more: false,
  };
}

async function requestJsonBody(input, options) {
  if (options.body !== undefined && options.body !== null) {
    if (typeof options.body === "string") return JSON.parse(options.body);
    return options.body;
  }
  if (input instanceof Request) return input.clone().json();
  return {};
}

async function projectFilesystemResponse(input, options, filesystem, method) {
  const project = await agentProjectForSession(filesystem.sessionId);
  if (!project) return null;

  const listResult = await nativeJson(projectApiPath(project.name));
  if (!listResult.response.ok || !Array.isArray(listResult.body?.data)) {
    return listResult.response;
  }
  const files = listResult.body.data;
  const relative = filesystem.relative;

  if (method === "GET") {
    const isDirectory = !relative || files.some((file) => file.startsWith(`${relative}/`));
    if (isDirectory) return jsonResponse(directoryListing(files, relative));
    if (!files.includes(relative)) {
      return jsonResponse({ error: { code: "not_found", message: `Markdown file not found: ${relative}` } }, 404);
    }
    const fileResult = await nativeJson(projectApiPath(project.name, relative));
    if (!fileResult.response.ok) return fileResult.response;
    const content = String(fileResult.body?.content ?? "");
    return jsonResponse({
      object: "session.environment.filesystem.file_content",
      path: relative,
      content,
      content_type: "text/markdown",
      encoding: "utf-8",
      bytes: new TextEncoder().encode(content).length,
      truncated: false,
    });
  }

  if (method === "PUT") {
    if (!files.includes(relative) || !agentMarkdownPath(relative)) {
      return jsonResponse({ detail: "기존 표준 Harness Markdown만 편집할 수 있습니다." }, 403);
    }
    const body = await requestJsonBody(input, options);
    const content = typeof body?.content === "string" ? body.content : "";
    const saved = await nativeFetch(`${BASE}${projectApiPath(project.name, relative)}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!saved.ok) return saved;
    return jsonResponse({
      operation: "write",
      path: relative,
      created: false,
      bytes_written: new TextEncoder().encode(content).length,
    });
  }

  return jsonResponse({ detail: "Agent Project에서는 Markdown 조회와 저장만 지원합니다." }, 405);
}

// Omnigent's stock file panel normally reads through the Runner sandbox.
// Agent Project files are a separate, explicit contract: the editable source
// folder is authoritative and .aops is disposable. Route the stock panel to
// the restricted project API so reads and saves always target that source.
window.fetch = async (input, options = {}) => {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
  const filesystem = filesystemRequest(url);
  if (filesystem === null) return nativeFetch(input, options);
  const method = String(options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  if (method !== "GET" && !agentMarkdownPath(filesystem.relative)) {
    return new Response(JSON.stringify({ detail: "표준 Harness Markdown만 편집할 수 있습니다." }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  const projectResponse = await projectFilesystemResponse(input, options, filesystem, method);
  return projectResponse || nativeFetch(input, options);
};

async function request(path, options) {
  const response = await fetch(`${BASE}${path}`, { credentials: "same-origin", ...options });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail || body.error?.message || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}

function node(tag, props = {}, children = []) {
  const result = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "className") result.className = value;
    else if (key === "text") result.textContent = value;
    else result.setAttribute(key, value);
  }
  result.append(...children);
  return result;
}

function installStyles() {
  if (document.getElementById("aops-project-css")) return;
  const style = node("style", { id: "aops-project-css" });
  style.textContent = `
    .aops-overlay{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;background:#0008;padding:20px;font-family:inherit}
    .aops-dialog{width:min(560px,100%);box-sizing:border-box;border:1px solid var(--border,#ccc);border-radius:14px;background:var(--background,#fff);color:var(--foreground,#111);box-shadow:0 24px 80px #0005;padding:22px}
    .aops-title{margin:0;font-size:18px;font-weight:650}.aops-description{margin:7px 0 18px;color:var(--muted-foreground,#666);font-size:13px;line-height:1.5}
    .aops-modes{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:18px}.aops-mode{cursor:pointer;border:1px solid var(--border,#ccc);border-radius:10px;background:transparent;color:inherit;padding:12px;text-align:left;font:inherit}.aops-mode.active{border-color:#ff5f46;background:#ff5f4614}
    .aops-field{display:grid;gap:6px;margin-top:13px;font-size:13px}.aops-field>span,.aops-help{color:var(--muted-foreground,#666)}.aops-field input,.aops-field select{width:100%;box-sizing:border-box;border:1px solid var(--border,#ccc);border-radius:9px;background:transparent;color:inherit;padding:10px 11px;font:inherit}.aops-help{margin:9px 0 0;font-size:12px;line-height:1.5}
    .aops-error{min-height:19px;margin:12px 0 0;color:#d0302f;font-size:13px}.aops-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.aops-button{cursor:pointer;border:1px solid var(--border,#ccc);border-radius:9px;background:transparent;color:inherit;padding:8px 14px;font:inherit}.aops-button.primary{border-color:#ff5f46;background:#ff5f46;color:#fff}.aops-button:disabled{cursor:default;opacity:.55}
    .aops-project-action{cursor:pointer;border:1px solid var(--border,#555);border-radius:7px;background:transparent;color:var(--muted-foreground,#aaa);padding:3px 8px;font:inherit;font-size:11px;line-height:1.4}.aops-project-action:hover{border-color:#ff5f46;color:var(--foreground,#fff)}
    .aops-summary{display:grid;gap:6px;margin:0 0 14px;padding:12px;border:1px solid var(--border,#ccc);border-radius:10px;background:var(--muted,#00000008);font-size:12px}.aops-summary-row{display:grid;grid-template-columns:90px 1fr;gap:10px}.aops-summary-key{color:var(--muted-foreground,#666)}.aops-summary-value{overflow-wrap:anywhere}
    .aops-status{min-height:42px;margin:14px 0 0;padding:11px 12px;border:1px solid var(--border,#ccc);border-radius:10px;color:var(--muted-foreground,#666);font-size:12px;line-height:1.5}.aops-status.success{border-color:#2f9e6688;background:#2f9e6612;color:#238653}.aops-status.error{border-color:#d0302f88;background:#d0302f12;color:#d0302f}`;
  document.head.append(style);
}

function openDialog() {
  installStyles();
  let mode = "new";
  let releases = [];
  let loaded = false;
  const overlay = node("div", { className: "aops-overlay" });
  const dialog = node("div", { className: "aops-dialog", role: "dialog", "aria-modal": "true" });
  const newMode = node("button", { type: "button", className: "aops-mode active", text: "＋ 새 Agent 만들기" });
  const loadMode = node("button", { type: "button", className: "aops-mode", text: "⇩ Registry에서 불러오기" });
  const fields = node("div");
  const error = node("p", { className: "aops-error", role: "alert" });
  const cancel = node("button", { type: "button", className: "aops-button", text: "취소" });
  const submit = node("button", { type: "button", className: "aops-button primary", text: "만들기" });
  dialog.append(
    node("h2", { className: "aops-title", text: "Agent Project 추가" }),
    node("p", { className: "aops-description", text: "Project 하나가 AGENTS.md, spec.md, Skills와 Sub-agent를 담는 Agent 폴더입니다." }),
    node("div", { className: "aops-modes" }, [newMode, loadMode]), fields, error,
    node("div", { className: "aops-actions" }, [cancel, submit]),
  );
  overlay.append(dialog);
  document.body.append(overlay);

  const showError = (reason) => { error.textContent = reason instanceof Error ? reason.message : String(reason); };
  const close = () => overlay.remove();

  function render() {
    fields.replaceChildren();
    error.textContent = "";
    submit.disabled = cancel.disabled = false;
    newMode.classList.toggle("active", mode === "new");
    loadMode.classList.toggle("active", mode === "registry");
    submit.textContent = mode === "new" ? "만들기" : "불러오기";
    let select;
    if (mode === "registry") {
      if (releases.length === 0) {
        fields.append(node("p", { className: "aops-help", text: "현재 사용자가 불러올 수 있는 Agent Release가 없습니다." }));
        submit.disabled = true;
        return;
      }
      select = node("select", { "aria-label": "Agent Release" });
      for (const item of releases) {
        select.append(node("option", { value: `${item.agent_id}\u0000${item.version}`, text: `${item.agent_id}@${item.version} · Skills ${item.skills.length}개` }));
      }
      fields.append(node("label", { className: "aops-field" }, [node("span", { text: "Agent Release" }), select]));
    }
    const input = node("input", { type: "text", maxlength: "80", placeholder: "예: document-review-agent", "aria-label": "로컬 Project 이름" });
    if (select?.value) input.value = select.value.split("\u0000")[0];
    fields.append(node("label", { className: "aops-field" }, [node("span", { text: "로컬 Project 이름" }), input]));
    if (mode === "registry") {
      fields.append(node("p", { className: "aops-help", text: "현재 사용자에게 허용된 Release와 UC Skills를 검증해 로컬 폴더로 복원합니다." }));
      select?.addEventListener("change", () => { input.value = select.value.split("\u0000")[0]; });
    }
    queueMicrotask(() => input.focus());
    submit.onclick = async () => {
      const name = input.value.trim();
      if (!name) return;
      submit.disabled = cancel.disabled = true;
      try {
        const selected = select?.value.split("\u0000") || [];
        const result = await request(mode === "new" ? "/v1/agentops/projects" : "/v1/agentops/projects/load", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(mode === "new" ? { name } : { name, agent_id: selected[0], version: selected[1] }),
        });
        window.location.assign(`${BASE}/?project=${encodeURIComponent(result.project.name)}`);
      } catch (reason) {
        showError(reason);
        submit.disabled = cancel.disabled = false;
      }
    };
    input.addEventListener("keydown", (event) => { if (event.key === "Enter") submit.click(); });
  }

  newMode.onclick = () => { mode = "new"; render(); };
  loadMode.onclick = async () => {
    mode = "registry";
    loadMode.disabled = true;
    if (!loaded) {
      try { releases = (await request("/v1/agentops/registry")).data; loaded = true; }
      catch (reason) {
        mode = "new";
        render();
        showError(reason);
        loadMode.disabled = false;
        return;
      }
    }
    loadMode.disabled = false;
    render();
  };
  cancel.onclick = close;
  overlay.onclick = (event) => { if (event.target === overlay) close(); };
  overlay.onkeydown = (event) => { if (event.key === "Escape") close(); };
  render();
}

function summaryRow(label, value) {
  return node("div", { className: "aops-summary-row" }, [
    node("span", { className: "aops-summary-key", text: label }),
    node("span", { className: "aops-summary-value", text: value || "-" }),
  ]);
}

async function openRegisterDialog() {
  installStyles();
  const overlay = node("div", { className: "aops-overlay" });
  const dialog = node("div", { className: "aops-dialog", role: "dialog", "aria-modal": "true" });
  const description = node("p", {
    className: "aops-description",
    text: "현재 Markdown과 Skill을 검증한 뒤 변경 불가능한 새 Agent Release로 등록합니다.",
  });
  const summary = node("div", { className: "aops-summary" });
  const versionInput = node("input", {
    type: "text",
    placeholder: "예: 1.2.0",
    "aria-label": "새 Agent Release 버전",
    autocomplete: "off",
  });
  const status = node("div", {
    className: "aops-status",
    role: "status",
    text: "현재 Agent Project를 확인하고 있습니다.",
  });
  const cancel = node("button", { type: "button", className: "aops-button", text: "닫기" });
  const validateButton = node("button", { type: "button", className: "aops-button", text: "다시 검증" });
  const registerButton = node("button", { type: "button", className: "aops-button primary", text: "새 Release 등록" });
  validateButton.disabled = true;
  registerButton.disabled = true;
  dialog.append(
    node("h2", { className: "aops-title", text: "Agent 검증 및 등록" }),
    description,
    summary,
    node("label", { className: "aops-field" }, [
      node("span", { text: "새 Release 버전" }),
      versionInput,
    ]),
    node("p", {
      className: "aops-help",
      text: "기존 Release는 변경하지 않습니다. 등록 시점의 Harness, Skill 버전과 체크섬이 고정됩니다.",
    }),
    status,
    node("div", { className: "aops-actions" }, [cancel, validateButton, registerButton]),
  );
  overlay.append(dialog);
  document.body.append(overlay);

  let project = null;
  let validated = false;
  let busy = false;

  const close = () => { if (!busy) overlay.remove(); };
  const setStatus = (message, kind = "") => {
    status.className = `aops-status${kind ? ` ${kind}` : ""}`;
    status.textContent = message;
  };
  const refreshButtons = () => {
    cancel.disabled = busy;
    validateButton.disabled = busy || !project;
    registerButton.disabled = busy || !project || !validated || !versionInput.value.trim();
  };
  const validateProject = async () => {
    if (!project || busy) return;
    busy = true;
    validated = false;
    registerButton.textContent = "새 Release 등록";
    refreshButtons();
    setStatus("Harness, Skills, Sub-agent와 Omnigent 실행 구성을 검증하고 있습니다.");
    try {
      const result = await request(
        `/v1/agentops/projects/${encodeURIComponent(project.name)}/prepare`,
        { method: "POST" },
      );
      validated = Boolean(result.valid);
      project.agentId = result.agent_id;
      setStatus(
        `검증 완료 · Agent ${result.agent_id} · Skills ${result.skills.length}개 · Sub-agents ${result.subagents.length}개`,
        "success",
      );
      summary.replaceChildren(
        summaryRow("Project", project.name),
        summaryRow("Agent ID", result.agent_id),
        summaryRow("현재 Release", result.version),
        summaryRow("원본 폴더", result.source),
      );
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      busy = false;
      refreshButtons();
    }
  };

  cancel.onclick = close;
  overlay.onclick = (event) => { if (event.target === overlay) close(); };
  overlay.onkeydown = (event) => { if (event.key === "Escape") close(); };
  versionInput.addEventListener("input", refreshButtons);
  validateButton.onclick = validateProject;
  registerButton.onclick = async () => {
    const version = versionInput.value.trim();
    if (!project || !validated || !version || busy) return;
    busy = true;
    refreshButtons();
    setStatus(`${project.agentId || project.name}@${version} Release를 Registry에 등록하고 있습니다.`);
    try {
      const result = await request(
        `/v1/agentops/projects/${encodeURIComponent(project.name)}/register`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version }),
        },
      );
      validated = false;
      project.release = result.version;
      registerButton.textContent = "등록 완료";
      summary.replaceChildren(
        summaryRow("Project", project.name),
        summaryRow("Agent ID", result.agent_id),
        summaryRow("현재 Release", result.version),
        summaryRow("원본 폴더", project.source),
      );
      setStatus(
        `등록 완료 · ${result.agent_id}@${result.version} · UC Skills ${result.skills.length}개 · Release Artifact 생성 완료`,
        "success",
      );
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      busy = false;
      refreshButtons();
    }
  };

  try {
    project = await activeAgentProject();
    if (!project) {
      setStatus("등록할 Agent Project의 세션을 먼저 선택하세요.", "error");
      refreshButtons();
      return;
    }
    summary.replaceChildren(
      summaryRow("Project", project.name),
      summaryRow("Agent ID", project.agentId),
      summaryRow("현재 Release", project.release),
      summaryRow("원본 폴더", project.source),
    );
    versionInput.value = nextReleaseVersion(project.release);
    refreshButtons();
    await validateProject();
  } catch (reason) {
    setStatus(reason instanceof Error ? reason.message : String(reason), "error");
    refreshButtons();
  }
}

function installProjectAction() {
  if (typeof document.querySelector !== "function") return;
  const addButton = document.querySelector('[data-testid="new-project"]');
  if (!addButton || document.getElementById("aops-register-project")) return;
  installStyles();
  const registerButton = node("button", {
    id: "aops-register-project",
    type: "button",
    className: "aops-project-action",
    text: "검증·등록",
    title: "현재 Agent Project를 검증하고 새 Release로 등록",
    "aria-label": "Agent 검증 및 등록",
  });
  registerButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openRegisterDialog();
  });
  addButton.parentElement?.insertBefore(registerButton, addButton);
}

document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-testid="new-project"]') : null;
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  openDialog();
}, true);

if (typeof MutationObserver !== "undefined" && document.documentElement) {
  installProjectAction();
  const projectActionObserver = new MutationObserver(installProjectAction);
  projectActionObserver.observe(document.documentElement, { childList: true, subtree: true });
}
