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

function filesystemPath(url) {
  const match = url.pathname.match(/\/v1\/sessions\/[^/]+\/resources\/environments\/[^/]+\/filesystem(?:\/(.*))?$/);
  if (!match) return null;
  try { return decodeURIComponent(match[1] || ""); }
  catch { return null; }
}

// Omnigent's standard workspace editor can browse every file visible to the
// Runner. Agent Project users should see only the editable Harness contract.
// The server also exposes a restricted Markdown API; this filter keeps the
// stock file tree and editor aligned with the same contract.
window.fetch = async (input, options = {}) => {
  const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
  const relative = filesystemPath(url);
  if (relative === null) return nativeFetch(input, options);
  const method = String(options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
  if (method !== "GET" && !agentMarkdownPath(relative)) {
    return new Response(JSON.stringify({ detail: "표준 Harness Markdown만 편집할 수 있습니다." }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  }
  const response = await nativeFetch(input, options);
  if (method !== "GET" || !response.ok) return response;
  let body;
  try { body = await response.clone().json(); }
  catch { return response; }
  if (!Array.isArray(body?.data)) return response;
  body.data = body.data.filter((item) => agentMarkdownPath(item.path, item.type));
  const headers = new Headers(response.headers);
  headers.delete("content-length");
  return new Response(JSON.stringify(body), { status: response.status, headers });
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
    .aops-error{min-height:19px;margin:12px 0 0;color:#d0302f;font-size:13px}.aops-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.aops-button{cursor:pointer;border:1px solid var(--border,#ccc);border-radius:9px;background:transparent;color:inherit;padding:8px 14px;font:inherit}.aops-button.primary{border-color:#ff5f46;background:#ff5f46;color:#fff}.aops-button:disabled{cursor:default;opacity:.55}`;
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

document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-testid="new-project"]') : null;
  if (!button) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  openDialog();
}, true);
