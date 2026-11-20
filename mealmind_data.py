"""MealMind's bundled data: ingredients, allergens and recipe cards.

Everything here sits beside app.py on purpose: `docs/` and `scripts/` are not
deployed, so a table kept there would be missing in production.

Where each number comes from
----------------------------

* **Energy and protein** are USDA FoodData Central, *SR Legacy* release
  (April 2018), per 100 g of the food as listed, raw unless the record says
  otherwise. Each ingredient carries its `fdc` id, so every value can be
  checked at https://fdc.nal.usda.gov/ . USDA FoodData Central data is in the
  public domain (CC0 1.0), so it may be shipped with the app.
* **`each_g`** (the weight of one egg, one tomato, ...) is the matching USDA
  portion weight from the same release, named in `each_label`.
* **`ml_g`** (grams per millilitre, for liquids) is worked out from the USDA
  portion weights: milk 244 g per US cup of 236.6 ml, soy sauce 16 g per
  tablespoon of 14.79 ml, oil 14 g per tablespoon.
* **Recipe cards** (quantities, servings, minutes and steps) were written for
  MealMind and are released with it. They are NOT taken from any outside
  source, so their minutes are the card's own estimate, never a measurement,
  and the app always says so.
* **Allergen tags** describe typical UK supermarket versions of each
  ingredient, using the 14 allergens UK food law names. `may` lists allergens
  that some common versions contain (egg in some dried pasta, milk in some
  bread). The app treats `may` the same as `contains` when it filters, and it
  never promises freedom from cross-contact.

No prices are bundled. A price is only used when the person gives it.
"""

from __future__ import annotations

#: The USDA release every nutrition value below comes from.
NUTRITION_SOURCE = (
    "USDA FoodData Central, SR Legacy (April 2018), public domain; "
    "kcal and protein per 100 g"
)

#: Allergens MealMind can check, with the words people use for them.
#: Lupin and sulphites are deliberately missing: the ingredient tags below do
#: not track them reliably, so the app refuses to claim it checked them.
ALLERGENS: dict[str, dict] = {
    "gluten": {
        "label": "gluten",
        "aliases": ["gluten", "wheat", "cereals containing gluten", "coeliac",
                    "celiac", "gluten free", "gluten-free"],
    },
    "egg": {
        "label": "egg",
        "aliases": ["egg", "eggs", "egg free", "egg-free"],
    },
    "milk": {
        "label": "milk",
        "aliases": ["milk", "dairy", "lactose", "dairy free", "dairy-free",
                    "lactose intolerant"],
    },
    "soy": {
        "label": "soy",
        "aliases": ["soy", "soya", "soybean", "soybeans"],
    },
    "peanuts": {
        "label": "peanuts",
        "aliases": ["peanut", "peanuts", "groundnut", "groundnuts"],
    },
    "tree_nuts": {
        "label": "tree nuts",
        "aliases": ["tree nut", "tree nuts"],
    },
    "sesame": {
        "label": "sesame",
        "aliases": ["sesame"],
    },
    "fish": {
        "label": "fish",
        "aliases": ["fish"],
    },
    "crustaceans": {
        "label": "crustaceans",
        "aliases": ["crustacean", "crustaceans", "prawn", "prawns", "shrimp",
                    "crab", "lobster"],
    },
    "molluscs": {
        "label": "molluscs",
        "aliases": ["mollusc", "molluscs", "mollusk", "mollusks", "squid",
                    "mussels"],
    },
    "mustard": {
        "label": "mustard",
        "aliases": ["mustard"],
    },
    "celery": {
        "label": "celery",
        "aliases": ["celery", "celeriac"],
    },
}

#: Words that mean more than one allergen at once. Read conservatively.
ALLERGEN_GROUPS: dict[str, list[str]] = {
    "nut": ["peanuts", "tree_nuts"],
    "nuts": ["peanuts", "tree_nuts"],
    "nut free": ["peanuts", "tree_nuts"],
    "nut-free": ["peanuts", "tree_nuts"],
    "shellfish": ["crustaceans", "molluscs"],
    "seafood": ["fish", "crustaceans", "molluscs"],
}

#: Allergens people name that MealMind cannot check. Named so that the app
#: refuses plainly instead of pretending to have checked them.
UNCHECKABLE_ALLERGENS: dict[str, str] = {
    "lupin": "lupin",
    "lupine": "lupin",
    "sulphite": "sulphites",
    "sulphites": "sulphites",
    "sulfite": "sulphites",
    "sulfites": "sulphites",
}

