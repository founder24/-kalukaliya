"""
ContentRenderer - Renders KnowledgeObject into full SEO-optimized HTML pages.
Supports 5 page types: notes, mcqs, summary, definitions, important-questions.
"""

import hashlib
import logging
from typing import Optional

from jinja2 import Environment, BaseLoader
from markupsafe import Markup

logger = logging.getLogger(__name__)

PAGE_TYPES = ["notes", "mcqs", "summary", "definitions", "important-questions"]

# Base HTML template with SEO structured data
BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ language }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }} | Syrabit</title>
<meta name="description" content="{{ description }}">
<meta name="keywords" content="{{ keywords }}">
<link rel="canonical" href="{{ canonical_url }}">
{% for alt in hreflang_links %}
<link rel="alternate" hreflang="{{ alt.lang }}" href="{{ alt.href }}">
{% endfor %}
<!-- Open Graph -->
<meta property="og:title" content="{{ title }}">
<meta property="og:description" content="{{ description }}">
<meta property="og:type" content="article">
<meta property="og:url" content="{{ canonical_url }}">
<meta property="og:site_name" content="Syrabit Education">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{ title }}">
<meta name="twitter:description" content="{{ description }}">
<!-- JSON-LD Structured Data -->
<script type="application/ld+json">{{ jsonld | safe }}</script>
</head>
<body>
<nav class="page-type-nav" aria-label="Page type navigation">
{% for pt in page_types %}
<a href="{{ base_path }}/{{ pt }}"{% if pt == current_page_type %} aria-current="page" class="active"{% endif %}>{{ pt | replace("-", " ") | title }}</a>
{% endfor %}
</nav>
<main>
{{ body | safe }}
</main>
</body>
</html>"""


class ContentRenderer:
    """Renders KnowledgeObject content into SEO-optimized HTML."""

    def __init__(self):
        self.env = Environment(loader=BaseLoader(), autoescape=True)
        self.template = self.env.from_string(BASE_TEMPLATE)

    def render(
        self,
        knowledge_obj,
        page_type: str = "notes",
        base_url: str = "https://syrabit.ai",
    ) -> str:
        """
        Render a knowledge object into a full HTML page for a given page type.

        Args:
            knowledge_obj: KnowledgeObject instance or dict
            page_type: One of notes, mcqs, summary, definitions, important-questions
            base_url: Base URL for canonical/hreflang links

        Returns:
            Rendered HTML string
        """
        if page_type not in PAGE_TYPES:
            page_type = "notes"

        meta = knowledge_obj.metadata if hasattr(knowledge_obj, "metadata") else knowledge_obj.get("metadata", {})
        if hasattr(meta, "model_dump"):
            meta_dict = meta.model_dump()
        elif isinstance(meta, dict):
            meta_dict = meta
        else:
            meta_dict = {}

        board = meta_dict.get("board", "")
        class_level = meta_dict.get("class_level", "")
        subject = meta_dict.get("subject", "")
        chapter = meta_dict.get("chapter", "")
        language = meta_dict.get("language", "en")
        keywords = meta_dict.get("keywords", [])

        title = getattr(knowledge_obj, "title", "") or knowledge_obj.get("title", "") if isinstance(knowledge_obj, dict) else knowledge_obj.title
        description = getattr(knowledge_obj, "description", "") if hasattr(knowledge_obj, "description") else knowledge_obj.get("description", "") if isinstance(knowledge_obj, dict) else ""

        base_path = f"/render/{board}/{class_level}/{subject}/{chapter}"
        canonical_url = f"{base_url}{base_path}/{page_type}"

        # Hreflang links
        hreflang_links = [
            {"lang": "en", "href": f"{base_url}{base_path}/{page_type}"},
            {"lang": "as", "href": f"{base_url}{base_path}/{page_type}?lang=as"},
        ]

        # Generate body content based on page type
        body_html = self._render_body(knowledge_obj, page_type)

        # Generate JSON-LD structured data
        jsonld = self._build_jsonld(
            knowledge_obj, page_type, canonical_url, base_url, base_path
        )

        html = self.template.render(
            title=f"{title} - {page_type.replace('-', ' ').title()}",
            description=description,
            keywords=", ".join(keywords),
            canonical_url=canonical_url,
            hreflang_links=hreflang_links,
            jsonld=Markup(jsonld),
            page_types=PAGE_TYPES,
            current_page_type=page_type,
            base_path=base_path,
            body=Markup(body_html),
            language=language,
        )
        return html

    def _render_body(self, knowledge_obj, page_type: str) -> str:
        """Render the body content for a specific page type."""
        if page_type == "notes":
            return self._render_notes(knowledge_obj)
        elif page_type == "mcqs":
            return self._render_mcqs(knowledge_obj)
        elif page_type == "summary":
            return self._render_summary(knowledge_obj)
        elif page_type == "definitions":
            return self._render_definitions(knowledge_obj)
        elif page_type == "important-questions":
            return self._render_important_questions(knowledge_obj)
        return ""

    def _render_notes(self, obj) -> str:
        """Render notes page from body_markdown."""
        body = obj.body_markdown if hasattr(obj, "body_markdown") else obj.get("body_markdown", "")
        # Simple markdown-to-html: wrap paragraphs
        paragraphs = body.split("\n\n") if body else []
        html_parts = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if p.startswith("# "):
                html_parts.append(f"<h1>{_escape(p[2:])}</h1>")
            elif p.startswith("## "):
                html_parts.append(f"<h2>{_escape(p[3:])}</h2>")
            elif p.startswith("### "):
                html_parts.append(f"<h3>{_escape(p[4:])}</h3>")
            else:
                html_parts.append(f"<p>{_escape(p)}</p>")
        return "\n".join(html_parts)

    def _render_mcqs(self, obj) -> str:
        """Render MCQ page."""
        generated = obj.generated if hasattr(obj, "generated") else obj.get("generated", {})
        if hasattr(generated, "model_dump"):
            generated = generated.model_dump()
        mcqs = generated.get("mcqs", []) if isinstance(generated, dict) else []

        if not mcqs:
            return "<p>No MCQs available yet.</p>"

        html_parts = ["<section class=\"mcqs\">"]
        for i, mcq in enumerate(mcqs, 1):
            question = mcq.get("question", "")
            options = mcq.get("options", [])
            answer = mcq.get("answer", "")
            html_parts.append(f"<article class=\"mcq\" data-index=\"{i}\">")
            html_parts.append(f"<h3>Q{i}. {_escape(question)}</h3>")
            html_parts.append("<ol type=\"A\">")
            for opt in options:
                html_parts.append(f"<li>{_escape(opt)}</li>")
            html_parts.append("</ol>")
            html_parts.append(f"<details><summary>Answer</summary><p>{_escape(answer)}</p></details>")
            html_parts.append("</article>")
        html_parts.append("</section>")
        return "\n".join(html_parts)

    def _render_summary(self, obj) -> str:
        """Render summary page."""
        generated = obj.generated if hasattr(obj, "generated") else obj.get("generated", {})
        if hasattr(generated, "model_dump"):
            generated = generated.model_dump()
        summary = generated.get("summary", "") if isinstance(generated, dict) else ""
        if not summary:
            return "<p>No summary available yet.</p>"
        paragraphs = summary.split("\n\n")
        return "\n".join(f"<p>{_escape(p.strip())}</p>" for p in paragraphs if p.strip())

    def _render_definitions(self, obj) -> str:
        """Render definitions page."""
        generated = obj.generated if hasattr(obj, "generated") else obj.get("generated", {})
        if hasattr(generated, "model_dump"):
            generated = generated.model_dump()
        definitions = generated.get("definitions", []) if isinstance(generated, dict) else []

        if not definitions:
            return "<p>No definitions available yet.</p>"

        html_parts = ["<dl class=\"definitions\">"]
        for defn in definitions:
            term = defn.get("term", "")
            definition = defn.get("definition", "")
            html_parts.append(f"<dt>{_escape(term)}</dt>")
            html_parts.append(f"<dd>{_escape(definition)}</dd>")
        html_parts.append("</dl>")
        return "\n".join(html_parts)

    def _render_important_questions(self, obj) -> str:
        """Render important questions page."""
        generated = obj.generated if hasattr(obj, "generated") else obj.get("generated", {})
        if hasattr(generated, "model_dump"):
            generated = generated.model_dump()
        questions = generated.get("important_questions", []) if isinstance(generated, dict) else []

        if not questions:
            return "<p>No important questions available yet.</p>"

        html_parts = ["<section class=\"important-questions\">"]
        for i, q in enumerate(questions, 1):
            question = q.get("question", "")
            answer = q.get("answer", "")
            marks = q.get("marks", "")
            marks_str = f" [{marks} marks]" if marks else ""
            html_parts.append(f"<article>")
            html_parts.append(f"<h3>Q{i}. {_escape(question)}{_escape(marks_str)}</h3>")
            if answer:
                html_parts.append(f"<div class=\"answer\"><p>{_escape(answer)}</p></div>")
            html_parts.append("</article>")
        html_parts.append("</section>")
        return "\n".join(html_parts)

    def _build_jsonld(
        self, obj, page_type: str, canonical_url: str, base_url: str, base_path: str
    ) -> str:
        """Build JSON-LD structured data based on page type."""
        import json

        title = obj.title if hasattr(obj, "title") else obj.get("title", "")
        description = obj.description if hasattr(obj, "description") else obj.get("description", "")
        meta = obj.metadata if hasattr(obj, "metadata") else obj.get("metadata", {})
        if hasattr(meta, "model_dump"):
            meta_dict = meta.model_dump()
        elif isinstance(meta, dict):
            meta_dict = meta
        else:
            meta_dict = {}

        # BreadcrumbList (always present)
        breadcrumb = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": base_url},
                {"@type": "ListItem", "position": 2, "name": meta_dict.get("board", ""), "item": f"{base_url}/render/{meta_dict.get('board', '')}"},
                {"@type": "ListItem", "position": 3, "name": f"Class {meta_dict.get('class_level', '')}", "item": f"{base_url}/render/{meta_dict.get('board', '')}/{meta_dict.get('class_level', '')}"},
                {"@type": "ListItem", "position": 4, "name": meta_dict.get("subject", "").replace("-", " ").title(), "item": f"{base_url}/render/{meta_dict.get('board', '')}/{meta_dict.get('class_level', '')}/{meta_dict.get('subject', '')}"},
                {"@type": "ListItem", "position": 5, "name": title, "item": canonical_url},
            ],
        }

        # Page-type specific structured data
        if page_type == "notes":
            specific = {
                "@context": "https://schema.org",
                "@type": "Course",
                "name": title,
                "description": description,
                "provider": {"@type": "Organization", "name": "Syrabit Education"},
            }
        elif page_type == "mcqs":
            specific = {
                "@context": "https://schema.org",
                "@type": "Quiz",
                "name": f"{title} - MCQs",
                "description": f"Multiple choice questions for {title}",
                "educationalLevel": f"Class {meta_dict.get('class_level', '')}",
            }
        elif page_type == "definitions":
            specific = {
                "@context": "https://schema.org",
                "@type": "DefinedTermSet",
                "name": f"{title} - Definitions",
                "description": f"Key definitions for {title}",
            }
        elif page_type == "important-questions":
            specific = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "name": f"{title} - Important Questions",
                "description": f"Important questions for {title}",
            }
        else:
            specific = {
                "@context": "https://schema.org",
                "@type": "Article",
                "name": f"{title} - Summary",
                "description": description,
            }

        result = [breadcrumb, specific]
        return json.dumps(result, ensure_ascii=False)

    def compute_hash(self, html: str) -> str:
        """Compute SHA256 hash of rendered HTML for change detection."""
        return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _escape(text: str) -> str:
    """HTML-escape text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


# Singleton
content_renderer = ContentRenderer()
