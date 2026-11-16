"use strict";
// Bushido console. Three views driven by the server state:
//   home (nobody riding) -> ride (a session is recording) -> summary (it just ended).
// Every clickable element carries data-action; `actions` maps them to handlers.

const $ = id => document.getElementById(id);

const BRAKE_TEXT = {
  connected: "Frein connecté",
  searching: "Pédale pour réveiller le frein",
  no_dongle: "Clé ANT introuvable : branche-la",
  offline: "Application arrêtée",
};
const SETTINGS = [
  { key: "mass_kg", label: "Poids du cycliste et du vélo", unit: "kg", step: 1,
    help: "Sert à calculer la résistance des pentes." },
  { key: "ftp_w", label: "FTP", unit: "W", step: 5,
    help: "La puissance que tu peux tenir une heure. Les entraînements sont calculés à partir d'elle. Le test FTP la mesure." },
  { key: "weekly_goal_km", label: "Objectif de la semaine", unit: "km", step: 5,
    help: "Mets 0 pour ne pas avoir d'objectif." },
  { key: "difficulty_pct", label: "Réalisme des pentes", unit: "%", step: 10,
    help: "À 100 %, les cols sont aussi durs que dehors. Baisse la valeur s'ils sont trop raides." },
  { key: "inertia_kg", label: "Élan simulé", unit: "kg", step: 1,
    help: "Adoucit les à-coups quand tu ralentis. Laisse 5 si tu hésites." },
];
const SLOPE_BANDS = [   // as on the boards along climbs
  { below: 4, color: "--slope-1", label: "moins de 4 %" },
  { below: 7, color: "--slope-2", label: "4 à 7 %" },
  { below: 10, color: "--slope-3", label: "7 à 10 %" },
  { below: Infinity, color: "--slope-4", label: "plus de 10 %" },
];
const ZONE_BANDS = [    // share of FTP
  { below: 0.6, color: "--zone-rest", label: "récupération" },
  { below: 0.9, color: "--slope-1", label: "endurance" },
  { below: 1.05, color: "--yellow", label: "seuil" },
  { below: Infinity, color: "--slope-3", label: "au-dessus du seuil" },
];
const WINDOW_NAMES = { 60: "1 minute", 300: "5 minutes", 1200: "20 minutes" };
const STEPS = { slope: 0.5, erg: 10 };
const ON_TARGET_SHARE = 0.07, ON_TARGET_MIN_W = 10;
const SLOPE_COLOUR_SPAN = 6;             // profile points on each side used to colour a stretch
const WORKOUT_CHART_TOP = 1.3;          // share of FTP at the top of a workout chart
const HISTORY_POINTS = 600;             // 5 minutes of free-ride power at 2 Hz
const TOAST_MS = 6000, SAVED_MS = 4000, CONFIRM_MS = 4000, SAVE_TIMEOUT_MS = 2500;
const LAST_SECONDS = 30, MIN_STEP_FOR_WARNING_S = 60;
const CALIBRATION_STEPS = {
  starting: ["Continue à pédaler", "Le frein prépare la mesure."],
  speed_up: ["Accélère jusqu'à 40 km/h", "Monte progressivement, reste assis."],
  stop_pedalling: ["Arrête de pédaler", "Ne freine pas."],
  coasting: ["Laisse la roue ralentir seule", "Ne pédale pas, ne freine pas."],
  processing: ["Calcul en cours", ""],
  resume: ["Repédale doucement", "Le frein a besoin de tourner pour donner le résultat."],
  reading: ["Lecture du résultat", "Continue à pédaler doucement."],
};
const CAL_MIN_N = 13, CAL_MAX_N = 19;

let ws = null, state = null, previous = null, pageVersion = null;
let library = { workouts: [], routes: [] }, stats = {}, profiles = {};
let picker = { kind: null, file: null, ghost: null, ghosts: [] };
let homeKey = "", settingsFor = null, pending = {}, savedTimers = {};
let calibrationOpen = false, noteDismissed = false, finishArmed = null;
let powerHistory = [], lastEventId = null, toastTimer = null, pip = null;
let voiceOn = localStorage.getItem("voice") === "1";
let tab = "ride", progress = null;
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
const DAY_MS = 86400000;

// ---- Small helpers ---------------------------------------------------------
const fr = (n, digits = 0) => Number(n).toLocaleString("fr-FR",
  { minimumFractionDigits: digits, maximumFractionDigits: digits });
const esc = text => String(text).replace(/[&<>"]/g,
  ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
const km = metres => fr(metres / 1000, 2);
function clock(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
}
function spoken(s) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return [m ? m + (m > 1 ? " minutes" : " minute") : "", sec ? sec + " secondes" : ""]
    .filter(Boolean).join(" ") || "0 seconde";
}
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const band = (bands, value) => bands.find(b => value < b.below);
const exportUrl = path => "/api/export/" + path.split("/").map(encodeURIComponent).join("/");
async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url + " : " + response.status);
  return response.json();
}

function toast(text, isError = false) {
  if (!text) return;
  $("toast").textContent = text;
  $("toast").className = isError ? "toast err" : "toast";
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, TOAST_MS);
}
function speak(text) {
  if (!voiceOn || !text || !("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "fr-FR";
  speechSynthesis.speak(utterance);
}
function tell(text) { toast(text); speak(text); }

// ---- Connection ------------------------------------------------------------
function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  else toast("L'application ne répond pas. Vérifie qu'elle est lancée.", true);
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.error) { onServerError(message.error); return; }
    if (pageVersion === null) pageVersion = message.version;
    // The server restarted with newer code: reload so page and server match.
    if (message.version !== pageVersion) { location.reload(); return; }
    state = message;
    $("banner").hidden = true;
    render();
  };
  ws.onclose = () => {
    $("brake").className = "status offline";
    $("brake-text").textContent = BRAKE_TEXT.offline;
    $("banner").textContent = "L'application est arrêtée. Relance-la dans le terminal, la page se reconnectera toute seule.";
    $("banner").hidden = false;
    setTimeout(connect, 1500);
  };
}

function onServerError(detail) {
  // Settings being saved are the usual cause: flag them where the rider is looking.
  for (const key of Object.keys(pending)) markSaved(key, "Non enregistré. Relance l'application puis réessaie.", true);
  pending = {};
  toast("Action refusée par l'application (" + detail + ").", true);
}

// ---- Data loaded on demand -------------------------------------------------
async function loadLibrary(select) {
  library = await getJson("/api/library");
  if (select) choose(select.endsWith(".gpx") ? "route" : "workout", select);
  renderHome(true);
}
async function loadStats() {
  stats = await getJson("/api/stats");
  renderHome(true);
}
async function loadProfile(file) {
  if (!profiles[file]) {
    try { profiles[file] = await getJson("/api/profile/" + encodeURIComponent(file)); }
    catch { profiles[file] = null; }
  }
  return profiles[file];
}

