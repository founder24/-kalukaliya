import json
import markdown
from jinja2 import Environment
from markupsafe import Markup
from app.models.knowledge import KnowledgeObject
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://syrabit.ai"
SITE_NAME = "Syrabit"

PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="{{ language }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <meta name="description" content="{{ meta_description }}">
    <link rel="canonical" href="{{ canonical_url }}">
    <link rel="alternate" hreflang="en" href="{{ canonical_url }}">
    <link rel="alternate" hreflang="as" href="{{ canonical_url }}?lang=as">
    <!-- Open Graph -->
    <meta property="og:title" content="{{ title }}">
    <meta property="og:description" content="{{ meta_description }}">
    <meta property="og:url" content="{{ canonical_url }}">
    <meta property="og:type" content="article">
    <meta property="og:site_name" content="{{ site_name }}">
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{{ title }}">
    <meta name="twitter:description" content="{{ meta_description }}">
    <!-- JSON-LD -->
    {% for schema in json_ld_schemas %}
    <script type="application/ld+json">{{ schema }}</script>
    {% endfor %}
</head>
<body>
    <nav aria-label="Breadcrumb">
        <ol>
            <li><a href="{{ base_url }}">Home</a></li>
            <li><a href="{{ base_url }}/{{ board }}">{{ board_name }}</a></li>
            <li><a href="{{ base_url }}/{{ board }}/{{ class_level }}">{{ class_level }}</a></li>
            <li><a href="{{ base_url }}/{{ board }}/{{ class_level }}/{{ subject }}">{{ subject }}</a></li>
            <li><a href="{{ canonical_url }}">{{ topic }}</a></li>
        </ol>
    </nav>
    <main>
        <h1>{{ heading }}</h1>
        {{ body_html }}
    </main>
    <nav aria-label="Page types">
        <ul>
        {% for pt in page_types %}
            <li><a href="{{ base_url }}/{{ board }}/{{ class_level }}/{{ subject }}/{{ chapter }}{% if pt != 'notes' %}/{{ pt }}{% endif %}">{{ pt | replace('-', ' ') | title }}</a></li>
        {% endfor %}
        </ul>
    </nav>
    <nav aria-label="Chapter navigation">
        <a href="{{ base_url }}/{{ board }}/{{ class_level }}/{{ subject }}">Back to {{ subject | replace('-', ' ') | title }}</a>
    </nav>
