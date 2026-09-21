// The shared-record confirmation, exercised as behaviour.
//
// The page's own script is extracted from the served template and run against a
// small DOM, so this drives the shipped code path rather than a copy of it.
// What matters is not that a dialog exists in the markup but that the flow
// around it is right:
//
//   * an exclusive weapon generates immediately, with no dialog and no
//     allow_shared flag - the flag must mean "the user agreed", not "the UI
//     always says yes";
//   * a shared weapon does NOT generate on click; it shows the dialog naming
//     every peer, and only a confirmation sends the request;
//   * cancelling sends nothing at all;
//   * the peers named in the dialog are the real ones for that weapon and
//     segment, taken from the API payload.
//
// Run:  node tests/test_confirm.mjs      (needs the GUI server on :8777)
const BASE = "http://127.0.0.1:8777";

let failures = 0, checks = 0;
function check(label, cond, detail = "") {
  checks++;
  if (cond) console.log(`  [PASS] ${label}`);
  else { console.log(`  [FAIL] ${label} ${detail}`); failures++; }
}

// ---- a DOM just large enough for this page -------------------------------
//
// Elements are registered from the ids that appear in whatever HTML is assigned
// to them, which is how the page's own render reaches its buttons.
function makeEl(id) {
  return {
    id, innerHTML: "", textContent: "", value: "", hidden: false,
    dataset: {}, style: {}, disabled: false, checked: false,
    onclick: null, oninput: null, className: "",
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    querySelectorAll: () => [], querySelector: () => null,
    appendChild() {}, addEventListener() {}, focus() {},
  };
}

const registry = new Map();
function reg(id) {
  if (!registry.has(id)) registry.set(id, makeEl(id));
  return registry.get(id);
}

// Scan assigned markup for id="..." and register a stub for each, so that a
// later getElementById finds the element the page just created.
function absorb(html) {
  for (const m of String(html).matchAll(/id="([^"]+)"/g)) reg(m[1]);
}

const document = {
  getElementById: (id) => registry.get(id) || null,
  // The page's filter reads `.chip[data-f="..."]` and checks for the "on" class.
  // Register the three real chips so those reads resolve.
  querySelector: (sel) => {
    const m = String(sel).match(/\.chip\[data-f="([^"]+)"\]/);
    if (m) {
      const chip = reg(`chip:${m[1]}`);
      chip.classList.contains = () => false;
      return chip;
    }
    return null;
  },
  querySelectorAll: () => [],
  createElement: () => makeEl("created"),
  addEventListener: () => {},
  body: makeEl("body"),
};

// innerHTML assignment is the only way the page creates elements, so absorb on
// write. A Proxy keeps that behaviour without patching every stub by hand.
function absorbing(el) {
  let v = el.innerHTML;
  Object.defineProperty(el, "innerHTML", {
    get: () => v,
    set: (x) => { v = x; absorb(x); },
    configurable: true,
  });
  return el;
}

// ---- load the page's script ----------------------------------------------
const html = await (await fetch(BASE + "/")).text();

// Static ids: the dialog lives in the document body, not in generated markup.
// Its initial state comes from the markup too - the stub cannot assume
// hidden=false, or "the dialog is not shown" would be untestable.
for (const m of html.matchAll(/<[^>]*id="([^"]+)"[^>]*>/g)) {
  const el = absorbing(reg(m[1]));
  if (/\shidden(\s|>|\/)/.test(m[0])) el.hidden = true;
}
absorb(html);

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const data = await (await fetch(BASE + "/api/weapons")).json();

// Capture what the page posts, instead of really generating a mod.
// The response shape matches the real API: generate() renders data.changes (the
// single-weapon path) and generateAll() renders data.edits[].changes (the batch
// path), so a stub missing either would crash the page code under test rather
// than exercise it.
let posted = [];
async function fakeFetch(url, opts) {
  if (opts && opts.method === "POST") {
    const sent = JSON.parse(opts.body);
    posted.push(sent);
    const edits = sent.edits
      ? sent.edits.map((e) => ({ weapon: e.weapon, changes: { damage: e.damage } }))
      : [{ weapon: sent.weapon, changes: { damage: sent.damage } }];
    return { ok: true, status: 200, json: async () => ({
      ok: true, weapons: [sent.weapon || "batch"], edits,
      changes: { damage: sent.damage ?? 0 }, zip: "build/x.zip",
      archive_bytes: 1, archive_sha256: "0".repeat(64),
      slot: "mods/cowboybingus/wide_angle_stratagems" }) };
  }
  return { ok: true, status: 200, json: async () => data };
}

