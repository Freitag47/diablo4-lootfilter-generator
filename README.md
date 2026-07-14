# d4-lootfilter-generator

[![Release](https://img.shields.io/github/v/release/Freitag47/d4-lootfilter-generator)](https://github.com/Freitag47/d4-lootfilter-generator/releases)
[![Downloads](https://img.shields.io/github/downloads/Freitag47/d4-lootfilter-generator/total)](https://github.com/Freitag47/d4-lootfilter-generator/releases)
[![License](https://img.shields.io/github/license/Freitag47/d4-lootfilter-generator)](LICENSE)

**[⬇ Download the latest release (zip)](https://github.com/Freitag47/d4-lootfilter-generator/releases/latest/download/d4-lootfilter-generator.zip)**, unpack it anywhere, double-click `run.bat`.

Turn a build guide into a native Diablo 4 loot filter.

The script reads a build from Mobalytics, D4Builds, InfinityBuilds or Maxroll: the
stat priorities of every gear slot, which stats the build wants as Greater Affixes,
its uniques, talisman set charms and seal. It maps all of that to the game's internal
ids and prints an import code for:

> Character Menu → Loot Filter → New Filter → **Import**

```
python d4_lootfilter.py "https://mobalytics.gg/diablo-4/builds/rogue-dance-of-knives"
python d4_lootfilter.py "https://d4builds.gg/builds/dance-of-knives-rogue-endgame/?var=0"
python d4_lootfilter.py "https://infinitybuilds.gg/en/builds/hFrM0wPM4G"
python d4_lootfilter.py "https://maxroll.gg/d4/build-guides/rain-of-arrows-rogue-guide"
```

On Windows you don't need a command line at all: double-click `run.bat` and
paste the link there ([Setup](#setup)).

```
Filter: Rogue Dance Of Knives   (variant 8)   class: rogue
Uniques: 1   Slot rules: 9   Unmapped: 0   Rules: 23/25   Sets: 1

Gear rules (Rare/Legendary, * = wanted as Greater Affix):
  Helm      3+ of 4  (helm)
            Dexterity, Maximum Life, Cooldown Reduction, Imbuements Skills
  Gloves    3+ of 4  (gloves)  [BiS tier]
            Vulnerable Damage*, Damage Over Time, Poison Damage, Dance of Knives
  ...
IMPORT CODE (D4 -> Loot Filter -> New Filter -> Import):
CiEKDUJ1aWxkIFVuaXF1ZXMQAh1QUP...
```

## How the filter works

It is a strict endgame filter: it shows what the build can use and hides the rest.
Rules are evaluated top to bottom in game, first match wins.

| # | Colour | Rule |
|---|---|---|
| 1 | red | the build's uniques and unique charms |
| 2 | purple | the build's talisman set charms |
| 3 | green | Codex of Power upgrades |
| 4 | white | per-slot BiS: right item type, all desired affixes, and the marked stats rolled as Greater Affix |
| 5 | blue | per-slot match: right item type, Rare/Legendary, all affix slots from the wanted pool |
| 6 | cyan | `--ga-threshold`+ Greater Affixes but not a build match (default 1) |
| 7 | magenta | Legendary/Unique seals (lower seal rarities are hidden) |
| 8 | shown | set charms of any set (magic/rare charms are hidden) |
| 9 | shown | all Uniques and Mythics |
| 10 | hidden | everything else that is gear, up to Legendary, Ancestral or not |

The slot rules are the core. A dropped Legendary has three affix slots and a build
lists four wanted stats per gear slot, so an item only lights up when its whole
affix roll comes out of that pool, on the right item type. A helm stat on an
amulet stays dark. The white tier additionally requires the stats the build marks
(the little GA arrows on the guide) to actually be Greater Affixes; an enchanted
affix can never become one, which is why this is checked per stat and not as a
count. Ring 1/2 and the two dual-wield slots merge into one rule each, and weapon
item types come from a per-class table (rogue melee is sword/dagger/hand crossbow,
and so on).

Slots occupied by a unique don't contribute affixes to the pools, since a unique's
stats are fixed. The item itself is matched by name in rule 1 instead.

Only Uniques and Mythics are always visible. The hide rule covers everything else
that matched nothing, including Ancestrals, but it is scoped to equipment item
types: gold, materials, elixirs and sigils are never touched. With
`--ancestral-uniques` (`run.bat` asks for it) rules 1 and 9 only match Ancestral
uniques (via the item-properties condition, any Greater Affix count) and the
hide rule swallows the rest, the build's uniques included. `--ancestral-gear`
(second `run.bat` question) does the same for the white and blue slot tiers:
only Ancestral rare/legendary drops light up, non-Ancestral matches get hidden. Recolors avoid
orange and yellow on purpose, the game already uses those for Legendary and Rare
item names.

A filter can hold 25 rules and a full build needs about 23. If a build would go
over, BiS rules are dropped (last slots first) and the report says so. Import the
code once and skim the rules in the in-game editor, especially after a game patch.

## Setup

### Windows

Grab the repo (**Code → Download ZIP**, unpack it anywhere) and double-click
**`run.bat`**. It checks whether Python, Playwright and Chromium are present,
runs the setup on its own if something is missing, then just asks for a build
link. No terminal or Python knowledge needed.

`setup.bat` can also be run on its own. It only installs what the PC does not
have yet:

- Python 3.12 via winget (where winget is unavailable it opens the python.org
  download page instead)
- the Playwright package
- Playwright's Chromium, a one-time download of roughly 150 MB

If it had to install Python, run it a second time afterwards; an already open
console does not see the fresh installation.

### Manual (macOS, Linux, or if you prefer pip)

Python 3.9 or newer, then:

```
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Maxroll builds, `--stats` and `--paste` need none of this, they run on a plain
Python install. If a fetch aborts with `Playwright is required to fetch from a URL`
or `Chromium is missing`, the two commands above are the fix.

## Usage

| Command | What it does |
|---|---|
| `d4_lootfilter.py "<url>"` | fetch a Mobalytics/D4Builds/InfinityBuilds/Maxroll build, print the import code |
| `d4_lootfilter.py "<url>" --print-detected` | also list the detected uniques and set charms |
| `d4_lootfilter.py --stats "vulnerable damage, max life, ..."` | build from a manual stat list |
| `d4_lootfilter.py --paste` | paste gear text from any site, end with an empty line |
| `d4_lootfilter.py "<url>" --html saved.html` | read a saved Mobalytics page offline |

| Flag | Meaning |
|---|---|
| `--variant ID` | Mobalytics variant id (default from the URL), d4builds `var` index, or InfinityBuilds/Maxroll variant index/name |
| `--name "..."` | filter name in game, max 30 chars (default from the build) |
| `--ga-threshold N` | Greater Affixes needed for the cyan rule (default 1) |
| `--class NAME` | override the auto-detected class (drives weapon item types) |
| `--no-hide` | never hide anything, only recolor/keep |
| `--ancestral-uniques` | show uniques, the build's own included, only when they drop as Ancestral |
| `--ancestral-gear` | match the per-slot BiS/gear rules only on Ancestral drops |
| `--include-tempering` | treat tempering stats as droppable affixes (loosens matching) |
| `--dump-json PATH` | save the raw extracted build data |

Manual input (`--stats`/`--paste`) has no slot information, so those modes fall
back to a single pool rule that wants 2 matching affixes.

### Picking a variant

Mobalytics and D4Builds put the open variant in the URL, so copying the link is
enough. InfinityBuilds and Maxroll keep it in client state: switching tabs there
changes nothing in the address bar, and a copied link cannot say which tab you
meant. So for those builds the script lists the variants once it has the build
and asks:

```
This build has 6 variants:
  [0] Midgame                      49 stats, 2 uniques
  [1] Endgame                      49 stats, 2 uniques   (default)
  [2] Bossing                      45 stats, 3 uniques
  [3] Tower Push                   32 stats, 7 uniques
  [4] Poison Endgame               45 stats, 3 uniques
  [5] Poison Tower Push            53 stats, 3 uniques
Which variant? [1]:
```

Enter takes the default: on Maxroll the profile the planner itself opens on, on
InfinityBuilds the first variant that actually carries wanted stats (its first
tab is often an empty leveling planner). `--variant 3`, `--variant "Tower Push"`
or a variant id skips the question, and a piped or scripted run is never asked
and keeps the default.

## Game data

All ids live in `data/` as JSON, so a game patch usually needs no code change:

- `affixes.json`: affix SNO ids with the keys used to match build-site stat names
- `uniques.json`: unique items, each name mapped to all of its SNO variant ids
- `talisman_sets.json`: charm sets and their pieces
- `item_types.json`: item type ids (weapons, armor, Charm, Horadric Seal, ...)

To regenerate after a patch, grab the latest `d4-data.json` from
[D4LootBench](https://github.com/ThunderEagle/D4LootBench) and run:

```
python tools/generate_affixes.py path/to/d4-data.json
```

This rewrites all four files. Stats the build sites name differently from the
game data are handled by normalization plus a fuzzy fallback; anything that still
can't be mapped is listed as "unmapped" in the report instead of being dropped
silently.

## Adding a site

An adapter only has to produce rows of `(slot, stat_name, wants_greater_affix)`
plus the build's unique names; id mapping, rule assembly and encoding are shared.
`slot` feeds the per-slot rules (rows with `slot=None` go to the fallback pool).

- **Mobalytics** (implemented): the build lives in `window.__PRELOADED_STATE__`;
  affixes sit at `buildVariants.values[].genericBuilder.slots[].gameEntity.modifiers.gearStats[]`.
- **D4Builds** (implemented): the build streams in client-side, so the adapter
  reads the rendered DOM. One `.builder__stats__group` per slot, GA mark =
  `greater__affix__button--filled`, rows whose dropdown carries an icon are
  tempering/aspect rows and get skipped. Equipped items via `.builder__gear__name`
  (`--unique`/`--mythic` class modifiers), charms and seal from img alt texts.
- **InfinityBuilds** (implemented): a Next.js app router page, so the build ships
  in the RSC flight payload (the `self.__next_f.push([1,"…"])` chunks concatenate
  into one text that carries `variants[].gear[]` as plain JSON). Every value is a
  game id rather than a display name, which the adapter resolves against `data/`
  alone, no site API needed: `affixId` "affix-s04-life" against the `sno` field
  (`S04_Life`), `itemId` "item-ring-unique-rogue-101-itm" against `internal`
  (`Ring_Unique_Rogue_101`), charms against the talisman set `internal`. Ids also
  encode the roll variant and item-type context (`X2_Life_Greater`,
  `S04_CritChanceJewelry`), which stem to the same filterable affix. The `greater`
  flag per affix is the GA mark; `tempered` rows, a unique's own stats and
  transfiguration bonuses are not affixes a drop can roll and are skipped. Note
  the item's `itemName` is a snapshot in whatever language the author used, so it
  is never read. The weapon family comes from the item type inside the item id,
  not from the slot name: builds do park a two-handed bow in the `offhand` slot.
  The first variant is often an empty leveling planner, so the adapter defaults to
  the first variant that actually carries wanted stats.
- **Maxroll** (implemented): the only site that needs no browser. The guide HTML
  links its planner as `maxroll.gg/d4/planner/<id>`, and
  `planners.maxroll.gg/profiles/load/d4/<id>` hands that profile out as plain
  JSON (a planner link works as the URL too). One profile per build variant, and
  its `items` map a slot number to an entry in a shared item pool. Affixes are
  numeric SNO ids (`nid`) — the very ids `affixes.json` stores as `hash` — but
  roll and item-type variants (`X2_Life_Greater`, `S04_CritChanceJewelry`) carry
  an id of their own, so a `nid` is resolved to its SNO name via maxroll's
  `data.min.json` and stemmed back to the filterable affix, exactly like the
  InfinityBuilds ids. Slot numbers differ per class (the barbarian arsenal alone
  holds four weapons), so the slot family comes from the item type inside the
  item id, never from the number. An item whose internal name says `_Unique_`
  contributes no pool affixes, so a unique the data files don't know yet still
  can't leak its fixed stats into a slot rule. Passive and kill-streak ranks
  (`S04_PassiveRankBonus_*`, `S12_KillStreak_*`) do drop, but the game's filter
  has no condition for them, so they are reported as unmapped instead.

## Format notes

The wire format follows the community protobuf schema from
[fnuecke/diablo4-loot-filter-viewer](https://github.com/fnuecke/diablo4-loot-filter-viewer):
each rule carries a name, visibility, an ARGB color and a list of AND-ed
conditions. The per-stat Greater Affix requirement of the white tier uses the
schema's `params2` pair encoding, as seen in real game exports.

## Credits

Built on community reverse engineering, see [NOTICE.md](NOTICE.md) for the full
list: D4LootBench (data), fnuecke's filter viewer (schema), Upsilon72's generator
and the d4lf project. Diablo 4 is a trademark of Blizzard Entertainment; this
tool is unofficial.

MIT licensed, see [LICENSE](LICENSE).
