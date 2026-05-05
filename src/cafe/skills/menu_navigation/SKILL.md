---
name: menu_navigation
description: RAG-grounded menu search guidance for budgets, dietary needs, categories, recommendations, and cart handoff.
---

# Menu Navigation

Use this skill whenever the Product Search agent answers menu questions. The
menu and menu-attributes RAG collections are the only sources of truth
available to this agent.

## Grounding
- Use `search_product_knowledge` for descriptions, add-ons, serving size,
  dietary notes, price-range lists, cheapest/most expensive queries, and
  recommendations.
- Use `search_menu_attribute_knowledge` for taste, ingredients, allergens,
  sweetness, spice level, caffeine, dairy/milk, body, customizations, avoid-if
  notes, and good-for matching.
- Use `search_product_and_attribute_knowledge` for personalized
  recommendations so menu knowledge and attribute knowledge are retrieved in
  parallel.
- Do not infer ingredients, caffeine, allergens, vegan status, or add-ons from
  general knowledge. Only state what the menu RAG, attribute RAG, or tools
  provide.
- If the retrieved menu text does not contain a requested detail, say that the
  detail was not found in the retrieved context.

## Budget requests
When a user gives a budget like "under INR 150":
1. Retrieve relevant menu knowledge with `search_product_knowledge`, because
   the menu docs include price quick-reference sections.
2. Filter only by confirmed prices. Do not include items with unknown prices.
3. Mention the budget naturally, for example: "Under INR 150, I found..."

## Dietary requests
When a user asks for vegan or vegetarian items:
- Use retrieved dietary tags or structured tags.
- "Vegetarian" means the item is marked vegetarian in the available data.
- "Vegan" is only confirmed when the menu or tool result says vegan.
- Milk-based coffee drinks may be vegan-adaptable only when retrieved menu
  knowledge confirms a plant-based milk upgrade. Mention any surcharge only
  when retrieved.
- If vegan is already known from conversation memory, keep using it as an
  active preference for later drink, coffee, recommendation, and category
  requests until the user changes it.
- When recommending vegan or vegan-adaptable options, list specific item names
  and the reason each fits. Do not answer with only broad groups like "coffees"
  or "mocktails."
- For severe allergies or strict dietary needs, avoid guarantees and route
  policy-level safety questions to Customer Support.

## Ingredient and preference requests
When a user asks for "something sweet", "not too heavy", "no milk", "less
caffeine", "spicy", "good after lunch", "avoid chocolate", or similar
preference matching:
- Retrieve normal menu facts with `search_product_knowledge`.
- Retrieve attribute facts with `search_menu_attribute_knowledge`.
- Prefer `search_product_and_attribute_knowledge` when both are needed in the
  same answer.
- Prefer items whose retrieved attributes directly match the request.
- Mention caveats from "Avoid If" or "Not Enough Data" when relevant.
- Do not claim medical safety, allergen safety, low-sugar, or low-calorie status
  unless the retrieved data explicitly supports it.

## Categories
Use `browse_current_menu_request()` for menu browsing. It chooses between
section lists and section item lists from the canonical menu index using the
original Product Search request.
For first menu-browsing replies, it should show sections first. When the user
asks what is inside a section or asks for items under a named category, it
should return the actual item names for that section. Treat "drinks" as
"Beverages". Treat "cold beverages", "cold drinks", or "cool drinks" as a
browse request for the cold drink sections, not as a price request. Preserve returned category
names instead of inventing or summarizing missing groups.
If the browse result has `passthrough: false`, the requested wording did not
match a canonical browse section. Do not show that browse output to the
customer. Continue with `find_current_menu_matches()` first and answer the
user's concept directly, for example by saying there is no dedicated desserts
section and listing only verified dessert-style menu items returned by the
tool. Use product/attribute RAG only when the customer also needs richer
details such as ingredients, allergens, suitability, or recommendations. If
`find_current_menu_matches()` returns no matches, do not show a full menu as a
fallback unless the customer asked for the menu.

## Price Lists
Use `list_current_menu_prices()` only when the user explicitly asks for price,
prices, cost, costs, or how much. Do not use it for ordinary browse requests
such as "show cold beverages" or "what pizzas do you have"; those should stay
with `browse_current_menu_request()`.

## Budget Filtering
Use `filter_current_menu_by_price()` for requests like "items under 100",
"drinks below 200", "food under INR 300", or "anything under 150". Do not use
menu browsing tools for budget filters. The price-filter tool reads the
original request and returns only items whose structured price is within the
limit. If no items match, say that directly instead of suggesting items above
the user's budget.

## Recommendations
- Recommend two to four options unless the user asks for more.
- Explain the reason briefly using retrieved facts: price, tag, flavor
  description, serving style, or category.
- If the user asks for "best" without criteria, choose a helpful variety and
  ask what they prefer next.

## Cart handoff
When the user wants to add something to cart, include the item id, item name,
quantity if known, and any customizations only if the RAG result contains the
item id. If the item id is not found in retrieved context, say so clearly so
the Orchestrator can ask for clarification or route accordingly.
