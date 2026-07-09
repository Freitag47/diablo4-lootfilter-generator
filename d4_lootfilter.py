#!/usr/bin/env python3
"""Generate a native Diablo 4 loot filter import code from a build guide.

Reads a Mobalytics or D4Builds build (per-slot stat priorities, Greater Affix
marks, uniques, talisman set charms, seal), maps everything to the game's SNO
ids and prints the Base64 code for:
    Character Menu -> Loot Filter -> New Filter -> Import

    python d4_lootfilter.py "https://mobalytics.gg/diablo-4/builds/rogue-dance-of-knives"

See README.md for the rule layout the filter is built from.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import re
import struct
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

if sys.version_info < (3, 9):
    raise SystemExit(f"Python 3.9+ required, this is {sys.version.split()[0]}")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VERSION = "1.0"

# Rules are written highest priority first, matching real game exports.
# Flip if an in-game test ever shows the order inverted.
BYTES_HIGHEST_FIRST = True

MAX_RULES = 25          # in-game limit per filter
NATURAL_AFFIXES = 3     # affixes on a dropped legendary; the occultist rerolls one

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------
# data files
# --------------------------------------------------------------------------
def _data_file(name: str) -> Path:
    here = Path(__file__).resolve().parent
    for p in (here / "data" / name, here / name, Path.cwd() / name):
        if p.exists():
            return p
    raise SystemExit(f"data file not found: {name} (looked in ./data/ and script dir)")


def _norm(s: str) -> str:
    s = s.strip().lstrip("+%").strip().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class _DB:
    """Key to hash lookup with a fuzzy fallback, shared by affixes/item types."""
    def __init__(self, path: Path, arr_key: str):
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.entries = doc[arr_key]
        self.by_hash, self.key2hash = {}, {}
        for e in self.entries:
            h = int(e["hash"], 16)
            e["_int"] = h
            self.by_hash[h] = e
            for k in e["keys"]:
                self.key2hash.setdefault(k, h)
        self.all_keys = list(self.key2hash)

    def name(self, h):
        e = self.by_hash.get(h)
        return e["name"] if e else f"0x{h:08X}"

    def _lookup(self, k, extra=()):
        for c in (k, *extra):
            if c in self.key2hash:
                return self.key2hash[c]
        m = difflib.get_close_matches(k, self.all_keys, n=1, cutoff=0.88)
        return self.key2hash[m[0]] if m else None


class AffixDB(_DB):
    def __init__(self, path): super().__init__(path, "affixes")

    def classes_of(self, h):
        e = self.by_hash.get(h)
        return e.get("classes") if e else None

    def map_slug(self, slug):
        k = _norm(slug)
        extra = []
        if k.startswith("ranks-to-"):
            extra.append(k[len("ranks-to-"):])
        extra.append(re.sub(r"-(multiplier|bonus|chance)$", "", k))
        h = self._lookup(k, extra)
        return (h, self.name(h)) if h is not None else (None, None)


class UniqueDB:
    """Maps a unique name to all of its SNO ids. The game data carries several
    variant ids per unique; the filter embeds all of them so any drop matches."""
    def __init__(self, path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.by_hash, self.key2hashes = {}, {}
        for e in doc["uniques"]:
            h = int(e["hash"], 16)
            self.by_hash[h] = e
            for k in e["keys"]:
                self.key2hashes.setdefault(k, [])
                if h not in self.key2hashes[k]:
                    self.key2hashes[k].append(h)
        self.all_keys = list(self.key2hashes)

    def name(self, h):
        e = self.by_hash.get(h)
        return e["name"] if e else f"0x{h:08X}"

    def map_all(self, slug):
        k = _norm(slug)
        hs = self.key2hashes.get(k)
        if not hs:
            m = difflib.get_close_matches(k, self.all_keys, n=1, cutoff=0.9)
            hs = self.key2hashes.get(m[0]) if m else None
        return (hs, self.name(hs[0])) if hs else (None, None)


class ItemTypeDB(_DB):
    def __init__(self, path): super().__init__(path, "itemTypes")

    def type_id(self, slug):
        return self._lookup(_norm(slug))


class TalismanSetDB:
    """Talisman (charm) sets. Builds list individual set pieces like
    'beru-of-spellbound-steel'; the trailing set slug maps a piece to its set."""
    def __init__(self, path):
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.by_hash = {int(s["hash"], 16): s for s in doc["talismanSets"]}
        self.key2hash = {}
        for s in doc["talismanSets"]:
            for k in s["keys"]:
                self.key2hash.setdefault(k, int(s["hash"], 16))
        self.keys_by_len = sorted(self.key2hash, key=len, reverse=True)

    def name(self, h):
        s = self.by_hash.get(h)
        return s["name"] if s else f"0x{h:08X}"

    def match_charm(self, slug):
        k = _norm(slug)
        if k in self.key2hash:
            return self.key2hash[k]
        for sk in self.keys_by_len:          # longest set slug first
            if k.endswith(sk):
                return self.key2hash[sk]
        return None


# --------------------------------------------------------------------------
# protobuf encoder (schema: fnuecke/diablo4-loot-filter-viewer)
# --------------------------------------------------------------------------
def _varint(v):
    out = bytearray()
    while True:
        out.append(v & 0x7F); v //= 128
        if v == 0: break
    for i in range(len(out) - 1):
        out[i] |= 0x80
    return bytes(out)


def _efv(fn, v):  return _varint((fn << 3) | 0) + _varint(v)
def _ef32(fn, v): return _varint((fn << 3) | 5) + struct.pack("<I", v & 0xFFFFFFFF)
def _efb(fn, d):  return _varint((fn << 3) | 2) + _varint(len(d)) + d
def _efs(fn, s):  return _efb(fn, s.encode("utf-8"))


# Condition types: 1 rarity, 2 properties, 3 codex, 4 greater-affix, 5 item-type,
# 6 required-affixes, 7 optional-affixes, 8 specific-unique, 9 talisman-set.
# Condition fields: 2 params1 (repeated fixed32), 3 params2 (repeated pair
# message), 4 value1, 5 value2, 6 value3.
def _c_rarity(mask):   return _efb(4, _efv(1, 1) + _efv(4, mask))
def _c_props(mask):    return _efb(4, _efv(1, 2) + _efv(4, mask))
def _c_codex():        return _efb(4, _efv(1, 3) + _efv(6, 1))
def _c_greater(n):     return _efb(4, _efv(1, 4) + _efv(4, n) + _efv(6, 1))
def _c_affixes(ids, n, ga_ids=()):
    """At least n of `ids`; each id in `ga_ids` must also be a Greater Affix
    (encoded as a params2 pair of the affix id with itself)."""
    inner = _efv(1, 6)
    for i in ids: inner += _ef32(2, i)
    for g in ga_ids: inner += _efb(3, _ef32(1, g) + _ef32(2, g))
    return _efb(4, inner + _efv(4, n))
def _c_params(ctype, ids):
    inner = _efv(1, ctype)
    for i in ids: inner += _ef32(2, i)
    return _efb(4, inner)
def _c_uniques(ids):      return _c_params(8, ids)
def _c_itemtype(ids):     return _c_params(5, ids)
def _c_talisman_set(ids): return _c_params(9, ids)


SHOW, RECOLOR, HIDE = 0, 2, 3
COMMON, MAGIC, RARE, LEGENDARY, UNIQUE, MYTHIC, TALISMAN = 1, 2, 4, 8, 16, 32, 64
PROP_ANCESTRAL = 4      # item-properties mask: 1 = none, 4 = ancestral


def _color(r, g, b, a=255): return ((a << 24) | (r << 16) | (g << 8) | b) & 0xFFFFFFFF

# No orange or yellow recolors: the game already prints Legendary names in
# orange and Rare names in yellow, so those would blend in on the ground.
C_UNIQUE = _color(255, 80, 80)     # light red: the build's uniques
C_CYAN   = _color(0, 255, 255)     # greater affix catch
C_GREEN  = _color(0, 200, 0)       # codex upgrades
C_BIS    = _color(255, 255, 255)   # white: per-slot BiS tier
C_GEAR   = _color(0, 150, 255)     # sky blue: per-slot gear tier
C_SET    = _color(190, 130, 255)   # purple: the build's talisman set charms
C_SEAL   = _color(255, 100, 200)   # magenta: horadric seals


def _rule(name, vis, conds, color=None):
    r = _efs(1, name) + _efv(2, vis)
    if color is not None:
        r += _ef32(3, color)
    for c in conds:
        r += c
    return _efb(1, r + _efv(5, 1))


def _filter_bytes(name, rules):
    seq = rules if BYTES_HIGHEST_FIRST else list(reversed(rules))
    # trailing fields 3/4 are always 3 in real game exports
    return b"".join(seq) + _efs(2, name) + _efv(3, 3) + _efv(4, 3)


# --------------------------------------------------------------------------
# slot -> item type mapping
# --------------------------------------------------------------------------
CLASS_NAMES = ("barbarian", "druid", "necromancer", "rogue", "sorcerer",
               "spiritborn", "paladin")

ARMOR_SLOT_TYPES = {
    "helm": ["helm"], "chest-armor": ["chest-armor"], "gloves": ["gloves"],
    "pants": ["pants"], "boots": ["boots"], "amulet": ["amulet"], "ring": ["ring"],
}

CLASS_WEAPONS = {
    "rogue":       {"1h": ["sword", "dagger", "hand-crossbow"],
                    "2h": [], "ranged": ["bow", "crossbow"], "offhand": []},
    "barbarian":   {"1h": ["axe", "mace", "sword"],
                    "2h": ["two-handed-axe", "two-handed-mace", "two-handed-sword", "polearm"],
                    "ranged": [], "offhand": []},
    "necromancer": {"1h": ["sword", "dagger", "wand", "scythe"],
                    "2h": ["two-handed-sword", "two-handed-scythe"],
                    "ranged": [], "offhand": ["focus", "shield"]},
    "sorcerer":    {"1h": ["wand"], "2h": ["staff"], "ranged": [], "offhand": ["focus"]},
    "druid":       {"1h": ["axe", "mace"],
                    "2h": ["two-handed-axe", "two-handed-mace", "staff"],
                    "ranged": [], "offhand": ["totem"]},
    "spiritborn":  {"1h": [], "2h": ["staff", "polearm"], "ranged": [], "offhand": []},
}

SLOT_LABELS = {"chest-armor": "Chest", "ring": "Rings", "dual-wield-weapon": "Melee",
               "ranged-weapon": "Ranged", "main-hand-weapon": "Weapon",
               "off-hand-weapon": "Off-Hand", "two-handed-bludgeoning-weapon": "2H Blunt",
               "two-handed-slashing-weapon": "2H Slash"}


def slot_type_keys(slot, cls):
    """Slot slug -> (item type keys or None, slot family). ring-1/ring-2 and
    dual-wield-weapon-1/2 collapse into one family so their pools merge."""
    base = re.sub(r"-\d+$", "", _norm(slot or ""))
    if base in ARMOR_SLOT_TYPES:
        return ARMOR_SLOT_TYPES[base], base
    w = CLASS_WEAPONS.get(cls or "", {})
    if "ranged" in base:
        keys = w.get("ranged")
    elif "dual-wield" in base:
        keys = w.get("1h")
    elif "bludgeoning" in base and cls == "barbarian":
        keys = ["two-handed-mace"]
    elif "slashing" in base and cls == "barbarian":
        keys = ["two-handed-sword", "two-handed-axe", "polearm"]
    elif "two-handed" in base:
        keys = w.get("2h")
    elif "off-hand" in base:
        keys = w.get("offhand")
    elif "main-hand" in base or "weapon" in base:
        keys = (w.get("1h") or []) + (w.get("2h") or [])
    else:
        keys = None
    return (keys or None), base


def slot_label(base):
    return SLOT_LABELS.get(base) or base.replace("-", " ").title()


def detect_class(url, adb, gear_rows):
    """Class from the URL slug if exactly one class name appears in it, else
    from the class restrictions the build's own affixes carry in affixes.json."""
    slug = (urlparse(url).path.rstrip("/").split("/")[-1] or "").lower() if url else ""
    hits = [c for c in CLASS_NAMES if c in slug]
    if len(hits) == 1:
        return hits[0]
    candidates = None
    for _slot, s, _g in gear_rows:
        h, _n = adb.map_slug(s)
        cl = adb.classes_of(h) if h is not None else None
        if cl and "All" not in cl:
            cs = {c.lower() for c in cl}
            candidates = cs if candidates is None else (candidates & cs)
    return candidates.pop() if candidates and len(candidates) == 1 else None


