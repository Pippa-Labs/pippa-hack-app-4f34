"""MealMind: pick a recipe from what is in the message.

One outcome, `meal.match`. Everything it knows comes from the message (the
ingredients someone has, the minutes, the budget and prices they give, their
diet and allergens, nutrition targets, meals they mention having had) and from
the bundled data in `mealmind_data.py`. It keeps nothing and asks nothing.

How it decides, in order, so that every answer can be explained:

1. **Hard rules first.** A recipe is ruled out if any ingredient contains, or
   may contain, a named allergen; breaks a named diet; uses something the
   person wants left out; takes longer than their limit; or costs more than
   their budget to buy now (only when that cost is actually known). These are
   never relaxed. When nothing survives, the answer says which rule removed
   what.
2. **Then a score**, with every point shown as a reason:
   coverage of their ingredients up to +60, -8 per thing to buy, protein
   target met +15 / missed -15, calorie limit met +10 / missed -10, budget
   within +10 / unverifiable -5, the same meal as a recent one -40, the same
   main ingredient -15, and for TheMealDB recipes -10 for an unknown time and
   -15 for unknown nutrition when a target was given.
3. **Ties** go to fewer things to buy, then the shorter time, then the name.

No language model runs here: the same message always gives the same answer.

Optional second source: TheMealDB, only when the builder has set the app key
`THEMEALDB_API_KEY` in Pippa. It has no times, weights, nutrition or prices,
so its recipes are only offered when no allergen, diet, exclusion or time
limit was given, and the answer says what is unknown about them.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import quote, urlencode

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

from mealmind_data import (
    ALLERGEN_GROUPS,
    ALLERGENS,
    DIET_AS_ALLERGEN,
    DIETS,
    INGREDIENTS,
    NUTRITION_SOURCE,
    RECIPES,
    UNCHECKABLE_ALLERGENS,
)

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")
OUTCOME = "meal.match"

#: TheMealDB answers most clients without one, but a descriptive User-Agent
#: is what docs/API_APPS.md asks of every app, and urllib sends none useful.
USER_AGENT = "pippa-agent-app-mealmind/0.1 (https://github.com/Pippa-Labs)"

#: The fixed address the optional key is sent to, and nowhere else. The key
#: is part of the path, so redirects are refused (see `_NoRedirect`).
MEALDB_BASE = "https://www.themealdb.com/api/json/v1/"
MEALDB_KEY_ENV = "THEMEALDB_API_KEY"
MEALDB_PAGE = "https://www.themealdb.com/meal/"
MEALDB_DEADLINE_S = 9.0
MEALDB_REQUEST_S = 4.0
MEALDB_MAX_FILTERS = 2
MEALDB_MAX_LOOKUPS = 3

CARD_SOURCE = "MealMind recipe card"
MAX_LISTED = 10


# ── Errors the person can act on ─────────────────────────────────────────────


class NothingToGoOn(AgentAppError):
    user_message = (
        "Tell me something to go on: the ingredients you have, how many "
        "minutes you have, a budget with prices, a diet or allergy, or a "
        "calorie or protein target."
    )
    retryable = False


class InvalidNumber(AgentAppError):
    user_message = "One of the numbers in that request did not make sense."
    retryable = False


class UnsupportedAllergen(AgentAppError):
    user_message = "I cannot check recipes for that allergen."
    retryable = False


class UnknownIngredients(AgentAppError):
    user_message = "I did not recognise any of those ingredients."
    retryable = False


class MixedCurrencies(AgentAppError):
    user_message = "Those prices are in more than one currency, so I cannot add them up."
    retryable = False


def _error(cls: type[AgentAppError], internal: str, message: str) -> AgentAppError:
    """An error whose user message names the problem. `internal` goes to the
    logs only, so it never carries what the person typed."""
    error = cls(internal)
    error.user_message = message
    return error


# ── Text ─────────────────────────────────────────────────────────────────────


def _norm(text) -> str:
    """Lower case, full-width forms folded (NFKC), hyphens as spaces."""
    text = unicodedata.normalize("NFKC", str(text)).lower()
    text = text.replace("’", "'").replace("-", " ")
    return " ".join(text.split())


_SPLIT_LIST = re.compile(r"[,;\n]+")
_SPLIT_AND = re.compile(r"\s+(?:and|&|plus)\s+")


def _items(value, *, split_and: bool = True) -> list[str]:
    """A list input as clean, distinct strings. Absent, None and "" are []."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for entry in value:
        if entry is None:
            continue
        # "1,80" is a price with a decimal comma, not two items.
        text = re.sub(r"(\d),(\d{2})(?!\d)", r"\1.\2", _norm(entry))
        for part in _SPLIT_LIST.split(text):
            pieces = _SPLIT_AND.split(part) if split_and else [part]
            for piece in pieces:
                piece = piece.strip(" .!?")
                if piece and piece not in out:
                    out.append(piece)
    return out


# ── Ingredient names ─────────────────────────────────────────────────────────

_ALIAS: dict[str, tuple[str, bool]] = {}
for _iid, _ing in INGREDIENTS.items():
    for _alias in _ing["aliases"]:
        _ALIAS.setdefault(_norm(_alias), (_iid, False))
    for _alias in _ing.get("cooked_aliases", []):
        _ALIAS.setdefault(_norm(_alias), (_iid, True))

#: Aliases safe to look for inside a longer string: a two-letter word is too
#: likely to be part of something else.
_SCAN: list[tuple[str, tuple[str, bool], re.Pattern]] = [
    (_alias, _hit, re.compile(rf"\b{re.escape(_alias)}(?:e?s)?\b"))
    for _alias, _hit in sorted(_ALIAS.items(), key=lambda kv: -len(kv[0]))
    if len(_alias) >= 3
]

_FILLER = re.compile(
    r"\b(?:of|the|some|about|around|approx|approximately|roughly|for|per|each|"
    r"only|x|is|are|costs?|at)\b|[=:/()\[\]×*~+]"
)
_DESCRIPTORS = {
    "large", "medium", "small", "big", "ripe", "raw", "dry", "dried",
    "organic", "fresh", "whole", "chopped", "sliced", "diced", "plain",
    "uncooked", "frozen", "bunch", "handful", "bit", "little", "few",
    "couple", "free", "range", "a", "an",
}
_CONNECTORS = {"and", "with", "plus"}


