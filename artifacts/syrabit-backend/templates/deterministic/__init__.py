"""Deterministic content templates — Task #10 / blueprint #573.

Each ``.md`` file in this package is a Python ``str.format``-style
template rendered by ``content_formatter._render_deterministic_template``
when the caller passes a materialization-eligible ``query_type``
(definition / mcq / flashcard / glossary / chapter_summary).

Templates are intentionally **format-string** (no Jinja2 dependency)
so a missing placeholder fails loud (V4 §12) instead of silently
producing an empty section.
"""