// The page declares top-level `let DATA, SEL`; run it inside a function so those
// stay reachable, then expose what the tests need.
const load = new Function(
  "document", "window", "fetch", "location", "encodeURIComponent",
  script + "\n;return { boot, renderMain, generate, generateAll, askShared, " +
           "setSel: (w) => { SEL = w; }, setQueue: (q) => { QUEUE = q; }, " +
           "getQueue: () => QUEUE, setData: (d) => { DATA = d; } };"
);

globalThis.document = document;
const win = { location: { search: "" }, addEventListener: () => {} };
const api = load(document, win, fakeFetch, win.location, encodeURIComponent);
api.setData(data);

// Render a weapon so its inputs and buttons exist, then fill the inputs.
//
// Every field the generator reads must be filled: the AP angles default to
// empty strings, and three empty strings compare as "disagreeing" rather than
// as "unset", so a test that sets only the damage field is refused before it
// reaches the code under test.
function openWeapon(page) {
  const w = data.weapons.find((x) => x.page === page);
  if (!w) throw new Error(`no such weapon: ${page}`);
  api.setSel(w);
  api.renderMain();
  return w;
}
// Fill one segment's inputs from a plain object, leaving anything unset alone.
function fill(prefix, vals) {
  for (const [k, v] of Object.entries(vals)) setField(`f_${prefix}${k}`, v);
}
function setField(id, v) { reg(id).value = String(v); }