// ---- Rendering: top bar and view switch -------------------------------------
function render() {
  // While a session records, the ride screen always wins over the tabs.
  const view = state.session ? "ride" : state.last_session ? "summary"
             : tab === "progress" ? "progress" : "home";
  $("view-home").hidden = view !== "home";
  $("view-progress").hidden = view !== "progress";
  $("tabs").hidden = !!state.session;
  $("tab-ride").classList.toggle("on", view !== "progress");
  $("tab-progress").classList.toggle("on", view === "progress");
  $("view-ride").hidden = view !== "ride";
  $("view-summary").hidden = view !== "summary";

  $("brake").className = "status " + state.brake.status;
  $("brake-text").textContent = BRAKE_TEXT[state.brake.status];
  $("btn-voice").classList.toggle("on", voiceOn);

  if (!previous || previous.profile.active !== state.profile.active
      || !!previous.session !== !!state.session) { loadStats(); loadProgress(); }
  if (previous && !previous.session && state.session) { powerHistory = []; noteDismissed = false; }

  if (view === "home") renderHome(false);
  if (view === "ride") renderRide();
  if (view === "summary") renderSummary();
  renderSettings();
  renderCalibration();
  announce(previous, state);
  renderPip();
  previous = state;
}

// ---- Home ------------------------------------------------------------------
function renderHome(force) {
  if (!state) return;
  const me = state.profile.active, mine = stats[me];
  // Rebuild the lists only when something they show changed, so clicks are never lost.
  const key = JSON.stringify([state.profile, library, stats, picker, state.brake.status,
                              state.calibration.last, state.settings.weekly_goal_km]);
  if (!force && key === homeKey) return;
  homeKey = key;

  $("riders").innerHTML = state.profile.names.map(name =>
    `<button class="sign rider ${name === me ? "on" : ""}" data-action="selectRider" data-name="${esc(name)}">${esc(name)}</button>`
  ).join("") + `<button class="quiet" data-action="newRider">Ajouter un cycliste</button>`;

  const goal = state.settings.weekly_goal_km, week = mine ? mine.week_km : 0;
  $("week").innerHTML = goal > 0
    ? `<span><b>${fr(week, 1)} km</b> cette semaine, sur un objectif de ${fr(goal)} km</span>
       <div class="bar"><div style="width:${Math.min(week / goal * 100, 100)}%"></div></div>
       ${mine && mine.streak_weeks > 1 ? `<span>${mine.streak_weeks} semaines réussies d'affilée</span>` : ""}`
    : `<span><b>${fr(week, 1)} km</b> cette semaine</span>
       <button class="quiet" data-action="openSettings">Se fixer un objectif</button>`;

  $("home-title").textContent = `Que veux-tu faire, ${me} ?`;
  const ftpTest = library.workouts.find(w => w.ftp_test);
  const choices = [
    ["free", "Sortie libre", "Tu roules, tu règles la pente ou la puissance quand tu veux."],
    ["workout", "Entraînement", "Des intervalles guidés, calculés selon ton FTP."],
    ["route", "Parcours", "Un vrai tracé : la pente suit le relief."],
  ];
  if (ftpTest) choices.push(["ftp", "Test FTP", "20 minutes à fond pour mesurer ton niveau."]);
  $("choices").innerHTML = choices.map(([kind, title, text]) =>
    `<button class="sign choice ${picker.kind === kind ? "on" : ""}" data-action="choose" data-kind="${kind}">
       <strong>${title}</strong><span>${text}</span></button>`).join("");

  renderPicker();

  const last = state.calibration.last;
  const connected = state.brake.status === "connected";
  $("calcard").innerHTML = `<h2>Calibration du frein</h2>
    <p>${last ? `Dernière mesure : ${fr(last.value_n, 1)} N, le ${new Date(last.time).toLocaleDateString("fr-FR")}.`
              : "Pas encore faite sur cette installation."}</p>
    <p class="hint">À refaire quand tu changes de vélo, de pression de pneu ou de serrage du rouleau. Compte 2 minutes, après quelques minutes d'échauffement.</p>
    <button class="sign" data-action="startCalibration" ${connected ? "" : "disabled"}>Calibrer maintenant</button>
    ${connected ? "" : `<p class="hint">Pédale d'abord pour réveiller le frein.</p>`}`;

  const watts = value => value == null ? "–" : fr(value) + " W";
  const rows = Object.entries(stats).map(([name, p]) =>
    `<tr class="${name === me ? "me" : ""}"><td>${esc(name)}</td>
       <td>${watts(p.best_power[60])}</td><td>${watts(p.best_power[300])}</td><td>${watts(p.best_power[1200])}</td>
       <td>${fr(p.longest_km, 1)} km</td><td>${fr(p.week_km, 1)} km</td><td>${fr(p.total_km, 1)} km</td></tr>`).join("");
  const anySession = Object.values(stats).some(p => p.sessions > 0);
  $("board").innerHTML = anySession
    ? `<div class="scroll"><table><thead><tr><th>Cycliste</th><th>Record 1 min</th><th>5 min</th><th>20 min</th>
         <th>Plus longue sortie</th><th>Cette semaine</th><th>Total</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : `<p class="lede">Aucune séance enregistrée pour l'instant. Les records et les kilomètres de chacun apparaîtront ici après la première sortie.</p>`;
}

function renderPicker() {
  const kind = picker.kind, listed = kind === "workout" || kind === "route";
  $("picker").hidden = !kind || kind === "free";
  if (!kind || kind === "free") return;
  const items = kind === "route" ? library.routes : library.workouts.filter(w => !w.ftp_test);
  $("picker-list").hidden = !listed;
  $("picker-list").innerHTML = items.map(item => {
    const facts = kind === "route"
      ? `${fr(item.distance_m / 1000, 1)} km, ${fr(item.ascent_m)} m de dénivelé`
      : `${Math.round(item.duration_s / 60)} minutes`;
    return `<button class="sign card ${picker.file === item.file ? "on" : ""}" data-action="pick" data-file="${esc(item.file)}">
              <strong>${esc(item.name)}</strong><span>${facts}</span></button>`;
  }).join("") || `<p class="lede">Aucun fichier pour l'instant. Ajoute-en un avec le lien ci-dessous.</p>`;

  const all = library.workouts.concat(library.routes);
  const item = all.find(i => i.file === picker.file);
  $("picker-detail").hidden = !item;
  if (!item) return;
  $("detail-name").textContent = item.name;
  $("detail-text").textContent = item.description
    || (kind === "route" ? `${fr(item.distance_m / 1000, 1)} km et ${fr(item.ascent_m)} m de dénivelé.` : "");
  $("detail-start").textContent = "Démarrer";
  $("detail-ghosts").innerHTML = picker.ghosts.length
    ? `<span>Rouler contre :</span>` + picker.ghosts.map(g =>
        `<button class="sign ${picker.ghost === g.profile ? "on" : ""}" data-action="pickGhost" data-profile="${esc(g.profile)}">
           le meilleur temps de ${esc(g.profile)} (${clock(g.time_s)})</button>`).join("")
      + `<button class="sign ${picker.ghost ? "" : "on"}" data-action="pickGhost" data-profile="">personne</button>`
    : "";
  loadProfile(item.file).then(profile => {
    if (picker.file !== item.file) return;
    drawProfile("detail", profile, null);
  });
}