# --------------------------------------------------------------------------
# rule assembly
# --------------------------------------------------------------------------
def build_filter_code(name, unique_ids, slot_rules, fallback_ids=(), ga_n=1,
                      hide_junk=True, set_ids=(), seal_type=None, charm_type=None,
                      all_type_ids=(), ancestral_uniques=False,
                      ancestral_gear=False):
    """Assemble the ruleset, highest priority first:
      1. build uniques (red), the build's set charms (purple), codex upgrades (green)
      2. per-slot BiS (white): right item type, every affix slot desired, and the
         marked stats rolled as Greater Affix
      3. per-slot gear (blue): same affix match without the GA requirement
      4. greater affix catch (cyan), below the gear tiers so it only marks GA
         items that are not a build match
      5. keeps: Legendary+ seals (magenta), set charms of any set, uniques/mythics
      6. hide everything else that is gear, ancestral or not; scoped to equipment
         item types so materials, sigils and other drops stay untouched
    With ancestral_uniques rules 1 and 5 only match Ancestral uniques/mythics
    (item-properties condition, any Greater Affix count) and 6 hides plain
    uniques too, the build's own included. With ancestral_gear the slot tiers
    in 2/3 and the fallback rule only match Ancestral drops.
    Returns (code, rule_count, dropped_bis_labels)."""
    def slot_conds(sr, n, ga):
        conds = [_c_rarity(RARE | LEGENDARY)]
        if ancestral_gear:
            conds.append(_c_props(PROP_ANCESTRAL))
        if sr["type_ids"]:
            conds.append(_c_itemtype(sr["type_ids"]))
        conds.append(_c_affixes(sr["ids"], n, ga))
        return conds

    def assemble(bis_rules):
        rules = []
        if unique_ids:
            uconds = [_c_uniques(unique_ids)]
            if ancestral_uniques:
                uconds.append(_c_props(PROP_ANCESTRAL))
            rules.append(_rule("Build Uniques", RECOLOR, uconds, C_UNIQUE))
        if set_ids:
            rules.append(_rule("Set Charms", RECOLOR, [_c_talisman_set(list(set_ids))], C_SET))
        rules.append(_rule("Codex Upgrade", RECOLOR, [_c_codex()], C_GREEN))
        for sr in bis_rules:
            rules.append(_rule(f"BiS: {sr['label']}", RECOLOR,
                               slot_conds(sr, sr["n_bis"], sr["ga"]), C_BIS))
        for sr in slot_rules:
            rules.append(_rule(f"Gear: {sr['label']}", RECOLOR,
                               slot_conds(sr, sr["n_fix"], ()), C_GEAR))
        if fallback_ids:
            n = 2 if len(fallback_ids) >= NATURAL_AFFIXES else 1
            fconds = [_c_rarity(RARE | LEGENDARY)]
            if ancestral_gear:
                fconds.append(_c_props(PROP_ANCESTRAL))
            fconds.append(_c_affixes(fallback_ids, n))
            rules.append(_rule("Build Affix", RECOLOR, fconds, C_GEAR))
        rules.append(_rule(f"{ga_n}+ Greater Affix", RECOLOR, [_c_greater(ga_n)], C_CYAN))
        if seal_type is not None:
            rules.append(_rule("Legendary Seals", RECOLOR,
                               [_c_rarity(LEGENDARY | UNIQUE | MYTHIC),
                                _c_itemtype([seal_type])], C_SEAL))
        if charm_type is not None:
            rules.append(_rule("Set Charms (all)", SHOW,
                               [_c_rarity(TALISMAN), _c_itemtype([charm_type])]))
        if ancestral_uniques:
            rules.append(_rule("Ancestral Uniques", SHOW,
                               [_c_rarity(UNIQUE | MYTHIC), _c_props(PROP_ANCESTRAL)]))
        else:
            rules.append(_rule("Keep Uniques", SHOW, [_c_rarity(UNIQUE | MYTHIC)]))
        if hide_junk:
            mask = COMMON | MAGIC | RARE | LEGENDARY
            if ancestral_uniques:
                mask |= UNIQUE
            conds = [_c_rarity(mask)]
            if all_type_ids:
                conds.append(_c_itemtype(list(all_type_ids)))
            rules.append(_rule("Hide Junk Gear", HIDE, conds))
        return rules

    # BiS rules exist only for slots with a GA mark; drop them from the back
    # if the rule budget would overflow (the gear tier always stays)
    bis = [sr for sr in slot_rules if sr["ga"]]
    dropped = []
    rules = assemble(bis)
    while len(rules) > MAX_RULES and bis:
        dropped.append(bis.pop()["label"])
        rules = assemble(bis)
    if len(rules) > MAX_RULES:
        print(f"[warn] {len(rules)} rules exceed the in-game limit of {MAX_RULES}",
              file=sys.stderr)
    return (base64.b64encode(_filter_bytes(name, rules)).decode("ascii"),
            len(rules), dropped)