#: Diets MealMind can apply, and which kinds of ingredient each one rules out.
#: `animal` on an ingredient is one of: meat, poultry, fish, egg, dairy.
DIETS: dict[str, dict] = {
    "vegetarian": {
        "label": "vegetarian",
        "rules_out": {"meat", "poultry", "fish"},
        "aliases": ["vegetarian", "veggie"],
    },
    "vegan": {
        "label": "vegan",
        "rules_out": {"meat", "poultry", "fish", "egg", "dairy"},
        # A vegan cannot rely on bread or pasta that MAY contain milk or egg.
        "also_rules_out_allergens": {"egg", "milk"},
        "aliases": ["vegan", "plant based", "plant-based"],
    },
    "pescatarian": {
        "label": "pescatarian",
        "rules_out": {"meat", "poultry"},
        "aliases": ["pescatarian", "pescetarian"],
    },
    "no_pork": {
        "label": "no pork",
        "rules_out": {"pork"},
        "aliases": ["no pork", "pork free", "pork-free"],
    },
    "no_beef": {
        "label": "no beef",
        "rules_out": {"beef"},
        "aliases": ["no beef", "beef free", "beef-free"],
    },
}

#: Diet words that are allergen rules in disguise.
DIET_AS_ALLERGEN: dict[str, str] = {
    "gluten free": "gluten",
    "gluten-free": "gluten",
    "dairy free": "milk",
    "dairy-free": "milk",
    "lactose free": "milk",
    "lactose-free": "milk",
    "egg free": "egg",
    "egg-free": "egg",
}

# ── Ingredients ──────────────────────────────────────────────────────────────
#
# kcal / protein: per 100 g, from the USDA record `fdc`.
# staple: assumed to be in the cupboard when the person does not mention it
#         (said so in every answer). Only cooking oil is a staple; salt,
#         pepper and water are not listed on any card at all.
# mealdb: the ingredient's name in TheMealDB's filter, for the optional lookup.

