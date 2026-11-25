// Booking demo: Pyodide runs the repo's slot engine and booking function against in-memory SQLite.
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const $ = (s) => document.querySelector(s);
const ZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney", "UTC"];
let py;
let lastBooked = null;

const call = (fn, ...args) => JSON.parse(py.globals.get("_call")(fn, JSON.stringify(args)));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function setStatus(text, kind) {
  $("#status").textContent = text;
  $("#status").dataset.kind = kind;
}

function showResult(res, label) {
  const el = $("#result");
  el.hidden = false;
  el.className = `card ${res.ok ? "ok" : "bad"}`;
  el.textContent = res.ok
    ? `${label}: HTTP ${res.status} Created, booking #${res.id} at ${res.start_utc} UTC`
    : `${label}: HTTP ${res.status} refused: ${res.detail}`;
}

function refresh() {
  const staff = Number($("#staff").value);
  const service = Number($("#service").value);
  const list = call("slots", staff, service, $("#day").value, $("#viewer").value);
  $("#count").textContent = `(${list.length})`;
  $("#slots").innerHTML = list.length
    ? list.map((s) => `<button class="slot" data-utc="${s.utc}">${esc(s.viewer_local)}<small>staff: ${esc(s.staff_local)}</small></button>`).join("")
    : `<p class="note">No open slots that day (weekend, time off, or everything is booked).</p>`;
  for (const b of document.querySelectorAll(".slot")) b.onclick = () => book(b.dataset.utc);
  $("#bookings tbody").innerHTML = call("bookings")
    .map((b) => `<tr><td>${b.id}</td><td>${esc(b.staff)}</td><td>${esc(b.customer)}</td><td>${esc(b.start_staff_local)}</td><td>${esc(b.status)}</td></tr>`)
    .join("");
}

function book(utc) {
  const args = [Number($("#staff").value), Number($("#service").value), utc, "Test Customer", "customer@example.com"];
  const res = call("book", ...args);
  showResult(res, "POST /bookings");
  if (res.ok) {
    lastBooked = args;
    $("#again").disabled = false;
  }
  refresh();
}

async function boot() {
  setStatus("Loading Python in your browser (first load takes a few seconds)...", "busy");
  try {
    const { loadPyodide } = await import(`${PYODIDE_URL}pyodide.mjs`);
    py = await loadPyodide({ indexURL: PYODIDE_URL });
    await py.loadPackage("micropip");
    const micropip = py.pyimport("micropip");
    // Pyodide's own pydantic build first, then versions of sqlmodel and pydantic-settings that accept it.
    await micropip.install(["pydantic", "sqlalchemy", "sqlite3", "tzdata"]);
    await micropip.install(["sqlmodel==0.0.22", "pydantic-settings==2.7.1"]);
    py.unpackArchive(await (await fetch("pkg.zip")).arrayBuffer(), "zip", { extractDir: "/app" });
    await py.runPythonAsync(`
import sys, json
sys.path.insert(0, "/app")
import booking_demo
booking_demo.seed()
def _call(fn, args):
    return json.dumps(getattr(booking_demo, fn)(*json.loads(args)))
`);
    const cat = call("catalog");
    $("#staff").innerHTML = cat.staff.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    $("#service").innerHTML = cat.services.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    $("#day").value = cat.first_day;
    const mine = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const zones = ZONES.includes(mine) ? ZONES : [mine, ...ZONES];
    $("#viewer").innerHTML = zones.map((z) => `<option ${z === mine ? "selected" : ""}>${z}</option>`).join("");
    for (const id of ["#staff", "#service", "#day", "#viewer"]) $(id).onchange = refresh;
    $("#again").onclick = () => {
      if (lastBooked) {
        showResult(call("book", ...lastBooked), "POST /bookings (same slot again)");
        refresh();
      }
    };
    refresh();
    setStatus(`Ready. Pyodide ${py.version}, SQLite in memory.`, "ok");
    document.body.dataset.ready = "1";
  } catch (err) {
    setStatus(`Could not load the Python runtime: ${err.message}`, "bad");
    console.error(err);
  }
}

boot();
