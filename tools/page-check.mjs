// Walks through every screen of the page against the simulator, with a stub DOM, and
// fails on any runtime error. No browser needed (useful where none can be started).
//   1. .venv/bin/python -m bushido.app --simulate --port 8982   (on a fresh sim-data/)
//   2. node tools/page-check.mjs [port]
import fs from "node:fs";
const port = process.argv[2] || "8982";
const dir = new URL("../bushido/static", import.meta.url).pathname, base = `http://127.0.0.1:${port}`;
const html = fs.readFileSync(dir + "/index.html", "utf8"), script = fs.readFileSync(dir + "/app.js", "utf8");
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
const errors = [], missing = new Set(), spoken = [], toasts = [], els = {};
const ctx = new Proxy({}, { get: (t, k) => k in t ? t[k] : () => {}, set: (t, k, v) => (t[k] = v, true) });
function el(id) {
  if (!ids.has(id) && !/^(set|saved)-/.test(id)) missing.add(id);
  if (/^(set|saved)-/.test(id) && !(els["settings-fields"]?.innerHTML || "").includes(`id="${id}"`)) return null;
  return els[id] ??= { id, style: {}, dataset: {}, hidden: /hidden/.test((html.match(new RegExp(`id="${id}"[^>]*>`)) || [""])[0]),
    value: "", textContent: "", innerHTML: "", className: "", href: "",
    classList: { toggle() {}, add() {}, remove() {} }, clientWidth: 900, clientHeight: 160, width: 0, height: 0,
    getContext: () => ctx, addEventListener() {}, click() {}, focus() {}, files: [] };
}
globalThis.document = { getElementById: el, documentElement: {}, addEventListener() {} };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#123456" });
globalThis.window = globalThis; globalThis.devicePixelRatio = 2;
globalThis.location = { host: `127.0.0.1:${port}`, reload: () => errors.push("unexpected reload") };
globalThis.localStorage = { getItem: () => "1", setItem() {} };
globalThis.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
globalThis.speechSynthesis = { speak: u => spoken.push(u.text), cancel() {} };
const realFetch = fetch;
globalThis.fetch = (u, o) => realFetch(base + u, o);
process.on("unhandledRejection", e => { console.log("REJECTION", e?.stack || e); errors.push("rejection: " + (e?.stack || e)); });
process.on("uncaughtException", e => { console.log("EXCEPTION", e?.stack || e); errors.push("exception"); process.exit(2); });
new Function(script + "\n;globalThis.__t = { actions, saveSetting, send, submitRide, submitEvent, get state() { return state; }, get progress() { return progress; } };")();
const t = globalThis.__t, wait = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(...a), flat = s => String(s).replace(/\s+/g, " ").trim();
const view = () => ["home", "ride", "summary"].filter(v => !els["view-" + v].hidden).join("+");
const seeToast = () => { if (els.toast && !els.toast.hidden && toasts.at(-1) !== els.toast.textContent) toasts.push(els.toast.textContent); };
setInterval(seeToast, 100);

await wait(2500);
log("1 home:", view(), "|", els["home-title"].textContent, "| choices:", (els.choices.innerHTML.match(/data-kind/g) || []).length,
    "| riders:", flat(els.riders.innerHTML).slice(0, 60), "| brake:", els["brake-text"].textContent);