# --------------------------------------------------------------------------
# mobalytics
# --------------------------------------------------------------------------
def detect_site(url):
    host = (urlparse(url).hostname or "").lower()
    return ("mobalytics" if "mobalytics.gg" in host else
            "maxroll" if "maxroll.gg" in host else
            "d4builds" if "d4builds.gg" in host else "unknown")


def _active_variant_id(url):
    for k, vals in parse_qs(urlparse(url).query).items():
        if k.startswith("ws-"):
            for raw in vals:
                if "activeVariantId" in raw and (p := raw.split(","))[-1]:
                    return p[-1]
    return None


def _find_variant_lists(obj):
    if isinstance(obj, dict):
        bv = obj.get("buildVariants")
        if isinstance(bv, dict) and isinstance(bv.get("values"), list):
            yield bv["values"]
        for v in obj.values():
            yield from _find_variant_lists(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_variant_lists(v)


def _has_slots(v):
    return isinstance(v, dict) and isinstance((v.get("genericBuilder") or {}).get("slots"), list)


def extract_mobalytics(url, state, variant_id, include_tempering=False):
    """Return (variant_id, gear_rows[(slot, slug, greater)], unique_slugs)."""
    lists = list(_find_variant_lists(state))
    if not lists:
        raise RuntimeError("no buildVariants in page state")
    variants = max(lists, key=lambda vs: sum(1 for v in vs if _has_slots(v)))
    chosen = None
    if variant_id is not None:
        chosen = next((v for v in variants if _has_slots(v) and str(v.get("id")) == str(variant_id)), None)
        if chosen is None:
            print(f"[warn] variant {variant_id!r} not found "
                  f"({[str(v.get('id')) for v in variants]}); using first", file=sys.stderr)
    chosen = chosen or next((v for v in variants if _has_slots(v)), None)
    if chosen is None:
        raise RuntimeError("no variant with equipment slots")

    rows, uniques = [], []
    for sl in chosen["genericBuilder"]["slots"]:
        ge = sl.get("gameEntity") or {}
        if ge.get("type") == "uniqueItems":
            # fixed unique affixes stay out of the legendary pools; the item
            # itself is matched by the specific-unique rule
            if ge.get("slug"):
                uniques.append(ge["slug"])
            continue
        mods = ge.get("modifiers") or {}
        for b in ["gearStats"] + (["temperingStats"] if include_tempering else []):
            for g in (mods.get(b) or []):
                if g and g.get("id"):
                    rows.append((sl.get("gameSlotSlug"), g["id"], bool(g.get("isGreater"))))
    return str(chosen.get("id")), rows, uniques


def _choose_variant(state, variant_id):
    lists = list(_find_variant_lists(state))
    variants = max(lists, key=lambda vs: sum(1 for v in vs if _has_slots(v))) if lists else []
    if variant_id is not None:
        v = next((v for v in variants if _has_slots(v) and str(v.get("id")) == str(variant_id)), None)
        if v is not None:
            return v
    return next((v for v in variants if _has_slots(v)), None)


def extract_talismans(state, variant_id):
    """Return (charm_slugs, has_seal) from the variant's talismansPriorityList."""
    var = _choose_variant(state, variant_id) or {}
    charm_slugs, has_seal = [], False
    for entry in var.get("talismansPriorityList") or []:
        etype, slug = str(entry.get("type") or ""), entry.get("slug")
        if "seal" in etype:
            has_seal = True
        elif "charm" in etype and slug:
            charm_slugs.append(slug)
    return charm_slugs, has_seal


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "Playwright is required to fetch from a URL:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium\n"
            "Or use --stats / --paste / --html instead.")
    return sync_playwright