function choose(kind, file) {
  picker = { kind, file: file || null, ghost: null, ghosts: [] };
  if (kind === "ftp") picker.file = (library.workouts.find(w => w.ftp_test) || {}).file || null;
  if (picker.file) pickFile(picker.file); else renderHome(true);
}

async function pickFile(file) {
  picker.file = file; picker.ghost = null; picker.ghosts = [];
  renderHome(true);
  if (!file.endsWith(".gpx")) return;
  const ghosts = await getJson("/api/ghosts/" + encodeURIComponent(file));
  if (picker.file !== file) return;
  picker.ghosts = ghosts;
  // Race your own best time by default, else the fastest rider.
  const mine = ghosts.find(g => g.profile === state.profile.active);
  picker.ghost = mine ? mine.profile : ghosts.length ? ghosts[0].profile : null;
  renderHome(true);
}

// ---- Progress --------------------------------------------------------------
async function loadProgress() {
  progress = await getJson("/api/progress");
  renderProgress();
}

const longDate = iso => {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
};

function renderProgress() {
  if (!progress || !state) return;
  const p = progress, week = p.week, event = p.event;
  $("prog-title").textContent = `Suivi de ${p.profile}`;

  // The event being prepared: a countdown, and a form to set or change it.
  const eventForm = `<form class="inline-form" id="event-form">
      <input id="ev-name" maxlength="60" placeholder="Nom de l'objectif" value="${esc(event ? event.name : "")}">
      <input type="date" id="ev-date" value="${event ? event.date : ""}">
      <button type="submit" class="sign">${event ? "Modifier" : "Enregistrer l'objectif"}</button></form>`;
  if (event) {
    const days = event.days_left, weeks = Math.floor(days / 7);
    $("prog-event").innerHTML = `<h2>${esc(event.name)}</h2>
      <div class="figure">${days >= 0 ? fr(days) : 0}<small>${days === 1 ? "jour" : "jours"}</small></div>
      <p>${days > 0 ? `Le ${longDate(event.date)}, soit ${fr(weeks)} semaines d'entraînement devant toi.`
          : days === 0 ? "C'est aujourd'hui. Bonne route !" : `C'était le ${longDate(event.date)}.`}</p>${eventForm}`;
  } else {
    $("prog-event").innerHTML = `<h2>Ton objectif</h2>
      <p class="lede">Une date pour laquelle tu te prépares : une cyclosportive, un col, des vacances à vélo.</p>${eventForm}`;
  }

  // This week against the weekly goal, and what is left to ride.
  if (week.goal_km > 0) {
    const done = week.km >= week.goal_km;
    const perDay = week.remaining_km / week.days_left;
    $("prog-week").innerHTML = `<h2>Cette semaine</h2>
      <div class="figure">${fr(week.km, 1)}<small>km sur ${fr(week.goal_km)}</small></div>
      <div class="bar"><div style="width:${Math.min(week.km / week.goal_km * 100, 100)}%"></div></div>
      <p>${done ? "Objectif de la semaine atteint."
              : `Il te reste ${fr(week.remaining_km, 1)} km à faire d'ici dimanche soir, soit environ ${fr(perDay, 0)} km par jour sur ${week.days_left} ${week.days_left > 1 ? "jours" : "jour"}.`}</p>
      ${p.stats.streak_weeks > 0 ? `<p>${p.stats.streak_weeks} ${p.stats.streak_weeks > 1 ? "semaines réussies" : "semaine réussie"} d'affilée.</p>` : ""}
      <button class="quiet" data-action="openSettings">Changer l'objectif de la semaine</button>`;
  } else {
    $("prog-week").innerHTML = `<h2>Cette semaine</h2>
      <div class="figure">${fr(week.km, 1)}<small>km</small></div>
      <p class="lede">Fixe-toi un nombre de kilomètres par semaine pour voir ce qu'il te reste à faire.</p>
      <button class="sign" data-action="openSettings">Se fixer un objectif</button>`;
  }

  drawWeeks(p);
  drawPower(p);

  $("prog-rides").innerHTML = p.rides.length ? `<div class="scroll"><table><thead><tr><th class="left">Date</th><th class="left">Où</th>
      <th>Distance</th><th>Durée</th><th>Dénivelé</th><th>Puissance moyenne</th><th></th></tr></thead><tbody>`
    + p.rides.map(r => `<tr><td>${longDate(r.start)}</td>
        <td class="left">${r.outdoor ? "Sur route" + (r.note ? " : " + esc(r.note) : "")
                                              : "Home trainer" + (r.activities.length ? " : " + esc(r.activities[0].replace(/\.(zwo|gpx)$/, "")) : "")}</td>
        <td>${fr(r.distance_m / 1000, 1)} km</td><td>${clock(r.elapsed_s)}</td>
        <td>${r.outdoor && r.ascent_m ? fr(r.ascent_m) + " m" : "–"}</td>
        <td>${r.avg_power_w ? fr(r.avg_power_w) + " W" : "–"}</td>
        <td>${r.outdoor ? `<button class="quiet" data-action="deleteRide" data-id="${r.id}">Supprimer</button>` : ""}</td></tr>`).join("")
    + `</tbody></table></div>`
    : `<p class="lede">Aucune sortie pour l'instant. Roule sur le home trainer ou ajoute une sortie sur route ci-dessus.</p>`;
  if (!$("rf-date").value) $("rf-date").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

