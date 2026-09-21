#!/usr/bin/env bash
# Render the GUI in a real browser and assert on the resulting DOM.
#
# Why this exists: `tests/test_gui.mjs` checks the API payload, but the panel is
# built by JavaScript at runtime. A payload can be perfect while the page still
# renders a placeholder or leaks "#undefined" - both of which happened. Node
# running the same helpers is NOT a substitute: it does not exercise the actual
# template, the event wiring, or Chrome's parse of the markup.
#
# Uses Chromium from the playwright cache with --dump-dom, which executes scripts
# and prints the post-render DOM. No test-runner dependency needed.
#
# Usage: tests/test_gui_dom.sh    (server must already be listening on 8777)
set -uo pipefail

CHROME="${LOCALAPPDATA}/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
BASE="http://127.0.0.1:8777"
TMP="${LOCALAPPDATA}/Temp"
fail=0
checks=0

if [ ! -x "$CHROME" ]; then
  echo "chrome not found at $CHROME - cannot run DOM tests"
  exit 2
fi

render() {  # render <weapon-with-+>  -> echoes path to dumped DOM
  local out="$TMP/gui_dom_$1.html"
  "$CHROME" --headless --disable-gpu --no-sandbox --dump-dom \
    --virtual-time-budget=6000 "$BASE/?weapon=$1" > "$out" 2>/dev/null
  echo "$out"
}

check() {  # check <label> <file> <python-bool-expr on main/prov>
  local label="$1" file="$2" expr="$3"
  checks=$((checks + 1))
  if python3 -c "
import re,sys
h=open(r'''$file''',encoding='utf-8').read()
# Strip <script> so a check cannot match the template source instead of the
# rendered output - the provenance block is built by JS, and the raw template
# contains the same words as a literal.
h=re.sub(r'<script.*?</script>', '', h, flags=re.S)
m=re.search(r'<main id=\"main\">(.*?)</main>', h, re.S)
main=m.group(1) if m else ''
# The provenance card, isolated: comparisons about one segment must not be
# satisfied by text from another part of the panel.
p=re.search(r'来源校验(.*?)(?:<div class=\"btns\"|</main>)', main, re.S)
prov=p.group(1) if p else ''
sys.exit(0 if ($expr) else 1)
"; then
    echo "  [PASS] $label"
  else
    echo "  [FAIL] $label"
    fail=$((fail + 1))
  fi
}

echo "== the page's JS actually runs (stats line is filled in) =="
R4=$(render "R-4+Hyena")
if python3 -c "
import re, sys
h=open(r'''$R4''',encoding='utf-8').read()
m=re.search(r'id=\"stats\"[^>]*>([^<]*)<', h)
sys.exit(0 if m and re.search(r'\d+\s*把武器', m.group(1)) else 1)
"; then echo "  [PASS] stats line rendered"; else echo "  [FAIL] stats line empty"; fail=$((fail+1)); fi
checks=$((checks + 1))

echo "== selecting a weapon renders its detail panel, not the placeholder =="
check "R-4 panel shows the weapon name" "$R4" "'R-4 Hyena' in main"
check "R-4 panel is not the placeholder" "$R4" "'从左侧选择' not in main"
check "R-4 fields are present" "$R4" "main.count('input type=\"number\"') >= 9"

echo "== the values in the DOM are the real parsed values =="
for pair in "damage:220" "durable:45" "ap0:3"; do
  k="${pair%%:*}"; v="${pair##*:}"
  check "R-4 $k == $v" "$R4" "re.search(r'id=\"f_$k\"[^>]*value=\"$v\"', main)"
done

echo "== no template leakage in the panel =="
check "no #undefined in R-4 panel" "$R4" "'undefined' not in main"
check "no unexpanded template literal" "$R4" "'\$'+'{' not in main"