def _launch_chromium(pw):
    try:
        return pw.chromium.launch(headless=True)
    except Exception as e:
        if "xecutable doesn't exist" in str(e) or "playwright install" in str(e):
            raise SystemExit(
                "Playwright is installed but its Chromium is missing. Run once:\n"
                "  python -m playwright install chromium")
        raise


def fetch_state_playwright(url, timeout_ms=60000):
    with _playwright()() as pw:
        b = _launch_chromium(pw)
        try:
            page = b.new_context(viewport={"width": 1440, "height": 2000}, user_agent=_UA).new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            for _ in range(20):
                st = page.evaluate("() => window.__PRELOADED_STATE__ || null")
                if st:
                    return st
                page.wait_for_timeout(500)
            raise RuntimeError("window.__PRELOADED_STATE__ never populated")
        finally:
            b.close()


def _preloaded_state_from_html(html):
    m = re.search(r"__PRELOADED_STATE__\s*=", html)
    if not m:
        raise RuntimeError("__PRELOADED_STATE__ not found in HTML")
    start = html.find("{", m.end())
    depth, i, instr, esc = 0, start, False, False
    while i < len(html):
        c = html[i]
        if instr:
            esc = c == "\\" and not esc
            if c == '"' and not esc:
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:i + 1])
        i += 1
    raise RuntimeError("unterminated __PRELOADED_STATE__")