// Kilometres per week, home trainer and road stacked, with the weekly goal as a line.
function drawWeeks(p) {
  const { g, width, height } = canvasContext("prog-weeks");
  const weeks = p.weeks, goal = p.week.goal_km, base = height - 22, left = 4;
  const top = Math.max(goal, ...weeks.map(w => w.indoor_km + w.outdoor_km), 10) * 1.15;
  const slot = (width - left) / weeks.length, bar = slot * 0.62;
  g.font = `500 12px ${css("--text") || "sans-serif"}`; g.textAlign = "center";
  weeks.forEach((w, i) => {
    const x = left + i * slot + (slot - bar) / 2;
    const indoor = w.indoor_km / top * (base - 8), outdoor = w.outdoor_km / top * (base - 8);
    g.fillStyle = css("--ink"); g.fillRect(x, base - indoor, bar, indoor);
    g.fillStyle = css("--yellow"); g.fillRect(x, base - indoor - outdoor, bar, outdoor);
    const [, month, day] = w.start.split("-").map(Number);
    g.fillStyle = css("--muted"); g.fillText(`${day}/${month}`, x + bar / 2, height - 6);
    const total = w.indoor_km + w.outdoor_km;
    if (total > 0) { g.fillStyle = css("--ink"); g.fillText(fr(total, 0), x + bar / 2, base - indoor - outdoor - 5); }
  });
  if (goal > 0) {
    const y = base - goal / top * (base - 8);
    g.beginPath(); g.setLineDash([7, 5]); g.moveTo(0, y); g.lineTo(width, y);
    g.strokeStyle = css("--red"); g.lineWidth = 2; g.stroke(); g.setLineDash([]);
  }
  $("prog-weeks-legend").innerHTML = `<span><i style="background:var(--ink)"></i>home trainer</span>
    <span><i style="background:var(--yellow)"></i>sur route</span>`
    + (goal > 0 ? `<span><i style="background:var(--red)"></i>objectif de ${fr(goal)} km par semaine</span>` : "")
    + `<span>Chaque barre est une semaine, datée de son lundi. Le chiffre est le total en km.</span>`;
}

// Best 5- and 20-minute power of each week, with the FTP in force as a reference line.
function drawPower(p) {
  const { g, width, height } = canvasContext("prog-power");
  const weeks = p.weeks, values = weeks.flatMap(w => [w.best_300_w, w.best_1200_w]).filter(Boolean);
  const records = p.stats.best_power;
  $("prog-power-text").textContent = values.length
    ? `Tes records : ${records[60] ? fr(records[60]) + " W sur 1 minute" : "pas encore de record sur 1 minute"}`
      + `${records[300] ? ", " + fr(records[300]) + " W sur 5 minutes" : ""}`
      + `${records[1200] ? ", " + fr(records[1200]) + " W sur 20 minutes" : ""}. FTP réglé à ${fr(p.ftp_w)} W.`
    : `Pas encore de séance assez longue sur le home trainer. Après quelques sorties de plus de 5 minutes, ta progression s'affichera ici. FTP réglé à ${fr(p.ftp_w)} W.`;
  $("prog-power-legend").innerHTML = values.length ? `<span><i style="background:var(--blue)"></i>meilleure puissance sur 5 minutes dans la semaine</span>
    <span><i style="background:var(--green)"></i>sur 20 minutes</span><span><i style="background:var(--red)"></i>ton FTP actuel</span>` : "";
  if (!values.length) return;
  const top = Math.max(...values, p.ftp_w) * 1.12, low = Math.min(...values, p.ftp_w) * 0.75;
  const base = height - 22, slot = width / weeks.length;
  const y = watts => base - (watts - low) / (top - low) * (base - 10);
  g.font = `500 12px ${css("--text") || "sans-serif"}`; g.textAlign = "center";
  weeks.forEach((w, i) => {
    const [, month, day] = w.start.split("-").map(Number);
    g.fillStyle = css("--muted"); g.fillText(`${day}/${month}`, (i + 0.5) * slot, height - 6);
  });
  g.beginPath(); g.setLineDash([7, 5]); g.moveTo(0, y(p.ftp_w)); g.lineTo(width, y(p.ftp_w));
  g.strokeStyle = css("--red"); g.lineWidth = 2; g.stroke(); g.setLineDash([]);
  for (const [field, color] of [["best_300_w", "--blue"], ["best_1200_w", "--green"]]) {
    g.beginPath(); let started = false;
    weeks.forEach((w, i) => {
      if (!w[field]) return;
      const x = (i + 0.5) * slot;
      if (started) g.lineTo(x, y(w[field])); else g.moveTo(x, y(w[field]));
      started = true;
    });
    g.strokeStyle = css(color); g.lineWidth = 3; g.stroke();
    weeks.forEach((w, i) => {
      if (!w[field]) return;
      g.beginPath(); g.arc((i + 0.5) * slot, y(w[field]), 5, 0, 2 * Math.PI); g.fillStyle = css(color); g.fill();
      // 5-minute values above their point, 20-minute values below, so close values stay readable.
      g.fillStyle = css("--ink");
      g.fillText(fr(w[field]), (i + 0.5) * slot, y(w[field]) + (field === "best_300_w" ? -9 : 18));
    });
  }
}

async function submitRide(event) {
  event.preventDefault();
  const minutes = Number($("rf-hours").value || 0) * 60 + Number($("rf-minutes").value || 0);
  const body = { date: $("rf-date").value, km: Number(String($("rf-km").value).replace(",", ".")),
                 minutes, ascent_m: Number($("rf-ascent").value || 0), note: $("rf-note").value };
  const response = await fetch("/api/rides", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify(body) });
  if (!response.ok) { toast("Sortie refusée : vérifie la date et la distance.", true); return; }
  toast(`Sortie de ${fr(body.km, 1)} km ajoutée.`);
  $("rf-km").value = ""; $("rf-note").value = ""; $("rf-hours").value = 0; $("rf-minutes").value = 0; $("rf-ascent").value = 0;
  loadProgress(); loadStats();
}

function submitEvent(event) {
  event.preventDefault();
  const name = $("ev-name").value.trim(), day = $("ev-date").value;
  if (!!name !== !!day) { toast("Donne un nom et une date à ton objectif.", true); return; }
  send({ cmd: "event_set", name, date: day });
  toast(name ? `Objectif « ${name} » enregistré.` : "Objectif retiré.");
  setTimeout(loadProgress, 400);
}

