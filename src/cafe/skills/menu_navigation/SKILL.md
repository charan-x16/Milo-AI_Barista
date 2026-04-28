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
Use `list_menu_categories` when the user asks for all categories, menu
sections, category choices, or what they can select from. Call it with
`include_items=true` when the user asks what is inside the categories. Treat
"drinks" as "Beverages" and preserve the returned category names instead of
inventing or summarizing missing groups.

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