INGREDIENTS: dict[str, dict] = {
    "egg": {
        "name": "eggs", "fdc": 171287,
        "usda": "Egg, whole, raw, fresh", "kcal": 143.0, "protein": 12.56,
        "each_g": 50.0, "each_label": "large egg",
        "contains": ["egg"], "may": [], "animal": "egg",
        "aliases": ["egg", "eggs", "large egg", "large eggs", "free range eggs"],
        "mealdb": "egg",
    },
    "rice": {
        "name": "white rice (dry)", "fdc": 168877,
        "usda": "Rice, white, long-grain, regular, raw, enriched",
        "kcal": 365.0, "protein": 7.13,
        "contains": [], "may": [], "animal": None,
        "aliases": ["rice", "white rice", "long grain rice", "basmati rice",
                    "jasmine rice"],
        # Cooked rice is a different weight from dry rice, so a quantity of it
        # is never compared with a dry weight on a card.
        "cooked_aliases": ["cooked rice", "leftover rice"],
        "mealdb": "rice",
    },
    "tomato": {
        "name": "tomatoes", "fdc": 170457,
        "usda": "Tomatoes, red, ripe, raw, year round average",
        "kcal": 18.0, "protein": 0.88,
        "each_g": 123.0, "each_label": "medium tomato",
        "contains": [], "may": [], "animal": None,
        "aliases": ["tomato", "tomatoes", "fresh tomatoes"],
        "mealdb": "tomatoes",
    },
    "canned_tomatoes": {
        "name": "tinned chopped tomatoes", "fdc": 170051,
        "usda": "Tomatoes, red, ripe, canned, packed in tomato juice",
        "kcal": 16.0, "protein": 0.79,
        "contains": [], "may": [], "animal": None,
        "aliases": ["tinned tomatoes", "canned tomatoes", "chopped tomatoes",
                    "tinned chopped tomatoes", "canned chopped tomatoes",
                    "tin of tomatoes", "can of tomatoes"],
        "mealdb": "chopped_tomatoes",
    },
    "onion": {
        "name": "onion", "fdc": 170000,
        "usda": "Onions, raw", "kcal": 40.0, "protein": 1.1,
        "each_g": 110.0, "each_label": "medium onion",
        "contains": [], "may": [], "animal": None,
        "aliases": ["onion", "onions", "brown onion", "red onion"],
        "mealdb": "onion",
    },
    "garlic": {
        "name": "garlic", "fdc": 169230,
        "usda": "Garlic, raw", "kcal": 149.0, "protein": 6.36,
        "each_g": 3.0, "each_label": "clove",
        "contains": [], "may": [], "animal": None,
        "aliases": ["garlic", "garlic clove", "garlic cloves"],
        "mealdb": "garlic",
    },
    "spring_onion": {
        "name": "spring onions", "fdc": 170005,
        "usda": "Onions, spring or scallions (includes tops and bulb), raw",
        "kcal": 32.0, "protein": 1.83,
        "each_g": 15.0, "each_label": "medium spring onion",
        "contains": [], "may": [], "animal": None,
        "aliases": ["spring onion", "spring onions", "scallion", "scallions",
                    "green onion", "green onions"],
        "mealdb": "spring_onions",
    },
    "soy_sauce": {
        "name": "soy sauce", "fdc": 174277,
        "usda": "Soy sauce made from soy and wheat (shoyu)",
        "kcal": 53.0, "protein": 8.14, "ml_g": 1.082,
        "contains": ["soy", "gluten"], "may": [], "animal": None,
        "aliases": ["soy sauce", "soya sauce", "light soy sauce"],
        "mealdb": "soy_sauce",
    },
    "oil": {
        "name": "cooking oil", "fdc": 171017,
        "usda": "Oil, sunflower, linoleic (less than 60%)",
        "kcal": 884.0, "protein": 0.0, "ml_g": 0.947,
        "contains": [], "may": [], "animal": None, "staple": True,
        "aliases": ["oil", "cooking oil", "vegetable oil", "sunflower oil",
                    "rapeseed oil", "olive oil"],
        "mealdb": None,
    },
    "pasta": {
        "name": "dried pasta", "fdc": 169736,
        "usda": "Pasta, dry, enriched", "kcal": 371.0, "protein": 13.04,
        "contains": ["gluten"], "may": ["egg"], "animal": None,
        "aliases": ["pasta", "dried pasta", "spaghetti", "penne", "fusilli",
                    "macaroni"],
        "mealdb": "spaghetti",
    },
    "chickpeas": {
        "name": "tinned chickpeas (drained)", "fdc": 173800,
        "usda": "Chickpeas (garbanzo beans, bengal gram), mature seeds, canned, drained solids",
        "kcal": 139.0, "protein": 7.05,
        "contains": [], "may": [], "animal": None,
        "aliases": ["chickpeas", "chickpea", "garbanzo beans", "tinned chickpeas",
                    "canned chickpeas"],
        "mealdb": "chickpeas",
    },
    "red_lentils": {
        "name": "red lentils (dry)", "fdc": 174284,
        "usda": "Lentils, pink or red, raw", "kcal": 358.0, "protein": 23.91,
        # Dried pulses are often packed alongside wheat.
        "contains": [], "may": ["gluten"], "animal": None,
        "aliases": ["red lentils", "red lentil", "lentils", "split red lentils"],
        "mealdb": "lentils",
    },
    "chicken_breast": {
        "name": "chicken breast", "fdc": 171077,
        "usda": "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw",
        "kcal": 120.0, "protein": 22.5,
        "contains": [], "may": [], "animal": "poultry",
        "aliases": ["chicken breast", "chicken breasts", "chicken", "chicken fillet",
                    "chicken fillets"],
        "mealdb": "chicken_breast",
    },
    "tofu": {
        "name": "firm tofu", "fdc": 172475,
        "usda": "Tofu, raw, firm, prepared with calcium sulfate",
        "kcal": 144.0, "protein": 17.27,
        "contains": ["soy"], "may": [], "animal": None,
        "aliases": ["tofu", "firm tofu", "bean curd"],
        "mealdb": "tofu",
    },
    "peas": {
        "name": "frozen peas", "fdc": 170016,
        "usda": "Peas, green, frozen, unprepared",
        "kcal": 77.0, "protein": 5.22,
        "contains": [], "may": [], "animal": None,
        "aliases": ["peas", "frozen peas", "garden peas"],
        "mealdb": "peas",
    },
    "potato": {
        "name": "potatoes", "fdc": 170026,
        "usda": "Potatoes, flesh and skin, raw", "kcal": 77.0, "protein": 2.05,
        "each_g": 213.0, "each_label": "medium potato",
        "contains": [], "may": [], "animal": None,
        "aliases": ["potato", "potatoes", "baking potato", "baking potatoes"],
        "mealdb": "potatoes",
    },
    "oats": {
        "name": "porridge oats", "fdc": 173904,
        "usda": "Cereals, oats, regular and quick, not fortified, dry",
        "kcal": 379.0, "protein": 13.15,
        # UK law lists oats among the cereals containing gluten.
        "contains": ["gluten"], "may": [], "animal": None,
        "aliases": ["oats", "porridge oats", "rolled oats", "oatmeal"],
        "mealdb": "oats",
    },
    "milk": {
        "name": "milk", "fdc": 171267,
        "usda": "Milk, reduced fat, fluid, 2% milkfat, with added vitamin A and vitamin D",
        "kcal": 50.0, "protein": 3.3, "ml_g": 1.031,
        "contains": ["milk"], "may": [], "animal": "dairy",
        "aliases": ["milk", "semi skimmed milk", "semi-skimmed milk", "whole milk"],
        "mealdb": "milk",
    },
    "banana": {
        "name": "bananas", "fdc": 173944,
        "usda": "Bananas, raw", "kcal": 89.0, "protein": 1.09,
        "each_g": 118.0, "each_label": "medium banana",
        "contains": [], "may": [], "animal": None,
        "aliases": ["banana", "bananas"],
        "mealdb": "banana",
    },
    "bread": {
        "name": "white bread", "fdc": 174924,
        "usda": "Bread, white, commercially prepared (includes soft bread crumbs)",
        "kcal": 266.0, "protein": 8.85,
        "each_g": 29.0, "each_label": "slice",
        "contains": ["gluten"], "may": ["milk", "soy", "sesame"], "animal": None,
        "aliases": ["bread", "white bread", "sliced bread", "toast", "bread slices"],
        "mealdb": "bread",
    },
    "cheddar": {
        "name": "cheddar", "fdc": 173414,
        "usda": "Cheese, cheddar", "kcal": 403.0, "protein": 22.87,
        "contains": ["milk"], "may": [], "animal": "dairy",
        "aliases": ["cheddar", "cheddar cheese", "cheese", "grated cheese"],
        "mealdb": "cheddar_cheese",
    },
    "tuna": {
        "name": "tinned tuna in water (drained)", "fdc": 171986,
        "usda": "Fish, tuna, light, canned in water, without salt, drained solids",
        "kcal": 116.0, "protein": 25.5,
        "contains": ["fish"], "may": [], "animal": "fish",
        "aliases": ["tuna", "tinned tuna", "canned tuna", "tuna chunks"],
        "mealdb": "tuna",
    },
    "spinach": {
        "name": "spinach", "fdc": 168462,
        "usda": "Spinach, raw", "kcal": 23.0, "protein": 2.86,
        "contains": [], "may": [], "animal": None,
        "aliases": ["spinach", "baby spinach"],
        "mealdb": "spinach",
    },
    "peanut_butter": {
        "name": "peanut butter", "fdc": 172470,
        "usda": "Peanut butter, smooth style, without salt",
        "kcal": 598.0, "protein": 22.2,
        "contains": ["peanuts"], "may": ["tree_nuts"], "animal": None,
        "aliases": ["peanut butter"],
        "mealdb": "peanut_butter",
    },
    "egg_noodles": {
        "name": "dried egg noodles", "fdc": 169731,
        "usda": "Noodles, egg, dry, enriched", "kcal": 384.0, "protein": 14.16,
        "contains": ["gluten", "egg"], "may": ["soy"], "animal": "egg",
        "aliases": ["egg noodles", "noodles", "dried noodles"],
        "mealdb": "egg_noodles",
    },
    "kidney_beans": {
        "name": "tinned kidney beans (drained)", "fdc": 174285,
        "usda": "Beans, kidney, red, mature seeds, canned, drained solids",
        "kcal": 124.0, "protein": 7.98,
        "contains": [], "may": [], "animal": None,
        "aliases": ["kidney beans", "red kidney beans", "tinned kidney beans"],
        "mealdb": "kidney_beans",
    },
    "butter": {
        "name": "butter", "fdc": 173410,
        "usda": "Butter, salted", "kcal": 717.0, "protein": 0.85,
        "contains": ["milk"], "may": [], "animal": "dairy",
        "aliases": ["butter", "salted butter"],
        "mealdb": "butter",
    },
    "mushrooms": {
        "name": "mushrooms", "fdc": 169251,
        "usda": "Mushrooms, white, raw", "kcal": 22.0, "protein": 3.09,
        "contains": [], "may": [], "animal": None,
        "aliases": ["mushroom", "mushrooms", "button mushrooms"],
        "mealdb": "mushrooms",
    },
    "carrot": {
        "name": "carrots", "fdc": 170393,
        "usda": "Carrots, raw", "kcal": 41.0, "protein": 0.93,
        "each_g": 61.0, "each_label": "medium carrot",
        "contains": [], "may": [], "animal": None,
        "aliases": ["carrot", "carrots"],
        "mealdb": "carrots",
    },
    "curry_powder": {
        "name": "curry powder", "fdc": 170924,
        "usda": "Spices, curry powder", "kcal": 325.0, "protein": 14.29,
        # UK blends often contain mustard or celery seed.
        "contains": [], "may": ["mustard", "celery"], "animal": None,
        "aliases": ["curry powder"],
        "mealdb": "curry_powder",
    },
    "chilli_powder": {
        "name": "chilli powder", "fdc": 171319,
        "usda": "Spices, chili powder", "kcal": 282.0, "protein": 13.46,
        "contains": [], "may": ["mustard", "celery"], "animal": None,
        "aliases": ["chilli powder", "chili powder", "chilli", "chili", "chillies"],
        "mealdb": "chilli_powder",
    },
    "baked_beans": {
        "name": "baked beans", "fdc": 175182,
        "usda": "Beans, baked, canned, plain or vegetarian",
        "kcal": 94.0, "protein": 4.75,
        "contains": [], "may": [], "animal": None,
        "aliases": ["baked beans", "beans in tomato sauce"],
        "mealdb": "baked_beans",
    },
}