# --------------------------------------------------------------------------
# d4builds (the build streams in client-side, so read the rendered DOM)
# --------------------------------------------------------------------------
_D4B_DOM_JS = """() => {
  const CLASSES = ['barbarian','druid','necromancer','rogue','sorcerer','spiritborn','paladin'];
  const dd = document.querySelector('.stat__dropdown');
  const cls = dd ? (dd.className.split(/\\s+/).find(t => CLASSES.includes(t)) || null) : null;
  const groups = [...document.querySelectorAll('.builder__stats__group')].map(g => ({
    slot: (g.querySelector('.builder__stats__slot')?.textContent || '').trim(),
    stats: [...g.querySelectorAll('.builder__stat')].map(st => ({
      name: (st.querySelector('.dropdown__button span')?.textContent || '').trim(),
      ga: !!st.querySelector('.greater__affix__button--filled'),
      filled: /\\bfilled\\b/.test(st.querySelector('.stat__dropdown')?.className || ''),
      icon: st.querySelector('.dropdown__button__wrapper img')?.className || '',
    })),
  }));
  const names = [...document.querySelectorAll('.builder__gear__name')];
  const slots = [...document.querySelectorAll('.builder__gear__slot')];
  const items = names.map((n, i) => ({
    name: n.textContent.trim(),
    kind: /--mythic/.test(n.className) ? 'mythic'
        : /--unique/.test(n.className) ? 'unique' : 'aspect',
    slot: (slots[i]?.textContent || '').trim(),
  }));
  const charms = [...document.querySelectorAll('.builder__charm img')]
      .map(c => (c.alt || '').trim()).filter(Boolean);
  const seal = ((document.querySelector('.builder__seal img') || {}).alt || '').trim();
  const hdr = document.querySelector('.builder__header__name');
  const name = ((hdr && (hdr.value || hdr.textContent)) || document.title || '').trim();
  const variants = [...document.querySelectorAll('.builder__variant__input')]
      .map(i => (i.value || '').trim());
  return {cls, groups, items, charms, seal, name, variants};
}"""


def _d4builds_build_name(raw):
    """Strip the SEO suffix from guide titles; planner names pass through."""
    if not raw:
        return None
    n = re.sub(r"\s*(?:Build Guide)?\s*[-–·|]\s*(?:Diablo 4|D4 Builds).*$",
               "", raw.strip())
    n = re.sub(r"\s*Build Guide\s*$", "", n)
    return n or None