// Click whichever button the page wired up for an id.
function click(id) {
  const el = reg(id);
  if (typeof el.onclick !== "function") throw new Error(`${id} has no handler`);
  return el.onclick();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- 1. an exclusive weapon generates with no dialog ----------------------
console.log("== an exclusive weapon is not interrupted ==");
const r4 = openWeapon("R-4 Hyena");
check("R-4 has no peers on its damage row", (r4.shared_with || []).length === 0,
      JSON.stringify(r4.shared_with));
fill("", { damage: 400, durable: 45, ap0: 3, ap1: 3, ap2: 3 });
posted = [];
const p1 = api.generate("damage", "");
await sleep(20);
check("no dialog appeared", reg("confirm").hidden === true,
      `hidden=${reg("confirm").hidden}`);
check("the request went out without a confirmation", posted.length === 1,
      `posted ${posted.length}`);
check("an exclusive edit carries allow_shared=false",
      posted[0] && posted[0].allow_shared === false,
      JSON.stringify(posted[0] && posted[0].allow_shared));
await p1;

// ---- 2. a shared weapon asks first, and cancelling sends nothing ----------
console.log("\n== a shared weapon asks before it writes ==");
// S-11 Speargun shares its IMPACT row with G-16 Impact.
const speargun = data.weapons.find((w) => w.page === "S-11 Speargun");
check("S-11 shares its impact row", (speargun?.impact_shared_with || []).length > 0,
      JSON.stringify(speargun?.impact_shared_with));

openWeapon("S-11 Speargun");
fill("imp_", { damage: 7000, durable: 100, ap0: 5, ap1: 5, ap2: 5 });
posted = [];
const p2 = api.generate("impact", "imp_");
await sleep(20);
check("the dialog is shown", reg("confirm").hidden === false);
check("nothing was posted yet", posted.length === 0, `posted ${posted.length}`);
const bodyText = reg("confBody").innerHTML;
check("the dialog names the peer weapon", bodyText.includes("G-16 Impact"),
      bodyText.slice(0, 120));
check("the dialog names the segment", bodyText.includes("弹头直击"),
      bodyText.slice(0, 120));
check("the dialog asks for a decision", bodyText.includes("确定要修改吗"),
      bodyText.slice(0, 160));

// Cancel: the promise resolves false and no request is made.
click("confNo");
await p2;
await sleep(20);
check("cancelling posts nothing", posted.length === 0, `posted ${posted.length}`);
check("cancelling closes the dialog", reg("confirm").hidden === true);
check("cancelling says so", reg("out").innerHTML.includes("已取消"),
      reg("out").innerHTML.slice(0, 80));

// ---- 3. confirming sends allow_shared=true -------------------------------
console.log("\n== confirming writes, and only then ==");
openWeapon("S-11 Speargun");
fill("imp_", { damage: 7000, durable: 100, ap0: 5, ap1: 5, ap2: 5 });
posted = [];
const p3 = api.generate("impact", "imp_");
await sleep(20);
check("the dialog is shown again for a new edit", reg("confirm").hidden === false);
click("confYes");
await p3;
await sleep(20);
check("confirming posts exactly once", posted.length === 1, `posted ${posted.length}`);
check("the confirmed edit carries allow_shared=true",
      posted[0] && posted[0].allow_shared === true,
      JSON.stringify(posted[0] && posted[0].allow_shared));
check("the confirmed edit targets the impact segment",
      posted[0] && posted[0].segment === "impact",
      JSON.stringify(posted[0] && posted[0].segment));
check("the dialog is closed after confirming", reg("confirm").hidden === true);

// ---- 4. the batch path lists every shared edit once ----------------------
console.log("\n== a batch names each affected record once ==");
// One shared edit and one exclusive edit: only the shared one is questioned,
// and its flag must not leak onto the exclusive edit.
const jar = data.weapons.find((w) => w.page === "JAR-5 Dominator");
api.setQueue([
  { weapon: "S-11 Speargun", damage: 7000, durable: 100, ap: 5,
    segment: "impact", label: "S-11 Speargun（弹头直击）" },
  { weapon: "JAR-5 Dominator", damage: 300, durable: 90, ap: 5,
    segment: "damage", label: "JAR-5 Dominator" },
]);
check("the batch has one exclusive member", (jar?.shared_with || []).length === 0,
      JSON.stringify(jar?.shared_with));
posted = [];
const p4 = api.generateAll();
await sleep(20);
check("one dialog covers the batch", reg("confirm").hidden === false);
const batchText = reg("confBody").innerHTML;
check("the batch dialog names the shared weapon", batchText.includes("S-11 Speargun"),
      batchText.slice(0, 140));
check("the batch dialog does not question the exclusive weapon",
      !batchText.includes("JAR-5"), batchText.slice(0, 200));
click("confYes");
await p4;
await sleep(20);
check("the batch posted once", posted.length === 1, `posted ${posted.length}`);
const sent = posted[0]?.edits || [];
check("both edits were sent", sent.length === 2, `sent ${sent.length}`);
check("the shared edit is flagged",
      sent.find((e) => e.weapon === "S-11 Speargun")?.allow_shared === true,
      JSON.stringify(sent.map((e) => [e.weapon, e.allow_shared])));
check("the exclusive edit is NOT flagged",
      sent.find((e) => e.weapon === "JAR-5 Dominator")?.allow_shared === false,
      JSON.stringify(sent.map((e) => [e.weapon, e.allow_shared])));

// ---- 5. cancelling a batch sends nothing ---------------------------------
console.log("\n== cancelling a batch writes nothing ==");
api.setQueue([
  { weapon: "S-11 Speargun", damage: 7000, durable: 100, ap: 5,
    segment: "impact", label: "S-11 Speargun（弹头直击）" },
]);
posted = [];
const p5 = api.generateAll();
await sleep(20);
click("confNo");
await p5;
await sleep(20);
check("cancelling the batch posts nothing", posted.length === 0,
      `posted ${posted.length}`);

console.log();
if (failures === 0) {
  console.log(`test_confirm: PASS (${checks} checks)`);
  process.exit(0);
}
console.log(`test_confirm: FAIL (${failures} of ${checks} failed)`);
process.exit(1);