log("  week:", flat(els.week.innerHTML).slice(0, 90)); log("  board:", flat(els.board.innerHTML).slice(0, 110));
log("  calcard:", flat(els.calcard.innerHTML).slice(0, 110));
t.actions.choose({ kind: "workout" }); await wait(300);
log("2 picker cards:", (els["picker-list"].innerHTML.match(/data-file/g) || []).length, "| detail hidden:", els["picker-detail"].hidden);
t.actions.pick({ file: "Test court.zwo" }); await wait(600);
log("  detail:", els["detail-name"].textContent, "|", els["detail-text"].textContent.slice(0, 50), "| legend:", flat(els["detail-legend"].innerHTML).length > 0);
t.actions.openSettings(); await wait(600);
log("3 settings:", els["settings-title"].textContent, "| fields:", (els["settings-fields"].innerHTML.match(/data-setting/g) || []).length);
t.saveSetting("ftp_w", "230"); await wait(900); log("  ftp saved:", JSON.stringify(els["saved-ftp_w"].textContent), "| server:", t.state.settings.ftp_w);
t.saveSetting("mass_kg", "5"); await wait(900); log("  out of range:", JSON.stringify(els["saved-mass_kg"].textContent), "| server:", t.state.settings.mass_kg);
t.saveSetting("ftp_w", "abc"); log("  not a number:", JSON.stringify(els["saved-ftp_w"].textContent));
t.send({ cmd: "bogus" }); await wait(600);
t.actions.closeSettings();
t.actions.startActivity(); await wait(2500);
log("4 ride:", view(), "|", els["ride-title"].textContent, "| power:", els.power.textContent, els["power-box"].className, "|", els["power-target"].textContent);
log("  now:", flat(els.now.innerHTML).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 170));
log("  metrics:", els["m-speed"].textContent, els["m-cadence"].textContent, els["m-distance"].textContent, els["m-time"].textContent, "| skip hidden:", els["btn-skip"].hidden, "| adjust hidden:", els.adjust.hidden);
t.actions.skip(); await wait(1200);
t.actions.quitActivity(); await wait(1200);
log("5 free ride:", els["ride-title"].textContent, "| modes hidden:", els.modes.hidden, "| setpoint:", els.setpoint.textContent, els["setpoint-label"].textContent, "|", els["power-target"].textContent);
t.actions.nudge({ sign: "1" }); t.actions.nudge({ sign: "1" }); await wait(700);
t.actions.setMode({ mode: "erg" }); await wait(700); log("  after +2 and erg:", els.setpoint.textContent, "|", els["power-target"].textContent);
t.actions.finish(); log("  finish armed:", els["btn-finish"].textContent); await wait(8000); t.actions.finish(); t.actions.finish(); await wait(1500);
log("6 summary:", view(), "|", els["sum-activity"].textContent, "|", flat(els["sum-stats"].innerHTML).replace(/<[^>]+>/g, " ").replace(/\s+/g, " "), "| export:", els["sum-export"].href);
t.actions.closeSummary(); await wait(900); log("  back:", view());
t.actions.choose({ kind: "route" }); await wait(300); t.actions.pick({ file: "Demo col synthetique.gpx" }); await wait(800);
log("7 route detail:", els["detail-name"].textContent, "|", els["detail-text"].textContent, "| plan hidden:", els["detail-plan"].hidden);
t.actions.startActivity(); await wait(3000);
log("  route ride:", flat(els.now.innerHTML).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 150), "| plan hidden:", els["ride-plan"].hidden);
t.actions.finishNow(); await wait(1200); t.actions.closeSummary(); await wait(600);
t.actions.choose({ kind: "ftp" }); await wait(500); log("8 ftp choice:", els["detail-name"].textContent, "| list hidden:", els["picker-list"].hidden);
t.actions.startCalibration(); await wait(1200);
const steps = new Set(); for (let i = 0; i < 80 && t.state.calibration.phase !== "done"; i++) { steps.add(els["cal-step"].textContent); await wait(250); }
await wait(600);
log("9 calibration steps:", [...steps].join(" > "), "| result:", els["cal-step"].textContent, "|", els["cal-hint"].textContent.slice(0, 60), "| button:", els["cal-close"].textContent);
t.actions.closeCalibration(); await wait(500); log("  overlay hidden:", els.calibration.hidden, "| calcard:", flat(els.calcard.innerHTML).replace(/<[^>]+>/g, " ").slice(0, 80));
t.actions.choose({ kind: "free" }); await wait(1500); log("10 free from home:", view(), els["ride-title"].textContent); t.actions.finishNow(); await wait(1000);
t.actions.showTab({ tab: "progress" }); await wait(900);
const text = id => flat(els[id].innerHTML).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 230);
log("11 progress view:", view() || "progress:" + !els["view-progress"].hidden, "|", els["prog-title"].textContent);
log("  event:", text("prog-event")); log("  week:", text("prog-week"));
els["ev-name"] = { value: "Étape du Tour" }; els["ev-date"] = { value: "2027-07-11" };
t.submitEvent({ preventDefault() {} }); await wait(1200); log("  event set:", text("prog-event"));
t.saveSetting("weekly_goal_km", "80"); await wait(1500);
for (const [id, v] of [["rf-date", new Date().toISOString().slice(0, 10)], ["rf-km", "62,5"], ["rf-hours", "2"], ["rf-minutes", "30"], ["rf-ascent", "800"], ["rf-note", "Vallée de l'Ourthe"]]) el(id).value = v;
await t.submitRide({ preventDefault() {} }); await wait(1200);
log("  week after ride + goal 80:", text("prog-week"));
log("  rides:", text("prog-rides")); log("  power:", els["prog-power-text"].textContent.slice(0, 160));
log("  legends:", flat(els["prog-weeks-legend"].innerHTML).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 120));
await t.actions.deleteRide({ id: "1" }); await wait(900); log("  after delete:", text("prog-rides").slice(0, 120));
t.actions.showTab({ tab: "ride" }); await wait(300); log("  back to:", view());
log("toasts:", JSON.stringify(toasts)); log("spoken:", JSON.stringify(spoken));
log("ids used but absent from the page:", [...missing]);
log("ERRORS:", errors.length ? errors.join("\n") : "none");
process.exit(errors.length ? 1 : 0);