# ── Recipe cards ─────────────────────────────────────────────────────────────
#
# Written for MealMind. `items` are (ingredient id, grams for `serves`
# servings, optional). Optional items are garnishes: they are left out of the
# nutrition, the cost and the shopping list, and the answer says so.
# `minutes` is the card's own estimate of the total time, with one hob and the
# rice or pasta started first. `main` is the ingredient that makes two meals
# feel like the same meal, for the "you had this recently" check.

RECIPES: list[dict] = [
    {
        "id": "mm01", "name": "Tomato and egg stir-fry with rice", "serves": 2, "minutes": 20, "main": "egg",
        "aka": ["tomato and egg", "tomato egg"],
        "items": [("rice", 150, False), ("egg", 150, False), ("tomato", 246, False),
                  ("oil", 15, False), ("spring_onion", 15, True)],
        "steps": [
            "Rinse the rice and simmer it in twice its volume of water, covered, for 15 minutes.",
            "Beat the eggs with a pinch of salt and scramble them softly in half the oil; lift out.",
            "Fry the chopped tomatoes in the rest of the oil for 3-4 minutes until saucy.",
            "Fold the eggs back in, season, and serve over the rice with sliced spring onion."],
    },
    {
        "id": "mm02", "name": "Egg fried rice with peas", "serves": 2, "minutes": 25, "main": "rice",
        "aka": ["egg fried rice", "fried rice"],
        "items": [("rice", 150, False), ("egg", 100, False), ("peas", 80, False),
                  ("soy_sauce", 15, False), ("oil", 15, False), ("spring_onion", 15, True)],
        "steps": [
            "Cook the rice for 15 minutes, then spread it out to steam dry.",
            "Scramble the eggs in the hot oil and push them to one side.",
            "Add the rice and peas and fry hard for 4 minutes.",
            "Stir in the soy sauce and top with spring onion."],
    },
    {
        "id": "mm03", "name": "Eggs baked in spiced tomato sauce", "serves": 2, "minutes": 25, "main": "egg",
        "aka": ["shakshuka", "eggs in tomato sauce"],
        "items": [("egg", 200, False), ("canned_tomatoes", 400, False), ("onion", 110, False),
                  ("garlic", 6, False), ("chilli_powder", 2, False), ("oil", 10, False),
                  ("bread", 116, False)],
        "steps": [
            "Soften the sliced onion and garlic in the oil for 6 minutes.",
            "Add the chilli powder and tomatoes and simmer for 8 minutes.",
            "Make four hollows, crack in the eggs, cover and cook 6-8 minutes.",
            "Serve with the bread."],
    },
    {
        "id": "mm04", "name": "Tomato and garlic pasta", "serves": 2, "minutes": 20, "main": "pasta",
        "aka": ["tomato pasta", "pasta pomodoro"],
        "items": [("pasta", 200, False), ("canned_tomatoes", 400, False), ("onion", 110, False),
                  ("garlic", 9, False), ("oil", 15, False)],
        "steps": [
            "Boil the pasta in salted water for the time on the packet.",
            "Meanwhile soften the onion and garlic in the oil for 5 minutes.",
            "Add the tomatoes and simmer for 10 minutes.",
            "Toss the drained pasta through the sauce."],
    },
    {
        "id": "mm05", "name": "Red lentil dal with rice", "serves": 3, "minutes": 30, "main": "red_lentils",
        "aka": ["dal", "dhal", "lentil curry"],
        "items": [("red_lentils", 200, False), ("canned_tomatoes", 400, False),
                  ("onion", 110, False), ("garlic", 9, False), ("curry_powder", 8, False),
                  ("oil", 10, False), ("rice", 225, False)],
        "steps": [
            "Start the rice: 15 minutes, covered.",
            "Soften the onion and garlic in the oil, then stir in the curry powder.",
            "Add the rinsed lentils, the tomatoes and 500 ml water; simmer 20 minutes, stirring.",
            "Season and serve over the rice."],
    },
    {
        "id": "mm06", "name": "Chickpea and spinach curry with rice", "serves": 3, "minutes": 25, "main": "chickpeas",
        "aka": ["chickpea curry", "chana masala"],
        "items": [("chickpeas", 240, False), ("canned_tomatoes", 400, False),
                  ("onion", 110, False), ("garlic", 6, False), ("curry_powder", 8, False),
                  ("spinach", 100, False), ("oil", 10, False), ("rice", 225, False)],
        "steps": [
            "Start the rice: 15 minutes, covered.",
            "Soften the onion and garlic in the oil, add the curry powder for a minute.",
            "Add the tomatoes and drained chickpeas; simmer 10 minutes.",
            "Stir in the spinach until it wilts, and serve with the rice."],
    },
    {
        "id": "mm07", "name": "Soy-garlic chicken rice bowl", "serves": 2, "minutes": 25, "main": "chicken_breast",
        "aka": ["chicken rice bowl", "chicken and rice"],
        "items": [("chicken_breast", 300, False), ("rice", 150, False), ("carrot", 122, False),
                  ("peas", 80, False), ("soy_sauce", 20, False), ("garlic", 6, False),
                  ("oil", 10, False)],
        "steps": [
            "Start the rice: 15 minutes, covered.",
            "Slice the chicken thinly and fry it in the oil for 6-7 minutes until cooked through.",
            "Add the garlic, sliced carrot and peas for 3 minutes.",
            "Add the soy sauce, toss, and serve over the rice."],
    },
    {
        "id": "mm08", "name": "Tuna, pea and cheddar pasta", "serves": 2, "minutes": 15, "main": "pasta",
        "aka": ["tuna pasta"],
        "items": [("pasta", 200, False), ("tuna", 110, False), ("peas", 100, False),
                  ("cheddar", 40, False)],
        "steps": [
            "Boil the pasta; add the peas for the last 3 minutes.",
            "Drain, keeping a splash of the water.",
            "Stir through the flaked tuna and grated cheddar with the water until glossy."],
    },
    {
        "id": "mm09", "name": "Potato and onion omelette", "serves": 3, "minutes": 35, "main": "egg",
        "aka": ["spanish omelette", "tortilla"],
        "items": [("potato", 426, False), ("egg", 300, False), ("onion", 110, False),
                  ("oil", 30, False)],
        "steps": [
            "Slice the potatoes and onion thinly and cook gently in the oil, covered, for 15 minutes.",
            "Beat the eggs, season, and stir in the potatoes and onion.",
            "Cook in the pan over a low heat for 8 minutes, then flip with a plate and cook 4 more."],
    },
    {
        "id": "mm10", "name": "Garlic tofu and spinach with rice", "serves": 2, "minutes": 25, "main": "tofu",
        "aka": ["tofu stir fry", "tofu rice"],
        "items": [("tofu", 280, False), ("spinach", 150, False), ("garlic", 6, False),
                  ("soy_sauce", 20, False), ("oil", 15, False), ("rice", 150, False)],
        "steps": [
            "Start the rice: 15 minutes, covered.",
            "Cube the tofu and fry it in the oil until golden, about 8 minutes.",
            "Add the garlic, then the spinach until wilted.",
            "Add the soy sauce and serve over the rice."],
    },
    {
        "id": "mm11", "name": "Peanut butter noodles", "serves": 2, "minutes": 15, "main": "egg_noodles",
        "aka": ["peanut noodles"],
        "items": [("egg_noodles", 150, False), ("peanut_butter", 40, False),
                  ("soy_sauce", 20, False), ("carrot", 61, False), ("spring_onion", 15, True)],
        "steps": [
            "Cook the noodles for the time on the packet.",
            "Whisk the peanut butter and soy sauce with 4 tablespoons of hot noodle water.",
            "Toss the drained noodles with the sauce and grated carrot; top with spring onion."],
    },
    {
        "id": "mm12", "name": "Banana porridge", "serves": 1, "minutes": 10, "main": "oats",
        "aka": ["porridge", "oatmeal"],
        "items": [("oats", 50, False), ("milk", 250, False), ("banana", 118, False)],
        "steps": [
            "Simmer the oats in the milk for 5 minutes, stirring.",
            "Slice the banana over the top."],
    },
    {
        "id": "mm13", "name": "Cheese and tomato omelette", "serves": 1, "minutes": 10, "main": "egg",
        "aka": ["omelette", "omelet", "cheese omelette"],
        "items": [("egg", 150, False), ("cheddar", 30, False), ("tomato", 123, False),
                  ("butter", 10, False)],
        "steps": [
            "Beat the eggs with a pinch of salt.",
            "Melt the butter in a pan, pour in the eggs and stir gently until nearly set.",
            "Scatter over the cheese and diced tomato, fold, and serve."],
    },
    {
        "id": "mm14", "name": "Kidney bean chilli with rice", "serves": 3, "minutes": 30, "main": "kidney_beans",
        "aka": ["bean chilli", "veggie chilli", "chilli"],
        "items": [("kidney_beans", 240, False), ("canned_tomatoes", 400, False),
                  ("onion", 110, False), ("garlic", 6, False), ("carrot", 122, False),
                  ("chilli_powder", 6, False), ("oil", 10, False), ("rice", 225, False)],
        "steps": [
            "Start the rice: 15 minutes, covered.",
            "Soften the onion, garlic and diced carrot in the oil for 6 minutes.",
            "Add the chilli powder, tomatoes and drained beans; simmer 15 minutes.",
            "Serve over the rice."],
    },
    {
        "id": "mm15", "name": "Mushroom and egg noodles", "serves": 2, "minutes": 15, "main": "egg_noodles",
        "aka": ["mushroom noodles", "fried noodles"],
        "items": [("egg_noodles", 150, False), ("mushrooms", 250, False), ("egg", 100, False),
                  ("soy_sauce", 15, False), ("oil", 10, False), ("spring_onion", 15, True)],
        "steps": [
            "Cook the noodles, drain and rinse in cold water.",
            "Fry the sliced mushrooms in the oil until browned, about 5 minutes.",
            "Push aside, scramble the eggs, then add the noodles and soy sauce and toss."],
    },
    {
        "id": "mm16", "name": "Microwave jacket potato with beans and cheese", "serves": 2, "minutes": 15, "main": "potato",
        "aka": ["jacket potato", "baked potato"],
        "items": [("potato", 426, False), ("baked_beans", 415, False), ("cheddar", 60, False)],
        "steps": [
            "Prick the potatoes and microwave on high for 10-12 minutes, turning once.",
            "Heat the beans for the last 2 minutes.",
            "Split the potatoes, spoon over the beans and grate over the cheese."],
    },
    {
        "id": "mm17", "name": "Scrambled eggs on toast", "serves": 1, "minutes": 10, "main": "egg",
        "aka": ["scrambled eggs", "eggs on toast"],
        "items": [("egg", 150, False), ("bread", 58, False), ("butter", 10, False),
                  ("milk", 30, False)],
        "steps": [
            "Toast the bread.",
            "Beat the eggs with the milk and a pinch of salt.",
            "Melt the butter over a low heat and stir the eggs slowly until just set.",
            "Spoon onto the toast."],
    }]
