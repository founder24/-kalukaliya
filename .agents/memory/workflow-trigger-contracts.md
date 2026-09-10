---
name: Workflow trigger contracts
description: Why GitHub Actions trigger guards must validate normalized YAML mappings.
---

Validate workflow trigger contracts with a YAML 1.2-compatible structural parser that rejects duplicate keys. Do not enforce semantic trigger rules with indentation or source-line regular expressions.

**Why:** Valid YAML can express equivalent trigger keys through quoted scalars or explicit mapping-key syntax, and duplicate keys can change effective behavior. Text parsing can miss these forms and approve a workflow whose CI scope was silently narrowed.

**How to apply:** When guarding GitHub Actions events, parse and normalize the `on` mapping, fail closed on malformed or duplicate mappings, and compare each event's complete key set and values against an explicit reviewed contract.