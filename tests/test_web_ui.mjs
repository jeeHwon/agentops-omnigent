import fs from "node:fs";
import assert from "node:assert/strict";

let nativeCalls = 0;
let savedContent = null;
const files = [
  "AGENTS.md",
  "README.md",
  "SPEC.md",
  "agents/validator/AGENTS.md",
  "skills/demo/SKILL.md",
];

globalThis.window = {
  __OMNIGENT_BASE_PATH__: "",
  location: { href: "http://127.0.0.1:6767/" },
  fetch: async (input, options = {}) => {
    nativeCalls += 1;
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
    if (url.pathname === "/v1/sessions/test") {
      return Response.json({ project_id: "project-1" });
    }
    if (url.pathname === "/v1/projects/project-1") {
      return Response.json({
        id: "project-1",
        name: "demo-agent",
        config: { agentops_source: "/tmp/demo-agent" },
      });
    }
    if (url.pathname === "/v1/agentops/projects/demo-agent/files") {
      return Response.json({ data: files });
    }
    if (url.pathname === "/v1/agentops/projects/demo-agent/files/AGENTS.md") {
      if (String(options.method || "GET").toUpperCase() === "PUT") {
        savedContent = JSON.parse(options.body).content;
        return Response.json({ saved: true });
      }
      return Response.json({ path: "AGENTS.md", content: "# Demo Agent\n" });
    }
    throw new Error(`Unexpected native fetch: ${url.pathname}`);
  },
};
globalThis.document = { addEventListener() {} };

const source = fs.readFileSync(new URL("../omnigent-web/agentops-projects.js", import.meta.url), "utf8");
globalThis.eval(source);

const filesystem = "http://127.0.0.1:6767/v1/sessions/test/resources/environments/default/filesystem";
const listing = await (await window.fetch(filesystem)).json();
assert.deepEqual(listing.data.map((item) => item.path), [
  "AGENTS.md",
  "README.md",
  "SPEC.md",
  "agents",
  "skills",
]);

const skillListing = await (await window.fetch(`${filesystem}/skills`)).json();
assert.deepEqual(skillListing.data.map((item) => item.path), ["skills/demo"]);

const skillFolder = await (await window.fetch(`${filesystem}/skills/demo`)).json();
assert.deepEqual(skillFolder.data.map((item) => item.path), ["skills/demo/SKILL.md"]);

const callsBeforeBlockedWrite = nativeCalls;
const blocked = await window.fetch(`${filesystem}/.aops/omnigent/config.yaml`, { method: "PUT" });
assert.equal(blocked.status, 403);
assert.equal(nativeCalls, callsBeforeBlockedWrite);

const file = await (await window.fetch(`${filesystem}/AGENTS.md`)).json();
assert.equal(file.content, "# Demo Agent\n");
assert.equal(file.encoding, "utf-8");

const allowed = await window.fetch(`${filesystem}/AGENTS.md`, {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ content: "# Changed\n" }),
});
assert.equal(allowed.status, 200);
assert.equal(savedContent, "# Changed\n");

console.log("Web UI Agent Project filesystem adapter: ok");