def _clean(text: str) -> str:
    return " ".join(_FILLER.sub(" ", text).split())


def _resolve(text: str) -> tuple[str, bool] | None:
    """The ingredient a whole string names, exactly, or None."""
    whole = _norm(text)
    if whole in _ALIAS:
        return _ALIAS[whole]
    t = _clean(whole)
    plain = " ".join(w for w in t.split() if w not in _DESCRIPTORS)
    for cand in (t, plain):
        for variant in (cand, cand[:-2] if cand.endswith("es") else "",
                        cand[:-1] if cand.endswith("s") else ""):
            if variant and variant in _ALIAS:
                return _ALIAS[variant]
    return None


def _scan(text: str) -> list[tuple[str, bool]]:
    """Several ingredients run together ("tomato egg"), longest
    name first. Only accepted when nothing unexplained is left over, so
    "cream cheese" is not read as cheddar."""
    rest = _clean(_norm(text))
    found: list[tuple[str, bool]] = []
    for alias, hit, pattern in _SCAN:
        if pattern.search(rest):
            found.append(hit)
            rest = pattern.sub(" ", rest)
    leftover = [w for w in rest.split() if w not in _DESCRIPTORS and w not in _CONNECTORS]
    return found if found and not leftover else []


# ── Quantities ───────────────────────────────────────────────────────────────

_UNIT_G = {
    "g": 1, "gram": 1, "grams": 1, "gramme": 1, "grammes": 1,
    "kg": 1000, "kilo": 1000, "kilos": 1000, "kilogram": 1000, "kilograms": 1000,
    "oz": 28.35, "ounce": 28.35, "ounces": 28.35, "lb": 453.6, "lbs": 453.6,
}
_UNIT_ML = {
    "ml": 1, "millilitre": 1, "millilitres": 1, "milliliter": 1, "milliliters": 1,
    "l": 1000, "litre": 1000, "litres": 1000, "liter": 1000, "liters": 1000,
    "tbsp": 15, "tablespoon": 15, "tablespoons": 15, "tsp": 5,
    "teaspoon": 5, "teaspoons": 5, "cup": 236.6, "cups": 236.6,
}
_COUNT = {"", "x", "each", "piece", "pieces", "pc", "pcs"}
_CLOVE = {"clove", "cloves"}
_SLICE = {"slice", "slices"}
_CONTAINER = {"tin", "tins", "can", "cans", "pack", "packs", "packet", "packets",
              "bag", "bags", "box", "boxes", "jar", "jars"}
_UNITS = (set(_UNIT_G) | set(_UNIT_ML) | _COUNT | _CLOVE | _SLICE | _CONTAINER) - {""}
_UNIT_RX = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))

_EN_NUMBERS = {
    "a couple of": 2, "couple of": 2, "half an": 0.5, "half a": 0.5, "a half": 0.5,
    "a dozen": 12, "dozen": 12, "half": 0.5, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "a": 1, "an": 1,
}
_EN_RX = "|".join(sorted((re.escape(w) for w in _EN_NUMBERS), key=len, reverse=True))

_Q_DIGIT = re.compile(
    rf"(?<![a-z0-9.])(?P<num>\d+(?:\.\d+)?(?:/\d+)?)\s*(?:x\s*)?(?P<unit>{_UNIT_RX})?(?![a-z])"
)
_Q_EN = re.compile(rf"\b(?P<num>{_EN_RX})\b\s*(?:(?P<unit>{_UNIT_RX})(?![a-z]))?")
_Q_PER = re.compile(rf"(?:\bper\b|/|\beach\b|\ba\b)\s*(?P<unit>{_UNIT_RX})(?![a-z])|\beach\b")


def _number(text: str) -> float | None:
    if "/" in text:
        top, _, bottom = text.partition("/")
        return float(top) / float(bottom) if float(bottom) else None
    return float(text)


def _parse_quantity(text: str) -> tuple[str, dict | None]:
    """(text with the quantity taken out, {"n": number, "unit": unit})."""
    for pattern in (_Q_DIGIT, _Q_EN, _Q_PER):
        match = pattern.search(text)
        if not match:
            continue
        raw = match.groupdict().get("num")
        unit = (match.group("unit") or "").strip()
        if pattern is _Q_DIGIT:
            n = _number(raw)
        elif pattern is _Q_EN:
            n = float(_EN_NUMBERS[raw])
        else:
            n = 1.0
        if n is None:
            continue
        rest = text[:match.start()] + " " + text[match.end():]
        return rest, {"n": n, "unit": unit}
    return text, None


def _grams(iid: str, qty: dict | None) -> float | None:
    """A quantity in grams of one ingredient, or None when it cannot be known."""
    if not qty:
        return None
    ing = INGREDIENTS[iid]
    n, unit = qty["n"], qty["unit"]
    if unit in _UNIT_G:
        return n * _UNIT_G[unit]
    if unit in _UNIT_ML:
        return n * _UNIT_ML[unit] * ing["ml_g"] if "ml_g" in ing else None
    if unit in _COUNT:
        return n * ing["each_g"] if "each_g" in ing else None
    if unit in _CLOVE:
        return n * ing["each_g"] if iid == "garlic" else None
    if unit in _SLICE:
        return n * ing["each_g"] if ing.get("each_label") == "slice" else None
    return None  # a tin or a pack: its weight is not something to assume


def _parse_ingredient(text: str) -> tuple[list[tuple[str, bool, float | None]], bool]:
    """[(ingredient id, cooked, grams or None)] for one item, and whether the
    item said zero of it."""
    hit = _resolve(text)
    rest, qty = text, None
    if not hit:
        rest, qty = _parse_quantity(text)
        hit = _resolve(rest)
    if hit:
        iid, cooked = hit
        if qty and qty["unit"] in ("tin", "tins", "can", "cans") and iid == "tomato":
            iid = "canned_tomatoes"
        if qty and qty["n"] <= 0:
            return [], True
        return [(iid, cooked, None if cooked else _grams(iid, qty))], False
    return [(iid, cooked, None) for iid, cooked in _scan(rest)], False


# ── Money ────────────────────────────────────────────────────────────────────

_CURRENCY_WORDS = {
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "quid": "GBP",
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "¥": "CNY", "cny": "CNY", "rmb": "CNY", "yuan": "CNY",
}
_CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€", "CNY": "¥"}