</body>
</html>
"""


class ContentRenderer:
    """Renders a KnowledgeObject into HTML for multiple page types."""

    def __init__(self):
        self.env = Environment(autoescape=True)
        self.template = self.env.from_string(PAGE_TEMPLATE)
        self.page_types = [
            "notes",
            "mcqs",
            "summary",
            "definitions",
            "important-questions",
        ]

    def render(self, knowledge_object: KnowledgeObject, page_type: str = "notes") -> str:
        """Render a KnowledgeObject into HTML for the given page_type.

        page_types: "notes", "mcqs", "summary", "definitions", "important-questions"
        """
        ko = knowledge_object
        canonical_url = self._build_canonical_url(ko, page_type)
        title = self._build_title(ko, page_type)
        meta_description = self._build_meta_description(ko, page_type)
        json_ld_schemas = self._build_json_ld(ko, page_type, canonical_url)
        body_html = self._render_body(ko, page_type)
        heading = self._build_heading(ko, page_type)

        # Mark pre-rendered HTML and JSON-LD as safe to prevent double-escaping
        body_html_safe = Markup(body_html)
        json_ld_safe = [Markup(json.dumps(s, ensure_ascii=False)) for s in json_ld_schemas]

        html = self.template.render(
            language=ko.metadata.language,
            title=title,
            meta_description=meta_description,
            canonical_url=canonical_url,
            site_name=SITE_NAME,
            base_url=BASE_URL,
            board=ko.board,
            board_name=ko.metadata.board_name or ko.board,
            class_level=ko.class_level,
            subject=ko.subject,
            chapter=ko.chapter,
            topic=ko.topic,
            heading=heading,
            body_html=body_html_safe,
            json_ld_schemas=json_ld_safe,
            page_types=self.page_types,
        )
        return html

    def _build_canonical_url(self, ko: KnowledgeObject, page_type: str) -> str:
        base = f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}/{ko.chapter}"
        if page_type != "notes":
            base += f"/{page_type}"
        return base

    def _build_title(self, ko: KnowledgeObject, page_type: str) -> str:
        type_labels = {
            "notes": "Notes",
            "mcqs": "MCQs",
            "summary": "Summary",
            "definitions": "Definitions",
            "important-questions": "Important Questions",
        }
        label = type_labels.get(page_type, "Notes")
        class_display = ko.class_level.replace("-", " ").title()
        subject_display = ko.subject.replace("-", " ").title()
        return f"{ko.topic} {label} - {subject_display} {class_display} | {SITE_NAME}"

    def _build_meta_description(self, ko: KnowledgeObject, page_type: str) -> str:
        type_descriptions = {
            "notes": f"Comprehensive notes on {ko.topic} for {ko.subject.replace('-', ' ').title()} students. Key concepts, formulas, and learning objectives covered.",
            "mcqs": f"Practice MCQs on {ko.topic} with answers and explanations. Test your understanding of key concepts in {ko.subject.replace('-', ' ').title()}.",
            "summary": f"Quick summary of {ko.topic} covering key takeaways and important points for {ko.subject.replace('-', ' ').title()} exam preparation.",
            "definitions": f"Important definitions from {ko.topic} chapter in {ko.subject.replace('-', ' ').title()}. Clear explanations with examples.",
            "important-questions": f"Important questions from {ko.topic} including previous year questions for {ko.subject.replace('-', ' ').title()} exam preparation.",
        }
        desc = type_descriptions.get(page_type, type_descriptions["notes"])
        # Truncate to 160 chars
        if len(desc) > 160:
            desc = desc[:157] + "..."
        return desc

    def _build_heading(self, ko: KnowledgeObject, page_type: str) -> str:
        type_labels = {
            "notes": "Notes",
            "mcqs": "MCQs",
            "summary": "Summary",
            "definitions": "Definitions",
            "important-questions": "Important Questions",
        }
        label = type_labels.get(page_type, "Notes")
        return f"{ko.topic} - {label}"

    def _build_json_ld(self, ko: KnowledgeObject, page_type: str, canonical_url: str) -> list[dict]:
        schemas = []

        # BreadcrumbList - always included
        breadcrumb = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": BASE_URL,
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": ko.metadata.board_name or ko.board,
                    "item": f"{BASE_URL}/{ko.board}",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": ko.class_level.replace("-", " ").title(),
                    "item": f"{BASE_URL}/{ko.board}/{ko.class_level}",
                },
                {
                    "@type": "ListItem",
                    "position": 4,
                    "name": ko.subject.replace("-", " ").title(),
                    "item": f"{BASE_URL}/{ko.board}/{ko.class_level}/{ko.subject}",
                },
                {
                    "@type": "ListItem",
                    "position": 5,
                    "name": ko.topic,
                    "item": canonical_url,
                },
            ],
        }
        schemas.append(breadcrumb)

        # Page-type specific schemas
        if page_type == "notes":
            course_schema = {
                "@context": "https://schema.org",
                "@type": "Course",
                "name": ko.topic,
                "description": f"Comprehensive notes on {ko.topic}",
                "provider": {
                    "@type": "Organization",
                    "name": SITE_NAME,
                    "sameAs": BASE_URL,
                },
                "educationalLevel": ko.class_level.replace("-", " ").title(),
                "about": ko.subject.replace("-", " ").title(),
            }
            schemas.append(course_schema)

        elif page_type == "mcqs":
            quiz_schema = {
                "@context": "https://schema.org",
                "@type": "Quiz",
                "name": f"{ko.topic} MCQs",
                "about": {
                    "@type": "Thing",
                    "name": ko.topic,
                },
                "educationalLevel": ko.class_level.replace("-", " ").title(),
            }
            schemas.append(quiz_schema)

        elif page_type == "definitions":
            defined_term_set = {
                "@context": "https://schema.org",
                "@type": "DefinedTermSet",
                "name": f"{ko.topic} Definitions",
                "hasDefinedTerm": [],
            }
            for defn in ko.content.definitions:
                if defn.get("term") and defn.get("definition"):
                    defined_term_set["hasDefinedTerm"].append({
                        "@type": "DefinedTerm",
                        "name": defn["term"],
                        "description": defn["definition"],
                    })
            schemas.append(defined_term_set)

        # FAQPage - if FAQ content exists
        if ko.content.faq:
            faq_schema = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [],
            }
            for item in ko.content.faq:
                if item.get("question") and item.get("answer"):
                    faq_schema["mainEntity"].append({
                        "@type": "Question",
                        "name": item["question"],
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": item["answer"],
                        },
                    })
            if faq_schema["mainEntity"]:
                schemas.append(faq_schema)

        return schemas

    def _render_body(self, ko: KnowledgeObject, page_type: str) -> str:
        if page_type == "notes":
            return self._render_notes(ko)
        elif page_type == "mcqs":
            return self._render_mcqs(ko)
        elif page_type == "summary":
            return self._render_summary(ko)
        elif page_type == "definitions":
            return self._render_definitions(ko)
        elif page_type == "important-questions":
            return self._render_important_questions(ko)
        return self._render_notes(ko)

    def _render_notes(self, ko: KnowledgeObject) -> str:
        parts = []

        # Learning objectives
        if ko.content.learning_objectives:
            parts.append("<section class=\"learning-objectives\">")
            parts.append("<h2>Learning Objectives</h2>")
            parts.append("<ul>")
            for obj in ko.content.learning_objectives:
                parts.append(f"<li>{obj}</li>")
            parts.append("</ul>")
            parts.append("</section>")

        # Main content from markdown
        body_html = markdown.markdown(
            ko.content.body_markdown,
            extensions=["tables", "fenced_code"],
        )
        parts.append(f"<section class=\"content\">{body_html}</section>")

        # Key concepts
        if ko.content.key_concepts:
            parts.append("<section class=\"key-concepts\">")
            parts.append("<h2>Key Concepts</h2>")
            parts.append("<ul>")
            for concept in ko.content.key_concepts:
                parts.append(f"<li>{concept}</li>")
            parts.append("</ul>")
            parts.append("</section>")

        # Formulas
        if ko.content.formulas:
            parts.append("<section class=\"formulas\">")
            parts.append("<h2>Important Formulas</h2>")
            parts.append("<ul>")
            for formula in ko.content.formulas:
                parts.append(f"<li><code>{formula}</code></li>")
            parts.append("</ul>")
            parts.append("</section>")

        return "\n".join(parts)

    def _render_mcqs(self, ko: KnowledgeObject) -> str:
        parts = []
        mcqs = ko.generated.mcqs
        if not mcqs:
            parts.append("<p>No MCQs available yet for this topic.</p>")
            return "\n".join(parts)

        parts.append("<section class=\"mcqs\">")
        parts.append("<h2>Multiple Choice Questions</h2>")
        for i, mcq in enumerate(mcqs, 1):
            parts.append(f"<div class=\"mcq\" data-question=\"{i}\">")
            parts.append(f"<p class=\"question\"><strong>Q{i}.</strong> {mcq.get('question', '')}</p>")
            options = mcq.get("options", [])
            parts.append("<ol type=\"a\">")
            for opt in options:
                parts.append(f"<li>{opt}</li>")
            parts.append("</ol>")
            correct = mcq.get("correct", "")
            explanation = mcq.get("explanation", "")
            parts.append(f"<details><summary>Answer</summary><p><strong>Correct:</strong> {correct}</p>")
            if explanation:
                parts.append(f"<p><strong>Explanation:</strong> {explanation}</p>")
            parts.append("</details>")
            parts.append("</div>")
        parts.append("</section>")
        return "\n".join(parts)

    def _render_summary(self, ko: KnowledgeObject) -> str:
        parts = []
        summary_text = ko.generated.summary
        if not summary_text:
            summary_text = ko.content.body_markdown[:500]

        parts.append("<section class=\"summary\">")
        parts.append("<h2>Chapter Summary</h2>")
        summary_html = markdown.markdown(summary_text, extensions=["tables"])
        parts.append(summary_html)
        parts.append("</section>")

        # Key takeaways from key concepts
        if ko.content.key_concepts:
            parts.append("<section class=\"key-takeaways\">")
            parts.append("<h2>Key Takeaways</h2>")
            parts.append("<ul>")
            for concept in ko.content.key_concepts:
                parts.append(f"<li>{concept}</li>")
            parts.append("</ul>")
            parts.append("</section>")

        return "\n".join(parts)

    def _render_definitions(self, ko: KnowledgeObject) -> str:
        parts = []
        definitions = ko.content.definitions
        if not definitions:
            parts.append("<p>No definitions available yet for this topic.</p>")
            return "\n".join(parts)

        parts.append("<section class=\"definitions\">")
        parts.append("<h2>Important Definitions</h2>")
        parts.append("<dl>")
        for defn in definitions:
            term = defn.get("term", "")
            definition = defn.get("definition", "")
            parts.append(f"<dt>{term}</dt>")
            parts.append(f"<dd>{definition}</dd>")
        parts.append("</dl>")
        parts.append("</section>")

        return "\n".join(parts)

    def _render_important_questions(self, ko: KnowledgeObject) -> str:
        parts = []

        # Previous year questions
        if ko.content.prev_year_questions:
            parts.append("<section class=\"prev-year-questions\">")
            parts.append("<h2>Previous Year Questions</h2>")
            for i, q in enumerate(ko.content.prev_year_questions, 1):
                year = q.get("year", "")
                question = q.get("question", "")
                answer = q.get("answer", "")
                marks = q.get("marks", "")
                parts.append(f"<div class=\"question-item\">")
                parts.append(f"<p><strong>Q{i}.</strong> {question}")
                if year:
                    parts.append(f" <span class=\"year\">[{year}]</span>")
                if marks:
                    parts.append(f" <span class=\"marks\">({marks} marks)</span>")
                parts.append("</p>")
                if answer:
                    parts.append(f"<details><summary>Answer</summary><p>{answer}</p></details>")
                parts.append("</div>")
            parts.append("</section>")

        # Generated important questions
        if ko.generated.important_questions:
            parts.append("<section class=\"important-questions\">")
            parts.append("<h2>Important Questions for Exam</h2>")
            for i, q in enumerate(ko.generated.important_questions, 1):
                question = q.get("question", "")
                marks = q.get("marks", "")
                frequency = q.get("frequency", "")
                parts.append(f"<div class=\"question-item\">")
                parts.append(f"<p><strong>Q{i}.</strong> {question}")
                if marks:
                    parts.append(f" <span class=\"marks\">({marks} marks)</span>")
                if frequency:
                    parts.append(f" <span class=\"frequency\">[Asked {frequency} times]</span>")
                parts.append("</p>")
                parts.append("</div>")
            parts.append("</section>")

        if not parts:
            parts.append("<p>No important questions available yet for this topic.</p>")

        return "\n".join(parts)
