// Screenshots of every screen, taken from the simulator with headless Chrome.
//   1. .venv/bin/python -m bushido.app --simulate --port 8982
//   2. node tools/screenshots.mjs [port] -> sim-data/shots/*.png
// Used to review the look of the page without riding. No dependency beyond Node 22 and Chrome.
import { spawn } from "node:child_process";
import fs from "node:fs";
const simPort = process.argv[2] || "8982";      // node tools/screenshots.mjs [port]
const steps = [
 {
  "size": [
   1440,
   900
  ],
  "dark": false,
  "url": `http://127.0.0.1:${simPort}/`,
  "wait": 3000,
  "shot": "01-accueil",
  "full": true
 },
 {
  "eval": "actions.choose({kind:'workout'})",
  "wait": 500
 },
 {
  "eval": "actions.pick({file:'Sweet spot 3x10.zwo'})",
  "wait": 1200,
  "shot": "02-choix-entrainement",
  "full": true
 },
 {
  "eval": "actions.openSettings()",
  "wait": 600,
  "shot": "03-reglages"
 },
 {
  "eval": "saveSetting('ftp_w','185')",
  "wait": 1200,
  "shot": "04-reglage-enregistre"
 },
 {
  "eval": "actions.closeSettings(); actions.startActivity()",
  "wait": 6000,
  "shot": "05-en-selle-entrainement"
 },
 {
  "eval": "actions.quitActivity()",
  "wait": 1500
 },
 {
  "eval": "actions.nudge({sign:'1'}); actions.nudge({sign:'1'}); actions.nudge({sign:'1'}); actions.nudge({sign:'1'})",
  "wait": 14000,
  "shot": "06-sortie-libre"
 },
 {
  "eval": "actions.finishNow()",
  "wait": 2000,
  "shot": "07-bilan"
 },
 {
  "eval": "actions.closeSummary()",
  "wait": 800
 },
 {
  "eval": "actions.choose({kind:'route'})",
  "wait": 400
 },
 {
  "eval": "actions.pick({file:'Demo col synthetique.gpx'})",
  "wait": 1500,
  "shot": "08-choix-parcours",
  "full": true
 },
 {
  "eval": "actions.startActivity()",
  "wait": 16000,
  "shot": "09-en-selle-parcours"
 },
 {
  "eval": "actions.finishNow()",
  "wait": 1500
 },
 {
  "eval": "actions.closeSummary()",
  "wait": 800
 },
 {
  "eval": "actions.showTab({tab:'progress'})",
  "wait": 1800,
  "shot": "10-suivi",
  "full": true
 },
 {
  "eval": "actions.showTab({tab:'ride'}); actions.startCalibration()",
  "wait": 2500,
  "shot": "11-calibration"
 },
 {
  "eval": "actions.closeCalibration()",
  "wait": 600
 },
 {
  "dark": true,
  "wait": 800,
  "shot": "12-accueil-sombre",
  "full": true
 },
 {
  "eval": "actions.choose({kind:'free'})",
  "wait": 9000,
  "shot": "13-en-selle-sombre"
 },
 {
  "eval": "actions.finishNow()",
  "wait": 1500
 },
 {
  "eval": "actions.closeSummary()",
  "wait": 800
 },
 {
  "dark": false,
  "size": [
   820,
   1000
  ],
  "wait": 800,
  "shot": "14-accueil-etroit",
  "full": true
 },
 {
  "eval": "actions.choose({kind:'free'})",
  "wait": 7000,
  "shot": "15-en-selle-etroit",
  "full": true
 },
 {
  "eval": "actions.finishNow()",
  "wait": 1200
 },
 {
  "eval": "actions.closeSummary()",
  "wait": 500
 }
];
const out = new URL("../sim-data/shots/", import.meta.url).pathname;
fs.mkdirSync(out, { recursive: true });
const port = 9333;
const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${out}chrome-profile`,
   "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1", "--autoplay-policy=no-user-gesture-required",
   "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
let chromeErrors = "", exited = null;
chrome.stderr.on("data", chunk => { chromeErrors += chunk; });
chrome.on("exit", (code, signal) => { exited = `exit code ${code}, signal ${signal}`; });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target;
for (let i = 0; i < 80 && !target && !exited; i++) {
  await sleep(250);
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === "page"); } catch {}
}
if (!target) {
  console.log("Chrome did not start.", exited ? `It stopped: ${exited}.` : "It runs but its debugging port never answered.");
  console.log(chromeErrors.split("\n").filter(Boolean).slice(-8).join("\n")); chrome.kill(); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const waiting = new Map(), logs = [];
ws.addEventListener("message", e => {
  const m = JSON.parse(e.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") logs.push("EXCEPTION " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(m.params.type)) logs.push(m.params.type + " " + m.params.args.map(a => a.value ?? a.description).join(" "));
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") logs.push("LOG " + m.params.entry.text + " " + (m.params.entry.url || ""));
});
const send = (method, params = {}) => new Promise(r => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
for (const s of steps) {
  if (s.size) await send("Emulation.setDeviceMetricsOverride", { width: s.size[0], height: s.size[1], deviceScaleFactor: 1, mobile: false });
  if (s.dark !== undefined) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: s.dark ? "dark" : "light" }] });
  if (s.url) await send("Page.navigate", { url: s.url });
  if (s.eval) { const r = await send("Runtime.evaluate", { expression: s.eval, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) logs.push("EVAL " + s.eval.slice(0, 60) + " -> " + (r.result.exceptionDetails.exception?.description || "").slice(0, 200));
    else if (s.print) console.log(s.print, JSON.stringify(r.result?.result?.value)); }
  if (s.wait) await sleep(s.wait);
  if (s.shot) {
    let clip;
    if (s.full) { const m = await send("Page.getLayoutMetrics"); const c = m.result.cssContentSize; clip = { x: 0, y: 0, width: c.width, height: c.height, scale: 1 }; }
    const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: !!s.full, clip });
    fs.writeFileSync(`${out}${s.shot}.png`, Buffer.from(r.result.data, "base64")); console.log("shot", s.shot);
  }
}
fs.writeFileSync(`${out}console.txt`, logs.length ? logs.join("\n") : "clean");
console.log("console:", logs.length ? logs.join("\n") : "clean", "\nimages in", out);
ws.close(); chrome.kill(); process.exit(0);
