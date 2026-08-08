<!--
  PLACEHOLDER category file — E7 (#45).

  This is a minimal, valid category definition so the container image builds and
  the processor can start. The production categories are authored in E9 (#42),
  which replaces the content below. Do NOT treat these categories as final.

  Format (parsed by src/categories.py):
    - `## Name`            a category, ordered by appearance
    - prose before bullets  the category's description (optional)
    - `-`/`*`/`+` bullets    few-shot examples (at least one required per category)
    - `## unknown`          reserved fallback bucket; never a real category
-->

# Document categories

## Contract
Agreements and executed instruments that create binding obligations between parties.

- Master services agreement between two companies
- Non-disclosure agreement signed by both parties
- Amendment or addendum to an existing contract

## Correspondence
Letters, emails, and memos exchanged between parties or with third parties.

- Demand letter to an opposing party
- Email thread confirming a meeting date
- Internal memorandum summarizing a call

## Invoice
Billing documents requesting payment for goods or services rendered.

- Vendor invoice with line items and a total due
- Statement of outstanding account balance
- Receipt acknowledging a payment made

## unknown
Reserved fallback bucket for documents that fit none of the categories above.
The classifier emits ``unknown`` rather than forcing a poor match; this section
defines no examples and is never treated as a real category.
