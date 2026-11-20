"""Tests for MealMind, run through LocalRunner so the manifest is enforced.

The network never runs here (conftest.py refuses every socket): TheMealDB is
reached only through `App._fetch`, which the tests replace on the instance.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone

import pytest
from pippa_agentapp_sdk import LocalRunner, OutputShapeViolation

from app import (
    MEALDB_BASE,
    App,
    InvalidNumber,
    MixedCurrencies,
    NothingToGoOn,
    UnknownIngredients,
    UnsupportedAllergen,
    _NoRedirect,
)

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
OUTCOME = "meal.match"
FAKE_KEY = "fakekey123456"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _no_mealdb_key(monkeypatch):
    """Every test starts without the optional key, as the build does."""
    monkeypatch.delenv("THEMEALDB_API_KEY", raising=False)


def run(inputs: dict, *, explicit: bool = False, fetch=None):
    app = App()
    if fetch is not None:
        app._fetch = fetch
    return LocalRunner(app).call(
        OUTCOME, inputs, granted_secrets={}, now=NOW, request_id="t1",
        explicit=explicit,
    ).result


def never_fetch(url, timeout=4.0):
    raise AssertionError("the network was used when it should not have been")


# ── The contract ─────────────────────────────────────────────────────────────


def test_the_manifest_is_valid_and_has_one_outcome():
    app = App()
    assert app.manifest.app_id == "hack.app_4f34"
    assert [o.id for o in app.manifest.outcomes] == [OUTCOME]
    phrases = app.manifest.outcomes[0].example_phrases
    assert 5 <= len(phrases) <= 10
    assert all(p == p.lower() and not p.endswith("?") for p in phrases)
    assert " by " not in f" {app.manifest.summary.lower()} "


def test_the_runner_enforces_the_manifest(monkeypatch):
    from pippa_agentapp_sdk import Result

    monkeypatch.setattr(App, "handle", lambda self, c: Result(data={"surprise": 1}))
    with pytest.raises(OutputShapeViolation):
        run({"ingredients": ["eggs"]})


def test_picked_by_name_it_answers_exactly_the_same():
    inputs = {"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20}
    assert run(inputs, explicit=True).data == run(inputs).data
    with pytest.raises(NothingToGoOn):
        run({}, explicit=True)


# ── Demo 1: ingredients and time ─────────────────────────────────────────────


def test_eggs_rice_tomatoes_in_twenty_minutes():
    result = run({"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20})
    data = result.data
    assert data["status"] == "matched"
    assert data["top_pick"].startswith("Tomato and egg stir-fry with rice")
    assert data["top_pick_source"] == "MealMind recipe card"
    assert data["to_buy"] == []
    assert data["top_pick_minutes"] == 20
    assert "estimate" in data["time_basis"]
    assert data["top_pick_servings"] == 2
    assert data["assumed_staples"] == ["cooking oil"]
    # 150 g rice, 150 g egg, 246 g tomato, 15 g oil, from the USDA values.
    assert data["kcal_per_serving"] == 469
    assert data["protein_g_per_serving"] == pytest.approx(15.85, abs=0.06)
    assert "Per serving, 1 of 2" in data["nutrition_basis"]
    assert any("USDA" in s for s in data["data_sources"])
    assert data["budget_status"] == "no_budget_given"
    assert "spend_now" not in data
    assert len(data["alternatives"]) <= 2
    # Every recipe over the limit is named, with the rule.
    assert any("over your 20 min" in line for line in data["excluded"])
    assert "Top pick" in result.narrative


def test_the_time_limit_is_inclusive():
    exactly = run({"ingredients": ["eggs", "bread"], "max_minutes": 10}).data
    assert exactly["status"] == "matched"
    assert exactly["top_pick_minutes"] <= 10
    under = run({"ingredients": ["eggs", "bread"], "max_minutes": 9}).data
    assert under["status"] == "no_match"
    assert any("longer than 9 min" in c for c in under["conflicts"])


def test_word_amounts_and_cooked_rice_are_read():
    data = run({"ingredients": ["three eggs", "leftover rice", "two tomatoes"],
                "max_minutes": 20}).data
    assert data["top_pick"] == "Tomato and egg stir-fry with rice"
    assert data["to_buy"] == []
    assert any("cooked rice" in line for line in data["limitations"])


def test_more_servings_scale_the_amounts_not_the_nutrition():
    two = run({"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20}).data
    four = run({"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20,
                "servings": 4}).data
    assert four["top_pick_servings"] == 4
    assert "eggs: 300 g (about 6 large eggs)" in four["top_pick_ingredients"]
    assert four["kcal_per_serving"] == two["kcal_per_serving"]


def test_too_little_of_something_goes_on_the_shopping_list():
    data = run({"ingredients": ["1 egg", "rice", "tomatoes"], "max_minutes": 20}).data
    assert data["top_pick"].startswith("Tomato and egg")
    assert any(line.startswith("eggs: 100 g") and "more" in line for line in data["to_buy"])


# ── Demo 2: nutrition ────────────────────────────────────────────────────────


def test_protein_target_with_explicit_amounts():
    data = run({"ingredients": ["300g chicken breast", "150 g rice", "2 carrots", "peas"],
                "servings": 2, "min_protein_g": 40}).data
    assert data["top_pick"].startswith("Soy-garlic chicken rice bowl")
    # 300 g chicken (22.5), 150 g rice (7.13), 122 g carrot (0.93), 80 g peas
    # (5.22), 20 g soy sauce (8.14), 6 g garlic (6.36) per 100 g, over 2.
    assert data["protein_g_per_serving"] == pytest.approx(42.76, abs=0.06)
    assert any("meets your 40 g target (+15)" in r for r in data["reasons"])
    assert sorted(t.split(":")[0] for t in data["to_buy"]) == ["garlic", "soy sauce"]


def test_a_target_nothing_meets_is_reported_not_hidden():
    data = run({"ingredients": ["oats", "milk", "banana"], "min_protein_g": 80}).data
    assert data["status"] == "matched"
    assert any("80 g" in c for c in data["conflicts"])
    assert any("under your 80 g target (-15)" in r for r in data["reasons"])


def test_calorie_limit_is_scored():
    data = run({"ingredients": ["pasta"], "max_calories": 500}).data
    assert any("kcal limit" in r for r in data["reasons"])


# ── Demo 3: budget ───────────────────────────────────────────────────────────

PRICES = ["eggs £1.80 for 6", "rice 1.20 per kg", "tomatoes 30p each"]


def test_budget_with_prices_keeps_three_costs_apart():
    data = run({"ingredients": ["eggs", "rice"], "servings": 2, "budget": 3,
                "currency": "GBP", "prices": PRICES}).data
    assert data["top_pick"].startswith("Tomato and egg")
    assert data["currency"] == "GBP"
    assert data["budget_status"] == "within_budget"
    assert data["spend_now"] == 0.60          # two tomatoes at 30p
    assert data["used_ingredients_cost"] == 1.68  # 0.90 egg + 0.18 rice + 0.60
    assert data["cost_per_serving"] == 0.84
    assert data["whole_pack_cost"] == 3.60    # a box of eggs, a bag of rice, 2 tomatoes
    assert "spend_now" in data["cost_basis"]


def test_budget_boundary_is_inclusive_and_over_is_excluded():
    inputs = {"ingredients": ["eggs", "rice"], "prices": PRICES, "max_minutes": 20}
    exactly = run({**inputs, "budget": 0.60}).data
    assert exactly["top_pick"].startswith("Tomato and egg")
    assert exactly["budget_status"] == "within_budget"
    under = run({**inputs, "budget": 0.59}).data
    assert any(line.startswith("Tomato and egg stir-fry with rice: £0.60 to buy now")
               for line in under["excluded"])


def test_a_per_person_budget_is_multiplied_out():
    data = run({"ingredients": ["eggs", "rice"], "prices": PRICES, "budget": 0.5,
                "budget_per_person": True, "servings": 2}).data
    assert data["budget_total"] == 1.0


def test_budget_without_prices_cannot_be_verified():
    data = run({"ingredients": ["eggs", "rice"], "budget": 3}).data
    assert data["budget_status"] == "cannot_verify"
    for field in ("spend_now", "used_ingredients_cost", "cost_per_serving", "whole_pack_cost"):
        assert field not in data
    assert any("Budget cannot be verified" in line for line in data["limitations"])


def test_a_price_without_a_pack_size_is_not_guessed():
    data = run({"ingredients": ["pasta"], "max_minutes": 20, "budget": 5,
                "prices": ["tinned tomatoes 0.45 a tin", "pasta 0.80 for 500g"]}).data
    names = [data["top_pick"]] + data["alternatives"]
    assert any(name.startswith("Tomato and garlic pasta") for name in names)
    assert data["budget_status"] == "cannot_verify"
    assert "used_ingredients_cost" not in data
    assert any("tinned chopped tomatoes" in line and "could not be worked out" in line
               for line in data["limitations"])


def test_prices_in_two_currencies_are_refused():
    with pytest.raises(MixedCurrencies):
        run({"ingredients": ["eggs"], "prices": ["eggs £1.80 for 6", "rice $2 per kg"]})


# ── Allergens and diets ──────────────────────────────────────────────────────


def test_an_allergen_rules_out_contains_and_may_contain():
    data = run({"ingredients": ["rice", "tomatoes", "eggs", "pasta"], "allergens": ["egg"]}).data
    assert data["status"] == "matched"
    for name in [data["top_pick"]] + data["alternatives"]:
        assert "egg" not in name.lower() and "pasta" not in name.lower()
    assert "eggs" not in data["you_have"]
    assert any("dried pasta may contain egg" in line for line in data["excluded"])
    assert any("cross-contact" in line for line in data["limitations"])


def test_peanut_allergy_removes_peanut_noodles():
    data = run({"ingredients": ["peanut butter", "noodles"], "allergens": ["nuts"]}).data
    names = [data.get("top_pick", "")] + data["alternatives"]
    assert not any("Peanut" in name for name in names)
    assert any(line.startswith("Peanut butter noodles") for line in data["excluded"])


def test_vegan_rules_out_bread_that_may_contain_milk():
    data = run({"diet": ["vegan"], "ingredients": ["bread", "baked beans"]}).data
    names = [data.get("top_pick", "")] + data["alternatives"]
    assert not any("toast" in name.lower() or "omelette" in name.lower() for name in names)


def test_nothing_safe_is_a_no_match_that_names_the_conflict():
    data = run({"ingredients": ["eggs", "bread"], "allergens": ["egg"], "max_minutes": 10}).data
    assert data["status"] == "no_match"
    assert "top_pick" not in data
    assert data["alternatives"] == []
    assert any("not relaxed" in c for c in data["conflicts"])


def test_an_allergen_that_cannot_be_checked_is_refused():
    with pytest.raises(UnsupportedAllergen) as failure:
        run({"ingredients": ["eggs"], "allergens": ["lupin"]})
    assert "lupin" in failure.value.user_message
    with pytest.raises(UnsupportedAllergen):
        run({"ingredients": ["eggs"], "allergens": ["kiwi"]})


def test_a_diet_that_cannot_be_applied_is_reported():
    data = run({"ingredients": ["pasta"], "diet": ["keto"]}).data
    assert any("keto" in line and "not applied" in line for line in data["unrecognised_inputs"])


def test_exclusions_are_hard_rules():
    data = run({"ingredients": ["pasta", "tuna"], "exclude": ["cheese"]}).data
    assert not data["top_pick"].startswith("Tuna, pea and cheddar")
    assert any("cheddar, which you want left out" in line for line in data["excluded"])


# ── Recent meals ─────────────────────────────────────────────────────────────


def test_a_recent_meal_costs_points_and_says_so():
    fresh = run({"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20}).data
    data = run({"ingredients": ["eggs", "rice", "tomatoes"], "max_minutes": 20,
                "recent_meals": ["tomato and egg"]}).data
    # Still first here (60 points of coverage), but 40 points lower, with the reason.
    assert data["top_pick"].startswith("Tomato and egg")
    assert "you had this recently (-40)" in data["reasons"]
    assert fresh["reasons"][0].endswith("with 60 points")
    assert data["reasons"][0].endswith("with 20 points")


def test_the_same_main_ingredient_costs_less():
    data = run({"ingredients": ["eggs", "bread"], "max_minutes": 10,
                "recent_meals": ["omelette"]}).data
    assert data["top_pick"].startswith("Scrambled eggs on toast")
    assert any("(-15)" in r for r in data["reasons"])


# ── Bad and missing input ────────────────────────────────────────────────────


def test_nothing_to_go_on_says_what_to_type():
    with pytest.raises(NothingToGoOn) as failure:
        run({})
    assert "ingredients" in failure.value.user_message
    with pytest.raises(NothingToGoOn):
        run({"servings": 2, "ingredients": [], "currency": ""})


@pytest.mark.parametrize("inputs", [
    {"max_minutes": 0}, {"max_minutes": 601}, {"servings": 21}, {"servings": 0},
    {"budget": -1}, {"min_protein_g": 301}, {"max_calories": 0},
])
def test_numbers_out_of_range_are_refused(inputs):
    with pytest.raises(InvalidNumber):
        run({"ingredients": ["eggs"], **inputs})


def test_unknown_ingredients_are_refused_or_reported():
    with pytest.raises(UnknownIngredients):
        run({"ingredients": ["durian", "dragon fruit"]})
    data = run({"ingredients": ["eggs", "durian"]}).data
    assert any("durian" in line for line in data["unrecognised_inputs"])


def test_empty_optional_inputs_are_ignored():
    data = run({"ingredients": ["eggs", ""], "currency": "", "prices": [],
                "recent_meals": []}).data
    assert data["status"] == "matched"


# ── TheMealDB, optional and stubbed ──────────────────────────────────────────

MEAL = {"idMeal": "52771", "strMeal": "Buttery Milk Pudding",
        "strIngredient1": "Butter", "strIngredient2": "Milk", "strIngredient3": ""}


def mealdb_stub(calls):
    def fetch(url, timeout=4.0):
        assert url.startswith(MEALDB_BASE)
        assert timeout <= 4.0
        calls.append(url)
        if "filter.php" in url:
            return json.dumps({"meals": [{"idMeal": "52771", "strMeal": "x"}]}).encode()
        return json.dumps({"meals": [MEAL]}).encode()
    return fetch


def test_without_a_key_the_network_is_never_used():
    data = run({"ingredients": ["butter", "milk"]}, fetch=never_fetch).data
    assert data["top_pick_source"] == "MealMind recipe card"


def test_mealdb_unknowns_are_never_shown_as_known(monkeypatch):
    monkeypatch.setenv("THEMEALDB_API_KEY", FAKE_KEY)
    calls: list[str] = []
    data = run({"ingredients": ["butter", "milk"]}, fetch=mealdb_stub(calls)).data
    assert data["top_pick_source"] == "TheMealDB"
    assert data["top_pick_link"] == "https://www.themealdb.com/meal/52771"
    for field in ("kcal_per_serving", "protein_g_per_serving", "top_pick_minutes",
                  "top_pick_servings", "spend_now"):
        assert field not in data
    assert data["nutrition_basis"].startswith("unknown")
    assert data["time_basis"].startswith("unknown")
    assert any("no cooking time listed (-10)" in r for r in data["reasons"])
    assert 1 <= len(calls) <= 5
    # The key goes only to TheMealDB, never into the answer.
    assert FAKE_KEY not in json.dumps(data)


def test_mealdb_is_skipped_when_allergens_are_named(monkeypatch):
    monkeypatch.setenv("THEMEALDB_API_KEY", FAKE_KEY)
    data = run({"ingredients": ["butter", "milk"], "allergens": ["peanuts"]},
               fetch=never_fetch).data
    assert any("TheMealDB recipes were left out" in line for line in data["limitations"])


def _raiser(exc):
    def fetch(url, timeout=4.0):
        raise exc
    return fetch


@pytest.mark.parametrize("fetch, words", [
    (_raiser(urllib.error.HTTPError(MEALDB_BASE, 429, "busy", {}, None)), "busy"),
    (_raiser(urllib.error.HTTPError(MEALDB_BASE, 404, "no", {}, None)), "key"),
    (_raiser(urllib.error.HTTPError(MEALDB_BASE, 500, "err", {}, None)), "could not be reached"),
    (_raiser(TimeoutError("slow")), "could not be reached"),
    (_raiser(urllib.error.URLError("dns")), "could not be reached"),
    (lambda url, timeout=4.0: b"<html>not json", "could not read"),
    (lambda url, timeout=4.0: b'{"meals": null}', "no recipe"),
    (lambda url, timeout=4.0: b"", "could not read"),
])
def test_mealdb_failures_fall_back_to_the_cards(monkeypatch, fetch, words):
    monkeypatch.setenv("THEMEALDB_API_KEY", FAKE_KEY)
    data = run({"ingredients": ["butter", "milk"]}, fetch=fetch).data
    assert data["status"] == "matched"
    assert data["top_pick_source"] == "MealMind recipe card"
    assert any(words in line for line in data["limitations"])
    assert FAKE_KEY not in json.dumps(data)


def test_a_short_test_key_is_not_used(monkeypatch):
    monkeypatch.setenv("THEMEALDB_API_KEY", "1")
    data = run({"ingredients": ["butter", "milk"]}, fetch=never_fetch).data
    assert any("not a full API key" in line for line in data["limitations"])


def test_redirects_are_refused():
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://x.example/") is None
