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

check() {  # check <label> <file> <python-bool-expr on h>
  local label="$1" file="$2" expr="$3"
  checks=$((checks + 1))
  if python3 -c "
import re,sys
h=open(r'''$file''',encoding='utf-8').read()
m=re.search(r'<main id=\"main\">(.*?)</main>', h, re.S)
main=m.group(1) if m else ''
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

echo "== a shared record is warned about and export is blocked =="
LIB=$(render "AR-23+Liberator")
check "shared banner names a peer weapon" "$LIB" "'Liberator Carbine' in main or 'Stalwart' in main"
# Export is no longer blocked outright: shared rows became editable on purpose,
# behind a checkbox that says which other weapons will change. Assert the
# control exists rather than that the button is dead.
check "shared rows are gated behind an explicit opt-in" "$LIB" "'allowshared' in main"
check "the opt-in names what it affects" "$LIB" "'允许修改共享记录' in main"
# Wording is a statement of consequence, not an instruction to the reader.
check "no second-person instructions in the shared banner" "$LIB"   "'若这正是' not in main and '勾选下方' not in main"

echo "== an unverified record is blocked =="
P11=$(render "P-11+Stim+Pistol")
check "export blocked for unverified" "$P11" "'生成未开放' in main"

echo "== an explosion-payload weapon explains its two records =="
GL=$(render "GL-21+Grenade+Launcher")
check "the impact segment is shown" "$GL" "'弹头直击' in main"
check "the explosion segment is shown" "$GL" "'爆炸伤害' in main"
check "banner names payload row" "$GL" "'#352' in main"
check "banner names impact row" "$GL" "'#32' in main"
check "no #undefined in explosion banner" "$GL" "'undefined' not in main"
# Each segment has its own inputs, so both can be edited without a mode switch.
check "impact half has its own input row" "$GL" "'f_imp_damage' in main"
check "explosion half has its own input row" "$GL" "'f_damage' in main"
check "each block has its own generate button" "$GL" "'seggen' in main"

echo
if [ "$fail" -eq 0 ]; then
  echo "test_gui_dom: PASS ($checks checks)"
  exit 0
fi
echo "test_gui_dom: FAIL ($fail of $checks failed)"
exit 1
