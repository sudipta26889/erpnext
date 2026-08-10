// Does the built bundle actually start in a browser?
//
// This exists because a node smoke test cannot answer that question: node defines
// `process`, so a bundle that throws `ReferenceError: process is not defined` in
// every browser loads perfectly under node. That shipped to production once.
//
// jsdom gives a window with no `process`, which is the property that matters here.
// Run after `yarn build`:  node test/smoke.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const bundle = join(root, "erpnext/public/ai/ai.js");

// The desk page script is evaluated with `new Function`, so a syntax error there
// takes the page down with a message that points at frappe's bundle, not at us.
// (A dropped "//" on a comment continuation did exactly that.)
const { execFileSync } = await import("node:child_process");
execFileSync(process.execPath, ["--check", join(root, "erpnext/ai/page/ai_chat/ai_chat.js")]);

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
	url: "http://erp.test/desk/ai-chat",
	pretendToBeVisual: true,
	runScripts: "outside-only",
});
const w = dom.window;
w.frappe = { csrf_token: "t" };
const replies = {
	"get_boot_info": { enabled: true, company: "Test Co", agent_id: "a-1" },
	"get_board_thread": { issue_id: "i-1", comments: [{ id: "c1", body: "hello", authorUserId: "board-concierge" }] },
	"list_approvals": { action_requests: [] },
};
w.fetch = async (url) => {
	const key = Object.keys(replies).find((k) => String(url).includes(k));
	return { ok: true, status: 200, json: async () => ({ message: replies[key] ?? {} }) };
};

const fail = (msg) => { console.error("FAIL:", msg); process.exit(1); };

w.eval(readFileSync(bundle, "utf8"));
if (typeof w.mountAI !== "function") fail("bundle did not define window.mountAI");

const host = w.document.getElementById("host");
w.mountAI(host);
await new Promise((r) => setTimeout(r, 800));

const html = host.innerHTML;
for (const expected of ["ai-app", "ai-composer", "Board room", "hello"]) {
	if (!html.includes(expected)) fail(`rendered output is missing ${JSON.stringify(expected)}: ${html.slice(0, 300)}`);
}
console.log("OK: bundle starts and renders in a browser-like environment");
// jsdom's pretendToBeVisual keeps a timer alive, so the process never exits on its
// own -- and `yarn build` chains this, which would hang the build forever.
dom.window.close();
process.exit(0);