// ---- Ride ------------------------------------------------------------------
function renderRide() {
  const { brake, control, session, activity: act } = state;
  const freeRide = !act;
  $("ride-title").textContent = act ? act.name : "Sortie libre";
  $("ride-sub").textContent = brake.status === "connected" ? "" : BRAKE_TEXT[brake.status];

  // Power, coloured by how far it is from the target when there is one.
  const power = brake.power_w || 0;
  const target = control.mode === "erg" ? control.erg_w : null;
  let position = "";
  if (target != null && brake.status === "connected") {
    const margin = Math.max(ON_TARGET_MIN_W, target * ON_TARGET_SHARE);
    position = power < target - margin ? "under" : power > target + margin ? "over" : "on";
  }
  $("power-box").className = "power " + position;
  $("power").textContent = fr(power);
  $("power-target").textContent = target != null
    ? `Cible ${fr(target)} W` + (position === "under" ? " : appuie un peu plus" : position === "over" ? " : relâche un peu" : "")
    : control.mode === "slope" ? `Pente simulée ${fr(control.slope_pct, 1)} %` : "";

  $("m-speed").textContent = fr(brake.speed_kmh || 0, 1);
  $("m-cadence").textContent = fr(brake.cadence_rpm || 0);
  $("m-distance").textContent = km(session.distance_m);
  $("m-time").textContent = clock(session.elapsed_s);
  $("m-avg").textContent = fr(session.avg_power_w);

  $("now").innerHTML = nowHtml(act);

  // End of a workout or a route: say so, and offer the two ways on.
  const done = state.finished_activity && !act && !noteDismissed;
  $("ride-note").hidden = !done;
  if (done) $("ride-note-text").textContent =
    `« ${state.finished_activity} » est terminé. La séance continue d'enregistrer tant que tu roules.`;

  // Controls: the rider adjusts the slope or the power, except when a workout step dictates it.
  const freeStep = act && act.kind === "workout" && act.target_w == null;
  const adjustable = freeRide || freeStep;
  $("modes").hidden = !freeRide;
  $("mode-slope").classList.toggle("on", control.mode === "slope");
  $("mode-erg").classList.toggle("on", control.mode === "erg");
  $("adjust").hidden = !adjustable || !(control.mode in STEPS);
  $("setpoint").textContent = control.mode === "erg" ? fr(control.erg_w) + " W" : fr(control.slope_pct, 1) + " %";
  $("setpoint-label").textContent = control.mode === "erg" ? "puissance imposée" : "pente simulée";
  $("btn-skip").hidden = !(act && act.kind === "workout");
  $("btn-quit-activity").hidden = !act;
  $("btn-finish").textContent = finishArmed ? "Confirmer la fin" : "Terminer la séance";
  $("btn-finish").classList.toggle("on", !!finishArmed);

  // Chart: the activity's profile, or the last minutes of power on a free ride.
  if (act) {
    loadProfile(act.file).then(profile => {
      if (state.activity && state.activity.file === act.file) drawProfile("ride", profile, state.activity);
    });
  } else {
    powerHistory.push(power);
    if (powerHistory.length > HISTORY_POINTS) powerHistory.shift();
    drawPowerHistory();
  }
}

function nowHtml(act) {
  if (!act) return "";
  if (act.kind === "workout") {
    const what = act.target_w != null ? `${esc(act.label)}`
      : act.ftp_test ? "Test : à fond, régulier" : "Roule à ton rythme";
    const average = act.ftp_test && act.test_average_w != null
      ? `<div class="now-line">Moyenne depuis le début du test <b>${fr(act.test_average_w)} W</b></div>` : "";
    const next = act.next_label
      ? `<div class="now-line">Ensuite : ${esc(act.next_label)}${act.next_w == null ? "" : ", " + fr(act.next_w) + " W"}</div>`
      : `<div class="now-line">Dernière étape</div>`;
    return `<div class="now-main">${what}</div>
      <div class="now-line">Encore <b>${clock(act.step_remaining_s)}</b> sur cette étape (${act.step} sur ${act.steps})</div>
      ${average}${next}
      <div class="now-line">Fin de l'entraînement dans <b>${clock(act.total_s - act.elapsed_s)}</b></div>`;
  }
  const g = act.ghost;
  const ghost = g ? `<div class="now-line ${g.gap_s >= 0 ? "ahead" : "behind"}">
      ${g.gap_s >= 0 ? "Avance" : "Retard"} sur ${esc(g.profile)} : <b>${clock(Math.abs(g.gap_s))}</b>, soit ${fr(Math.abs(g.gap_m))} m</div>` : "";
  return `<div class="now-main">Pente ${fr(act.slope_pct, 1)} %</div>
    <div class="now-line">Encore <b>${km(act.total_m - act.distance_m)} km</b> sur ${fr(act.total_m / 1000, 1)}</div>
    <div class="now-line">Altitude ${fr(act.elevation_m)} m, temps ${clock(act.elapsed_s)}</div>${ghost}`;
}

// ---- Summary ---------------------------------------------------------------
function renderSummary() {
  const s = state.last_session;
  $("sum-activity").textContent = (s.activity ? `« ${s.activity} ». ` : "Sortie libre. ")
    + `Séance enregistrée dans le profil de ${state.profile.active}.`;
  $("sum-stats").innerHTML = [
    [clock(s.elapsed_s), "durée"], [km(s.distance_m), "km"],
    [fr(s.avg_power_w), "W moyens"], [fr(s.energy_kj), "kJ dépensés"],
  ].map(([value, label]) => `<div><span class="m-value">${value}</span><span class="m-label">${label}</span></div>`).join("");
  const notes = s.events.map(e => `<div class="note">${esc(eventText(e))}</div>`);
  if (s.ftp_result) notes.push(`<div class="note"><span>Test FTP : ${fr(s.ftp_result.average_w)} W de moyenne sur 20 minutes,
      soit un FTP estimé à ${fr(s.ftp_result.ftp_w)} W.</span>
    <button class="sign" data-action="applyFtp" data-value="${s.ftp_result.ftp_w}">Utiliser ${fr(s.ftp_result.ftp_w)} W comme FTP</button></div>`);
  $("sum-extras").innerHTML = notes.join("");
  $("sum-export").hidden = !s.export;
  if (s.export) $("sum-export").href = exportUrl(s.export);
}

// ---- Settings --------------------------------------------------------------
function renderSettings() {
  if ($("settings").hidden) return;
  const me = state.profile.active;
  if (settingsFor !== me) {              // build the form once per rider, then only watch values
    settingsFor = me;
    $("settings-title").textContent = `Réglages de ${me}`;
    $("settings-fields").innerHTML = SETTINGS.map(f => `
      <div class="field">
        <label for="set-${f.key}">${f.label}</label>
        <div class="entry"><input type="number" id="set-${f.key}" data-setting="${f.key}" step="${f.step}"
             value="${state.settings[f.key]}" inputmode="decimal"><span>${f.unit}</span></div>
        <div class="help">${f.help}</div>
        <div class="saved" id="saved-${f.key}"></div>
      </div>`).join("");
  }
  // A value sent to the server counts as saved once the server reports it back.
  // The server keeps values within limits, so it may answer with another one.
  for (const [key, sent] of Object.entries(pending)) {
    const now = state.settings[key];
    if (now === sent.value) {
      delete pending[key];
      markSaved(key, "Enregistré", false);
    } else if (now !== sent.before || Date.now() - sent.at > SAVE_TIMEOUT_MS) {
      delete pending[key];
      if ($("set-" + key)) $("set-" + key).value = now;
      markSaved(key, `Valeur hors limites : réglage gardé à ${fr(now)}.`, true);
    }
  }
}

