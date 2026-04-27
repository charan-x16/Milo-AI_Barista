---
name: cart_etiquette
description: Rules for cart customizations, merging duplicate items, and quantity etiquette.
---

# Cart Etiquette

## Merging
Two add_to_cart calls for the same item_id with the SAME customizations
list will merge into one line with summed quantity. Different
customizations → separate lines.

## Customizations
Free-text. Examples: "oat milk", "extra hot", "no sugar", "decaf".
If the user says "two cappuccinos, one with oat milk":
- Call add_to_cart twice — once with [], once with ["oat milk"]

## Quantities
Always positive. If user says "remove a chai" but cart has 3, this tool
removes ALL three. Confirm first if quantity > 1.