_PRICE_MARKED = re.compile(
    r"(?P<sym>[£$€¥])\s*(?P<num>\d+(?:\.\d{1,2})?)"
    r"|(?<![a-z0-9.])(?P<num2>\d+(?:\.\d{1,2})?)\s*(?P<word>pence\b|p\b"
    r"|gbp\b|usd\b|eur\b|cny\b|rmb\b|yuan\b|pounds?\b|dollars?\b|euros?\b|quid\b)"
)
_NOT_A_UNIT = rf"(?!\s*(?:{_UNIT_RX})(?![a-z]))"
_PRICE_DECIMAL = re.compile(rf"(?<![a-z0-9.])(?P<num>\d+\.\d{{1,2}})(?![\d.]){_NOT_A_UNIT}")
_PRICE_AFTER = re.compile(rf"\b(?:for|costs?|at|is)\s+(?P<num>\d+(?:\.\d{{1,2}})?)(?![\d.]){_NOT_A_UNIT}")
_PRICE_ANY = re.compile(rf"(?<![a-z0-9.])(?P<num>\d+(?:\.\d{{1,2}})?)(?![\d.]){_NOT_A_UNIT}")


def _minor(amount: str | float) -> int:
    """Major units to integer hundredths, rounding half up. All money below is
    integer hundredths (pence, cents, fen) and only divided once, at the end."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _major(minor: int) -> float:
    return round(minor / 100, 2)


def _currency_code(text) -> str | None:
    t = _norm(text or "").strip()
    if not t:
        return None
    if t in _CURRENCY_WORDS:
        return _CURRENCY_WORDS[t]
    if re.fullmatch(r"[a-z]{3}", t):
        return t.upper()
    return None


def _parse_price(text: str) -> dict | None:
    """One "eggs = 1.80 / 6" style price, or None when it cannot be read."""
    currency, minor, span = None, None, None
    match = _PRICE_MARKED.search(text)
    if match:
        if match.group("sym"):
            currency = _CURRENCY_WORDS[match.group("sym")]
            minor = _minor(match.group("num"))
        else:
            word = match.group("word")
            if word in ("p", "pence"):
                currency, minor = "GBP", _minor(Decimal(match.group("num2")) / 100)
            else:
                currency, minor = _CURRENCY_WORDS.get(word), _minor(match.group("num2"))
        span = match.span()
    else:
        sep = "=" if "=" in text else ":" if ":" in text else None
        start = text.index(sep) + 1 if sep else 0
        attempts = [(_PRICE_DECIMAL, start), (_PRICE_AFTER, 0)]
        if sep:
            attempts.append((_PRICE_ANY, start))
        for pattern, pos in attempts:
            found = pattern.search(text, pos)
            if found:
                minor, span = _minor(found.group("num")), found.span("num")
                break
    if span is None or minor is None:
        return None
    rest = text[:span[0]] + " " + text[span[1]:]
    # The pack size first: "each" or "per kg" would otherwise be cleaned
    # away as filler before it is read.
    rest_q, qty = _parse_quantity(rest)
    hit = _resolve(rest_q)
    if not hit:
        hit, qty = _resolve(rest), None
    if not hit or hit[1]:
        return None
    iid = hit[0]
    if qty and qty["unit"] in ("tin", "tins", "can", "cans") and iid == "tomato":
        iid = "canned_tomatoes"
    pack_g = _grams(iid, qty) if qty and qty["n"] > 0 else None
    return {"id": iid, "minor": minor, "pack_g": pack_g, "currency": currency}


# ── Reading the request ──────────────────────────────────────────────────────


def _bounded(inputs: dict, name: str, low: float, high: float, message: str,
             *, integer: bool = False, above_low: bool = False) -> float | int | None:
    value = inputs.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise _error(InvalidNumber, f"{name} is not a number", message) from None
    if (not math.isfinite(number) or number < low or number > high
            or (above_low and number <= low) or (integer and number != int(number))):
        raise _error(InvalidNumber, f"{name} out of range", message)
    return int(number) if integer else number


_ALLERGY_WORDS = re.compile(
    r"\b(?:allerg(?:y|ies|ic)(?: to)?|intoleran(?:t|ce)|free|no|without|avoid)\b"
)
_ALLERGEN_ALIAS = {_norm(a): key for key, spec in ALLERGENS.items() for a in spec["aliases"]}
_DIET_ALIAS = {_norm(a): key for key, spec in DIETS.items() for a in spec["aliases"]}
_EXCLUDE_ANIMALS = {
    "meat": {"meat", "poultry", "fish"}, "poultry": {"poultry"},
    "pork": {"pork"}, "beef": {"beef"},
}


def _allergen_keys(text: str) -> list[str] | None:
    for candidate in (text, " ".join(_ALLERGY_WORDS.sub(" ", text).split())):
        if candidate in ALLERGEN_GROUPS:
            return list(ALLERGEN_GROUPS[candidate])
        if candidate in _ALLERGEN_ALIAS:
            return [_ALLERGEN_ALIAS[candidate]]
        if candidate in DIET_AS_ALLERGEN:
            return [DIET_AS_ALLERGEN[candidate]]
    return None


def _read_request(inputs: dict) -> dict:
    req: dict = {"unrecognised": [], "notes": []}
    req["max_minutes"] = _bounded(
        inputs, "max_minutes", 1, 600,
        "The cooking time has to be a whole number of minutes from 1 to 600.", integer=True)
    req["servings"] = _bounded(
        inputs, "servings", 1, 20, "Servings have to be a whole number from 1 to 20.",
        integer=True)
    req["budget"] = _bounded(
        inputs, "budget", 0, 10000, "The budget has to be a number from 0 to 10,000.")
    req["min_protein"] = _bounded(
        inputs, "min_protein_g", 0, 300,
        "The protein target has to be between 0 and 300 g per serving.")
    req["max_kcal"] = _bounded(
        inputs, "max_calories", 0, 5000,
        "The calorie limit has to be more than 0 and at most 5,000 kcal per serving.",
        above_low=True)
    req["per_person"] = inputs.get("budget_per_person") is True

    # Ingredients the person has.
    have: dict[str, dict] = {}
    ingredient_items = _items(inputs.get("ingredients"))
    for item in ingredient_items:
        parsed, zero = _parse_ingredient(item)
        if zero:
            continue
        if not parsed:
            req["unrecognised"].append(f"ingredient '{item}' is not in MealMind's list")
            continue
        for iid, cooked, grams in parsed:
            held = have.get(iid)
            if held is None:
                have[iid] = {"grams": grams, "cooked": cooked}
            else:
                held["cooked"] = held["cooked"] or cooked
                held["grams"] = (None if held["grams"] is None or grams is None or held["cooked"]
                                 else held["grams"] + grams)
    if ingredient_items and not have:
        known = ", ".join(sorted({INGREDIENTS[i]["name"].split(" (")[0] for i in
                                  ("egg", "rice", "pasta", "tomato", "chicken_breast",
                                   "tofu", "chickpeas", "potato")}))
        raise _error(UnknownIngredients, "no ingredient recognised",
                     "I did not recognise any of those ingredients. I know about 30 "
                     f"everyday ones, such as {known}.")
    req["have"] = have

    # Allergens. One that cannot be checked is refused, never ignored.
    allergens: set[str] = set()
    excluded_ids: set[str] = set()
    for item in _items(inputs.get("allergens")):
        stripped = " ".join(_ALLERGY_WORDS.sub(" ", item).split())
        for word in (item, stripped):
            if word in UNCHECKABLE_ALLERGENS:
                raise _error(
                    UnsupportedAllergen, "uncheckable allergen requested",
                    f"I cannot check recipes for {UNCHECKABLE_ALLERGENS[word]}, so I will "
                    "not suggest one. I can check gluten, egg, milk, soy, peanuts, tree "
                    "nuts, sesame, fish, crustaceans, molluscs, mustard and celery.")
        keys = _allergen_keys(item)
        if keys:
            allergens.update(keys)
            continue
        hit = _resolve(stripped) or _resolve(item)
        if hit:
            excluded_ids.add(hit[0])
            req["notes"].append(
                f"'{item}' is not one of the 14 listed allergens; recipes with "
                f"{INGREDIENTS[hit[0]]['name']} were left out.")
            continue
        raise _error(
            UnsupportedAllergen, "unknown allergen requested",
            f"I cannot check recipes for '{item}', so I will not suggest one. I can check "
            "gluten, egg, milk, soy, peanuts, tree nuts, sesame, fish, crustaceans, "
            "molluscs, mustard and celery.")

    # Diets. One MealMind cannot apply is reported, not silently dropped.
    diets: set[str] = set()
    for item in _items(inputs.get("diet")):
        word = " ".join(re.sub(r"\b(?:diet|only|strict)\b", " ", item).split())
        if word in _DIET_ALIAS:
            diets.add(_DIET_ALIAS[word])
        elif _allergen_keys(word):
            allergens.update(_allergen_keys(word))
        else:
            req["unrecognised"].append(f"diet '{item}' was not applied: MealMind cannot check it")
    for diet in diets:
        allergens.update(DIETS[diet].get("also_rules_out_allergens", ()))

    # Things to leave out.
    excluded_animals: set[str] = set()
    for item in _items(inputs.get("exclude")):
        stripped = " ".join(_ALLERGY_WORDS.sub(" ", item).split())
        keys = _allergen_keys(item)
        if keys:
            allergens.update(keys)
            continue
        if stripped in _EXCLUDE_ANIMALS or item in _EXCLUDE_ANIMALS:
            excluded_animals |= _EXCLUDE_ANIMALS.get(stripped) or _EXCLUDE_ANIMALS[item]
            continue
        parsed, _ = _parse_ingredient(stripped or item)
        if parsed:
            excluded_ids.update(iid for iid, _, _ in parsed)
        else:
            req["unrecognised"].append(
                f"'{item}' to leave out is not an ingredient MealMind knows; none of its "
                "own cards uses anything under that name")
    req.update(allergens=allergens, diets=diets, excluded_ids=excluded_ids,
               excluded_animals=excluded_animals)

    # Prices and currency.
    prices: dict[str, dict] = {}
    currencies: set[str] = set()
    named = inputs.get("currency")
    if isinstance(named, str) and named.strip():
        code = _currency_code(named)
        if code:
            currencies.add(code)
        else:
            req["unrecognised"].append(f"currency '{named.strip()}' was not recognised")
    for item in _items(inputs.get("prices"), split_and=False):
        price = _parse_price(item)
        if price is None:
            req["unrecognised"].append(f"price '{item}' could not be read")
            continue
        if price["currency"]:
            currencies.add(price["currency"])
        if price["pack_g"] is None:
            req["notes"].append(
                f"The price for {INGREDIENTS[price['id']]['name']} has no weight or count "
                "I can be sure of, so its cost could not be worked out.")
        prices.setdefault(price["id"], price)
    if len(currencies) > 1:
        raise _error(MixedCurrencies, "more than one currency",
                     "Those prices are in more than one currency "
                     f"({', '.join(sorted(currencies))}), so I cannot add them up. "
                     "Give them all in one currency.")
    req["prices"] = prices
    req["currency"] = next(iter(currencies), None)
    req["recent"] = _items(inputs.get("recent_meals"), split_and=False)

    req["nothing"] = not any((
        have, req["max_minutes"], req["budget"] is not None, prices, allergens,
        diets, excluded_ids, excluded_animals, req["min_protein"] is not None,
        req["max_kcal"] is not None, req["recent"],
    )) and not ingredient_items
    return req


# ── Recent meals ─────────────────────────────────────────────────────────────


def _mentions(text: str) -> set[str]:
    found: set[str] = set()
    rest = text
    for alias, (iid, _), pattern in _SCAN:
        if pattern is None:
            if alias in rest:
                found.add(iid)
                rest = rest.replace(alias, " ")
        elif pattern.search(rest):
            found.add(iid)
            rest = pattern.sub(" ", rest)
    return found


def _recent(items: list[str]) -> tuple[set[str], dict[str, str]]:
    """(ids of cards eaten recently, {main ingredient id: the meal it came from})."""
    eaten: set[str] = set()
    mains: dict[str, str] = {}
    for text in items:
        keys = {card["id"]: [_norm(card["name"])] + [_norm(a) for a in card["aka"]]
                for card in RECIPES}
        same = {cid for cid, names in keys.items() if text in names}
        if not same:
            same = {cid for cid, names in keys.items() for name in names
                    if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", text)}
        eaten |= same
        cards = [card for card in RECIPES if card["id"] in same]
        for main in ({card["main"] for card in cards} or _mentions(text)):
            mains.setdefault(main, text)
    return eaten, mains


# ── Candidates ───────────────────────────────────────────────────────────────


def _amount(iid: str, grams: float) -> str:
    ing = INGREDIENTS[iid]
    shown = f"{round(grams)} g"
    if "each_g" in ing:
        count = round(grams / ing["each_g"] * 2) / 2
        if count >= 0.5:
            label = ing["each_label"]
            if count != 1:
                label += "es" if label.endswith("o") else "s"
            shown += f" (about {count:g} {label})"
    elif "ml_g" in ing:
        shown = f"{round(grams / ing['ml_g'])} ml"
    return shown


def _violations(items: list[tuple[str, float, bool]], req: dict) -> list[str]:
    found: list[str] = []
    for iid, _, _ in items:
        ing = INGREDIENTS[iid]
        for key in sorted(req["allergens"]):
            label = ALLERGENS[key]["label"]
            if key in ing["contains"]:
                found.append(f"{ing['name']} contains {label}")
            elif key in ing["may"]:
                found.append(f"{ing['name']} may contain {label}")
        for diet in sorted(req["diets"]):
            if ing["animal"] in DIETS[diet]["rules_out"]:
                found.append(f"{ing['name']} is not {DIETS[diet]['label']}")
        if iid in req["excluded_ids"]:
            found.append(f"it uses {ing['name']}, which you want left out")
        elif ing["animal"] in req["excluded_animals"]:
            found.append(f"it uses {ing['name']}, which you want left out")
    return list(dict.fromkeys(found))


def _card_candidate(card: dict, req: dict) -> dict:
    servings = req["servings"] or card["serves"]
    scale = servings / card["serves"]
    have = req["have"]
    cand = {
        "source": CARD_SOURCE, "id": card["id"], "name": card["name"],
        "minutes": card["minutes"], "servings": servings, "main": card["main"],
        "steps": card["steps"], "link": None, "you_have": [], "to_buy": [],
        "staples": [], "lines": [], "buy_g": {}, "need_g": {}, "assumed": [],
        "needed": 0, "held": 0, "cooked_rice": False,
    }
    for iid, grams, optional in card["items"]:
        ing = INGREDIENTS[iid]
        g = grams * scale
        held = have.get(iid)
        if optional:
            cand["lines"].append(f"{ing['name']}: {_amount(iid, g)}, optional")
            if held:
                cand["you_have"].append(f"{ing['name']} (optional)")
            continue
        if ing.get("staple"):
            cand["lines"].append(f"{ing['name']}: {_amount(iid, g)}")
            (cand["you_have"] if held else cand["staples"]).append(ing["name"])
            continue
        cand["lines"].append(f"{ing['name']}: {_amount(iid, g)}")
        cand["needed"] += 1
        cand["need_g"][iid] = g
        if held is None:
            cand["to_buy"].append(f"{ing['name']}: {_amount(iid, g)}")
            cand["buy_g"][iid] = g
            continue
        cand["held"] += 1
        if held["cooked"]:
            cand["you_have"].append(f"{ing['name']} (you have it cooked)")
            cand["cooked_rice"] = True
        elif held["grams"] is None:
            cand["you_have"].append(ing["name"])
            cand["assumed"].append(ing["name"])
        elif held["grams"] + 1e-6 >= g:
            cand["you_have"].append(ing["name"])
        else:
            short = g - held["grams"]
            cand["you_have"].append(f"{ing['name']} (not enough)")
            cand["to_buy"].append(
                f"{ing['name']}: {_amount(iid, short)} more (you have "
                f"{round(held['grams'])} g, it needs {round(g)} g)")
            cand["buy_g"][iid] = short
    eaten = [(iid, grams) for iid, grams, optional in card["items"] if not optional]
    cand["kcal"] = sum(g * INGREDIENTS[i]["kcal"] / 100 for i, g in eaten) / card["serves"]
    cand["protein"] = sum(g * INGREDIENTS[i]["protein"] / 100 for i, g in eaten) / card["serves"]
    cand["violations"] = _violations(card["items"], req)
    _cost(cand, req)
    return cand


def _cost(cand: dict, req: dict) -> None:
    """The three costs, in integer hundredths, each only when every price it
    needs is known: what must be bought now (whole packs), the share of the
    ingredients this meal uses, and every pack the recipe touches."""
    prices = req["prices"]
    spend = used = whole = 0
    spend_ok = used_ok = whole_ok = True
    unpriced: list[str] = []
    for iid, need in cand["need_g"].items():
        price = prices.get(iid)
        buy = cand["buy_g"].get(iid, 0.0)
        if price is None or not price["pack_g"]:
            unpriced.append(INGREDIENTS[iid]["name"])
            spend_ok = spend_ok and buy == 0
            used_ok = whole_ok = False
            continue
        pack, minor = price["pack_g"], price["minor"]
        if buy > 0:
            spend += math.ceil(buy / pack - 1e-9) * minor
        used += int((Decimal(minor) * Decimal(str(round(need, 3))) / Decimal(str(pack)))
                    .quantize(Decimal(1), rounding=ROUND_HALF_UP))
        whole += math.ceil(need / pack - 1e-9) * minor
    cand["spend"] = spend if spend_ok else None
    cand["used"] = used if used_ok else None
    cand["whole"] = whole if whole_ok else None
    cand["unpriced"] = unpriced
    cand["budget"] = None
    if req["budget"] is not None:
        total = _minor(req["budget"]) * (cand["servings"] if req["per_person"] else 1)
        cand["budget_total"] = total
        if cand["spend"] is None:
            cand["budget"] = "cannot_verify"
        else:
            cand["budget"] = "within_budget" if cand["spend"] <= total else "over_budget"


def _points(score: int) -> str:
    return f"{score} point" if abs(score) == 1 else f"{score} points"

def _score(cand: dict, req: dict, eaten: set[str], mains: dict[str, str]) -> None:
    points, why = 0, []
    if cand["needed"]:
        share = cand["held"] / cand["needed"]
        gained = round(60 * share)
        points += gained
        if req["have"]:
            why.append(f"you have {cand['held']} of its {cand['needed']} main ingredients (+{gained})")
    buys = len(cand["to_buy"])
    if buys:
        points -= 8 * buys
        why.append(f"{buys} thing{'s' if buys != 1 else ''} to buy (-{8 * buys})")
    elif req["have"]:
        why.append("nothing to buy")
    targets = req["min_protein"] is not None or req["max_kcal"] is not None
    if targets and cand["protein"] is None:
        points -= 15
        why.append("nutrition unknown, so your targets cannot be checked (-15)")
    if req["min_protein"] is not None and cand["protein"] is not None:
        if cand["protein"] + 1e-9 >= req["min_protein"]:
            points += 15
            why.append(f"{cand['protein']:.1f} g protein per serving meets your "
                       f"{req['min_protein']:g} g target (+15)")
        else:
            points -= 15
            why.append(f"{cand['protein']:.1f} g protein per serving is under your "
                       f"{req['min_protein']:g} g target (-15)")
    if req["max_kcal"] is not None and cand["kcal"] is not None:
        if cand["kcal"] <= req["max_kcal"] + 1e-9:
            points += 10
            why.append(f"{round(cand['kcal'])} kcal per serving is within your "
                       f"{req['max_kcal']:g} kcal limit (+10)")
        else:
            points -= 10
            why.append(f"{round(cand['kcal'])} kcal per serving is over your "
                       f"{req['max_kcal']:g} kcal limit (-10)")
    if cand["budget"] == "within_budget":
        points += 10
        why.append("within your budget for what must be bought now (+10)")
    elif cand["budget"] == "cannot_verify":
        points -= 5
        why.append("budget cannot be verified (-5)")
    if cand["id"] in eaten:
        points -= 40
        why.append("you had this recently (-40)")
    elif cand["main"] in mains:
        points -= 15
        why.append(f"{INGREDIENTS[cand['main']]['name']} again, like '{mains[cand['main']]}' (-15)")
    if cand["minutes"] is None:
        points -= 10
        why.append("no cooking time listed (-10)")
    elif req["max_minutes"]:
        why.append(f"about {cand['minutes']} min, within your {req['max_minutes']} min")
    cand["score"] = points
    cand["why"] = why


# ── TheMealDB, optional ──────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The key is in the URL path, so a redirect must never be followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class _Upstream(Exception):
    """TheMealDB could not be used. `kind` picks the sentence; nothing from
    the reply, the URL or the key is ever kept."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