function markSaved(key, text, isError) {
  const el = $("saved-" + key);
  if (!el) return;
  el.textContent = text;
  el.className = "saved " + (isError ? "err" : "ok");
  clearTimeout(savedTimers[key]);
  if (!isError) savedTimers[key] = setTimeout(() => { el.textContent = ""; }, SAVED_MS);
}

function saveSetting(key, raw) {
  const value = Number(String(raw).replace(",", "."));
  if (raw === "" || Number.isNaN(value)) { markSaved(key, "Entre un nombre.", true); return; }
  pending[key] = { value, before: state.settings[key], at: Date.now() };
  markSaved(key, "Enregistrement…", false);
  send({ cmd: "setting", key, value });
}

// ---- Calibration -----------------------------------------------------------
function renderCalibration() {
  const { phase, value_n } = state.calibration;
  const running = phase && phase !== "done" && phase !== "error";
  $("calibration").hidden = !calibrationOpen;
  if (!calibrationOpen) return;
  $("cal-speed").textContent = fr(state.brake.speed_kmh || 0);
  $("cal-close").textContent = running ? "Annuler" : "Fermer";
  let step, hint;
  if (running) [step, hint] = CALIBRATION_STEPS[phase];
  else if (phase === "done" && value_n != null) {
    step = `Mesure : ${fr(value_n, 1)} N`;
    hint = value_n < CAL_MIN_N ? `C'est un peu faible (la plage normale va de ${CAL_MIN_N} à ${CAL_MAX_N} N). Serre légèrement le rouleau contre le pneu, puis recommence.`
         : value_n > CAL_MAX_N ? `C'est un peu élevé (la plage normale va de ${CAL_MIN_N} à ${CAL_MAX_N} N). Desserre légèrement le rouleau, puis recommence.`
         : "C'est dans la plage normale. Le frein en tient compte dès maintenant.";
  } else if (phase === "error") {
    step = "Calibration interrompue";
    hint = "Le frein a quitté la procédure, souvent parce que la roue s'est arrêtée. Ferme, repédale et relance.";
  } else { step = "Pédale pour commencer"; hint = ""; }
  $("cal-step").textContent = step;
  $("cal-hint").textContent = hint;
}

// ---- Announcements ---------------------------------------------------------
function eventText(e) {
  if (e.type === "record") return `Record battu : ${fr(e.watts)} W sur ${WINDOW_NAMES[e.window_s]}.`;
  if (e.type === "goal") return `Objectif de la semaine atteint : ${fr(e.km)} km.`;
  if (e.type === "workout_finished") return `Entraînement « ${e.name} » terminé.`;
  if (e.type === "route_finished") {
    let text = `Parcours « ${e.name} » terminé en ${spoken(e.time_s)}.`;
    if (e.ghost_profile) text += e.ghost_gap_s >= 0
      ? ` Tu bats le meilleur temps de ${e.ghost_profile} de ${spoken(e.ghost_gap_s)}.`
      : ` ${e.ghost_profile} garde ${spoken(-e.ghost_gap_s)} d'avance.`;
    return text;
  }
  return "";
}

// Compare the previous and the new state, and say what changed.
function announce(before, now) {
  const newest = now.events.length ? now.events[now.events.length - 1].id : 0;
  if (lastEventId === null) lastEventId = newest;          // nothing to replay on page load
  for (const e of now.events) if (e.id > lastEventId) tell(eventText(e));
  lastEventId = Math.max(lastEventId, newest);
  if (!before) return;

  if (!before.session && now.session) tell("Séance démarrée. Elle s'enregistre dès que tu roules.");
  if (before.session && !now.session)
    tell(now.last_session ? "Séance enregistrée." : "Séance trop courte : elle n'a pas été enregistrée.");
  if (before.brake.status === "connected" && now.brake.status === "searching" && now.session)
    toast("Le frein s'est endormi : la séance est en pause jusqu'à ce que tu repédales.");
  if (before.profile.active !== now.profile.active) toast(`Profil de ${now.profile.active} activé.`);

  const a = now.activity, b = before.activity;
  if (a && a.kind === "workout") {
    if (!b || b.file !== a.file || b.step !== a.step) {
      const profile = profiles[a.file], segment = profile && profile.segments[a.step - 1];
      const length = spoken(segment ? segment[1] : a.step_remaining_s);
      speak(a.target_w != null ? `${a.label}, ${a.target_w} watts, ${length}.` : `${a.label}, ${length}.`);
    } else if (b.step_remaining_s > LAST_SECONDS && a.step_remaining_s <= LAST_SECONDS
               && stepLength(a) >= MIN_STEP_FOR_WARNING_S) {
      speak(`Plus que ${LAST_SECONDS} secondes.`);
    }
  }
  if (a && b && a.kind === "route" && b.kind === "route") {
    const done = Math.floor(a.distance_m / 1000);
    if (done > Math.floor(b.distance_m / 1000)) {
      let text = `Kilomètre ${done}. Pente ${Math.round(a.slope_pct)} pour cent.`;
      if (a.ghost) text += a.ghost.gap_s >= 0 ? ` ${spoken(a.ghost.gap_s)} d'avance sur ${a.ghost.profile}.`
                                              : ` ${spoken(-a.ghost.gap_s)} de retard sur ${a.ghost.profile}.`;
      speak(text);
    }
  }
  if (now.ftp_result && !before.ftp_result)
    tell(`Test terminé : ${now.ftp_result.average_w} watts de moyenne, FTP estimé à ${now.ftp_result.ftp_w} watts.`);
  const cal = now.calibration, was = before.calibration;
  if (cal.phase !== was.phase && CALIBRATION_STEPS[cal.phase]) speak(CALIBRATION_STEPS[cal.phase][0] + ".");
  if (cal.phase === "done" && was.phase !== "done" && cal.value_n != null)
    speak(`Calibration terminée : ${fr(cal.value_n, 1)} newtons.`);
}

function stepLength(act) {
  const profile = profiles[act.file];
  return profile && profile.segments[act.step - 1] ? profile.segments[act.step - 1][1] : 0;
}

// ---- Charts ----------------------------------------------------------------
function canvasContext(id) {
  const canvas = $(id), ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, width, height);
  return { g, width, height };
}

