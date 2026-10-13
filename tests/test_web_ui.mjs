import fs from "node:fs";
import assert from "node:assert/strict";

let nativeCalls = 0;
globalThis.window = {
  __OMNIGENT_BASE_PATH__: "",
  location: { href: "http://127.0.0.1:6767/" },
  fetch: async () => {
    nativeCalls += 1;
    return new Response(JSON.stringify({ data: [
      { path: ".aops", type: "directory" },
      { path: ".gitignore", type: "file" },
      { path: "AGENTS.md", type: "file" },
      { path: "skills", type: "directory" },
      { path: "skills/demo/SKILL.md", type: "file" },
      { path: "skills/demo/tool.py", type: "file" },
    ] }), { status: 200, headers: { "Content-Type": "application/json" } });
  },
};
globalThis.document = { addEventListener() {} };

const source = fs.readFileSync(new URL("../omnigent-web/agentops-projects.js", import.meta.url), "utf8");
globalThis.eval(source);

const filesystem = "http://127.0.0.1:6767/v1/sessions/test/resources/environments/default/filesystem";
const listing = await (await window.fetch(filesystem)).json();
assert.deepEqual(listing.data.map((item) => item.path), [
  "AGENTS.md",
  "skills",
  "skills/demo/SKILL.md",
]);

const callsBeforeBlockedWrite = nativeCalls;
const blocked = await window.fetch(`${filesystem}/.aops/omnigent/config.yaml`, { method: "PUT" });
assert.equal(blocked.status, 403);
assert.equal(nativeCalls, callsBeforeBlockedWrite);

const allowed = await window.fetch(`${filesystem}/skills/demo/SKILL.md`, { method: "PUT" });
assert.equal(allowed.status, 200);
assert.equal(nativeCalls, callsBeforeBlockedWrite + 1);

console.log("Web UI Markdown filter: ok");
