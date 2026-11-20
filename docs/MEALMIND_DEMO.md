# MealMind — demo script and routing prompts

MealMind finds a recipe for what you already have, within your time, budget
and diet. It answers with a top pick, up to two alternatives, the reasons for
the ranking, and what it could not check. It keeps nothing between messages.

Every figure below was worked out from the bundled recipe cards and USDA
FoodData Central (SR Legacy) values, the same way the app does it.

## Three demos

### 1. Ingredients and time

> what can i cook with eggs, rice and tomatoes in 20 minutes

- **Top pick:** Tomato and egg stir-fry with rice, about 20 min,
  for 2. Nothing to buy. Cooking oil is assumed.
- **Per serving (1 of 2):** about 469 kcal and 15.8 g protein.
- **Excluded:** each card over 20 min, by name, with its time.
- **What to point out:** the time is the card's own estimate, not a measured
  one, and the answer says so (`time_basis`).

### 2. A protein target

> high protein dinner for 2 with 300g chicken breast, 150g rice, 2 carrots and peas, at least 40g protein

- **Top pick:** Soy-garlic chicken rice bowl, about 42.8 g protein per serving.
- **Reasons:** "meets your 40 g target (+15)".
- **To buy:** garlic and soy sauce.
- **What to point out:** the amounts you give are the ones it uses, and the
  nutrition is per serving, with the serving count stated.

### 3. A budget, with your own prices

> cheap dinner for two under £3, i have eggs and rice. eggs £1.80 for 6, rice £1.20 per kg, tomatoes 30p each

| Cost | Amount | What it means |
|---|---|---|
| Spend now | £0.60 | two tomatoes, the only thing to buy |
| Used ingredients | £1.68 | the share of each pack the meal uses |
| Per serving | £0.84 | |
| Whole packs | £3.60 | buying everything from scratch |

- **Top pick:** the tomato and egg stir-fry, within budget.
- **What to point out:** the three costs are kept apart, and the budget is
  compared with what you must spend now.

### Counter-example: a budget without prices

> cheap meal under £3 with eggs and rice

`budget_status` is `cannot_verify`. No cost fields appear at all, rather than
showing 0, and the limitations say *Budget cannot be verified*. MealMind has
no shop prices of its own.

### Safety: an allergen it cannot satisfy

> something with eggs and bread in 10 minutes, i'm allergic to egg

`status` is `no_match`. The conflicts say the allergen rule was **not
relaxed**, and the answer notes that it cannot rule out cross-contact.

## Routing prompts

Send these **without** `@`, then check the reply carries the
`[via MealMind 0.1.0 …]` badge.

**Should reach MealMind** (declared phrases):

- how many calories and protein per serving if i cook eggs, rice and tomatoes
- high protein recipe with at least 30g protein, i have chicken and rice
- cost per serving of a meal for two: eggs £1.80 for 6, rice £1.20 per kg

**Should reach MealMind** (paraphrases, not declared):

- i've got tofu and spinach, how much protein would a dinner give me
- i'm allergic to peanuts, what can i make with noodles and eggs
- what's the cheapest thing i can cook with pasta if tinned tomatoes are 45p

**Should NOT reach MealMind:**

- why are eggs a good source of protein *(explaining, not finding a recipe)*
- is pasta healthier than rice *(an opinion)*
- order me a pizza *(MealMind never orders)*

A plain "what can i cook with eggs and rice" is often answered by Pippa
herself: suggesting a dish is something she does from memory. MealMind is
chosen when the question needs its figures (calories, protein, cost per
serving) or a hard allergen rule.

## `@MealMind` versus Pippa choosing

- **Without `@`:** Pippa decides whether MealMind fits, from its summary and
  example phrases. A miss here is a routing problem.
- **With `@`:** start the message with `@` and pick MealMind. Pippa skips
  choosing and calls it whatever the words. The answer is the same as when
  Pippa chooses it (`call.context.explicit` changes nothing in MealMind). If
  MealMind cannot help, you see its own reason, for example *"MealMind
  couldn't answer: Tell me something to go on: the ingredients you have, how
  many minutes you have, …"*, instead of
  Pippa answering in her own words.
- **When a question misses:** send it again with `@` first. If it answers
  now, the app works and the phrases need work. If it still fails, the app or
  its inputs are at fault.
