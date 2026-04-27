---
name: menu_navigation
description: Guidance for searching and filtering the cafe menu by budget, dietary tags, and category.
---

# Menu Navigation

When a user gives a budget like "under ₹150":
1. Call search_products with their broader query first.
2. Filter the results yourself by `price_inr <= budget`.
3. Mention the budget in your reply ("Under ₹150, I have…").

When a user asks for "vegan" or "vegetarian":
- Look at the `tags` field. "vegetarian" tag = safe vegetarian.
- "vegan" tag isn't on most items — say "ask for oat/soy milk substitution"
  if they want a vegan version of a milk drink.

Categories: coffee, tea, food, dessert.
