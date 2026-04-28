---
name: order_lifecycle
description: Grounded order lifecycle guidance covering placement, tracking, cancellation, budgets, and status transitions.
---

# Order Lifecycle

Use this skill whenever the Order Management agent places, tracks, or cancels
orders. Order behavior is grounded in order tool output and the current cart
total supplied by the Cart agent.

## States
Typical state flow:
pending -> confirmed -> preparing -> ready -> delivered

Cancellation is allowed only from pending or confirmed, according to the
current order service behavior.

## On place_order
- New orders enter `confirmed`.
- The current prototype does not include a payment step.
- Cart is cleared after a successful order.
- Include the order id, status, and total returned by the tool.
- Do not promise preparation times unless support RAG has been consulted.

## On cancel_order
- Allowed: pending, confirmed
- Rejected: preparing, ready, delivered, cancelled
- The error message names the current status. Pass it through plainly and do
  not soften it into a different policy.

## Budget rule
If `max_budget_inr` is given and the cart total exceeds the budget, the order
tool rejects the order. Do not trim the cart, remove items, or substitute
items. Tell the user the order was not placed and route them back to Cart or
Product Search if they want to adjust.

## Missing details
- Missing cart total before placement: ask the Orchestrator to get the current
  total from Cart.
- Missing order id before tracking/cancellation: ask for the order id.
- Tool error: report it plainly and stop.
