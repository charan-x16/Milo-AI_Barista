---
name: order_lifecycle
description: Order state machine, valid transitions, and budget handling rules.
---

# Order Lifecycle

## States
pending → confirmed → preparing → ready → delivered
Or: any → cancelled (only from pending/confirmed)

## On place_order
- New orders enter `confirmed` (we don't have a payment step in this prototype).
- Cart is cleared after a successful order.

## On cancel_order
- Allowed: pending, confirmed
- Rejected: preparing, ready, delivered, cancelled
- The error message names the current status — pass it through verbatim.

## Budget rule
If max_budget_inr is given and total > budget: REJECT, don't trim. Tell
the user how much over they are.