_UPSTREAM_NOTES = {
    "key": "TheMealDB did not accept the app's key, so only MealMind's own cards were used.",
    "busy": "TheMealDB is busy just now, so only MealMind's own cards were used. Try again shortly.",
    "down": "TheMealDB could not be reached in time, so only MealMind's own cards were used.",
    "bad": "TheMealDB sent a reply MealMind could not read, so only its own cards were used.",
}


def _mealdb_candidate(meal: dict, req: dict) -> dict | None:
    meal_id = str(meal.get("idMeal") or "")
    name = " ".join(str(meal.get("strMeal") or "").split())[:120]
    if not meal_id.isdigit() or not name:
        return None
    mapped: list[str] = []
    to_buy: list[str] = []
    you_have: list[str] = []
    for n in range(1, 21):
        raw = " ".join(str(meal.get(f"strIngredient{n}") or "").split())[:60]
        if not raw:
            continue
        hit = _resolve(raw)
        if hit and hit[0] in mapped:
            continue
        if hit:
            mapped.append(hit[0])
            if INGREDIENTS[hit[0]].get("staple"):
                continue
            (you_have if hit[0] in req["have"] else to_buy).append(INGREDIENTS[hit[0]]["name"])
        else:
            to_buy.append(raw.lower())
    needed = len(you_have) + len(to_buy)
    return {
        "source": "TheMealDB", "id": f"mealdb:{meal_id}", "name": name,
        "minutes": None, "servings": None, "main": None, "steps": [],
        "link": MEALDB_PAGE + meal_id, "you_have": you_have, "to_buy": to_buy,
        "staples": [], "lines": [], "assumed": [], "needed": needed,
        "held": len(you_have), "kcal": None, "protein": None, "violations": [],
        "spend": None, "used": None, "whole": None, "unpriced": [],
        "budget": None if req["budget"] is None else "cannot_verify",
        "budget_total": None if req["budget"] is None else _minor(req["budget"]),
        "cooked_rice": False,
    }


