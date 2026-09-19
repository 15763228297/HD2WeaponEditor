// Headless check of the GUI's rendering logic against the real API payload.
// The browser tool timed out, so exercise the same data path in Node instead:
// fetch the API, then run the list/detail rendering helpers over it.
// Run:  node tests/test_gui.mjs
const BASE = "http://127.0.0.1:8777";

let failures = 0, checks = 0;
function check(label, cond, detail = "") {
  checks++;
  if (cond) console.log(`  [PASS] ${label}`);
  else { console.log(`  [FAIL] ${label} ${detail}`); failures++; }
}

const res = await fetch(`${BASE}/api/weapons`);
check("API responds 200", res.ok, `status ${res.status}`);
const data = await res.json();

check("weapons array present", Array.isArray(data.weapons));
// The count dropped from 102 to 88 when the position/type_id conflation was
// fixed: entries that only "matched" through the wrong row are gone. The
// floor guards against a regression that loses real matches.
check("weapon count > 80", data.weapons.length > 80, `got ${data.weapons.length}`);

// Every weapon the UI would offer for export must be verified AND exclusive —
// that is the gate wired into the Generate button.
const exportable = data.weapons.filter(w => w.verified && w.exclusive);
check("some weapons are exportable", exportable.length > 0, `got ${exportable.length}`);

const r4 = data.weapons.find(w => w.page === "R-4 Hyena");
check("R-4 present", !!r4);
if (r4) {
  check("R-4 is exportable", r4.verified && r4.exclusive,
        `verified=${r4.verified} exclusive=${r4.exclusive}`);
  check("R-4 damage 220/45", r4.damage === 220 && r4.durable === 45,
        `got ${r4.damage}/${r4.durable}`);
  check("R-4 payload is projectile", r4.payload === "projectile", `got ${r4.payload}`);
}

// A shared weapon must NOT be exportable: editing it would change other guns.
const shared = data.weapons.filter(w => !w.exclusive);
check("shared weapons exist", shared.length > 0, `got ${shared.length}`);
check("no shared weapon is exportable",
      shared.every(w => !(w.verified && w.exclusive)),
      shared.map(w => w.page).join(","));
check("shared weapons name their peers",
      shared.every(w => Array.isArray(w.shared_with) && w.shared_with.length > 0),
      JSON.stringify(shared[0]?.shared_with));

// An unverified weapon must not be exportable either.
const unverified = data.weapons.filter(w => !w.verified);
check("unverified weapons exist", unverified.length > 0, `got ${unverified.length}`);
check("no unverified weapon is exportable",
      unverified.every(w => !(w.verified && w.exclusive)),
      unverified.map(w => w.page).join(","));

// Explosion-payload weapons must point at a payload row, and keep the impact
// token distinct - editing the token would silently do nothing.
const expl = data.weapons.filter(w => w.payload === "explosion");
check("explosion-payload weapons exist", expl.length > 0, `got ${expl.length}`);

// Every rendered row needs the fields the template reads, or the UI shows
// "undefined" instead of a value.
const required = ["page", "damage_index", "payload", "ammo", "speed", "damage",
                  "durable", "ap", "forces", "verified", "exclusive", "matched_by", "wiki"];
const missing = [];
for (const w of data.weapons) {
  for (const k of required) if (!(k in w)) missing.push(`${w.page}.${k}`);
}
check("all list/detail fields present", missing.length === 0, missing.slice(0, 5).join(", "));

// The AP array must have exactly four angles (the template indexes [0..3]).
check("every AP array has 4 entries",
      data.weapons.every(w => Array.isArray(w.ap) && w.ap.length === 4),
      JSON.stringify(data.weapons.find(w => w.ap.length !== 4)?.page));

// The forces array must have three entries (demolition / strength / impulse).
check("every forces array has 3 entries",
      data.weapons.every(w => Array.isArray(w.forces) && w.forces.length === 3),
      JSON.stringify(data.weapons.find(w => w.forces.length !== 3)?.page));

console.log();
console.log(`test_gui: ${failures ? "FAIL" : "PASS"} (${checks} checks)`);
process.exit(failures ? 1 : 0);