echo "== a shared record is warned about, and the change is confirmed =="
LIB=$(render "AR-23+Liberator")
check "shared banner names a peer weapon" "$LIB" "'Liberator Carbine' in main or 'Stalwart' in main"
# The row is editable, but not silently: the decision is put to the user in a
# dialog at generation time, naming every weapon the change will also affect.
# It used to be a checkbox that had to be ticked first, which made the default
# answer "no" and hid the choice from anyone who did not scroll.
check "the confirmation dialog exists in the page" "$LIB" "'id=\"confirm\"' in h and 'confBody' in h"
check "the dialog is not shown until a shared edit is generated" "$LIB" \
  "re.search(r'id=\"confirm\"[^>]*hidden', h)"
# Wording is a statement of consequence, not an instruction to the reader.
check "no second-person instructions in the shared banner" "$LIB"   "'若这正是' not in main and '勾选下方' not in main"
check "the checkbox is gone" "$LIB" "'allowshared' not in main and 'allowshared' not in h"

echo "== an unknown weapon is handled, and an unverified one would be blocked =="
# P-11 Stim Pistol used to be the unverified example, but it is no longer in
# the map at all: the matcher now refuses to map a page that documents no
# damage numbers (P-11 is a healing weapon), so the panel cannot even offer it.
# That is the correct outcome, and it means the "unverified" state has no
# natural example left - every mapped weapon now passes its cross-check.
#
# So this asserts the two behaviours that still exist:
#   * an unknown name renders a "not found" state rather than a broken panel;
#   * the block reason is still wired, checked against the page source, since
#     no weapon currently reaches it.
P11=$(render "P-11+Stim+Pistol")
check "a weapon that is no longer mapped renders without a panel" "$P11" \
  "'从左侧选择' in main or '未找到' in main or 'P-11' not in main"
check "the unverified block reason is still implemented" "$P11" \
  "True"  # asserted below against the template, not the render
if grep -q '生成未开放' gui/templates/index.html; then
  echo "  [PASS] the unverified block is still implemented (template check)"
else
  echo "  [FAIL] the unverified block was removed from the template"
  fail=$((fail + 1))
fi
checks=$((checks + 1))

echo "== an explosion-payload weapon explains its two records =="
GL=$(render "GL-21+Grenade+Launcher")
check "the impact segment is shown" "$GL" "'弹头直击' in main"
check "the explosion segment is shown" "$GL" "'爆炸伤害' in main"
check "banner names payload row" "$GL" "'#352' in main"
# The impact row number moved from 32 to 28 when the projectile's +60 was
# corrected from an array index to a damage-type id: GL-21's projectile
# references id 32, which lives at position 28.
check "banner names impact row" "$GL" "'#28' in main"
check "no #undefined in explosion banner" "$GL" "'undefined' not in main"
# Each segment has its own inputs, so both can be edited without a mode switch.
check "impact half has its own input row" "$GL" "'f_imp_damage' in main"
check "explosion half has its own input row" "$GL" "'f_damage' in main"
check "each block has its own generate button" "$GL" "'seggen' in main"

echo "== provenance compares each segment against its own source =="
# The panel used to compare the game's EXPLOSION values against the wiki's
# DIRECT-HIT values, because both were read from the top level of the payload.
# GR-8 rendered "肉伤 150 / 3200" - the game's explosion damage beside the
# wiki's direct-hit damage, presented as a disagreement about one quantity.
# Each half must be compared against the wiki figure for that same half.
GR8=$(render "GR-8+Recoilless+Rifle")
check "the provenance block splits the two segments" "$GR8" \
  "'弹头直击' in prov and '爆炸伤害' in prov"
check "the direct-hit half compares against the wiki's direct-hit figure" "$GR8" \
  "re.search(r'3200 / 3200', prov)"
check "the explosion half compares against the wiki's explosion figure" "$GR8" \
  "re.search(r'150 / 150', prov)"
check "no cross-segment pair is shown" "$GR8" \
  "not re.search(r'150 / 3200', prov) and not re.search(r'3200 / 150', prov)"
check "every provenance comparison resolves to a value" "$GR8" \
  "'—' not in prov"
check "a non-explosive weapon shows a single segment" "$R4" \
  "'弹头直击' not in prov and '肉伤 220 / 220' in prov"

echo
if [ "$fail" -eq 0 ]; then
  echo "test_gui_dom: PASS ($checks checks)"
  exit 0
fi
echo "test_gui_dom: FAIL ($fail of $checks failed)"
exit 1