# ── The app ──────────────────────────────────────────────────────────────────


class App:
    """Pick a recipe card that fits the message, and say exactly why."""

    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    # The one network seam. Tests replace it on the instance.
    def _fetch(self, url: str, timeout: float = MEALDB_REQUEST_S) -> bytes:
        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with opener.open(request, timeout=timeout) as response:
            return response.read(2_000_000)

    def handle(self, call: Call) -> Result:
        if call.outcome_id != OUTCOME:
            raise AgentAppError("undeclared outcome requested")
        req = _read_request(call.inputs or {})
        if req["nothing"]:
            raise NothingToGoOn("request had nothing usable")

        eaten, mains = _recent(req["recent"])
        candidates = [_card_candidate(card, req) for card in RECIPES]
        notes = list(req["notes"])
        sources = [f"{CARD_SOURCE}s: written for MealMind; minutes are the card's estimate"]

        # Read inside handle(): Pippa sets app keys for each call only.
        key = (os.environ.get(MEALDB_KEY_ENV) or "").strip()
        if key and req["have"]:
            blocked = []
            if req["allergens"] or req["diets"] or req["excluded_ids"] or req["excluded_animals"]:
                blocked.append("its ingredient lists cannot be checked for allergens or diets")
            if req["max_minutes"]:
                blocked.append("it lists no cooking times")
            if blocked:
                notes.append("TheMealDB recipes were left out: " + " and ".join(blocked) + ".")
            else:
                found = self._mealdb(key, req, notes)
                if found:
                    sources.append("TheMealDB (themealdb.com): ingredient lists only, "
                                   "no times, weights or nutrition")
                candidates += found
        return self._answer(req, candidates, eaten, mains, notes, sources)

    # ── TheMealDB ────────────────────────────────────────────────────────────

    def _mealdb_json(self, key: str, endpoint: str, params: dict, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            raise _Upstream("down")
        url = f"{MEALDB_BASE}{quote(key, safe='')}/{endpoint}?{urlencode(params)}"
        try:
            raw = self._fetch(url, timeout=min(MEALDB_REQUEST_S, remaining))
        except urllib.error.HTTPError as exc:  # must come first: it IS a URLError
            raise _Upstream("busy" if exc.code == 429 else
                            "key" if exc.code in (401, 403, 404) else "down") from None
        except TimeoutError:  # not a URLError
            raise _Upstream("down") from None
        except urllib.error.URLError:
            raise _Upstream("down") from None
        except (OSError, http.client.HTTPException):
            raise _Upstream("down") from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _Upstream("bad") from None
        if not isinstance(data, dict) or "meals" not in data:
            raise _Upstream("bad")
        meals = data["meals"]
        if meals is None or meals == "":
            return []
        if not isinstance(meals, list):
            raise _Upstream("bad")
        return [m for m in meals if isinstance(m, dict)]

    def _mealdb(self, key: str, req: dict, notes: list[str]) -> list[dict]:
        if not re.fullmatch(r"[A-Za-z0-9]{6,64}", key):
            # A one-character test key would also be masked everywhere it
            # appears in the answer, digits included, so it is not used.
            notes.append("TheMealDB was not used: the app's key is not a full API key.")
            return []
        deadline = time.monotonic() + MEALDB_DEADLINE_S
        names = [INGREDIENTS[i]["mealdb"] for i in req["have"] if INGREDIENTS[i].get("mealdb")]
        names = names[:MEALDB_MAX_FILTERS]
        if not names:
            return []
        try:
            sets = []
            for name in names:
                meals = self._mealdb_json(key, "filter.php", {"i": name}, deadline)
                sets.append([str(m.get("idMeal")) for m in meals
                             if str(m.get("idMeal") or "").isdigit()])
            common = [i for i in sets[0] if all(i in s for s in sets[1:])] or sets[0]
            ids = sorted(dict.fromkeys(common), key=int)[:MEALDB_MAX_LOOKUPS]
            if not ids:
                notes.append("TheMealDB has no recipe with those ingredients.")
                return []
            found = []
            for meal_id in ids:
                try:
                    meals = self._mealdb_json(key, "lookup.php", {"i": meal_id}, deadline)
                except _Upstream as exc:
                    if found:
                        notes.append(_UPSTREAM_NOTES[exc.kind].split(",")[0] +
                                     ", so fewer of its recipes were compared.")
                        break
                    raise
                cand = _mealdb_candidate(meals[0], req) if meals else None
                if cand:
                    found.append(cand)
            return found
        except _Upstream as exc:
            notes.append(_UPSTREAM_NOTES[exc.kind])
            return []

    # ── The answer ───────────────────────────────────────────────────────────

    def _answer(self, req, candidates, eaten, mains, notes, sources) -> Result:
        excluded: list[str] = []
        counts = {"rules": 0, "time": 0, "budget": 0, "overlap": 0}
        fit: list[dict] = []
        passed_rules: list[dict] = []
        for cand in candidates:
            if cand["violations"]:
                counts["rules"] += 1
                more = len(cand["violations"]) - 1
                excluded.append(f"{cand['name']}: {cand['violations'][0]}"
                                + (f" (+{more} more)" if more else ""))
                continue
            passed_rules.append(cand)
            if req["max_minutes"] and cand["minutes"] is not None \
                    and cand["minutes"] > req["max_minutes"]:
                counts["time"] += 1
                excluded.append(f"{cand['name']}: about {cand['minutes']} min, over your "
                                f"{req['max_minutes']} min")
                continue
            if cand["budget"] == "over_budget":
                counts["budget"] += 1
                excluded.append(f"{cand['name']}: {self._money(cand['spend'], req)} to buy now, "
                                f"over your {self._money(cand['budget_total'], req)}")
                continue
            if req["have"] and any(not INGREDIENTS[i].get("staple") for i in req["have"]) \
                    and cand["held"] == 0:
                counts["overlap"] += 1
                continue
            _score(cand, req, eaten, mains)
            fit.append(cand)
        fit.sort(key=lambda c: (-c["score"], len(c["to_buy"]),
                                c["minutes"] if c["minutes"] is not None else 10_000, c["name"]))

        limitations = list(notes)
        if req["allergens"] or req["diets"]:
            limitations.append(
                "Allergen and diet checks use typical UK versions of each ingredient and "
                "treat 'may contain' as 'contains'. They cannot rule out cross-contact: "
                "check the label of everything you use.")
        else:
            limitations.append("No allergens were checked, because none were named.")
        if req["budget"] is not None or req["prices"]:
            limitations.append("MealMind has no shop prices: costs use only the prices "
                               "you gave, and the budget is compared with what must be "
                               "bought now.")
        if req["prices"]:
            sources.append("Prices: the ones in your message")

        data: dict = {
            "excluded": excluded[:MAX_LISTED] + (
                [f"and {len(excluded) - MAX_LISTED} more"] if len(excluded) > MAX_LISTED else []),
            "data_sources": sources,
        }
        if req["unrecognised"]:
            data["unrecognised_inputs"] = req["unrecognised"]

        if not fit:
            return self._no_match(req, candidates, passed_rules, counts, data, limitations)

        top, alternatives = fit[0], fit[1:3]
        sources.append(NUTRITION_SOURCE)
        data.update(
            status="matched",
            top_pick=top["name"],
            top_pick_source=top["source"],
            reasons=[f"Ranked first of {len(fit)} recipe{'s that fit' if len(fit) != 1 else ' that fits'}, "
                     f"with {_points(top['score'])}"] + top["why"],
            alternatives=[self._summary_line(c) for c in alternatives],
            you_have=top["you_have"],
            to_buy=top["to_buy"],
            assumed_staples=top["staples"],
        )
        if top["link"]:
            data["top_pick_link"] = top["link"]
        if top["source"] == CARD_SOURCE:
            data.update(
                top_pick_servings=top["servings"],
                top_pick_minutes=top["minutes"],
                time_basis="MealMind card estimate, not a measured time",
                top_pick_ingredients=top["lines"],
                top_pick_steps=top["steps"],
                kcal_per_serving=float(round(top["kcal"])),
                protein_g_per_serving=round(top["protein"], 1),
                nutrition_basis=(
                    f"Per serving, 1 of {top['servings']}, worked out from the card's raw "
                    "weights and USDA FoodData Central SR Legacy values; optional "
                    "garnishes and salt are not counted. An estimate, not dietary advice."),
            )
            if top["staples"]:
                limitations.append(f"Assumed you have {', '.join(top['staples'])}; "
                                   "it is not in the shopping list or the costs.")
            if top["assumed"]:
                limitations.append(f"You did not say how much {', '.join(top['assumed'])} "
                                   "you have, so MealMind assumed it is enough.")
            if top["cooked_rice"]:
                limitations.append("You have cooked rice: skip cooking the rice. Its amount "
                                   "was not compared with the card's dry weight.")
        else:
            data.update(
                time_basis="unknown: TheMealDB lists no cooking time",
                nutrition_basis="unknown: TheMealDB gives no weights, so no nutrition",
            )
            limitations.append("TheMealDB lists ingredients without weights, so amounts, "
                               "nutrition and cost are unknown for this recipe.")

        self._money_fields(top, req, data, limitations)
        conflicts = self._soft_conflicts(req, fit)
        if conflicts:
            data["conflicts"] = conflicts
        data["limitations"] = limitations
        return Result(data=data, narrative=self._narrative(top, data))

    def _no_match(self, req, candidates, passed_rules, counts, data, limitations) -> Result:
        conflicts: list[str] = []
        if counts["rules"]:
            conflicts.append(f"Your allergens, diet or exclusions rule out {counts['rules']} "
                             f"of the {len(candidates)} recipes. These rules were not relaxed.")
        if counts["time"]:
            quickest = min(c["minutes"] for c in passed_rules
                           if c["minutes"] is not None and c["minutes"] > req["max_minutes"])
            conflicts.append(f"{counts['time']} more take longer than {req['max_minutes']} min; "
                             f"the quickest of those takes about {quickest} min.")
        if counts["budget"]:
            cheapest = min(c["spend"] for c in passed_rules
                           if c["budget"] == "over_budget")
            conflicts.append(f"{counts['budget']} cost more than your budget to buy now; the "
                             f"cheapest of those costs {self._money(cheapest, req)}.")
        if counts["overlap"]:
            conflicts.append(f"{counts['overlap']} {'uses' if counts['overlap'] == 1 else 'use'} "
                             "none of the ingredients you have.")
        data.update(
            status="no_match",
            reasons=[f"None of the {len(candidates)} recipes fits everything you asked for."],
            alternatives=[],
            conflicts=conflicts,
            budget_status="no_budget_given" if req["budget"] is None else "cannot_verify",
            limitations=limitations,
        )
        return Result(data=data, narrative="No recipe fits all of that. " + (conflicts[0] if conflicts else ""))

    def _money_fields(self, top, req, data, limitations) -> None:
        data["budget_status"] = top["budget"] or "no_budget_given"
        if req["currency"]:
            data["currency"] = req["currency"]
        if req["budget"] is not None:
            data["budget_total"] = _major(top["budget_total"])
        if not req["prices"]:
            if req["budget"] is not None:
                limitations.append("Budget cannot be verified: no prices were given.")
            return
        basis = ["whole packs for what must be bought now",
                 "the share of each pack this meal uses",
                 "every whole pack the recipe needs from scratch"]
        if top["spend"] is not None:
            data["spend_now"] = _major(top["spend"])
        if top["used"] is not None:
            data["used_ingredients_cost"] = _major(top["used"])
            if top["servings"]:
                data["cost_per_serving"] = _major(
                    int((Decimal(top["used"]) / top["servings"]).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP)))
        if top["whole"] is not None:
            data["whole_pack_cost"] = _major(top["whole"])
        data["cost_basis"] = "spend_now: " + basis[0] + "; used_ingredients_cost: " + \
            basis[1] + "; whole_pack_cost: " + basis[2] + "."
        if top["unpriced"]:
            limitations.append("No usable price for " + ", ".join(top["unpriced"]) +
                               ", so a cost that needs it is left out.")
        if top["budget"] == "cannot_verify":
            limitations.append("Budget cannot be verified: something that must be bought "
                               "has no usable price.")

    @staticmethod
    def _soft_conflicts(req, fit) -> list[str]:
        out = []
        top = fit[0]
        if req["min_protein"] is not None and top["protein"] is not None \
                and top["protein"] + 1e-9 < req["min_protein"]:
            best = max((c for c in fit if c["protein"] is not None),
                       key=lambda c: c["protein"])
            if best is top:
                out.append(f"No recipe that fits reaches {req['min_protein']:g} g protein "
                           f"per serving; the highest is {top['protein']:.1f} g.")
            else:
                out.append(f"The top pick has {top['protein']:.1f} g protein per serving, "
                           f"under your {req['min_protein']:g} g target; {best['name']} has "
                           f"the most that fits, {best['protein']:.1f} g.")
        if req["max_kcal"] is not None and top["kcal"] is not None \
                and top["kcal"] > req["max_kcal"] + 1e-9:
            out.append(f"The top pick is over your {req['max_kcal']:g} kcal limit, at "
                       f"{round(top['kcal'])} kcal per serving.")
        return out

    def _money(self, minor: int, req) -> str:
        symbol = _CURRENCY_SYMBOL.get(req["currency"] or "", "")
        code = "" if symbol or not req["currency"] else f" {req['currency']}"
        return f"{symbol}{minor / 100:.2f}{code}"

    @staticmethod
    def _summary_line(cand: dict) -> str:
        parts = [cand["name"]]
        parts.append(f"about {cand['minutes']} min" if cand["minutes"] is not None
                     else "time unknown")
        parts.append("nothing to buy" if not cand["to_buy"] else
                     f"to buy: {', '.join(t.split(':')[0] for t in cand['to_buy'][:4])}")
        if cand["kcal"] is not None:
            parts.append(f"{round(cand['kcal'])} kcal and {cand['protein']:.1f} g protein per serving")
        parts.append(_points(cand["score"]))
        if cand["link"]:
            parts.append(cand["link"])
        return "; ".join(parts)

    @staticmethod
    def _narrative(top: dict, data: dict) -> str:
        if top["minutes"] is not None:
            when = f", about {top['minutes']} minutes for {top['servings']} (card estimate)"
        else:
            when = " from TheMealDB (no cooking time listed)"
        buy = ("you have everything" if not top["to_buy"] else
               f"you need to buy {len(top['to_buy'])} thing{'s' if len(top['to_buy']) != 1 else ''}")
        return f"Top pick: {data['top_pick']}{when}; {buy}."