// `where` is "detail" (preview on the home screen) or "ride"; `act` is the live activity or null.
function drawProfile(where, profile, act) {
  const { g, width, height } = canvasContext(where + "-profile");
  $(where + "-plan").hidden = !profile || profile.kind !== "route";
  $(where + "-legend").innerHTML = "";
  if (!profile) return;
  const bands = profile.kind === "route" ? SLOPE_BANDS : ZONE_BANDS;
  $(where + "-legend").innerHTML = bands.map(b =>
    `<span><i style="background:var(${b.color})"></i>${b.label}</span>`).join("");
  let cursor = null, ghostCursor = null;
  if (profile.kind === "workout") {
    for (const [start, duration, from, to] of profile.segments) {
      const x0 = start / profile.total_s * width, x1 = (start + duration) / profile.total_s * width;
      const y = share => height - Math.min(share, WORKOUT_CHART_TOP) / WORKOUT_CHART_TOP * (height - 8);
      g.beginPath();
      if (from == null) {                         // free step: a low hatched block
        g.rect(x0, height * 0.55, x1 - x0, height * 0.45);
        g.fillStyle = css("--hair");
      } else {
        g.moveTo(x0, height); g.lineTo(x0, y(from)); g.lineTo(x1, y(to)); g.lineTo(x1, height);
        g.fillStyle = css(band(ZONE_BANDS, (from + to) / 2).color);
      }
      g.fill();
      g.fillStyle = css("--panel"); g.fillRect(x1 - 1, 0, 1, height);    // thin gap between steps
    }
    if (act) cursor = act.elapsed_s / profile.total_s * width;
  } else {
    const points = profile.points, elevations = points.map(p => p[1]);
    const low = Math.min(...elevations), span = Math.max(Math.max(...elevations) - low, 30);
    const y = elevation => height - 8 - (elevation - low) / span * (height - 20);
    for (let i = 1; i < points.length; i++) {      // one column per stretch, coloured by its slope
      const [d0, e0] = points[i - 1], [d1, e1] = points[i];
      // The colour uses the slope over a longer stretch, so it reads as bands, not stripes.
      const a = points[Math.max(i - SLOPE_COLOUR_SPAN, 0)], b = points[Math.min(i + SLOPE_COLOUR_SPAN, points.length - 1)];
      const slope = b[0] > a[0] ? (b[1] - a[1]) / (b[0] - a[0]) * 100 : 0;
      g.beginPath();
      g.moveTo(d0 / profile.total_m * width, height); g.lineTo(d0 / profile.total_m * width, y(e0));
      g.lineTo(d1 / profile.total_m * width + 0.5, y(e1)); g.lineTo(d1 / profile.total_m * width + 0.5, height);
      g.fillStyle = css(band(SLOPE_BANDS, slope).color);
      g.fill();
    }
    drawPlan(where + "-map", points, act);
    if (act) {
      cursor = act.distance_m / profile.total_m * width;
      if (act.ghost) ghostCursor = act.ghost.distance_m / profile.total_m * width;
    }
  }
  if (ghostCursor != null) verticalLine(g, ghostCursor, height, css("--muted"), [6, 5]);
  if (cursor != null) verticalLine(g, cursor, height, css("--ink"), []);
}

function verticalLine(g, x, height, color, dash) {
  g.beginPath(); g.setLineDash(dash); g.moveTo(x, 0); g.lineTo(x, height);
  g.strokeStyle = color; g.lineWidth = 3; g.stroke(); g.setLineDash([]);
}

// Plan view of the route, north up, with the rider (filled) and the ghost (ring).
function drawPlan(id, points, act) {
  const { g, width, height } = canvasContext(id);
  const lats = points.map(p => p[2]), lons = points.map(p => p[3]);
  const latLow = Math.min(...lats), lonLow = Math.min(...lons);
  const squeeze = Math.cos(latLow * Math.PI / 180);         // a degree of longitude is shorter
  const latSpan = Math.max(...lats) - latLow, lonSpan = (Math.max(...lons) - lonLow) * squeeze;
  const pad = 8, scale = Math.min((width - 2 * pad) / Math.max(lonSpan, 1e-6), (height - 2 * pad) / Math.max(latSpan, 1e-6));
  const offsetX = (width - lonSpan * scale) / 2, offsetY = (height - latSpan * scale) / 2;
  const xy = (lat, lon) => [offsetX + (lon - lonLow) * squeeze * scale, height - offsetY - (lat - latLow) * scale];
  g.beginPath();
  points.forEach((p, i) => { const [x, y] = xy(p[2], p[3]); if (i) g.lineTo(x, y); else g.moveTo(x, y); });
  g.strokeStyle = css("--ink"); g.lineWidth = 2; g.lineJoin = "round"; g.stroke();
  if (!act) return;
  if (act.ghost) {
    const [x, y] = xy(act.ghost.lat, act.ghost.lon);
    g.beginPath(); g.arc(x, y, 5, 0, 2 * Math.PI); g.strokeStyle = css("--muted"); g.lineWidth = 2.5; g.stroke();
  }
  const [x, y] = xy(act.lat, act.lon);
  g.beginPath(); g.arc(x, y, 6, 0, 2 * Math.PI); g.fillStyle = css("--red"); g.fill();
}

function drawPowerHistory() {
  const { g, width, height } = canvasContext("ride-profile");
  $("ride-plan").hidden = true;
  $("ride-legend").innerHTML = `<span>Ta puissance sur les 5 dernières minutes</span>`;
  const top = Math.max(200, ...powerHistory) * 1.1;
  g.beginPath();
  powerHistory.forEach((watts, i) => {
    const x = width - (powerHistory.length - 1 - i) * (width / HISTORY_POINTS);
    const y = height - 6 - watts / top * (height - 12);
    if (i) g.lineTo(x, y); else g.moveTo(x, y);
  });
  g.strokeStyle = css("--ink"); g.lineWidth = 3; g.lineJoin = "round"; g.stroke();
}

// ---- Floating mini window (Chrome and Edge) ----------------------------------
async function togglePip() {
  if (pip) { pip.close(); return; }
  if (!("documentPictureInPicture" in window)) {
    toast("La mini-fenêtre demande Chrome ou Edge.", true);
    return;
  }
  pip = await documentPictureInPicture.requestWindow({ width: 360, height: 180 });
  pip.document.body.innerHTML = `<style>
    body { margin: 0; padding: 10px 16px; background: #1b1f22; color: #f2f3ef;
           font: 600 16px/1.25 "Avenir Next Condensed", "Arial Narrow", sans-serif;
           font-variant-numeric: tabular-nums; }
    #w { font-size: 4.4rem; font-weight: 700; line-height: .9; }
    #w small { font-size: 1.1rem; color: #a3abb1; margin-left: 6px; }
    #w.under { color: #5b9be0; } #w.over { color: #f0555a; } #w.on { color: #4cc27e; }
    #t { font-size: 1.3rem; margin: 6px 0 2px; }
    #o { color: #a3abb1; }
  </style><div id="w"></div><div id="t"></div><div id="o"></div>`;
  pip.addEventListener("pagehide", () => { pip = null; $("btn-pip").classList.remove("on"); });
  $("btn-pip").classList.add("on");
  renderPip();
}