def d4builds_variant_url(url, variant):
    if variant is None:
        return url
    parts = urlparse(url)
    q = parse_qs(parts.query)
    q["var"] = [str(variant)]
    return parts._replace(query=urlencode(q, doseq=True)).geturl()


def fetch_d4builds_playwright(url, timeout_ms=90000):
    with _playwright()() as pw:
        b = _launch_chromium(pw)
        try:
            ctx = b.new_context(viewport={"width": 1600, "height": 2400}, user_agent=_UA)
            # only the DOM is needed, not the assets or ads
            ctx.route("**/*", lambda r: r.abort()
                      if r.request.resource_type in ("image", "media", "font")
                      else r.continue_())
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector(".builder__stats__group", timeout=timeout_ms)
            for _ in range(30):
                data = page.evaluate(_D4B_DOM_JS)
                if any(st["filled"] for g in data["groups"] for st in g["stats"]):
                    page.wait_for_timeout(1000)     # let the last dropdowns settle
                    return page.evaluate(_D4B_DOM_JS)
                page.wait_for_timeout(500)
            raise RuntimeError("d4builds gear stats never populated")
        finally:
            b.close()


def extract_d4builds(data, include_tempering=False):
    """DOM data -> (gear_rows, unique_names, charm_names, has_seal, cls).
    Slot labels normalize to the same families as Mobalytics slugs. Slots
    holding a unique/mythic contribute no pool affixes. Plain stat rows have
    no icon in their dropdown; tempering/aspect/transfigure rows do."""
    unique_slots = {_norm(it["slot"]) for it in data["items"]
                    if it["kind"] in ("unique", "mythic")}
    rows = []
    for g in data["groups"]:
        slot = _norm(g["slot"])
        if not slot or slot in unique_slots:
            continue
        for st in g["stats"]:
            if not st["filled"] or not st["name"]:
                continue
            if re.fullmatch(r"stat-\d+", _norm(st["name"])):    # empty placeholder
                continue
            name, icon = st["name"], st.get("icon") or ""
            if icon:
                if "tempering" in icon and include_tempering:
                    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
                else:
                    continue
            rows.append((slot, name, bool(st["ga"])))
    uniques = [it["name"] for it in data["items"] if it["kind"] in ("unique", "mythic")]
    return rows, uniques, list(data.get("charms") or []), bool(data.get("seal")), data.get("cls")


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
def build(adb, udb, gear_rows, unique_slugs, name, ga_n, hide_junk, cls=None,
          tset_db=None, itype_db=None, charm_slugs=(), has_seal=False,
          ancestral_uniques=False, ancestral_gear=False):
    # group desired affixes per slot family; rows without a slot
    # (--stats / --paste input) feed one global fallback pool
    groups, order, fallback_ids, unmapped = {}, [], [], []
    for slot, slug, greater in gear_rows:
        h, nm = adb.map_slug(slug)
        if h is None:
            if slug not in [u[0] for u in unmapped]:
                unmapped.append((slug, slot))
            continue
        if slot is None:
            if h not in fallback_ids:
                fallback_ids.append(h)
            continue
        keys, base = slot_type_keys(slot, cls)
        gk = (base, tuple(keys or ()))
        g = groups.get(gk)
        if g is None:
            g = groups[gk] = {"base": base, "keys": keys, "ids": [], "ga": [],
                              "names": {}, "slots": []}
            order.append(gk)
        if slot not in g["slots"]:
            g["slots"].append(slot)
        if h not in g["ids"]:
            g["ids"].append(h)
            g["names"][h] = nm
        if greater and h not in g["ga"]:
            g["ga"].append(h)

    slot_rules = []
    for gk in order:
        g = groups[gk]
        if g["keys"] is None and g["base"] not in ("charm", "horadric-seal"):
            print(f"[warn] no item-type mapping for slot '{g['base']}' "
                  f"(class: {cls or 'unknown'}); rule matches any item type",
                  file=sys.stderr)
        type_ids = [t for t in (itype_db.type_id(k) for k in (g["keys"] or []))
                    if t is not None] if itype_db else []
        n_full = min(NATURAL_AFFIXES, len(g["ids"]))
        slot_rules.append({
            "label": slot_label(g["base"]), "slots": g["slots"], "keys": g["keys"] or [],
            "type_ids": type_ids, "ids": g["ids"], "ga": g["ga"], "names": g["names"],
            "n_bis": n_full,
            "n_fix": n_full,
        })

    # set-piece charms resolve to their set; anything else is a unique charm
    set_ids, charm_uniques = [], []
    for cs in charm_slugs:
        sid = tset_db.match_charm(cs) if tset_db else None
        if sid is not None and sid not in set_ids:
            set_ids.append(sid)
        elif sid is None:
            charm_uniques.append(cs)

    uids, uni_named, uni_unmapped = [], [], []
    for slug in list(unique_slugs) + charm_uniques:
        hs, nm = udb.map_all(slug)
        if hs:
            for h in hs:
                if h not in uids:
                    uids.append(h)
            uni_named.append(nm)
        else:
            uni_unmapped.append(slug)

    seal_type = itype_db.type_id("horadric-seal") if itype_db else None
    charm_type = itype_db.type_id("charm") if itype_db else None
    all_type_ids = [e["_int"] for e in itype_db.entries] if itype_db else []

    code, n_rules, dropped_bis = build_filter_code(
        name, uids, slot_rules, fallback_ids, ga_n, hide_junk,
        set_ids=set_ids, seal_type=seal_type, charm_type=charm_type,
        all_type_ids=all_type_ids, ancestral_uniques=ancestral_uniques,
        ancestral_gear=ancestral_gear)
    return code, {"slot_rules": slot_rules, "fallback": fallback_ids,
                  "unmapped": unmapped, "cls": cls,
                  "n_rules": n_rules, "dropped_bis": dropped_bis,
                  "uniques": uni_named, "uni_unmapped": uni_unmapped,
                  "sets": [tset_db.name(s) for s in set_ids] if tset_db else [],
                  "build_seal": has_seal, "hide_junk": hide_junk,
                  "ancestral_uniques": ancestral_uniques,
                  "ancestral_gear": ancestral_gear,
                  "kept": {"uniques": True, "set_charms": charm_type is not None,
                           "seals": seal_type is not None}}


