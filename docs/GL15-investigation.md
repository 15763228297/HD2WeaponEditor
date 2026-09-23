# GL-15 Evictor: where the 490 goes, and why the new weapons cannot be mapped

**Status: unresolved, and deliberately not guessed at.**
Investigated 2026-09-23 against game build 1.8.45850.

## The question

The wiki states `damage = 490` for GL-15 Evictor. No damage row in the game holds
490 (new table: 0 rows; old table: 0 rows). The user's hypothesis was that 490 is
a direct + explosion sum, "maybe 40 direct + 450 explosion".

## What was checked, and what it rules out

### 1. 490 is not a stored value anywhere

| check | result |
|---|---|
| `damage == 490` in the new table | 0 rows |
| `damage == 490` in the old table | 0 rows |
| `durable_damage == 490` in either table | 0 rows |
| any projectile with `direct + explosion == 490` | none |

The last one is a clean scan: it now skips the `explosion_type == 0` sentinel,
which matters because explosion row 0 is a *real* record (300/300 AP4, r 4.0/10.0).
Treating it as an explosion makes every plain projectile appear to carry a 300
explosion, which is what made an earlier pass report 45 bogus "sums near 490".

### 2. The hypothesis does not fit any row

| shape searched | result |
|---|---|
| direct 40 + explosion 450 | no row (in either table) |
| direct + explosion == 490 | no row |
| rows carrying the 450 explosion | only explosion row 167, carried by projectile row 324 (direct 60, unnamed) |

Projectile row 324 is `direct 60/0 AP4 + explosion 450/450 AP3, r 1.6/5.0`, sum
**510**, not 490. It is unnamed and its name hashes (2586036311 / 1962002280)
resolve to nothing. It is one of exactly two rows in the whole table that are new
since the previous build (the other is row 234).

So the closest thing in the data to the user's recollection is 60 + 450, and it
is not confirmed as GL-15's.

### 3. The wiki's own rule for the `damage` field

Rendered infoboxes show the field is a *labelled* figure, and for two-part weapons
it carries both parts:

| weapon | wiki prints | game direct | game explosion |
|---|---|---|---|
| EAT-17 | `2,000 Projectile` + `150` explosion | 2000 | 150 |
| GR-8 | `3,200 Projectile (HEAT)` + `150` | 3200 | 150 |
| GL-21 | `400 Explosion` | 0 (no direct) | 400 |
| R-36 Eruptor | `230 Impact` + `225` | 225 | 225 |
| CB-9 Crossbow | `270 Projectile` + `350` | 350 | 350 |

Two things follow. First, the field is not a sum - it is a per-segment figure,
which is why EAT-17 prints 2000 and not 2150. Second, GL-15's page prints a bare
`490` with **no segment label and no second figure**, unlike every two-part weapon
above. That is consistent with the page being unfinished rather than with 490
being a total.

### 4. The wiki has no data for the new weapons at all

The wiki's Attack Data table reads from these modules:

```
Module:Decodedata-Attacks/data.json              (281,118 chars)
Module:Decodedata-Attacks/weapons_data.json      (217,978 chars)
Module:Decodedata-Attacks/new_gear.json           (40,065 chars)
Module:Decodedata-Attacks/stratagems_data.json   (125,796 chars)
Module:Decodedata-Attacks/stratagemdata.json      (55,541 chars)
```

**None** of them contains `Evictor`, `Arbitrator`, `Breacher`, `Immolation`,
`Anti-Tank Seeker`, `Ironclad`, `GL-15`, `AR-11`, `P-34` or `G-60`. The rendered
Damage Comparison table (30,031 chars) has zero hits for any of them.

Consequently every one of the three new weapon pages renders:

> Weapon not found: "GL-15 EVICTOR". You can help by adding it into
> Template:Attack Data's data weapons json.

The new weapons' *infoboxes* were filled in by hand (`| damage = 490`), but the
machine-readable table the matcher needs has not been updated. The pages are
tagged `{{WIP}}` / `{{Last Updated|1.007.100}}`, while the game is at 1.8.45850.

## Why this blocks the mapping

The chain every weapon must satisfy is:

```
weapon name (wiki) -> ammo name (wiki) -> projectile row (game) -> damage row (game)
```

Each hop needs a key that exists on both sides.

1. **No ammo name.** GL-15, P-34 and AR-11 pages have no `| ammo =` and no Attack
   Data block, so hop 2 has no key. The matcher reports `no ammo parsed`.
2. **No durable-damage figure.** The infoboxes give one number, not the
   standard/durable pair. Even with an ammo name, the four-field cross-check
   (`damage`, `durable`, `AP`, `velocity`) cannot pass on one figure - and that
   check is what stops the tool writing to a guessed row.
3. **The new projectile rows are unnamed.** 89 projectile name keys do not resolve
   against `data/strings.json`. They are not in the 265 extracted `.strings`
   exports either - verified by reading the exports' `Items` lists directly (a
   control key, `12g Tri-Ball`, appears in 15 of them, so the lookup works).

   Separately: the weapon *names* the game added are present and resolve -
   `EVICTOR` / `Expulsor`, `ARBITRATOR` / `Schlichter`, `BREACHER` / `Sprenger`,
   `GL-15`, `AR-11`, `G-60`. Only the *ammo* strings are missing.

## Two dead ends worth recording

* **`data/raw/entities.dl_bin` is stale** (09-19, before the 09-22 update) and is
  a single opaque block (`0x80c1ca70`), not a table of settings. The only hits for
  new-weapon names in it are `IMMOLATION` / `Immolation`, which existed before this
  build. It is not a weapon-to-ammo map.
* **`generated_weapon_customization_settings.dl_bin`** holds scopes, triggers and
  cosmetics (97+3+4+52+36+16+12+3+18 records), not weapon-to-projectile links.

## What would settle it

Any one of these:

1. **The wiki's data module gets updated.** Then the normal matcher runs unchanged
   and the four-field check either passes or refuses. This is the expected path.
2. **A reliable figure for both segments** (direct and explosion, each with
   standard/durable/AP). With those, the row can be found by matching the pair
   against the tables - projectile row 324 is the current best candidate at
   60 + 450 = 510, but it is a candidate, not a match.
3. **An in-game measurement.** Kill-count or health-bar observation against a
   known-damage weapon would bound the value without needing the wiki.

Until one of those exists, the tool refuses to map GL-15. That refusal is the
feature: a wrong row would silently edit a different weapon.

## Reproducing the checks

```
python tools/diagnose_new_weapons.py   # per-weapon: which hop fails
python tools/find_gl15.py              # new rows by name, sums near 490
python tools/final_gl15.py             # orphan keys, clean 490 scan
python tools/new_records.py            # records new since the last build
python tools/wiki_field_check.py       # what the wiki prints vs the tables
python tools/check_string_exports.py   # raw .strings lookup
```

`tools/hash_probe.py` and `tools/name_the_rows.py` established that the string
keys are not a hash of the English text (15 candidate functions, 0/400 sample
match), so naming the orphan rows by brute force is not available.