function renderPip() {
  if (!pip || !state) return;
  const { brake, control, session, activity: a } = state, d = pip.document;
  const target = control.mode === "erg" ? `cible ${fr(control.erg_w)} W`
               : control.mode === "slope" ? `pente ${fr(control.slope_pct, 1)} %` : "";
  let line = brake.status === "connected" ? "" : BRAKE_TEXT[brake.status];
  if (a && a.kind === "workout") line = `${a.label}, encore ${clock(a.step_remaining_s)}`;
  else if (a && a.kind === "route") {
    line = `encore ${km(a.total_m - a.distance_m)} km`;
    if (a.ghost) line += `, ${a.ghost.gap_s >= 0 ? "avance" : "retard"} ${clock(Math.abs(a.ghost.gap_s))}`;
  }
  d.getElementById("w").className = $("power-box").className.replace("power", "").trim();
  d.getElementById("w").innerHTML = `${fr(brake.power_w || 0)}<small>W ${esc(target)}</small>`;
  d.getElementById("t").textContent = line;
  d.getElementById("o").textContent = `${fr(brake.speed_kmh || 0, 1)} km/h, ${brake.cadence_rpm || 0} tr/min`
    + (session ? `, ${km(session.distance_m)} km, ${clock(session.elapsed_s)}` : "");
}

// ---- Actions ---------------------------------------------------------------
const actions = {
  selectRider: d => send({ cmd: "profile_select", name: d.name }),
  newRider: () => { $("new-rider").hidden = false; $("new-rider-name").value = ""; $("new-rider-name").focus(); },
  cancelNewRider: () => { $("new-rider").hidden = true; },
  choose: d => {
    if (d.kind === "free") { startFreeRide(); return; }
    choose(d.kind);
  },
  pick: d => pickFile(d.file),
  pickGhost: d => { picker.ghost = d.profile || null; renderHome(true); },
  startActivity: () => {
    if (!picker.file) return;
    if (state.brake.status !== "connected") toast("Séance prête : elle démarre dès que tu pédales.");
    send({ cmd: "activity_start", file: picker.file, ghost: picker.ghost });
  },
  importFile: () => $("file-input").click(),
  startCalibration: () => { calibrationOpen = true; send({ cmd: "calibration_start" }); },
  closeCalibration: () => {
    const phase = state.calibration.phase;
    if (phase && phase !== "done" && phase !== "error") send({ cmd: "calibration_cancel" });
    calibrationOpen = false;
    renderCalibration();
    homeKey = "";
  },
  openSettings: () => { $("settings").hidden = false; settingsFor = null; renderSettings(); },
  closeSettings: () => { $("settings").hidden = true; },
  setMode: d => send({ cmd: "mode", mode: d.mode }),
  nudge: d => {
    const mode = state.control.mode, step = STEPS[mode];
    if (!step) return;
    const current = mode === "erg" ? state.control.erg_w : state.control.slope_pct;
    send({ cmd: mode, value: current + Number(d.sign) * step });
  },
  skip: () => send({ cmd: "activity_skip" }),
  quitActivity: () => { send({ cmd: "activity_stop" }); toast("Tu continues en sortie libre. La séance s'enregistre toujours."); },
  finish: () => {
    // Two clicks, so a stray touch on the screen does not end an hour of riding.
    if (finishArmed) { actions.finishNow(); return; }
    finishArmed = setTimeout(() => { finishArmed = null; if (state.session) renderRide(); }, CONFIRM_MS);
    renderRide();
  },
  finishNow: () => { clearTimeout(finishArmed); finishArmed = null; send({ cmd: "session_stop" }); },
  dismissNote: () => { noteDismissed = true; renderRide(); },
  closeSummary: () => { picker = { kind: null, file: null, ghost: null, ghosts: [] }; send({ cmd: "summary_close" }); },
  applyFtp: d => {
    send({ cmd: "setting", key: "ftp_w", value: Number(d.value) });
    toast(`FTP de ${state.profile.active} réglé sur ${fr(d.value)} W.`);
  },
  showTab: d => { tab = d.tab; if (tab === "progress") loadProgress(); render(); },
  deleteRide: async d => {
    const response = await fetch("/api/rides/" + d.id, { method: "DELETE" });
    toast(response.ok ? "Sortie supprimée." : "Suppression impossible.", !response.ok);
    loadProgress(); loadStats();
  },
  toggleVoice: () => {
    voiceOn = !voiceOn;
    localStorage.setItem("voice", voiceOn ? "1" : "0");
    $("btn-voice").classList.toggle("on", voiceOn);
    if (voiceOn) speak("Annonces vocales activées."); else speechSynthesis.cancel();
    toast(voiceOn ? "Annonces vocales activées." : "Annonces vocales coupées.");
  },
  togglePip,
};

function startFreeRide() {
  send({ cmd: "session_start" });
  send({ cmd: "mode", mode: "slope" });
  send({ cmd: "slope", value: 0 });
}

document.addEventListener("click", event => {
  const target = event.target.closest("[data-action]");
  if (target && actions[target.dataset.action]) actions[target.dataset.action](target.dataset, target);
});
document.addEventListener("change", async event => {
  const el = event.target;
  if (el.dataset && el.dataset.setting) {
    saveSetting(el.dataset.setting, el.value);
    if (el.dataset.setting === "weekly_goal_km" || el.dataset.setting === "ftp_w") setTimeout(loadProgress, 600);
    return;
  }
  if (el.id !== "file-input" || !el.files[0]) return;
  const file = el.files[0];
  const response = await fetch("/api/upload/" + encodeURIComponent(file.name), { method: "PUT", body: file });
  if (response.ok) { toast(`« ${file.name} » ajouté.`); loadLibrary(file.name); }
  else toast("Fichier refusé : " + (await response.json()).detail, true);
  el.value = "";
});
$("new-rider").addEventListener("submit", event => {
  event.preventDefault();
  const name = $("new-rider-name").value.trim();
  if (!name) return;
  send({ cmd: "profile_create", name });
  $("new-rider").hidden = true;
  // A new rider starts with default values: take them straight to their settings.
  setTimeout(() => { if (state.profile.active === name) { actions.openSettings(); toast(`Bienvenue ${name} ! Vérifie ton poids et ton FTP.`); } }, 900);
});
document.addEventListener("submit", event => {
  if (event.target.id === "ride-form") submitRide(event);
  if (event.target.id === "event-form") submitEvent(event);
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (!$("settings").hidden) actions.closeSettings();
});

loadLibrary();
connect();