def name_from_url(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1] or "D4 Filter"
    return re.sub(r"\s+", " ", slug.replace("-", " ").title())[:30]


def slugs_from_freetext(text):
    return [(None, _norm(p), False) for p in re.split(r"[,\n]", text) if p.strip()]


def _report(name, variant_id, rep, code, verbose):
    print(f"\n  Filter: {name}" + (f"   (variant {variant_id})" if variant_id else "")
          + (f"   class: {rep['cls']}" if rep.get("cls") else ""))
    extra = ([f"Sets: {len(rep['sets'])}"] if rep.get("sets") else []) + \
            (["build seal"] if rep.get("build_seal") else [])
    print(f"  Uniques: {len(rep['uniques'])}   Slot rules: {len(rep['slot_rules'])}   "
          f"Unmapped: {len(rep['unmapped'])}   Rules: {rep['n_rules']}/{MAX_RULES}"
          + ("   " + "   ".join(extra) if extra else ""))
    kept = rep.get("kept") or {}
    anc = rep.get("ancestral_uniques")
    uniq_label = ("Ancestral uniques/mythics" if anc
                  else "uniques/mythics")
    keep_names = [n for n, on in ((uniq_label, kept.get("uniques")),
                                  ("legendary+ seals", kept.get("seals")),
                                  ("set charms (any set)", kept.get("set_charms"))) if on]
    if keep_names:
        print(f"  Always shown: {', '.join(keep_names)}")
    if rep.get("hide_junk"):
        hidden = "all other gear up to Legendary"
        if rep.get("ancestral_gear"):
            hidden += " and non-Ancestral build matches"
        if anc:
            hidden += " and non-Ancestral uniques (build uniques too)"
        print(f"  Hidden: {hidden} (magic/rare charms, "
              "low seals, unmatched legendaries incl. Ancestral)")
    if rep["slot_rules"]:
        rl = ("Ancestral Rare/Legendary" if rep.get("ancestral_gear")
              else "Rare/Legendary")
        print(f"\n  Gear rules ({rl}, * = wanted as Greater Affix):")
        for sr in rep["slot_rules"]:
            stats = ", ".join(sr["names"][h] + ("*" if h in sr["ga"] else "")
                              for h in sr["ids"])
            types = "/".join(sr["keys"]) if sr["keys"] else "any type"
            bis = "  [BiS tier]" if sr["ga"] else ""
            print(f"    {sr['label']:<9} {sr['n_fix']}+ of {len(sr['ids'])}"
                  f"  ({types}){bis}\n              {stats}")
        if rep.get("dropped_bis"):
            print(f"  [!] BiS tier dropped to fit {MAX_RULES} rules: "
                  + ", ".join(rep["dropped_bis"]))
    if rep.get("fallback"):
        print(f"\n  Global affix pool (no slot info): {len(rep['fallback'])} affixes, "
              f"{2 if len(rep['fallback']) >= NATURAL_AFFIXES else 1}+ must match")
    if verbose:
        if rep["uniques"]:
            print("\n  Build uniques (highlighted):")
            for n in rep["uniques"]:
                print(f"    * {n}")
        if rep.get("sets"):
            print("\n  Set charms (highlighted):")
            for n in rep["sets"]:
                print(f"    + {n}")
    if rep["unmapped"] or rep["uni_unmapped"]:
        print("\n  Unmapped:")
        for slug, slot in rep["unmapped"]:
            print(f"    - {slug}" + (f"  ({slot})" if slot else ""))
        for slug in rep["uni_unmapped"]:
            print(f"    - {slug}  (unique)")
    print("\n" + "═" * 68)
    print("IMPORT CODE  (D4 -> Loot Filter -> New Filter -> Import):\n")
    print(code)
    print("═" * 68)


