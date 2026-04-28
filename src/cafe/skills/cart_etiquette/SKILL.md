---
name: cart_etiquette
description: RAG-safe cart operation etiquette for customizations, duplicate merging, quantities, removals, and cart summaries.
---

# Cart Etiquette

Use this skill whenever the Cart Management agent changes or summarizes a
session cart. Cart behavior is grounded in cart tool output and verified menu
item ids; do not infer missing item ids, prices, or availability.

## Merging
Two `add_to_cart` calls for the same item id with the same customizations
list will merge into one line with summed quantity. Different customizations
create separate lines.

## Customizations
Customizations are free-text notes passed to the cart tool. Examples:
"oat milk", "extra hot", "no sugar", "decaf".

If the user says "two cappuccinos, one with oat milk":
- Call `add_to_cart` twice: once with no customizations and once with
  `["oat milk"]`.

Only accept customizations that the user requested or that the Orchestrator
provided. Do not suggest customization charges from memory; that belongs to
Product Search or Support via RAG.

## Quantities
Always use positive integers. If the quantity is vague ("some", "a few",
"one more of those") and the referenced line is not obvious from tool output,
ask for clarification.

## Removals
`remove_from_cart` removes all units of the matching item id. If the user asks
to remove only one unit from a multi-unit line, explain that the current tool
removes the whole line and ask whether to proceed.

## Summaries
After mutations, include:
- what changed
- the updated item count or cart contents when provided
- the updated total in INR when provided

Keep the tone friendly and matter-of-fact.