def main():
    ap = argparse.ArgumentParser(
        description="Generate a Diablo 4 loot filter import code from a build URL.")
    ap.add_argument("url", nargs="?", help="Mobalytics or D4Builds build URL")
    ap.add_argument("--variant", help="Mobalytics variant id or d4builds var index")
    ap.add_argument("--name", help="filter name shown in game (max 30 chars)")
    ap.add_argument("--ga-threshold", type=int, default=1,
                    help="min greater affixes for the cyan rule (default 1)")
    ap.add_argument("--class", dest="cls", choices=CLASS_NAMES,
                    help="build class for weapon item types (default: auto-detect)")
    ap.add_argument("--no-hide", action="store_true",
                    help="never hide anything, only recolor/keep")
    ap.add_argument("--ancestral-uniques", action="store_true",
                    help="show uniques/mythics, the build's own included, only "
                         "when they drop as Ancestral (any Greater Affix count)")
    ap.add_argument("--ancestral-gear", action="store_true",
                    help="match the per-slot BiS/gear rules only on Ancestral "
                         "drops; non-Ancestral matches fall into the hide rule")
    ap.add_argument("--include-tempering", action="store_true",
                    help="treat tempering stats as droppable affixes")
    ap.add_argument("--print-detected", action="store_true",
                    help="also list the build's uniques and set charms")
    ap.add_argument("--stats", help="build from a comma-separated stat list, no browser")
    ap.add_argument("--paste", action="store_true",
                    help="read gear text from stdin, no browser")
    ap.add_argument("--html", help="read a saved Mobalytics page instead of fetching")
    ap.add_argument("--dump-json", help="save the raw extracted build data")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = ap.parse_args()

    adb = AffixDB(_data_file("affixes.json"))
    udb = UniqueDB(_data_file("uniques.json"))
    tset_db = TalismanSetDB(_data_file("talisman_sets.json"))
    itype_db = ItemTypeDB(_data_file("item_types.json"))
    hide_junk = not args.no_hide

    if args.stats or args.paste:
        text = args.stats if args.stats else _read_paste()
        rows = slugs_from_freetext(text)
        name = (args.name or (name_from_url(args.url) if args.url else "Custom Filter"))[:30]
        code, rep = build(adb, udb, rows, [], name, args.ga_threshold,
                          hide_junk, itype_db=itype_db,
                          ancestral_uniques=args.ancestral_uniques,
                          ancestral_gear=args.ancestral_gear)
        _report(name, None, rep, code, args.print_detected)
        return

    if not args.url and not args.html:
        ap.error("give a build URL, or use --stats / --paste / --html")

    site = detect_site(args.url) if args.url else "mobalytics"
    if site == "d4builds" and not args.html:
        url = d4builds_variant_url(args.url, args.variant)
        data = fetch_d4builds_playwright(url)
        if args.dump_json:
            Path(args.dump_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        rows, uniques, charm_slugs, has_seal, page_cls = extract_d4builds(data, args.include_tempering)
        var_idx = (parse_qs(urlparse(url).query).get("var") or ["0"])[0]
        vnames = data.get("variants") or []
        variant_id = (vnames[int(var_idx)] if var_idx.isdigit() and int(var_idx) < len(vnames)
                      and vnames[int(var_idx)] else var_idx)
        page_name = _d4builds_build_name(data.get("name"))
        cls = args.cls or page_cls or detect_class(args.url, adb, rows)
    else:
        if args.html:
            state = _preloaded_state_from_html(Path(args.html).read_text(encoding="utf-8", errors="replace"))
        elif site == "mobalytics":
            state = fetch_state_playwright(args.url)
        else:
            raise SystemExit(f"[info] '{site}' URLs aren't auto-supported yet; "
                             f"use --stats \"...\" or --paste.")

        if args.dump_json:
            Path(args.dump_json).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        vid = args.variant or (_active_variant_id(args.url) if args.url else None)
        variant_id, rows, uniques = extract_mobalytics(args.url or "", state, vid, args.include_tempering)
        charm_slugs, has_seal = extract_talismans(state, variant_id)
        cls = args.cls or detect_class(args.url, adb, rows)
        page_name = None

    name = (args.name or page_name
            or (name_from_url(args.url) if args.url else "D4 Filter"))[:30]
    code, rep = build(adb, udb, rows, uniques, name, args.ga_threshold,
                      hide_junk, cls=cls,
                      tset_db=tset_db, itype_db=itype_db,
                      charm_slugs=charm_slugs, has_seal=has_seal,
                      ancestral_uniques=args.ancestral_uniques,
                      ancestral_gear=args.ancestral_gear)
    _report(name, variant_id, rep, code, args.print_detected)


def _read_paste():
    print("Paste the gear/stats text, then an empty line "
          "(Ctrl+Z Enter on Windows, Ctrl+D elsewhere):\n", file=sys.stderr)
    return "".join(sys.stdin)


if __name__ == "__main__":
    main()
