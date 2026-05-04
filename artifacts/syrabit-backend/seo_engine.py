"""
Syrabit.ai — Programmatic SEO Engine
Generates thousands of search-indexed educational pages from MongoDB academic data.

Collections:
  - topics:     granular concepts under chapters (auto-extracted or admin-created)
  - seo_pages:  AI-generated study content per topic × page_type

URL pattern (4-segment):
  /{board}/{class}/{subject}/{topic}
  /{board}/{class}/{subject}/{topic}/{page_type}
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Cookie, Request
from fastapi.responses import Response, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from typing import Any, Callable, Coroutine, List, Optional
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime as _email_format_datetime
import asyncio, uuid, re, logging, json, html as html_mod, hashlib

try:
    import bing_webmaster as _bwt
except ImportError:
    _bwt = None


def _iso_to_rfc7231(value) -> Optional[str]:
    """Convert a Mongo `updated_at` (ISO 8601 string or datetime) into the
    RFC 7231 IMF-fixdate format ("Thu, 16 Apr 2026 12:30:45 GMT") used by
    HTTP `Last-Modified` / `If-Modified-Since` headers. Returns None when
    the input is missing or unparseable so callers can omit the header.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        return None
    return _email_format_datetime(dt.astimezone(timezone.utc), usegmt=True)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/seo", tags=["SEO Engine"])

_db: Optional[AsyncIOMotorDatabase] = None
_call_llm: Optional[Callable[..., Coroutine[Any, Any, str]]] = None
_get_admin_fn: Optional[Callable[..., Coroutine[Any, Any, dict]]] = None
_log_activity: Optional[Callable[..., Coroutine[Any, Any, None]]] = None
_security = HTTPBearer(auto_error=False)


def init_seo_engine(
    db: AsyncIOMotorDatabase,
    call_llm_api: Callable,
    get_admin_user_fn: Callable,
    log_activity_fn: Optional[Callable] = None,
):
    global _db, _call_llm, _get_admin_fn, _log_activity
    _db = db
    _call_llm = call_llm_api
    _get_admin_fn = get_admin_user_fn
    _log_activity = log_activity_fn


async def _seo_log(action: str, details: str, level: str = "info"):
    """Non-blocking activity log helper — fires-and-forgets."""
    if _log_activity is None:
        return
    try:
        await _log_activity({
            "id":         f"seo-{uuid.uuid4().hex[:8]}",
            "action":     action,
            "details":    details,
            "level":      level,
            "admin_name": "SEO Engine",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.warning(f"_seo_log failed: {exc}")


async def _require_admin(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    syrabit_admin_session: Optional[str] = Cookie(default=None),
):
    if _get_admin_fn is None:
        raise HTTPException(status_code=503, detail="Auth not initialized")
    return await _get_admin_fn(creds=creds, syrabit_admin_session=syrabit_admin_session)


def _slug(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-')


def _robust_parse_json_array(raw: str) -> list[str]:
    """Parse a JSON string array from LLM output, handling markdown fences,
    conversational prefixes, and other formatting variations."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    for attempt_text in [text, re.sub(r"^[^[]*", "", text, count=1)]:
        match = re.search(r"\[[\s\S]*\]", attempt_text)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    return [str(t).strip() for t in parsed if str(t).strip()]
            except json.JSONDecodeError:
                continue

    lines = [l.strip().lstrip("-•*0123456789.) ").strip('"').strip("'").strip()
             for l in text.split("\n") if l.strip() and not l.strip().startswith("{")]
    results = [l for l in lines if 2 <= len(l.split()) <= 12 and len(l) < 120]
    return results


PAGE_TYPES = ["notes", "definition", "important-questions", "mcqs", "examples"]
ALL_PAGE_TYPES = PAGE_TYPES + ["faq"]
AUTO_PAGE_TYPES = ["notes", "mcqs"]

def _topic_hash(topic_title: str, page_type: str, n_variants: int) -> int:
    """Deterministic variant selector based on topic+type. Stable across regenerations."""
    h = hashlib.md5(f"{topic_title}:{page_type}".encode()).hexdigest()
    return int(h, 16) % n_variants


PROMPT_VARIANTS = {
    "notes": [
        """You are an expert {board} teacher for {class_name} and a GEO (Generative Engine Optimization) specialist.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}
Primary keyword: "{topic} notes {board} {class_name}"

CRITICAL CONTENT RULES:
- Write ONLY about what {topic} actually covers according to the {board} {subject} syllabus under chapter "{chapter}". Do NOT invent content outside the topic's scope.
- First paragraph must be 2-3 sentences MAX, keyword-focused, starting with "{topic}" directly.
- Do NOT include generic math-style "solved examples" or "step-by-step problems" unless the topic genuinely involves calculations or problem-solving. For conceptual/theoretical topics, use illustrative case studies, real-world applications, or exam-style descriptive answers instead.
- Use keyword variations naturally: "{topic} notes", "{topic} summary", "{topic} {subject} notes {class_name}", "{topic} important points"
- Add human touches: "In simple terms…", "You can remember this as…", "Many students confuse X and Y — here's the difference."
- When mentioning other units/chapters, name them clearly (e.g., "Unit I: Introduction to..." or "Unit III: ...")

Write study notes using EXACTLY this structure — all sections required:

## What is {topic}? (Quick Answer)
[2-3 sentences ONLY. Direct, keyword-rich definition suitable for Google featured snippet. Start with "{topic} is..." or "{topic} refers to...". End with its relevance to {board} {class_name} {subject}.]

## {topic} — Detailed Notes for {board} {class_name}
[1500-2500 words. Cover ALL core concepts, sub-topics, and key ideas that {topic} contains according to the {board} syllabus for "{chapter}". Go deep into each concept — explain why it matters, how it works, and how it connects to other topics in this chapter. Break into multiple ### sub-headings for each major concept. Use natural student-friendly language. Include relevant real-world examples. Reference related syllabus topics by NAME. Be thorough and comprehensive — this is a long-form study resource.]

## Key Points for Revision
[8-10 bullet points covering the most important facts, definitions, and concepts from {topic}. Each should be a complete, exam-ready statement. Add one bullet like "Don't confuse [X] with [Y] — examiners love testing this."]

## Important Concepts & Applications
[For each major concept in {topic}, explain it with a real-world application or case study. Use relevant real-world examples. Format as 3-4 sub-sections with ### headings. This should demonstrate understanding, not math-style problem solving.]

## Exam-Style Questions with Answers
[6 questions with complete model answers covering {topic}:
- 2× short answer (1-2 marks): definition/factual recall
- 2× medium answer (3 marks): explain/compare/distinguish
- 2× long answer (5-7 marks): detailed analysis/discussion
Format: "Q (X marks): ..." with full model answer below each.]

## Frequently Asked Questions
Q1: What is {topic}? (1-mark answer)
A1: [Crisp 1-line answer]
Q2: What are the key concepts covered in {topic}?
A2: [List 4-5 main concepts]
Q3: Why is {topic} important for {board} exams?
A3: [Answer with exam relevance and mark weightage]
Q4: How does {topic} connect to other topics in {chapter}?
A4: [Show connections by naming related topics]

Language: simple, clear, and exam-focused for {class_name} students. Sound like a helpful teacher, not a textbook. Every section must contain substantive academic content specific to {topic}.""",

        """You are a {subject} expert specialising in {board} {class_name} exam preparation.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}
Primary keyword: "{topic} notes {board} {class_name}"

CRITICAL CONTENT RULES:
- Write ONLY about the actual academic content of {topic} as defined in the {board} syllabus under "{chapter}". Study the topic name carefully — it tells you exactly what to cover.
- Do NOT add generic "solved examples" with step-by-step math unless {topic} genuinely requires calculations. For descriptive/conceptual topics, use case studies, real-world illustrations, and application-based discussions instead.
- Naturally include search terms: "{topic} notes", "{topic} summary", "{topic} {subject} notes", "{topic} important questions"
- Add student-friendly lines: "Many students find this tricky — but it's actually straightforward.", "Pro tip:", "Common mistake:"
- Name other units/chapters explicitly when cross-referencing

Write comprehensive study notes using EXACTLY this structure:

## {topic} — Overview
[2-3 sentences ONLY. Start with "{topic}" directly. State what it covers, which chapter it belongs to, and why it matters for {board} {class_name} exams. Featured snippet paragraph.]

## Why {topic} Matters
[50-70 words: connect {topic} to real-world relevance, then link to {board} importance. Add: "This is one of the most scoring topics in {subject}."]

## Core Concept
[Formal definition citing {board} curriculum. Then simplified: "In simple terms, {topic} means..." Mention what prerequisite knowledge is needed from earlier topics.]

## Detailed Breakdown
[1500-2500 words. Break {topic} into 5-8 sub-concepts based on what the topic actually covers in the syllabus. Use numbered ### sub-headings. For each sub-concept:
- Explain the concept thoroughly (what, why, how) with 5-8 sentences minimum
- Give real-world applications or relevant examples
- Add informal touches: "You can remember this as...", "A common exam mistake here is..."
- Include diagrams described in text, comparisons, and detailed analysis
Cross-reference other units by name where relevant. Be comprehensive — this is a long-form study resource.]

## Key Points for Revision
[8-10 crisp bullet points — exam-ready, complete statements. Include one "Don't confuse X with Y" point.]

## Exam Corner
[5 questions with model answers directly testing knowledge of {topic}:
- 1× define/identify (1 mark)
- 2× explain/describe (2-3 marks)
- 2× discuss/analyze (5 marks)
Add tips like "Examiners expect you to mention..." for long answers.]

## FAQ
Q1: What is {topic}? (Define in one line)
A1: [Single crisp sentence]
Q2: What are the main concepts covered in {topic}?
A2: [List 3-4 key concepts]
Q3: How is {topic} different from [related concept in {chapter}]?
A3: [Precise comparison]
Q4: What are common mistakes students make with {topic}?
A4: [2-3 common errors and how to avoid them]

Write for {class_name} students. Every section must contain genuine academic content specific to what {topic} actually covers — no filler, no generic examples.""",

        """You are a senior {board} examiner and {subject} faculty.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}
Primary keyword: "{topic} notes {board} {class_name}"

CRITICAL CONTENT RULES:
- Focus EXCLUSIVELY on the academic content of {topic} as covered in the {board} {subject} syllabus under "{chapter}". The topic title tells you exactly what to write about.
- Do NOT force mathematical "solved examples" or "step-by-step problems" unless the topic involves actual problem-solving. For theory-based topics, use illustrative examples, case studies, comparisons, or exam-style descriptive answers instead.
- Include keyword variations: "{topic} notes", "{topic} summary", "{topic} {subject} notes {class_name}"
- Human signals: "This is arguably the most important concept in {chapter}.", "Here's what most toppers do...", "Don't skip this section."
- Name all cross-referenced units/chapters explicitly

Create study notes from an examiner's perspective using EXACTLY this structure:

## {topic} — Quick Summary
[2-3 sentences ONLY. Direct definition starting with "{topic}". State chapter, subject, board context. Featured-snippet optimized.]

## At a Glance
- **Topic**: {topic}
- **Chapter**: {chapter}
- **Subject**: {subject} ({board} {class_name})
- **Exam Weight**: [estimated marks based on topic importance]
- **Key Concepts**: [list 3-4 main concepts covered]
- **Related Topics**: [name 2-3 from same/nearby chapters]

## The Basics
[Academic definition with textbook citation. Then plain-English: "In simple terms..." Note prerequisite topics by name.]

## In-Depth Analysis
[1500-2500 words. Cover every important concept within {topic} thoroughly. Use cause-and-effect or thematic flow — NOT bullet lists. Break into ### sub-headings. Include:
- Deep explanations of each sub-concept within {topic} (5-8 sentences per concept)
- Cross-references to other chapters BY NAME
- Real-world applications or case studies
- Detailed comparisons, classifications, and analytical breakdowns
- "Many students confuse this with... — here's how to tell them apart."
- "A helpful way to remember this is..."
Be comprehensive and thorough — this is a long-form study resource.]

## Common Exam Patterns
[How {board} examiners frame questions on {topic}. What aspects they test most. What traps to watch for. What earns full marks. Add: "Pro tip: Always mention [X] in your answer — it's worth 1 extra mark."]

## Exam Questions with Model Answers
Q1 (1 mark): Define {topic}. → [1-line answer]
Q2 (2 marks): [Explain/describe question on {topic}] → [Complete answer]
Q3 (3 marks): [Compare/distinguish question] → [Structured answer]
Q4 (5 marks): [Discuss/analyze question on {topic}] → [Detailed answer with marking scheme points]

## Memory Aids
[2-3 mnemonics or tricks specific to {topic}. Format as memorable phrases students can actually use in exams.]

## Quick Revision Points
[7-10 bullet points covering everything a student must know about {topic} the night before the {board} exam. Each should be a complete statement. Include: "If you remember nothing else, remember THIS: ..."]

Tone: authoritative but warm. Every section must contain high-quality academic content specific to {topic} — no filler or generic content.""",
    ],

    "definition": [
        """You are an expert {board} teacher for {class_name}.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Write a definition article using EXACTLY this structure:

## Summary
[40-60 words: what {topic} means within the chapter "{chapter}" of {board} {class_name} {subject}, its significance, and when students encounter it in {board} exams]

## Definition of {topic}
[Precise, exam-ready academic definition in 2-3 sentences citing the {board}-prescribed textbook for {subject}]

## Meaning and Explanation
[Explain in simple terms — what it means, why it matters in the context of chapter "{chapter}", and how it connects to the {board} syllabus]

## Characteristics / Properties
[4-6 key characteristics or properties as a bullet list]

## Real-World Examples
[3-4 relatable real-world examples applicable to {topic}]

## Related Concepts
[3-4 related topics from the same chapter "{chapter}" or nearby chapters in {board} {class_name} {subject} syllabus, with brief explanation of each connection]

## Exam Questions on This Definition
[3 commonly asked questions in {board} exams with concise model answers matching {board} mark allocation pattern]

Keep language simple for {class_name} students.""",

        """You are a {subject} lexicographer writing for {board} {class_name} students.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create a thorough definition guide. Use EXACTLY this structure:

## In One Line
[Single crisp sentence: "{topic} is..." — suitable for a 1-mark {board} exam answer]

## Formal Definition
[Academic definition as it would appear in the {board}-prescribed {subject} textbook for {class_name}. 2-3 sentences]

## What It Really Means
[Explain like you're talking to a friend — use an analogy or everyday example to make it click. 60-100 words. Mention its role within chapter "{chapter}"]

## Key Features
[5-6 distinguishing characteristics, each as "Feature: Explanation" pairs]

## How It Connects
[Show how {topic} relates to 3-4 other concepts in chapter "{chapter}" and the broader {board} {class_name} {subject} syllabus. Use a brief sentence per connection]

## See It in Action
[2-3 concrete examples or scenarios where {topic} applies]

## Exam-Ready Answers
[Model answers for 3 likely {board} exam questions:
- 1-mark: Define {topic}. → [answer]
- 2-mark: Explain {topic} with an example. → [answer]
- 5-mark: Discuss {topic} in detail with reference to {chapter}. → [answer]]

Language: clear and exam-focused for {class_name} students.""",
    ],

    "important-questions": [
        """You are an expert {board} teacher for {class_name}.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create a question bank using EXACTLY this structure:

## Summary
[40-60 words: overview of {topic} within chapter "{chapter}" and which types of questions appear in {board} exams, with typical mark allocation]

## 1-Mark Questions
[5 questions with one-line answers — test basic recall of {topic} as per {board} syllabus]

## 2-Mark Questions
[5 questions with 2-3 sentence answers — test understanding. Include questions that connect {topic} to other concepts in "{chapter}"]

## 3-Mark Questions
[4 questions with structured answers — test application with real-world context]

## 5-Mark Questions (Long Answer)
[3 questions with detailed, exam-ready answers — test analysis. Include marking scheme breakdown showing how {board} examiners allocate marks]

## Exam-Style Questions (Board Pattern)
[4-5 questions commonly tested in {board} exams on {topic}, with complete answers following {board} marking conventions]

All answers must follow {board} marking scheme. Use exam-standard language.""",

        """You are a {board} paper-setter for {class_name} {subject}.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create an exam-focused question bank. Use EXACTLY this structure:

## What Examiners Ask About {topic}
[50-60 words: which aspects of {topic} (from chapter "{chapter}") are tested most often in {board} exams, what question formats appear, and typical mark distributions]

## Very Short Answer (1 mark each)
[6 questions — each needs only 1-2 sentences. Mix: 3 definition-based, 2 factual, 1 true/false with correction. Aligned with {board} paper Section A pattern]

## Short Answer (2-3 marks each)
[5 questions with answers. Include "why" and "how" questions connecting {topic} to other concepts in "{chapter}". Show expected word count per answer]

## Long Answer (5 marks each)
[3 questions with complete structured answers. Each answer should have sub-points matching {board} marking scheme. Include connections to related topics in the syllabus]

## Commonly Tested Questions
[4 questions frequently tested in {board} exams on {topic}, with model answers]

## Tricky / Higher-Order Questions
[2 application or analysis questions that go beyond textbook recall — test deeper understanding of {topic} within the {subject} syllabus]

Answers must match {board} marking scheme expectations.""",
    ],

    "mcqs": [
        """You are an expert {board} teacher for {class_name}.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create 15 MCQs using EXACTLY this structure:

## Summary
[40-60 words: what {topic} concepts (from chapter "{chapter}") these MCQs test, aligned with {board} exam pattern and mark allocation]

## Easy Level (MCQs 1-5)
[Test basic recall and definitions from {topic} as covered in {board} {class_name} {subject} syllabus — each with 4 options A/B/C/D, correct answer, brief explanation]

## Medium Level (MCQs 6-10)
[Test understanding and application — each with 4 options, correct answer, explanation. Include questions that connect {topic} to other concepts in chapter "{chapter}"]

## Hard Level (MCQs 11-15)
[Test analysis and problem-solving at {board} exam difficulty — each with 4 options, correct answer, detailed explanation]

Format each MCQ as:
Q: [question]
A) B) C) D)
Answer: [letter]
Explanation: [1-2 sentences referencing {board} syllabus concepts]

Match {board} exam pattern and difficulty level.""",

        """You are a competitive exam coach preparing {board} {class_name} students.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create 15 MCQs that test different cognitive levels for {topic} from chapter "{chapter}". Use EXACTLY this structure:

## About These Questions
[40-60 words: which specific concepts within {topic} (chapter: {chapter}) are tested and at what difficulty levels in {board} exams]

## Recall & Recognition (Q1-Q5)
[5 MCQs testing definitions, facts, and direct textbook knowledge from the {board}-prescribed {subject} textbook. Each: question, 4 options (A-D), correct answer, 1-sentence explanation]

## Understanding & Application (Q6-Q10)
[5 MCQs requiring students to apply {topic} concepts or interpret scenarios. Include at least 1 assertion-reason question. Use relevant real-world examples]

## Analysis & Evaluation (Q11-Q15)
[5 MCQs involving multi-step reasoning, comparison, or error identification. Include 1 "which of the following is INCORRECT" type. Test connections to related topics in "{chapter}"]

Format:
**Q[n].** [question text]
(a) ... (b) ... (c) ... (d) ...
**Ans:** [letter] — [explanation]

All questions aligned with {board} {class_name} exam standards.""",
    ],

    "examples": [
        """You are an expert {board} teacher for {class_name}.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create a solved examples guide using EXACTLY this structure:

## Summary
[40-60 words: what types of problems on {topic} (chapter: "{chapter}") appear in {board} exams, what skills they test, and typical mark allocation]

## Basic Examples
Example 1: [Problem statement relevant to {board} syllabus] → [Complete step-by-step solution]
Example 2: [Problem statement] → [Complete step-by-step solution]
Example 3: [Problem using relevant real-world context] → [Complete step-by-step solution]

## Intermediate Examples
Example 4: [Problem connecting {topic} to related concepts in "{chapter}"] → [Complete step-by-step solution]
Example 5: [Problem statement at {board} exam difficulty] → [Complete step-by-step solution]

## Exam-Level Examples
Example 6: [Problem matching {board} exam difficulty and paper pattern] → [Complete solution with all steps and mark allocation]
Example 7: [Problem matching {board} exam difficulty] → [Complete solution with all steps]

## Practice Problems (Try Yourself)
[5 unsolved problems with answers only — graded by {board} mark values (1-mark, 2-mark, 5-mark)]

Show complete working for all solved examples. Use {board} exam-standard notation and methods.""",

        """You are a {subject} tutor known for making problem-solving easy for {board} {class_name} students.

Topic: {topic}
Subject: {subject} | Chapter: {chapter} | Class: {class_name} | Board: {board}

Create a solved examples collection for {topic} from chapter "{chapter}". Use EXACTLY this structure:

## What to Expect
[40-60 words: the types of {topic} problems in {board} exams, marks distribution matching {board} paper pattern, and which formulas/rules from the {board} syllabus are needed]

## Foundation Examples (Warm-Up)
[3 examples. For each: state the problem, identify the approach from the {board} textbook, then solve step by step. Highlight the formula or rule used]

## Board-Exam Standard Examples
[3 examples at {board} exam difficulty. For each: problem statement, "Approach" paragraph explaining strategy, then detailed solution with all intermediate steps shown]

## Challenge Problems
[2 examples slightly above exam level — to build confidence. Full solutions provided. Connect to other concepts in chapter "{chapter}"]

## Common Mistakes to Avoid
[3-4 typical errors {board} {class_name} students make when solving {topic} problems, with the correct approach shown]

## Self-Test
[4 unsolved problems graded by {board} mark values (★ 1-mark, ★★ 2-3 marks, ★★★ 5 marks), with final answers provided]

Use {board}-standard notation. Show every step — never skip working.""",
    ],
}

PROMPTS = {k: v[0] for k, v in PROMPT_VARIANTS.items()}


TITLE_TEMPLATES = {
    "notes": [
        "{topic} Notes for {board} {subject} ({grade}) – Complete Guide",
        "{topic} Notes {board} {grade} {subject} | Summary & Key Points",
        "{topic} {subject} Notes – {board} {grade}",
        "{topic} Study Notes for {board} {grade} {subject} Exam Prep",
    ],
    "definition": [
        "What is {topic}? Definition for {board} {grade} {subject}",
        "{topic} Definition & Meaning – {board} {grade} {subject} Notes",
        "Define {topic} – {subject} {board} {grade} (1-Mark Answer)",
    ],
    "important-questions": [
        "{topic} Important Questions – {board} {grade} {subject} Exam",
        "{topic} Questions & Answers for {board} {grade} | 1 to 5 Marks",
        "{board} {grade} {topic} Important Questions with Answers | {subject}",
        "{topic} Question Bank – {subject} {board} {grade}",
    ],
    "mcqs": [
        "{topic} MCQs with Answers – {board} {grade} {subject}",
        "{topic} MCQ Practice for {board} {grade} Exam | {subject}",
        "{topic} Multiple Choice Questions – {grade} {board} {subject}",
    ],
    "examples": [
        "{topic} Solved Examples – {board} {grade} {subject}",
        "{topic} Problems with Solutions for {board} {grade} Exams",
        "{topic} Step-by-Step Solutions | {grade} {board} {subject}",
    ],
}


def _strip_markdown_symbols(text: str) -> str:
    """Remove all markdown symbols from text for clean meta descriptions."""
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}', '', text)
    text = re.sub(r'_{1,3}', '', text)
    text = re.sub(r'`{1,3}', '', text)
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _normalize_headings(content: str) -> str:
    """Normalize malformed markdown headings from LLM output.
    Fixes: **## H**, ## **H**, ### ## H, ---\\n## H, and missing spaces.
    Also converts standalone bold lines (like **Topic Name**) to ## headings."""
    content = re.sub(r'\*{1,3}(#{1,6})\s*(.+?)\*{1,3}', r'\1 \2', content, flags=re.MULTILINE)
    content = re.sub(r'^(#{1,6})\s*\*{1,3}(.+?)\*{1,3}\s*$', r'\1 \2', content, flags=re.MULTILINE)
    content = re.sub(r'^#{1,6}\s+#{1,6}\s+', '## ', content, flags=re.MULTILINE)
    content = re.sub(r'^(#{1,6})([^\s#])', r'\1 \2', content, flags=re.MULTILINE)
    content = re.sub(r'^---+\s*\n(#{1,6}\s)', r'\1', content, flags=re.MULTILINE)
    _skip = {'summary', 'introduction', 'key points', 'key facts', 'common mistake',
             'exam tip', 'important questions', 'conclusion', 'note', 'example'}
    lines = content.split('\n')
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r'^\*{2,4}(.+?)\*{2,4}$', stripped)
        if m and not stripped.startswith('#'):
            heading_text = m.group(1).strip().strip('*').strip()
            normalised_check = re.sub(r'[:\-.\s]+$', '', heading_text).lower()
            is_short_acronym = len(heading_text) <= 3 and heading_text.replace(' ', '').isalnum()
            if ((len(heading_text) >= 4 or is_short_acronym)
                    and len(heading_text) <= 120
                    and normalised_check not in _skip
                    and not heading_text.startswith('-')
                    and not heading_text.startswith('*')):
                prev_blank = (i == 0 or not lines[i - 1].strip())
                next_is_text = (i + 1 < len(lines) and lines[i + 1].strip()
                                and not lines[i + 1].strip().startswith('#')
                                and not lines[i + 1].strip().startswith('**'))
                if prev_blank or next_is_text:
                    result.append(f'## {heading_text}')
                    continue
        result.append(line)
    return '\n'.join(result)


def _clamp_meta_description(text: str, min_len: int = 140, max_len: int = 160) -> str:
    """Clamp text to 140-160 characters, trimming at word boundary or returning as-is if short."""
    if len(text) > max_len:
        text = text[:155].rsplit(' ', 1)[0] + '...'
    return text


def _extract_summary_from_content(content: str) -> str | None:
    """Extract the Summary section from generated markdown content.
    Tries known heading patterns first, then falls back to first paragraph.
    Returns a clean sentence of 140-160 characters with no markdown symbols."""
    candidates = []

    match = re.search(
        r'##\s*(?:Summary|At a Glance|In One Line|Why .+ Matters|What to Expect|'
        r'About These Questions|What Examiners Ask[^\n]*|'
        r'What is .+\?[^\n]*|.+ (?:—|–) Overview|.+ (?:—|–) Quick Summary)\s*\n+(.*?)(?:\n##|\Z)',
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        text = _strip_markdown_symbols(match.group(1).strip())
        if len(text) >= 30:
            candidates.append(text)

    lines = content.split('\n')
    current_para = []
    for line in lines + ['']:
        stripped = line.strip()
        if not stripped:
            if current_para:
                para_text = ' '.join(current_para)
                clean = _strip_markdown_symbols(para_text)
                if len(clean) >= 40:
                    candidates.append(clean)
                current_para = []
        elif stripped.startswith('#') or stripped.startswith('['):
            if current_para:
                para_text = ' '.join(current_para)
                clean = _strip_markdown_symbols(para_text)
                if len(clean) >= 40:
                    candidates.append(clean)
                current_para = []
        else:
            current_para.append(stripped)

    if not candidates:
        return None

    best = None
    for c in candidates:
        clamped = _clamp_meta_description(c)
        if 140 <= len(clamped) <= 160:
            return clamped
        if best is None or abs(len(clamped) - 150) < abs(len(best) - 150):
            best = clamped

    if best and len(best) < 140 and len(candidates) > 1:
        combined = ' '.join(candidates)
        combined = re.sub(r'\s+', ' ', combined).strip()
        return _clamp_meta_description(combined)

    return best


REQUIRED_SECTIONS = {
    "notes": ["explanation", "example", "key point", "revision", "faq", "exam"],
    "definition": ["definition", "meaning", "example"],
    "important-questions": ["1-mark", "2-mark", "5-mark", "long answer", "short answer"],
    "mcqs": ["easy", "medium", "hard", "answer", "explanation"],
    "examples": ["example", "solution", "step", "practice"],
}


_QUALITY_PUBLISH_THRESHOLD = 90

def _compute_quality_score(content: str, page_type: str, context: dict | None = None) -> dict:
    """Compute content quality indicators for a generated page.

    ``context`` is an optional dict with keys like board_name, subject_name,
    chapter_title used to verify curriculum anchoring.
    """
    words = content.split()
    word_count = len(words)
    headings = re.findall(r'^#{1,4}\s+.+', content, re.MULTILINE)
    heading_count = len(headings)
    content_lower = content.lower()

    has_faq = bool(re.search(r'#{2,4}\s*(FAQ|Frequently Asked|Common Question)', content, re.IGNORECASE))
    has_exam_q = bool(re.search(r'#{2,4}\s*(Exam.Style|Commonly Tested|Board Pattern|Previous Year|PYQ|Frequently Repeated|Board Question|Exam Question|Exam.Ready|Practice Question)', content, re.IGNORECASE))
    has_examples = bool(re.search(r'(Example\s*\d|#{2,4}\s*(Example|Solved Example|Illustration|Worked Example|Important Concepts|Applications|Case Stud|In.Depth Analysis))', content, re.IGNORECASE))
    has_key_points = bool(re.search(r'#{2,4}\s*(Key Point|Key Takeaway|Important Point|Revision|Summary|Points to Remember|Quick Recap|At a Glance)', content, re.IGNORECASE))
    has_tips = bool(re.search(r'(exam tip|revision tip|remember|important note|pro tip|study tip|scoring tip)', content_lower))
    has_citations = bool(re.search(r'(syllabus|NCERT|SCERT|textbook|curriculum|prescribed|as per the)', content, re.IGNORECASE))
    has_marks_ref = bool(re.search(r'(\d\s*-?\s*marks?\b|short answer|long answer|objective type|very short)', content, re.IGNORECASE))

    unique_words = set(w.lower() for w in words if len(w) > 3)
    unique_ratio = round(len(unique_words) / max(word_count, 1), 3)

    required = REQUIRED_SECTIONS.get(page_type, [])
    sections_present = sum(1 for s in required if s in content_lower)
    sections_ratio = round(sections_present / max(len(required), 1), 2)

    anchored = False
    if context:
        anchor_terms = [
            v.lower() for k in ("board_name", "subject_name", "chapter_title")
            if (v := context.get(k)) and len(v) > 2
        ]
        anchored = any(t in content_lower for t in anchor_terms) if anchor_terms else True
    else:
        anchored = True

    score = 0
    if word_count >= 3000: score += 18
    elif word_count >= 2000: score += 15
    elif word_count >= 1500: score += 12
    elif word_count >= 800: score += 8
    elif word_count >= 400: score += 4
    if heading_count >= 8: score += 12
    elif heading_count >= 6: score += 9
    elif heading_count >= 4: score += 5
    if unique_ratio >= 0.35: score += 8
    elif unique_ratio >= 0.28: score += 4
    if sections_ratio >= 0.8: score += 8
    elif sections_ratio >= 0.5: score += 4
    if has_faq: score += 10
    if has_exam_q: score += 10
    if has_examples: score += 8
    if has_key_points: score += 5
    if has_tips: score += 5
    if anchored: score += 4
    if has_citations: score += 6
    if has_marks_ref: score += 6

    return {
        "word_count": word_count,
        "heading_count": heading_count,
        "unique_ratio": unique_ratio,
        "sections_ratio": sections_ratio,
        "has_faq": has_faq,
        "has_exam_q": has_exam_q,
        "has_examples": has_examples,
        "has_key_points": has_key_points,
        "has_tips": has_tips,
        "anchored": anchored,
        "has_citations": has_citations,
        "has_marks_ref": has_marks_ref,
        "score": min(score, 100),
    }


# ─── Task #457: GEO (Generative Engine Optimisation) scoring ──────────────────

_ANSWER_SUMMARY_MIN_WORDS = 40
_ANSWER_SUMMARY_MAX_WORDS = 80


def _extract_answer_summary(content: str, topic_title: str = "") -> str:
    """Extract / synthesise an answer-first summary (~40-60 words).

    AI engines (Perplexity, Google AI Overviews, ChatGPT browsing) reward
    pages that lead with a direct, self-contained answer. We pull the first
    substantive paragraph after any leading H1, trim it to a clean sentence
    boundary inside the target window. If the page has no usable lead
    paragraph we synthesise a minimal answer from the title.
    """
    if not content:
        return f"{topic_title}: detailed explanation, examples, and exam-ready notes for students." if topic_title else ""
    body = re.sub(r'^#\s+.+\n+', '', content.lstrip(), count=1)
    body = re.sub(r'^\s*[\-*>]\s+', '', body, flags=re.MULTILINE)
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    lead = ""
    for p in paragraphs:
        if p.startswith('#') or p.startswith('|') or p.startswith('```'):
            continue
        cleaned = re.sub(r'\s+', ' ', re.sub(r'[*_`]+', '', p)).strip()
        if len(cleaned.split()) >= 15:
            lead = cleaned
            break
    if not lead:
        return f"{topic_title}: a concise study guide with definition, key points, examples and exam questions." if topic_title else ""
    words = lead.split()
    if len(words) > _ANSWER_SUMMARY_MAX_WORDS:
        truncated = " ".join(words[:_ANSWER_SUMMARY_MAX_WORDS])
        last = max(truncated.rfind('. '), truncated.rfind('? '), truncated.rfind('! '))
        if last > 80:
            return truncated[: last + 1].strip()
        return truncated.rstrip(',;:') + "."
    return lead


def _extract_key_facts(content: str, limit: int = 6) -> list[str]:
    """Pull short, citable facts from the page for an at-a-glance KEY FACTS block.

    Looks for bullet items under headings like "Key Points", "Quick Recap",
    "At a Glance", "Important Points". Falls back to the first short sentences
    of the page.
    """
    facts: list[str] = []
    if not content:
        return facts
    sect_re = re.compile(
        r'#{2,4}\s*(?:Key Point|Key Takeaway|Important Point|Quick Recap|Points to Remember|At a Glance|Key Fact|Summary)s?[^\n]*\n+([\s\S]*?)(?=\n#{1,4}\s|\Z)',
        re.IGNORECASE,
    )
    for m in sect_re.finditer(content):
        block = m.group(1)
        for ln in block.splitlines():
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            s = re.sub(r'^[\-*>•\d\.\)]+\s*', '', s)
            s = re.sub(r'[*_`]+', '', s).strip()
            if 4 <= len(s.split()) <= 30:
                facts.append(s)
                if len(facts) >= limit:
                    return facts
    if facts:
        return facts
    sents = re.split(r'(?<=[\.!?])\s+', re.sub(r'\s+', ' ', re.sub(r'[*_`#>]+', '', content)))
    for s in sents:
        s = s.strip()
        if 6 <= len(s.split()) <= 24 and not s.startswith('|'):
            facts.append(s)
            if len(facts) >= limit:
                break
    return facts


def _compute_geo_score(content: str, page_type: str, context: dict | None = None) -> dict:
    """Compute Generative Engine Optimisation score (0-100).

    Rewards: answer-first summary in target word window, key-facts block,
    inline citations / source mentions, freshness signal (year mention),
    explicit author / reviewer attribution, structured-data friendly cues
    (numbered Q&A, definition lists), and topic anchoring.
    """
    if not content:
        return {"score": 0}
    summary = _extract_answer_summary(content, (context or {}).get("topic_title", ""))
    summary_words = len(summary.split()) if summary else 0
    facts = _extract_key_facts(content)
    has_summary = _ANSWER_SUMMARY_MIN_WORDS <= summary_words <= (_ANSWER_SUMMARY_MAX_WORDS + 10)
    has_facts_block = len(facts) >= 3

    citations = len(re.findall(
        r'(NCERT|SCERT|syllabus|prescribed textbook|board guideline|Class\s*\d+\s+textbook|chapter\s+\d+)',
        content, re.IGNORECASE,
    ))
    current_year = datetime.now(timezone.utc).year
    has_freshness = bool(re.search(rf'\b({current_year}|{current_year-1})\b', content))
    has_attribution = bool(re.search(
        r'(Reviewed by|Written by|Author:|Reviewer:|Last updated|Last reviewed|Updated on)',
        content, re.IGNORECASE,
    ))
    has_qa_pairs = bool(re.search(
        r'(?:^|\n)(?:Q[:\.\)]|Question\s*\d+|\*\*Q\d*[:\.])',
        content, re.IGNORECASE | re.MULTILINE,
    ))
    has_definition = bool(re.search(
        r'#{2,4}\s*(Definition|What is|Meaning of|In simple words)',
        content, re.IGNORECASE,
    ))
    has_anchor = False
    if context:
        for k in ("subject_name", "chapter_title", "topic_title"):
            v = (context.get(k) or "").lower()
            if v and len(v) > 3 and v in content.lower():
                has_anchor = True
                break
    else:
        has_anchor = True

    score = 0
    if has_summary: score += 22
    elif summary_words >= 25: score += 12
    score += min(len(facts), 6) * 4              # up to 24
    score += min(citations, 4) * 4               # up to 16
    if has_freshness: score += 8
    if has_attribution: score += 8
    if has_qa_pairs: score += 8
    if has_definition: score += 6
    if has_anchor: score += 8

    missing: list[str] = []
    if not has_summary: missing.append("answer_summary")
    if not has_facts_block: missing.append("key_facts")
    if citations == 0: missing.append("citations")
    if not has_freshness: missing.append("freshness_year")
    if not has_attribution: missing.append("attribution")
    if not has_qa_pairs and page_type == "important-questions":
        missing.append("qa_pairs")
    if not has_anchor: missing.append("topic_anchor")

    return {
        "score": min(score, 100),
        "answer_summary": summary,
        "answer_summary_words": summary_words,
        "key_facts": facts,
        "key_facts_count": len(facts),
        "citations": citations,
        "has_freshness": has_freshness,
        "has_attribution": has_attribution,
        "has_qa_pairs": has_qa_pairs,
        "has_definition": has_definition,
        "anchored": has_anchor,
        "missing": missing,
    }


def _combined_quality_score(seo: dict, geo: dict) -> int:
    """Weighted blend of SEO and GEO sub-scores (70/30)."""
    s = int(seo.get("score", 0) or 0)
    g = int(geo.get("score", 0) or 0)
    return int(round(0.7 * s + 0.3 * g))


class TopicCreate(BaseModel):
    chapter_id: str
    title: str
    definition: Optional[str] = ""
    examples: Optional[str] = ""
    order: Optional[int] = 0

class TopicUpdate(BaseModel):
    title: Optional[str] = None
    definition: Optional[str] = None
    examples: Optional[str] = None
    order: Optional[int] = None
    status: Optional[str] = None

class GenerateRequest(BaseModel):
    topic_id: Optional[str] = None
    topic_ids: Optional[List[str]] = None
    page_types: Optional[List[str]] = None
    batch: Optional[bool] = False
    only_published: Optional[bool] = False


class PageTypesRequest(BaseModel):
    page_types: Optional[List[str]] = None


async def _resolve_hierarchy(topic: dict) -> dict:
    """Resolve full curriculum hierarchy for a topic.

    Task #457: lenient resolution. Historically this returned ``{}`` if
    the stream/class/board chain was incomplete, which silently dropped
    every page in ``_auto_run_bg``. We now require only chapter+subject
    and synthesise sensible fallbacks (board='DEGREE', class='General')
    when the upstream chain is missing, so the existing 991 topics
    actually generate pages. Slugs prefer the upstream docs but fall
    back to whatever the subject/topic doc itself carries.
    """
    if _db is None:
        return {}
    chapter = await _db.chapters.find_one({"id": topic.get("chapter_id", "")}, {"_id": 0})
    if not chapter:
        return {}
    subject = await _db.subjects.find_one({"id": chapter.get("subject_id", "")}, {"_id": 0})
    if not subject:
        # Without a subject we cannot build URLs; this is a real blocker.
        return {}
    stream = None
    cls = None
    board = None
    if subject.get("stream_id"):
        stream = await _db.streams.find_one({"id": subject.get("stream_id", "")}, {"_id": 0})
    if stream and stream.get("class_id"):
        cls = await _db.classes.find_one({"id": stream.get("class_id", "")}, {"_id": 0})
    if cls and cls.get("board_id"):
        board = await _db.boards.find_one({"id": cls.get("board_id", "")}, {"_id": 0})

    # Fallback objects so downstream code can keep using `.get("name")`
    # without exploding when the upstream chain is incomplete.
    fallback_board_name = (
        topic.get("board_name")
        or subject.get("board_name")
        or chapter.get("board_name")
        or "DEGREE"
    )
    fallback_class_name = (
        topic.get("class_name")
        or subject.get("class_name")
        or chapter.get("class_name")
        or "General"
    )
    if board is None:
        board = {
            "name": fallback_board_name,
            "slug": subject.get("board_slug") or _slug(fallback_board_name),
        }
    if cls is None:
        cls = {
            "name": fallback_class_name,
            "slug": subject.get("class_slug") or _slug(fallback_class_name),
        }
    if stream is None:
        stream = {
            "name": subject.get("stream_name", ""),
            "slug": subject.get("stream_slug", ""),
        }

    return {
        "chapter": chapter,
        "subject": subject,
        "stream": stream,
        "class": cls,
        "board": board,
        "board_slug": board.get("slug", "") or subject.get("board_slug", ""),
        "class_slug": cls.get("slug", "") or subject.get("class_slug", ""),
        "stream_slug": stream.get("slug", "") or subject.get("stream_slug", ""),
        "subject_slug": subject.get("slug", ""),
        "chapter_slug": chapter.get("slug") or _slug(chapter.get("title", "")),
    }


# ─── ADMIN: Topic CRUD ──────────────────────────────────────────────────────

@router.get("/topics/{board_slug}/{class_slug}/{subject_slug}")
async def list_topics_public(board_slug: str, class_slug: str, subject_slug: str):
    import re as _re
    board = await _db.boards.find_one({"slug": board_slug}, {"_id": 0})
    if not board: return []
    cls = await _db.classes.find_one({"slug": class_slug, "board_id": board["id"]}, {"_id": 0})
    if not cls: return []
    streams = await _db.streams.find({"class_id": cls["id"]}, {"_id": 0}).to_list(100)
    stream_ids = [s["id"] for s in streams]
    subj = await _db.subjects.find_one({"slug": subject_slug, "stream_id": {"$in": stream_ids}, "status": "published"}, {"_id": 0})
    if not subj: return []
    chapters = await _db.chapters.find({"subject_id": subj["id"]}, {"_id": 0}).to_list(200)
    ch_map = {}
    for ch in chapters:
        ch_slug = ch.get("slug") or _re.sub(r'[^a-z0-9]+', '-', ch.get("title", "").lower()).strip('-')
        ch_map[ch["id"]] = {"slug": ch_slug, "title": ch.get("title", "")}
    ch_ids = list(ch_map.keys())
    if not ch_ids: return []
    topics = await _db.topics.find({"chapter_id": {"$in": ch_ids}, "status": "published"}, {"_id": 0}).sort("order", 1).to_list(5000)
    topic_ids = [t["id"] for t in topics]
    published_pages = await _db.seo_pages.find(
        {"topic_id": {"$in": topic_ids}, "status": "published"},
        {"_id": 0, "topic_id": 1}
    ).to_list(50000)
    topics_with_pages = {p["topic_id"] for p in published_pages}

    matched = []
    for t in topics:
        if t["id"] not in topics_with_pages:
            continue
        ch_info = ch_map.get(t.get("chapter_id"), {})
        matched.append({
            "id": t["id"], "title": t.get("title", ""), "topic_slug": t.get("slug", ""),
            "chapter_slug": ch_info.get("slug", ""), "chapter_title": ch_info.get("title", ""),
            "order": t.get("order", 0),
        })
    return matched

@router.get("/topics")
async def list_topics(chapter_id: Optional[str] = None, _admin: dict = Depends(_require_admin)):
    query = {"chapter_id": chapter_id} if chapter_id else {}
    topics = await _db.topics.find(query, {"_id": 0}).sort("order", 1).to_list(1000)
    return topics


@router.post("/topics")
async def create_topic(data: TopicCreate, _admin: dict = Depends(_require_admin)):
    chapter = await _db.chapters.find_one({"id": data.chapter_id}, {"_id": 0})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topic = {
        "id": f"topic-{uuid.uuid4().hex[:8]}",
        "chapter_id": data.chapter_id,
        "subject_id": chapter.get("subject_id", ""),
        "title": data.title,
        "slug": _slug(data.title),
        "definition": data.definition or "",
        "examples": data.examples or "",
        "order": data.order or 0,
        "status": "published",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _db.topics.insert_one(topic)
    topic.pop("_id", None)
    return topic


@router.patch("/topics/{topic_id}")
async def update_topic(topic_id: str, data: TopicUpdate, _admin: dict = Depends(_require_admin)):
    updates = {k: v for k, v in data.dict().items() if v is not None}
    if "title" in updates:
        updates["slug"] = _slug(updates["title"])
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await _db.topics.update_one({"id": topic_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Topic not found")
    return {"message": "Updated"}


@router.delete("/topics/{topic_id}")
async def delete_topic(topic_id: str, _admin: dict = Depends(_require_admin)):
    await _db.topics.delete_one({"id": topic_id})
    await _db.seo_pages.delete_many({"topic_id": topic_id})
    return {"message": "Deleted"}


# ─── ADMIN: Auto-extract topics from chapters (AI-powered) ──────────────────

@router.post("/extract-topics")
async def extract_topics_from_chapters(
    subject_id: Optional[str] = None,
    force: bool = False,
    _admin: dict = Depends(_require_admin),
):
    """
    AI-powered topic extraction pipeline.

    For each chapter that has content text the LLM reads the content and
    returns 3-10 granular study topics (the real sub-headings students search
    for, NOT just the chapter title). For chapters with no content the chapter
    title is stored as a single fallback topic.

    subject_id — scope to one subject; omit to process everything.
    force      — re-extract even if topics already exist for a chapter.
    """
    query = {"subject_id": subject_id} if subject_id else {}
    chapters = await _db.chapters.find(query, {"_id": 0}).to_list(500)

    created = 0
    skipped = 0
    errors  = 0

    for ch in chapters:
        existing = await _db.topics.count_documents({"chapter_id": ch["id"]})
        if existing > 0 and not force:
            skipped += 1
            continue

        title   = ch.get("title", "")
        content = ch.get("content", "") or ""
        if not title:
            continue

        # ── AI extraction from chapter content ───────────────────────────
        topic_titles: list[str] = []

        subject_name = ch.get("subject_name", "")
        board_name = ch.get("board_name", "")
        if not board_name:
            try:
                _subj = await _db.subjects.find_one({"id": ch.get("subject_id", "")}, {"_id": 0, "stream_id": 1, "name": 1})
                if _subj:
                    if not subject_name:
                        subject_name = _subj.get("name", "")
                    _strm = await _db.streams.find_one({"id": _subj.get("stream_id", "")}, {"_id": 0, "class_id": 1}) if _subj.get("stream_id") else None
                    _cls = await _db.classes.find_one({"id": _strm.get("class_id", "")}, {"_id": 0, "board_id": 1}) if _strm and _strm.get("class_id") else None
                    _brd = await _db.boards.find_one({"id": _cls.get("board_id", "")}, {"_id": 0, "name": 1}) if _cls and _cls.get("board_id") else None
                    board_name = _brd.get("name", "Assamboard") if _brd else "Assamboard"
            except Exception:
                board_name = "Assamboard"

        if _call_llm and len(content.strip()) > 150:
            try:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert SEO strategist for educational content. "
                            "Given a chapter title and its content, extract GRANULAR TOPIC-LEVEL study pages "
                            "that students actively search for on Google. "
                            "Each topic should be a SPECIFIC concept, definition, theory, law, or process "
                            "within the chapter — NOT the chapter title itself and NOT a broad summary. "
                            "Each topic must be a clear, searchable phrase (2-8 words) that can sustain "
                            "3000-5000 words of deep, focused educational content on that ONE topic alone. "
                            "Think of how a textbook breaks a chapter into sub-headings — each sub-heading is a topic. "
                            "Prioritise topics that: (a) students search before exams, "
                            "(b) have clear learning intent, (c) are distinct and non-overlapping, "
                            "(d) each covers a single well-defined concept. "
                            "Return ONLY a valid JSON array of strings, e.g.: "
                            '["Definition of Supply", "Law of Demand Explained", "Types of Market Structure"]. '
                            "Aim for 5-12 distinct topics. Quality over quantity — no thin or overlapping topics."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Chapter: {title}\n"
                            f"Subject: {subject_name}\n"
                            f"Board: {board_name}\n\n"
                            f"Content (first 4000 chars):\n{content[:4000]}"
                        ),
                    },
                ]
                raw = await asyncio.wait_for(
                    _call_llm(messages, max_tokens=512), timeout=30
                )
                topic_titles = _robust_parse_json_array(raw)
            except Exception as exc:
                logger.warning(
                    f"AI topic extraction failed for chapter '{title}': {exc}"
                )
                errors += 1

        # ── Fallback: chapter title as single topic ───────────────────────
        if not topic_titles:
            topic_titles = [title]

        # ── Delete old topics if re-extracting ───────────────────────────
        if force and existing:
            await _db.topics.delete_many({"chapter_id": ch["id"]})

        # ── Persist each extracted topic ──────────────────────────────────
        base_order = ch.get("order_index", ch.get("chapter_number", 0))
        topic_ids_for_chapter = []
        for idx, topic_title in enumerate(topic_titles):
            topic_id = f"topic-{uuid.uuid4().hex[:8]}"
            topic_slug = _slug(topic_title)
            primary_kw = f"{topic_title.lower()} {subject_name.lower()} {board_name}".strip()
            topic = {
                "id":              topic_id,
                "chapter_id":      ch["id"],
                "subject_id":      ch.get("subject_id", ""),
                "chapter_title":   title,
                "subject_name":    subject_name,
                "board_name":      board_name,
                "title":           topic_title,
                "slug":            topic_slug,
                "primary_keyword": primary_kw[:120],
                "search_intent":   "informational",
                "definition":      "",
                "examples":        "",
                "order":           base_order * 100 + idx,
                "status":          "published",
                "created_at":      datetime.now(timezone.utc).isoformat(),
            }
            await _db.topics.insert_one(topic)
            topic_ids_for_chapter.append(topic_id)
            created += 1

        if topic_ids_for_chapter:
            await _db.chapters.update_one(
                {"id": ch["id"]},
                {"$set": {"linked_topic_ids": topic_ids_for_chapter}}
            )

    subject_label = ""
    if subject_id:
        sub = await _db.subjects.find_one({"id": subject_id}, {"_id": 0, "name": 1})
        subject_label = f" for {sub['name']}" if sub else f" for subject {subject_id[:8]}"

    asyncio.create_task(_seo_log(
        action  = "seo:topics_extracted",
        details = (
            f"AI extracted {created} topics from {len(chapters)} chapters"
            f"{subject_label}"
            + (f" · {skipped} already existed" if skipped else "")
            + (f" · {errors} AI errors" if errors else "")
        ),
        level = "info" if errors == 0 else "warn",
    ))

    return {
        "message": (
            f"Extracted {created} topics from {len(chapters)} chapters "
            f"({skipped} already had topics, {errors} AI errors)"
        ),
        "created": created,
        "skipped": skipped,
        "errors":  errors,
        "chapters": len(chapters),
    }


# ─── ADMIN: AI Content Generation ───────────────────────────────────────────

def _smart_grade_label(cn: str, bn: str) -> str:
    """
    Return the human-readable grade string for a given class_name / board_name.

    Rules:
      • class_name already contains "semester" → normalise to "Semester N"
      • DEGREE board → always "Semester N"
      • AHSEC ordinal ("HS 1st Year") → "Class 11", "HS 2nd Year" → "Class 12"
      • SEBA / other school boards → preserve as-is or "Class N"
      • Unknown board with digit → keep class_name unchanged (no blind "Class" prefix)
    """
    bn_up = (bn or "").strip().upper()
    cn_s  = (cn or "").strip()

    if re.search(r'\bsem(ester)?\b', cn_s, re.IGNORECASE):
        m = re.search(r'\d+', cn_s)
        return f"Semester {m.group()}" if m else cn_s

    if bn_up in {"DEGREE", "NEP FYUGP", "FYUGP"}:
        m = re.search(r'\d+', cn_s)
        return f"Semester {m.group()}" if m else cn_s

    ord_m = re.search(r'(\d+)(st|nd|rd|th)\s*year', cn_s, re.IGNORECASE)
    if ord_m:
        n = int(ord_m.group(1))
        return f"Class {10 + n}" if n <= 2 else f"Class {n}"

    if re.search(r'\bclass\s*\d+', cn_s, re.IGNORECASE):
        return cn_s

    m = re.search(r'\d+', cn_s)
    if m:
        if bn_up in {"AHSEC", "SEBA"}:
            return f"Class {m.group()}"
        return cn_s  # unknown board — don't blindly prefix "Class"

    return cn_s or "Class 12"


def _smart_board_display(bn: str) -> str:
    """Return a user-facing board label. 'DEGREE' in DB → 'NEP FYUGP' publicly."""
    _map = {"DEGREE": "NEP FYUGP", "AHSEC": "AHSEC", "SEBA": "SEBA"}
    return _map.get((bn or "").strip().upper(), bn or "AHSEC")


async def _bing_keyword_brief(topic_title: str, subject_name: str = "",
                              board_name: str = "") -> dict:
    """Pull real Bing search-volume data for the topic phrase + a board-qualified
    variant. Combines results so the LLM gets both the broad and exam-specific
    high-impression phrases. Always returns a dict (possibly with empty fields)."""
    if _bwt is None or not _bwt.is_configured() or not topic_title:
        return {"top_related_phrases": [], "total_impressions": 0, "queries_used": []}

    queries = [topic_title.strip()]
    qualified = " ".join(p for p in [topic_title.strip(), subject_name.strip(), board_name.strip()] if p)
    if qualified and qualified.lower() != topic_title.strip().lower():
        queries.append(qualified)

    try:
        briefs = await asyncio.gather(
            *[_bwt.keyword_brief(q, max_related=8) for q in queries],
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning(f"BWT keyword_brief gather failed: {exc}")
        return {"top_related_phrases": [], "total_impressions": 0, "queries_used": queries}

    seen: set[str] = set()
    merged: list[dict] = []
    total = 0
    for b in briefs:
        if isinstance(b, Exception) or not isinstance(b, dict):
            continue
        total += int(b.get("total_impressions", 0))
        for r in b.get("related", []):
            kw = (r.get("query") or "").strip()
            if not kw or kw.lower() in seen:
                continue
            seen.add(kw.lower())
            merged.append(r)

    merged.sort(key=lambda d: d.get("impressions", 0), reverse=True)
    return {
        "top_related_phrases": [m["query"] for m in merged[:8]],
        "total_impressions": total,
        "queries_used": queries,
    }


_BOARD_EXAM_CONTEXT = {
    "AHSEC": (
        "AHSEC conducts the HS Final Exam for Class 11-12. "
        "The exam follows NCERT/SCERT syllabus. Question paper pattern: Section A (1-mark MCQs/very short), "
        "Section B (2-mark short answers), Section C (3-mark answers), Section D (5-mark long answers/essays). "
        "Total marks typically 100 (70 theory + 30 internal)."
    ),
    "SEBA": (
        "SEBA conducts the HSLC exam for Class 10. "
        "Syllabus follows SCERT / NCERT guidelines. Paper pattern: very short (1 mark), short (2-3 marks), "
        "long answer (4-5 marks), and sometimes project/practical components. Total marks typically 100 per subject."
    ),
    "DEGREE": (
        "NEP FYUGP (Four Year Undergraduate Programme) under National Education Policy 2020. "
        "Semester-based assessment with internal (30%) + end-semester (70%). Course types: Major, Minor, MDC (Multi-Disciplinary), "
        "VAC (Value Added), SEC (Skill Enhancement), AEC (Ability Enhancement)."
    ),
}

async def _generate_single_page(topic: dict, page_type: str, hierarchy: dict):
    board_name    = hierarchy.get("board", {}).get("name", "AHSEC")
    class_name    = hierarchy.get("class", {}).get("name", "Class 12")
    subject_name  = hierarchy.get("subject", {}).get("name", "")
    chapter_obj   = hierarchy.get("chapter", {})
    chapter_title = chapter_obj.get("title", "")
    chapter_id    = chapter_obj.get("id", "")
    stream_name   = hierarchy.get("stream", {}).get("name", "")

    grade_str     = _smart_grade_label(class_name, board_name)
    board_display = _smart_board_display(board_name)
    is_degree     = board_name.upper() in {"DEGREE", "NEP FYUGP", "FYUGP"}

    _DEGREE_COURSE_TYPES = {"major", "minor", "mdc", "vac", "sec", "aec"}
    is_degree_stream = stream_name.lower().strip() in _DEGREE_COURSE_TYPES

    if is_degree or is_degree_stream:
        course_type_suffix = f" — {stream_name} Course" if stream_name else ""
        prompt_class_label = f"{grade_str} (NEP FYUGP Degree{course_type_suffix})"
    else:
        prompt_class_label = f"{grade_str} {board_display}".strip()

    sibling_topics = []
    syllabus_position = ""
    if chapter_id and _db is not None:
        siblings = await _db.topics.find(
            {"chapter_id": chapter_id, "status": "published"},
            {"_id": 0, "title": 1, "order": 1, "id": 1}
        ).sort("order", 1).to_list(200)
        sibling_topics = [s.get("title", "") for s in siblings if s.get("title")]
        for idx, s in enumerate(siblings):
            if s.get("id") == topic.get("id"):
                syllabus_position = f"Topic {idx + 1} of {len(siblings)} in Chapter: {chapter_title}"
                break

    sibling_list = ", ".join(sibling_topics[:15]) if sibling_topics else "N/A"

    board_key = board_name.upper()
    if board_key in {"NEP FYUGP", "FYUGP"}:
        board_key = "DEGREE"
    board_exam_context = _BOARD_EXAM_CONTEXT.get(board_key, _BOARD_EXAM_CONTEXT["AHSEC"])

    bwt_brief = await _bing_keyword_brief(topic["title"], subject_name, board_display)
    bwt_phrases = bwt_brief.get("top_related_phrases", [])
    if bwt_phrases:
        bwt_block = (
            f"\n--- REAL SEARCH DEMAND (Bing Webmaster Tools) ---\n"
            f"Students are actually searching these high-impression phrases related "
            f"to \"{topic['title']}\":\n"
            + "\n".join(f"  - {p}" for p in bwt_phrases[:8])
            + "\nUse these phrases naturally inside H2/H3 headings, FAQ questions, "
              "the answer-first opening paragraph, and at least once in the "
              "meta-description-worthy summary. Do NOT keyword-stuff — weave them "
              "into genuine educational sentences.\n"
        )
    else:
        bwt_block = ""

    syllabus_context = (
        f"\n\n--- SYLLABUS CONTEXT ---\n"
        f"Board: {board_display} | Class/Level: {prompt_class_label}\n"
        f"Subject: {subject_name} | Chapter: {chapter_title}\n"
        f"TOPIC (focus of this page): {topic['title']}\n"
        f"Position: {syllabus_position or 'Unknown'}\n"
        f"Sibling topics in this chapter: {sibling_list}\n"
        f"Stream/Course Type: {stream_name or 'General'}\n"
        f"IMPORTANT: Write ONLY about \"{topic['title']}\" — do NOT cover the entire chapter.\n"
        f"{bwt_block}\n"
        f"--- BOARD EXAM PATTERN ---\n"
        f"{board_exam_context}\n\n"
        f"--- END CONTEXT ---\n"
    )

    variants = PROMPT_VARIANTS.get(page_type, [])
    if not variants:
        return None
    variant_idx = _topic_hash(topic["title"], page_type, len(variants))
    prompt_template = variants[variant_idx]

    prompt = prompt_template.format(
        board=board_display,
        class_name=prompt_class_label,
        subject=subject_name,
        chapter=chapter_title,
        topic=topic["title"],
    )

    prompt = prompt + syllabus_context

    _QUALITY_SYSTEM = (
        f"You are an expert {board_display} teacher specialising in {subject_name} "
        f"for {prompt_class_label} students. "
        f"Chapter: \"{chapter_title}\" | Topic: \"{topic['title']}\" | Position: {syllabus_position or 'N/A'}. "
        f"Create educational content that is comprehensive, exam-focused, syllabus-aligned, "
        f"and easy to understand. This page covers ONE SPECIFIC TOPIC: \"{topic['title']}\" "
        f"— write ONLY about this topic in depth. Do NOT cover the entire chapter. "
        f"Stay focused on what \"{topic['title']}\" actually means within chapter \"{chapter_title}\". "
        f"Reference the chapter context and connect to sibling topics "
        f"({sibling_list}) where relevant for context, but keep the focus on this topic. "
        f"Use {board_display} exam marking patterns.\n\n"
        f"MANDATORY QUALITY RULES — your content MUST include ALL of these:\n"
        f"1. At least 3000-5000 words of detailed, original content with deep explanations focused on \"{topic['title']}\"\n"
        f"2. At least 12 Markdown headings (## or ###) for clear structure\n"
        f"3. A '## FAQ' or '## Frequently Asked Questions' section with 3-5 Q&As\n"
        f"4. A '## Exam-Style Questions' section with 2-mark, 5-mark, and long-answer board exam pattern questions\n"
        f"5. At least 2 concrete examples (labeled 'Example 1:', 'Example 2:' etc.)\n"
        f"6. A '## Key Points' or '## Revision Notes' section summarizing essentials\n"
        f"7. Include 'Exam tip:', 'Revision tip:', or 'Important note:' callouts\n"
        f"8. Mention the board name ({board_display}), subject ({subject_name}), "
        f"topic ({topic['title']}), and chapter ({chapter_title}) naturally in the text\n"
        f"9. Use diverse vocabulary — avoid repeating the same phrases\n"
        f"10. MANDATORY: Reference curriculum/syllabus sources — use phrases like "
        f"'As per the {board_display} syllabus', 'prescribed in the SCERT/NCERT curriculum', "
        f"'as outlined in the {subject_name} textbook', 'per the university syllabus'\n"
        f"11. MANDATORY: Include exam marking references — '2-mark question', '5-mark answer', "
        f"'long answer (10 marks)', 'short answer type' to match board exam patterns\n"
        f"12. Include relevant real-world examples and practical applications that connect the academic content to students' experience\n"
        f"13. GEO/AI-search ready: open with a 40-60 word answer-first paragraph "
        f"directly defining \"{topic['title']}\" before any heading.\n"
        f"14. GEO: include a '## Key Facts' (or '## Quick Facts') bullet list with 4-6 "
        f"crisp factual bullets a search engine can lift verbatim.\n"
        f"15. GEO: explicitly mention the current academic year ({datetime.now(timezone.utc).year}) "
        f"and a 'Last updated' or 'Reviewed' line so freshness is detectable.\n"
        f"16. GEO: end with an 'Author:' or 'Reviewed by' attribution line "
        f"(e.g., 'Reviewed by Syrabit Academic Team').\n"
    )

    messages = [
        {"role": "system", "content": _QUALITY_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    min_words = {"notes": 1500, "definition": 800, "important-questions": 1000, "mcqs": 800, "examples": 800}
    required_min = min_words.get(page_type, 800)

    async def _generate_and_score(msgs, attempt=1):
        try:
            raw = await asyncio.wait_for(_call_llm(msgs, max_tokens=7168), timeout=240)
        except asyncio.TimeoutError:
            logger.error(f"LLM timeout generating {page_type} for {topic['title']} (attempt {attempt})")
            return None, 0
        except Exception as e:
            logger.error(f"LLM error generating {page_type} for {topic['title']}: {type(e).__name__} (attempt {attempt})")
            return None, 0
        raw = _normalize_headings(raw)
        wc = len(raw.split())
        if wc < required_min:
            logger.warning(f"Content too short ({wc} words, min {required_min}) for {topic['title']}/{page_type} (attempt {attempt})")
            return None, 0
        qctx = {"board_name": board_display, "subject_name": subject_name, "chapter_title": chapter_title}
        qs = _compute_quality_score(raw, page_type, context=qctx)
        return raw, qs.get("score", 0)

    content, first_score = await _generate_and_score(messages, attempt=1)
    attempt_scores = [{"attempt": 1, "score": first_score}]

    def _build_retry_prompt(current_content, current_score, retry_number):
        qctx = {"board_name": board_display, "subject_name": subject_name, "chapter_title": chapter_title}
        diag = _compute_quality_score(current_content, page_type, context=qctx)

        if retry_number == 1:
            boost = (
                f"The previous attempt scored {current_score}/100. Improve it to score above {_QUALITY_PUBLISH_THRESHOLD}.\n"
                f"MISSING ELEMENTS — add ALL of these:\n"
            )
            if not diag.get("has_faq"):
                boost += "- Add a '## FAQ' section with 3-5 questions and answers\n"
            if not diag.get("has_exam_q"):
                boost += "- Add a '## Exam-Style Questions' section with 2-mark, 5-mark, and long-answer board-pattern questions\n"
            if not diag.get("has_examples"):
                boost += "- Add an 'Important Concepts & Applications' or 'Case Studies' section with real-world applications of the topic (NOT generic math-style solved examples unless the topic involves calculations)\n"
            if diag.get("heading_count", 0) < 8:
                boost += "- Add more ## and ### headings (need at least 8 for comprehensive lesson coverage)\n"
            if diag.get("word_count", 0) < 3000:
                boost += f"- Expand content to at least 3000-5000 words with deeper, more comprehensive explanations (currently {diag.get('word_count', 0)} words)\n"
            if not diag.get("anchored"):
                boost += f"- Mention {board_display}, {subject_name}, and {chapter_title} in the text\n"
            if not diag.get("has_key_points"):
                boost += "- Add a '## Key Points' or '## Revision Notes' section\n"
            if not diag.get("has_tips"):
                boost += "- Add exam tips, revision tips, or important notes throughout\n"
            if not diag.get("has_citations"):
                boost += f"- Add curriculum citations: 'As per the {board_display} syllabus', 'prescribed in SCERT/NCERT curriculum'\n"
            if not diag.get("has_marks_ref"):
                boost += "- Add exam marks references: '2-mark question', '5-mark answer', 'long answer (10 marks)'\n"
            boost += "\nRewrite the COMPLETE content with ALL improvements. Return ONLY the improved content."
        elif retry_number == 2:
            boost = (
                f"The previous attempt scored {current_score}/100 (target: {_QUALITY_PUBLISH_THRESHOLD}+).\n"
                f"Focus on DEPTH and WORD COUNT:\n"
                f"- Expand every section with deeper explanations, more detail, and real-world examples\n"
                f"- Target at least 3000-5000 words of dense, high-value content — be thorough and comprehensive\n"
                f"- Add more sub-headings (###) for better structure — aim for 12+ headings\n"
                f"- Ensure every section has at least 5-8 sentences of thorough explanation\n"
                f"- Add {board_display} exam patterns where relevant\n"
                f"- Add more exam-style questions with detailed model answers\n"
                f"\nRewrite the COMPLETE content with deeper, more detailed explanations. Return ONLY the improved content."
            )
        else:
            boost = (
                f"Previous attempts scored below {_QUALITY_PUBLISH_THRESHOLD}. Simplify and ensure these minimum requirements:\n"
                f"- At least 2000 words of thorough content\n"
                f"- At least 8 headings (## or ###)\n"
                f"- A FAQ section with 3 Q&As\n"
                f"- A Key Points section\n"
                f"- Exam-Style Questions with model answers\n"
                f"- Mention {board_display}, {subject_name}, and {chapter_title}\n"
                f"\nWrite clean, comprehensive content covering the topic thoroughly. Return ONLY the content."
            )
        return boost

    best_content, best_score = content, first_score

    for retry_num in range(1, 4):
        if best_content is not None and best_score >= _QUALITY_PUBLISH_THRESHOLD:
            break

        if best_content is not None:
            boost_prompt = _build_retry_prompt(best_content, best_score, retry_num)
            retry_msgs = [
                {"role": "system", "content": _QUALITY_SYSTEM},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": best_content},
                {"role": "user", "content": boost_prompt},
            ]
        else:
            retry_msgs = [
                {"role": "system", "content": _QUALITY_SYSTEM},
                {"role": "user", "content": prompt + "\n\nIMPORTANT: Ensure comprehensive, topic-focused coverage with at least 3000 words. Include FAQ, Key Points, Exam-Style Questions, and examples. Stay focused on this ONE topic only."},
            ]

        new_content, new_score = await _generate_and_score(retry_msgs, attempt=retry_num + 1)
        attempt_scores.append({"attempt": retry_num + 1, "score": new_score})
        logger.info(f"Retry {retry_num} for {topic['title']}/{page_type}: score={new_score} (best so far: {best_score})")

        if new_content is not None and (best_content is None or new_score > best_score):
            best_content, best_score = new_content, new_score

    content, first_score = best_content, best_score
    logger.info(f"Generation complete for {topic['title']}/{page_type}: final_score={first_score}, attempts={attempt_scores}")

    if content is None:
        return None

    word_count = len(content.split())
    if word_count < required_min:
        logger.warning(f"Generated content too short ({word_count} words, min {required_min}) for {topic['title']} / {page_type} — rejecting")
        return None

    title_templates = TITLE_TEMPLATES.get(page_type, ["{topic} — {board} {grade} {subject}"])
    title_idx = _topic_hash(topic["title"], page_type + ":title", len(title_templates))
    h = hierarchy
    title = title_templates[title_idx].format(
        topic=topic["title"],
        board=board_display,
        grade=grade_str,
        subject=subject_name,
    )

    extracted_desc = _extract_summary_from_content(content)
    if extracted_desc:
        meta_desc = extracted_desc
    else:
        type_label_map = {
            "notes": "notes with summary, key points & important questions",
            "definition": "definition, meaning & examples",
            "important-questions": "important questions with answers (1-mark to 5-mark)",
            "mcqs": "MCQ practice questions with answers",
            "examples": "solved examples with step-by-step solutions",
        }
        meta_desc = (
            f"{topic['title']} {type_label_map.get(page_type, 'notes')} "
            f"for {board_display} {grade_str} {subject_name}. "
            f"Exam-focused study material with detailed explanations."
        )

    quality_context = {
        "board_name": board_display,
        "subject_name": subject_name,
        "chapter_title": h.get("chapter", {}).get("title", ""),
        "topic_title": topic["title"],
    }
    quality_score = _compute_quality_score(content, page_type, context=quality_context)
    q_score = quality_score.get("score", 0)

    # Task #457: compute Generative Engine Optimisation sub-score. A page
    # is auto-published only when *both* the SEO sub-score and the GEO
    # sub-score clear the existing publish threshold (Task #41). Pages
    # that do not pass are stored as `draft` with structured failing
    # reasons so editors can review and fix them.
    geo_score = _compute_geo_score(content, page_type, context=quality_context)
    g_score = geo_score.get("score", 0)
    combined = _combined_quality_score(quality_score, geo_score)
    # Task #457 publish policy: both SEO and GEO sub-scores must clear
    # the same Task #41-aligned publish threshold. Gate is purely
    # threshold-based so behaviour is predictable and auditable; any
    # `geo_score.missing` markers are kept on the page doc for editor
    # context but do NOT independently force a draft.
    _GEO_PUBLISH_THRESHOLD = _QUALITY_PUBLISH_THRESHOLD

    fail_reasons: list[str] = []
    if q_score < _QUALITY_PUBLISH_THRESHOLD:
        fail_reasons.append(
            f"seo_below_threshold ({q_score}<{_QUALITY_PUBLISH_THRESHOLD})"
        )
    if g_score < _GEO_PUBLISH_THRESHOLD:
        fail_reasons.append(
            f"geo_below_threshold ({g_score}<{_GEO_PUBLISH_THRESHOLD})"
        )

    if not fail_reasons:
        page_status = "published"
    else:
        page_status = "draft"
        logger.warning(
            f"Page for {topic['title']}/{page_type} scored seo={q_score} geo={g_score} "
            f"combined={combined} — saved as draft ({'; '.join(fail_reasons)})"
        )

    page = {
        "id": f"seo-{uuid.uuid4().hex[:8]}",
        "topic_id": topic["id"],
        "topic_slug": topic["slug"],
        "chapter_slug": h.get("chapter_slug", ""),
        "subject_slug": h.get("subject_slug", ""),
        "stream_slug": h.get("stream_slug", ""),
        "class_slug": h.get("class_slug", ""),
        "board_slug": h.get("board_slug", ""),
        "page_type": page_type,
        "title": title,
        "content": content,
        "meta_description": meta_desc[:160],
        "word_count": word_count,
        "subject_name": subject_name,
        "class_name": grade_str,
        "board_name": board_display,
        "class_name_raw": class_name,
        "board_name_raw": board_name,
        "chapter_title": h.get("chapter", {}).get("title", ""),
        "topic_title": topic["title"],
        "source_chapter_title": h.get("chapter", {}).get("title", ""),
        "source_topic_title": topic["title"],
        "syllabus_position": syllabus_position,
        "sibling_topics": sibling_topics[:15],
        "stream_name": stream_name,
        "prompt_variant": variant_idx,
        "title_variant": title_idx,
        "quality_score": quality_score,
        "geo_score": geo_score,
        "combined_score": combined,
        "answer_summary": geo_score.get("answer_summary", ""),
        "key_facts": geo_score.get("key_facts", []),
        "quality": {
            "score": q_score,
            "geo_score": g_score,
            "combined_score": combined,
            "word_count": word_count,
        },
        "status": page_status,
        "fail_reasons": fail_reasons,
        "in_sitemap": page_status == "published",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Task #349: route through the shared helper so created_at /
    # updated_at are guaranteed and preserved across re-generations.
    from seo_writes import upsert_seo_page
    await upsert_seo_page(
        _db,
        {"topic_id": topic["id"], "page_type": page_type},
        page,
    )
    if page_status == "published":
        # SEO Phase A — content-time fan-out: IndexNow + edge cache purge
        # + synthetic Googlebot prewarm. Runs as a fire-and-forget task so
        # the generator returns immediately. Falls back gracefully when
        # SEO_FANOUT_ENABLED is off or there's no event loop.
        try:
            from seo_fanout import fanout_for_page
            fanout_for_page(page, source="seo_generate_single_page")
        except Exception as e:
            logger.debug(f"seo_fanout dispatch failed: {e}")
            # Belt-and-braces: keep the legacy queue call as a fallback so
            # the URL is still picked up by the next batched flush even if
            # the fan-out module misbehaves.
            try:
                from routes.bot_discovery import indexnow_batcher
                await indexnow_batcher.queue_page(page)
            except Exception:
                pass
        # Task #939 — agentic internal-linker. Schedule a background
        # task that picks 3-5 best contextual link sources elsewhere
        # on the site and either auto-applies (>= confidence
        # threshold) or files them for admin review. Fire-and-forget
        # so generation latency is unaffected; the linker swallows
        # its own errors so a failure here can never break Stage 3.
        try:
            from seo_internal_linker import schedule_propose
            schedule_propose(_db, page, source="stage3")
        except Exception as e:
            logger.debug(f"internal_linker dispatch failed: {e}")
    return page


@router.get("/admin/bing/keyword-brief")
async def bing_keyword_brief_endpoint(q: str, subject: str = "", board: str = "",
                                      _admin: dict = Depends(_require_admin)):
    """Diagnostic: return the BWT keyword brief for a phrase. Lets us validate
    the Bing Webmaster Tools wiring without re-running a content batch."""
    if _bwt is None or not _bwt.is_configured():
        raise HTTPException(status_code=503, detail="BING_WEBMASTER_API_KEY not configured")
    brief = await _bing_keyword_brief(q, subject, board)
    return {"configured": True, **brief}


@router.post("/refresh-meta")
async def refresh_meta_descriptions(_admin: dict = Depends(_require_admin)):
    """Bulk-refresh meta descriptions from existing content (no LLM calls).
    Also recomputes quality scores and diversifies titles for all published pages."""
    pages = await _db.seo_pages.find(
        {"status": "published"},
        {"_id": 0, "topic_id": 1, "page_type": 1, "content": 1, "topic_title": 1,
         "board_name": 1, "class_name": 1, "subject_name": 1, "chapter_title": 1},
    ).to_list(50000)

    updated = 0
    meta_refreshed = 0
    for p in pages:
        content = p.get("content", "")
        page_type = p.get("page_type", "notes")
        topic_title = p.get("topic_title", "")
        if not content or not topic_title:
            continue

        title_templates = TITLE_TEMPLATES.get(page_type, ["{topic} — {board} {grade} {subject}"])
        title_idx = _topic_hash(topic_title, page_type + ":title", len(title_templates))
        new_title = title_templates[title_idx].format(
            topic=topic_title,
            board=p.get("board_name", ""),
            grade=p.get("class_name", ""),
            subject=p.get("subject_name", ""),
        )

        ctx = {
            "board_name": p.get("board_name", ""),
            "subject_name": p.get("subject_name", ""),
            "chapter_title": p.get("chapter_title", ""),
        }
        quality = _compute_quality_score(content, page_type, context=ctx)

        update_fields = {
            "title": new_title,
            "title_variant": title_idx,
            "quality_score": quality,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        new_desc = _extract_summary_from_content(content)
        if new_desc:
            update_fields["meta_description"] = new_desc[:160]
            meta_refreshed += 1

        await _db.seo_pages.update_one(
            {"topic_id": p["topic_id"], "page_type": page_type},
            {"$set": update_fields},
        )
        updated += 1

    await _seo_log("refresh_meta", f"Refreshed {updated}/{len(pages)} pages ({meta_refreshed} meta descriptions)")
    return {
        "message": f"Refreshed {updated} pages ({meta_refreshed} meta descriptions updated)",
        "total_scanned": len(pages),
        "updated": updated,
        "meta_refreshed": meta_refreshed,
    }


@router.post("/generate")
async def generate_seo_content(data: GenerateRequest, background_tasks: BackgroundTasks, _admin: dict = Depends(_require_admin)):
    page_types = data.page_types or PAGE_TYPES

    if data.batch:
        # Task #457: tolerant query — pick up legacy/missing/draft
        # status topics so the existing 991 actually generate pages.
        topics = await _db.topics.find(
            _eligible_topic_filter(only_published=bool(data.only_published)),
            {"_id": 0},
        ).to_list(10000)
        if not topics:
            raise HTTPException(status_code=404, detail="No topics found. Run extract-topics or generate-lessons first.")

        background_tasks.add_task(_batch_generate, topics, page_types)
        return {
            "message": f"Batch generation started for {len(topics)} topics × {len(page_types)} page types",
            "total_pages": len(topics) * len(page_types),
        }

    if not data.topic_id:
        raise HTTPException(status_code=400, detail="Provide topic_id or set batch=true")

    topic = await _db.topics.find_one({"id": data.topic_id}, {"_id": 0})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    hierarchy = await _resolve_hierarchy(topic)
    if not hierarchy:
        raise HTTPException(status_code=404, detail="Could not resolve topic hierarchy")

    results = []
    for pt in page_types:
        page = await _generate_single_page(topic, pt, hierarchy)
        if page:
            results.append({"page_type": pt, "word_count": page["word_count"], "id": page["id"]})

    asyncio.create_task(_seo_log(
        action  = "seo:pages_generated",
        details = f"Generated {len(results)} SEO pages for topic '{topic.get('title', topic['id'])}': {', '.join(p['page_type'] for p in results)}",
    ))
    return {"message": f"Generated {len(results)} pages", "pages": results}


@router.post("/generate-lessons")
async def generate_lesson_pages(background_tasks: BackgroundTasks, _admin: dict = Depends(_require_admin)):
    """Lesson-wise SEO generation: 1 topic + 1 notes page per chapter.
    Directly links to content card lessons. Enforces max 1 page per chapter."""
    chapters = await _db.chapters.find({}, {"_id": 0}).to_list(5000)
    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found in database")

    existing_topics = {}
    async for t in _db.topics.find({}, {"_id": 0}):
        existing_topics[t.get("chapter_id", "")] = t

    created_topics = 0
    skipped = 0
    to_generate = []

    for ch in chapters:
        ch_id = ch["id"]
        if ch_id in existing_topics:
            existing_page = await _db.seo_pages.find_one(
                {"topic_id": existing_topics[ch_id]["id"], "status": "published"},
                {"_id": 0, "id": 1}
            )
            if existing_page:
                skipped += 1
                continue
            to_generate.append(existing_topics[ch_id])
            continue

        subj = await _db.subjects.find_one({"id": ch.get("subject_id", "")}, {"_id": 0})
        board = None
        if subj:
            board = await _db.boards.find_one({"id": subj.get("board_id", "")}, {"_id": 0})

        subject_name = subj["name"] if subj else ""
        board_name = board["name"] if board else "DEGREE"
        title = ch.get("title", "")

        topic_id = f"topic-{uuid.uuid4().hex[:8]}"
        topic_slug = _slug(title)
        primary_kw = f"{title.lower()} {subject_name.lower()} {board_name}".strip()[:120]

        topic = {
            "id": topic_id,
            "chapter_id": ch_id,
            "subject_id": ch.get("subject_id", ""),
            "chapter_title": title,
            "subject_name": subject_name,
            "board_name": board_name,
            "title": title,
            "slug": topic_slug,
            "primary_keyword": primary_kw,
            "search_intent": "informational",
            "definition": "",
            "examples": "",
            "order": ch.get("order_index", 0),
            "status": "published",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await _db.topics.insert_one(topic)
        await _db.chapters.update_one(
            {"id": ch_id},
            {"$set": {"linked_topic_ids": [topic_id]}}
        )
        created_topics += 1
        to_generate.append(topic)

    if to_generate:
        background_tasks.add_task(_batch_generate, to_generate, ["notes"])

    return {
        "message": (
            f"Lesson-wise generation: {created_topics} topics created, "
            f"{len(to_generate)} pages queued, {skipped} already complete"
        ),
        "total_chapters": len(chapters),
        "topics_created": created_topics,
        "pages_queued": len(to_generate),
        "already_complete": skipped,
    }


async def _batch_generate(topics: list, page_types: list, job_id: str | None = None):
    total = 0
    errors = 0
    skipped = 0
    seo_scores: list[int] = []
    geo_scores: list[int] = []
    outcomes_local: list[dict] = []

    def _record(tid: str, ttitle: str, pt: str, status: str, reason: str = ""):
        outcomes_local.append({
            "topic_id": tid, "topic_title": ttitle, "page_type": pt,
            "status": status, "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        if job_id:
            _job_record_outcome(job_id, tid, ttitle, pt, status, reason)

    for topic in topics:
        tid = topic.get("id", "")
        ttitle = topic.get("title", "")
        try:
            hierarchy = await _resolve_hierarchy(topic)
            if not hierarchy:
                for pt in page_types:
                    _record(tid, ttitle, pt, "skipped", "no hierarchy")
                    skipped += 1
                continue
            for pt in page_types:
                existing = await _db.seo_pages.find_one(
                    {"topic_id": topic["id"], "page_type": pt},
                    {"_id": 0, "id": 1}
                )
                if existing:
                    _record(tid, ttitle, pt, "skipped", "page already exists")
                    skipped += 1
                    continue
                try:
                    page = await _generate_single_page(topic, pt, hierarchy)
                    if page:
                        total += 1
                        q = (page.get("quality") or {}).get("score")
                        g = (page.get("quality") or {}).get("geo_score")
                        if isinstance(q, int): seo_scores.append(q)
                        if isinstance(g, int): geo_scores.append(g)
                        fr = page.get("fail_reasons") or []
                        _record(tid, ttitle, pt, "generated",
                                f"score={q} geo={g} status={page.get('status')}"
                                + (f" — {','.join(fr)}" if fr else ""))
                except Exception as e:
                    logger.error(f"Generation error for {topic['title']}/{pt}: {e}")
                    _record(tid, ttitle, pt, "failed", str(e)[:160])
                    errors += 1
        except Exception as e:
            logger.error(f"Hierarchy error for topic {topic.get('id')}: {e}")
            for pt in page_types:
                _record(tid, ttitle, pt, "failed", f"hierarchy: {str(e)[:140]}")
            errors += 1

    avg_seo = round(sum(seo_scores) / len(seo_scores)) if seo_scores else 0
    avg_geo = round(sum(geo_scores) / len(geo_scores)) if geo_scores else 0
    if job_id and job_id in _seo_jobs:
        _seo_jobs[job_id]["avg_seo_score"] = avg_seo
        _seo_jobs[job_id]["avg_geo_score"] = avg_geo

    logger.info(
        f"Batch generation complete: {total} pages generated, {skipped} skipped, "
        f"{errors} errors · avg_seo={avg_seo} avg_geo={avg_geo}"
    )
    await _db.seo_generation_log.insert_one({
        "id": f"genlog-{uuid.uuid4().hex[:8]}",
        "job_id": job_id,
        "total_generated": total,
        "skipped": skipped,
        "errors": errors,
        "avg_seo_score": avg_seo,
        "avg_geo_score": avg_geo,
        "outcomes": outcomes_local,
        "page_types": list(page_types),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        from routes.bot_discovery import indexnow_batcher
        flushed = await indexnow_batcher.flush(source="seo_batch_generate")
        logger.info(f"IndexNow flush after batch generation: {flushed} URLs")
    except Exception as e:
        logger.debug(f"IndexNow flush failed: {e}")
    await _seo_log(
        action  = "seo:batch_complete",
        details = f"Batch SEO generation complete — {total} pages created across {len(topics)} topics" +
                  (f" · {errors} errors" if errors else ""),
        level   = "info" if errors == 0 else "warn",
    )


class QualityEditRequest(BaseModel):
    min_score: Optional[int] = 90
    page_ids: Optional[List[str]] = None
    limit: Optional[int] = 50


@router.post("/quality-edit")
async def quality_edit_pages(data: QualityEditRequest, background_tasks: BackgroundTasks, _admin: dict = Depends(_require_admin)):
    """Delete low-scoring published pages and regenerate them with the quality retry loop."""
    query: dict = {"status": "published"}
    if data.page_ids:
        query["id"] = {"$in": data.page_ids}
    else:
        query["quality_score.score"] = {"$lt": data.min_score}

    low_pages = await _db.seo_pages.find(query, {"_id": 0, "id": 1, "topic_id": 1, "page_type": 1, "quality_score.score": 1}).to_list(data.limit)
    if not low_pages:
        return {"message": "No low-scoring pages found", "count": 0}

    page_ids_to_delete = [p["id"] for p in low_pages]
    regen_specs = [(p["topic_id"], p["page_type"]) for p in low_pages]

    await _db.seo_pages.delete_many({"id": {"$in": page_ids_to_delete}})

    background_tasks.add_task(_quality_regen_batch, regen_specs)
    return {
        "message": f"Deleted {len(page_ids_to_delete)} low-scoring pages, regeneration started",
        "count": len(page_ids_to_delete),
        "deleted_ids": page_ids_to_delete,
    }


async def _quality_regen_batch(specs: list):
    """Regenerate pages from (topic_id, page_type) specs."""
    total = 0
    errors = 0
    for topic_id, page_type in specs:
        try:
            topic = await _db.topics.find_one({"id": topic_id}, {"_id": 0})
            if not topic:
                continue
            hierarchy = await _resolve_hierarchy(topic)
            if not hierarchy:
                continue
            page = await _generate_single_page(topic, page_type, hierarchy)
            if page:
                total += 1
        except Exception as e:
            logger.error(f"Quality regen error for {topic_id}/{page_type}: {e}")
            errors += 1
        await asyncio.sleep(1)

    try:
        from routes.bot_discovery import indexnow_batcher
        flushed = await indexnow_batcher.flush(source="seo_quality_edit")
        logger.info(f"IndexNow flush after quality edit: {flushed} URLs")
    except Exception as e:
        logger.debug(f"IndexNow flush failed: {e}")
    await _seo_log(
        action="seo:quality_edit_complete",
        details=f"Quality edit regenerated {total}/{len(specs)} pages" + (f" · {errors} errors" if errors else ""),
        level="info" if errors == 0 else "warn",
    )


# ─── ADMIN: Stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def seo_stats(_admin: dict = Depends(_require_admin)):
    total_topics = await _db.topics.count_documents({})
    published_topics = await _db.topics.count_documents({"status": "published"})
    total_pages = await _db.seo_pages.count_documents({})
    published_pages = await _db.seo_pages.count_documents({"status": "published"})

    by_type = {}
    for pt in PAGE_TYPES:
        by_type[pt] = await _db.seo_pages.count_documents({"page_type": pt})

    last_log = await _db.seo_generation_log.find_one(
        {}, {"_id": 0}, sort=[("completed_at", -1)]
    )

    return {
        "topics": {"total": total_topics, "published": published_topics},
        "pages": {"total": total_pages, "published": published_pages, "by_type": by_type},
        "last_generation": last_log,
    }


# ─── ADMIN: Page management ─────────────────────────────────────────────────

@router.get("/pages")
async def list_seo_pages(
    topic_id: Optional[str] = None,
    page_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    _admin: dict = Depends(_require_admin),
):
    query = {}
    if topic_id:
        query["topic_id"] = topic_id
    if page_type:
        query["page_type"] = page_type
    if status:
        query["status"] = status

    pages = await _db.seo_pages.find(query, {"_id": 0, "content": 0}).sort("generated_at", -1).skip(offset).limit(limit).to_list(limit)
    total = await _db.seo_pages.count_documents(query)
    return {"pages": pages, "total": total}


@router.patch("/pages/{page_id}/status")
async def update_page_status(page_id: str, status: str = "published", _admin: dict = Depends(_require_admin)):
    if status not in ("published", "draft", "archived", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid status")
    updates = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if status == "published":
        updates["in_sitemap"] = True
    elif status in ("archived", "rejected"):
        updates["in_sitemap"] = False
    result = await _db.seo_pages.update_one(
        {"id": page_id},
        {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")

    if status == "published":
        # Task #332 — durable fan-out. `notify_indexnow_for_page`
        # routes through `sqs_fanout.enqueue("indexnow-page", ...)`
        # under WORKERS_BACKEND=aws, so the work IS persisted in SQS
        # before this request returns. Awaiting it (rather than
        # `asyncio.create_task`) is what gives the durability
        # guarantee the cutover requires — a process crash between
        # status flip and IndexNow ping can no longer drop the work.
        # The enqueue is sub-50ms and never raises (the producer
        # swallows transient SQS errors), so awaiting does not add
        # meaningful tail latency to the publish path.
        try:
            page = await _db.seo_pages.find_one({"id": page_id}, {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1, "page_type": 1})
            if page:
                from routes.bot_discovery import notify_indexnow_for_page
                await notify_indexnow_for_page(page)
        except Exception as _ix_err:
            logger.warning(f"[indexnow] publish enqueue failed for {page_id}: {_ix_err}")
        try:
            import asyncio
            from d1_sync import sync_tables as _d1_sync_tables, is_d1_configured as _d1_ok
            if _d1_ok():
                asyncio.create_task(_d1_sync_tables(_db, ["seo_pages"]))
        except Exception as e:
            logger.warning(f"D1 auto-sync trigger failed: {e}")

    return {"message": f"Status updated to {status}"}


# ─── ADMIN: D1 edge cache sync ────────────────────────────────────────────────

@router.get("/d1/status")
async def d1_status(_admin: dict = Depends(_require_admin)):
    """Report D1 edge cache configuration and last-known sync state."""
    try:
        from d1_sync import is_d1_configured, D1_SYNC_SECRET, EDGE_WORKER_URL
    except Exception as e:
        return {"configured": False, "sync_secret_set": False, "error": str(e)}
    last_sync = None
    try:
        meta = await _db.sync_meta.find_one({"_id": "d1_last_sync"}, {"_id": 0})
        if meta:
            last_sync = meta.get("last_sync") or meta.get("at")
    except Exception:
        pass
    return {
        "configured": is_d1_configured(),
        "sync_secret_set": bool(D1_SYNC_SECRET and D1_SYNC_SECRET != "REPLACE_WITH_SECURE_RANDOM_SECRET"),
        "edge_worker_url": EDGE_WORKER_URL,
        "last_sync": last_sync,
    }


@router.post("/d1/sync-full")
async def d1_sync_full(_admin: dict = Depends(_require_admin)):
    """Push the full content catalog (boards, subjects, chapters, topics, seo_pages) to D1."""
    from d1_sync import sync_full
    result = await sync_full(_db)
    if result.get("success"):
        try:
            await _db.sync_meta.update_one(
                {"_id": "d1_last_sync"},
                {"$set": {"last_sync": datetime.now(timezone.utc).isoformat(), "scope": "full"}},
                upsert=True,
            )
        except Exception:
            pass
    return result


@router.post("/d1/sync-tables")
async def d1_sync_tables_endpoint(
    body: dict,
    _admin: dict = Depends(_require_admin),
):
    """Push selected tables to D1. Body: {"tables": ["seo_pages", "topics"]}"""
    from d1_sync import sync_tables
    tables = body.get("tables") or []
    if not isinstance(tables, list) or not tables:
        raise HTTPException(status_code=400, detail="Body must include non-empty 'tables' array")
    result = await sync_tables(_db, tables)
    if result.get("success"):
        try:
            await _db.sync_meta.update_one(
                {"_id": "d1_last_sync"},
                {"$set": {"last_sync": datetime.now(timezone.utc).isoformat(), "scope": ",".join(tables)}},
                upsert=True,
            )
        except Exception:
            pass
    return result


# ─── ADMIN: Review queue ─────────────────────────────────────────────────────

@router.get("/review-queue")
async def get_review_queue(
    status: str = "draft",
    limit: int = 50,
    offset: int = 0,
    _admin: dict = Depends(_require_admin),
):
    """Return pages needing review, sorted by quality score ascending (worst first)."""
    query = {"status": status}
    pages = await _db.seo_pages.find(query, {"_id": 0, "content": 0}).sort(
        [("quality_score.score", 1), ("generated_at", -1)]
    ).skip(offset).limit(limit).to_list(limit)
    total = await _db.seo_pages.count_documents(query)
    return {"pages": pages, "total": total}


@router.post("/review-queue/bulk-action")
async def bulk_review_action(
    action: str,
    page_ids: List[str] = [],
    min_score: Optional[int] = None,
    _admin: dict = Depends(_require_admin),
):
    """Bulk approve (publish) or reject pages. Can filter by min_score threshold."""
    if action not in ("publish", "reject", "archive"):
        raise HTTPException(status_code=400, detail="Action must be publish, reject, or archive")

    status_map = {"publish": "published", "reject": "rejected", "archive": "archived"}
    new_status = status_map[action]

    query = {}
    if page_ids:
        query["id"] = {"$in": page_ids}
    elif min_score is not None and action == "publish":
        query["status"] = "draft"
        query["quality_score.score"] = {"$gte": min_score}
    else:
        raise HTTPException(status_code=400, detail="Provide page_ids or min_score for bulk publish")

    affected_ids = [
        doc["id"] async for doc in _db.seo_pages.find(query, {"_id": 0, "id": 1})
    ]
    result = await _db.seo_pages.update_many(
        query,
        {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if new_status == "published" and result.modified_count > 0 and affected_ids:
        try:
            published_pages = await _db.seo_pages.find(
                {"id": {"$in": affected_ids}},
                {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1, "page_type": 1}
            ).to_list(len(affected_ids))
            from routes.bot_discovery import indexnow_batcher
            for p in published_pages:
                await indexnow_batcher.queue_page(p)
            await indexnow_batcher.flush(source="seo_bulk_review")
        except Exception:
            pass
    await _seo_log("seo:bulk_review", f"Bulk {action}: {result.modified_count} pages → {new_status}")
    return {"modified": result.modified_count, "new_status": new_status}


@router.post("/flag-low-quality")
async def flag_low_quality_pages(_admin: dict = Depends(_require_admin)):
    """Recompute quality scores for all published pages using stricter criteria.
    Pages scoring below 50 are moved to draft for review. Returns count of affected pages."""
    published = await _db.seo_pages.find(
        {"status": "published"},
        {"_id": 0, "id": 1, "content": 1, "page_type": 1,
         "board_name": 1, "subject_name": 1, "chapter_title": 1, "topic_title": 1}
    ).to_list(50000)

    flagged = 0
    rescored = 0
    for p in published:
        ctx = {
            "board_name": p.get("board_name", ""),
            "subject_name": p.get("subject_name", ""),
            "chapter_title": p.get("chapter_title", ""),
        }
        new_score = _compute_quality_score(p.get("content", ""), p.get("page_type", "notes"), context=ctx)
        updates = {
            "quality_score": new_score,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if new_score["score"] < 50:
            updates["status"] = "draft"
            flagged += 1
        rescored += 1
        await _db.seo_pages.update_one({"id": p["id"]}, {"$set": updates})

    await _seo_log("seo:flag_low_quality", f"Rescored {rescored} pages, flagged {flagged} as draft")
    return {"rescored": rescored, "flagged_as_draft": flagged}


@router.get("/page/{page_id}/preview")
async def preview_page(page_id: str, _admin: dict = Depends(_require_admin)):
    """Get full page content for admin preview."""
    page = await _db.seo_pages.find_one({"id": page_id}, {"_id": 0})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


# ─── PUBLIC: Serve SEO pages ────────────────────────────────────────────────

async def _inject_qa(page: dict) -> dict:
    """Attach published QA pairs to a page dict (best-effort)."""
    try:
        qa = await _db.qa_pairs.find(
            {
                "board_slug": page.get("board_slug", ""),
                "class_slug": page.get("class_slug", ""),
                "subject_slug": page.get("subject_slug", ""),
                "topic_slug": page.get("topic_slug", ""),
                "status": "published",
            },
            {"_id": 0},
        ).sort("upvotes", -1).limit(20).to_list(20)
        page["qa_pairs"] = qa
    except Exception:
        page["qa_pairs"] = []
    return page


@router.get("/page/{board}/{class_slug}/{subject_slug}/{topic_slug}/{page_type}")
async def get_seo_page_typed(board: str, class_slug: str, subject_slug: str, topic_slug: str, page_type: str, request: Request = None):
    from starlette.responses import JSONResponse
    if page_type not in ALL_PAGE_TYPES:
        raise HTTPException(status_code=404, detail="Invalid page type")
    page = await _db.seo_pages.find_one(
        {
            "board_slug": board,
            "class_slug": class_slug,
            "subject_slug": subject_slug,
            "topic_slug": topic_slug,
            "page_type": page_type,
            "status": "published",
        },
        {"_id": 0},
    )
    if not page:
        # Distinguish permanently-gone from temporarily-unavailable:
        #  404 — a draft/pending document exists; content may be published later.
        #  410 — no document at all for these slugs; URL is permanently absent and
        #         crawlers should stop re-visiting it (saves crawl budget).
        draft = await _db.seo_pages.find_one(
            {
                "board_slug": board,
                "class_slug": class_slug,
                "subject_slug": subject_slug,
                "topic_slug": topic_slug,
                "page_type": page_type,
            },
            {"_id": 1},
        )
        if draft:
            raise HTTPException(status_code=404, detail="Page not published")
        raise HTTPException(status_code=410, detail="Page not found")
    result = await _inject_qa(page)
    kw = _build_expanded_keywords(
        result.get("topic_title", ""), result.get("subject_name", ""),
        result.get("board_name", ""), result.get("class_name", ""),
        result.get("chapter_title", ""), page_type,
    )
    if request and getattr(request.state, "is_search_bot", False):
        result["source"] = "Syrabit Browser — https://syrabit.ai"
        result["attribution"] = "Content powered by Syrabit.ai — AI Educational Browser for Assam Board Students"
        result["keywords"] = kw
        result["topicKeywords"] = [w.strip() for w in re.split(r'[\s,\-–—/&]+', result.get("topic_title", "")) if len(w.strip()) > 2]
    resp = JSONResponse(result)
    resp.headers["X-Topic-Keywords"] = kw[:500]
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    return resp


try:
    import mistune as _mistune_seo
    _seo_md_renderer = _mistune_seo.create_markdown(
        plugins=["table", "strikethrough", "footnotes", "task_lists"],
        escape=True,
    )
except Exception:
    _seo_md_renderer = None


def _md_to_html(text: str) -> str:
    if not text:
        return ""
    if _seo_md_renderer:
        return _seo_md_renderer(text) or ""
    h = html_mod.escape(text)
    h = re.sub(r'^#### (.+)$', r'<h4>\1</h4>', h, flags=re.MULTILINE)
    h = re.sub(r'^### (.+)$', r'<h3>\1</h3>', h, flags=re.MULTILINE)
    h = re.sub(r'^## (.+)$', r'<h2>\1</h2>', h, flags=re.MULTILINE)
    h = re.sub(r'^# (.+)$', r'<h1>\1</h1>', h, flags=re.MULTILINE)
    h = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', h)
    h = re.sub(r'\*(.+?)\*', r'<em>\1</em>', h)
    h = re.sub(r'^- (.+)$', r'<li>\1</li>', h, flags=re.MULTILINE)
    h = re.sub(r'\n\n', '</p><p>', h)
    return f"<p>{h}</p>"


_ASSAMESE_RE = re.compile(r'[\u0980-\u09FF]')


def _has_assamese(text: str) -> bool:
    return bool(_ASSAMESE_RE.search(text))


def _is_question_numbered(line: str) -> bool:
    return bool(
        re.match(r'^\s*(\d+)\s*[.)]\s', line)
        or re.match(r'^\s*\(([ivxlc]+)\)\s', line, re.IGNORECASE)
        or re.match(r'^\s*([ivxlc]+)\s*[.)]\s', line, re.IGNORECASE)
        or re.match(r'^\s*\([a-z]\)\s', line, re.IGNORECASE)
        or re.match(r'^\s*[a-z]\s*[.)]\s', line)
    )


def _looks_like_pyq(text: str) -> bool:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 3:
        return False
    numbered = sum(1 for l in lines if _is_question_numbered(l))
    has_asm = _has_assamese(text)
    return has_asm and numbered >= 3 and (numbered / len(lines)) >= 0.15


def _format_bilingual_block(lines: list) -> str:
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        is_q_num = _is_question_numbered(line)
        has_asm = _has_assamese(line)

        if is_q_num and not has_asm:
            block = f'<div class="pyq-q">{html_mod.escape(line)}'
            if i + 1 < len(lines) and _has_assamese(lines[i + 1]) and not _is_question_numbered(lines[i + 1]):
                i += 1
                block += f'<span class="asm">{html_mod.escape(lines[i].strip())}</span>'
            block += '</div>'
            out.append(block)
        elif has_asm and not _is_question_numbered(line):
            out.append(f'<div class="pyq-q"><span class="asm">{html_mod.escape(line.strip())}</span></div>')
        elif _is_question_numbered(line) and has_asm:
            asm_chars = _ASSAMESE_RE.findall(line)
            asm_start = line.index(asm_chars[0]) if asm_chars else len(line)
            if asm_start == 0:
                out.append(f'<div class="pyq-q"><span class="asm">{html_mod.escape(line.strip())}</span></div>')
            else:
                eng_text = line[:asm_start].rstrip()
                asm_text = line[asm_start:].strip()
                block = f'<div class="pyq-q">{html_mod.escape(eng_text)}'
                if asm_text:
                    block += f'<span class="asm">{html_mod.escape(asm_text)}</span>'
                block += '</div>'
                out.append(block)
        else:
            next_is_asm = (i + 1 < len(lines) and _has_assamese(lines[i + 1]) and not _is_question_numbered(lines[i + 1]))
            block = f'<div class="pyq-q">{html_mod.escape(line.strip())}'
            if next_is_asm:
                i += 1
                block += f'<span class="asm">{html_mod.escape(lines[i].strip())}</span>'
            block += '</div>'
            out.append(block)
        i += 1
    return '\n'.join(out)


def _format_content_html(raw_md: str) -> str:
    """Convert raw markdown to well-structured, textbook-style HTML.
    Handles proper list wrapping, tables, code blocks, and semantic structure.
    If content contains Assamese script (PYQ-style bilingual text), uses
    exam-paper formatting: Assamese on next line, no gap, small text."""
    if not raw_md:
        return ""

    md = raw_md.replace('\r\n', '\n').replace('\r', '\n')

    if _looks_like_pyq(md):
        lines = md.split('\n')
        bilingual_html = _format_bilingual_block(lines)
        return f'<div class="pyq-paper">{bilingual_html}</div>'

    html = _md_to_html(md)
    if not html:
        return ""

    html = re.sub(
        r'(<table)',
        r'<div class="table-responsive"><table',
        html,
    )
    html = re.sub(
        r'(</table>)',
        r'</table></div>',
        html,
    )

    return html


_PAGE_TYPE_LABELS = {
    "notes": "Notes",
    "definition": "Definitions",
    "important-questions": "Important Questions",
    "mcqs": "MCQs",
    "examples": "Solved Examples",
}

_ASSAM_GEO = {
    "@type": "Place",
    "name": "Assam, India",
    "geo": {"@type": "GeoCoordinates", "latitude": 26.2006, "longitude": 92.9376},
    "address": {"@type": "PostalAddress", "addressRegion": "Assam", "addressCountry": "IN"},
}

# ─────────────────────────────────────────────────────────────
# Founder entity. Reused (by @id) across every page so Google
# can build a stable knowledge graph linking Syrabit ↔ Dipak Rai.
# When someone searches "Syrabit", Google's brand-entity logic
# pulls founder + org info from the first crawled page that
# carries this graph — and every page does, via _ORG_NODE.
# ─────────────────────────────────────────────────────────────
_FOUNDER_NODE = {
    "@type": "Person",
    "@id": "https://syrabit.ai/#founder",
    "name": "Dipak Rai",
    "givenName": "Dipak",
    "familyName": "Rai",
    "jobTitle": "Founder",
    "nationality": {"@type": "Country", "name": "India"},
    "worksFor": {"@id": "https://syrabit.ai/#organization"},
    "knowsAbout": [
        "AHSEC syllabus",
        "SEBA syllabus",
        "Education technology",
        "AI in education",
        "Assam Board examinations",
    ],
    "url": "https://syrabit.ai/about",
}

_ORG_NODE = {
    "@type": ["Organization", "EducationalOrganization"],
    "@id": "https://syrabit.ai/#organization",
    "name": "Syrabit.ai",
    "alternateName": ["Syrabit", "Syra"],
    "legalName": "Syrabit",
    "url": "https://syrabit.ai",
    "logo": {"@type": "ImageObject", "url": "https://syrabit.ai/icons/icon-192x192.png"},
    "image": "https://syrabit.ai/icons/icon-512x512.png",
    "description": (
        "Syrabit.ai is an academic content platform that produces syllabus-aligned study material "
        "for AHSEC (Assam Higher Secondary Education Council), SEBA (Board of Secondary Education, Assam), "
        "and NEP FYUGP Degree students. Content follows official board/university curricula "
        "and is editorially reviewed for accuracy, exam relevance, and academic depth."
    ),
    "foundingDate": "2026",
    "founder": _FOUNDER_NODE,
    "founders": [_FOUNDER_NODE],
    "knowsAbout": [
        "AHSEC syllabus", "SEBA syllabus", "NEP FYUGP curriculum",
        "Assam Board examinations", "Gauhati University syllabus",
        "Dibrugarh University syllabus", "Higher education in Assam",
    ],
    "sameAs": ["https://twitter.com/SyrabitAI"],
    "areaServed": {
        "@type": "State",
        "name": "Assam",
        "containedInPlace": {"@type": "Country", "name": "India"},
    },
    "address": {
        "@type": "PostalAddress",
        "addressRegion": "Assam",
        "addressCountry": "IN",
    },
}

_BOARD_SYLLABUS_SOURCE = {
    "AHSEC": "Assam Higher Secondary Education Council (AHSEC) official syllabus",
    "SEBA": "Board of Secondary Education, Assam (SEBA) official syllabus",
    "NEP FYUGP": "National Education Policy (NEP) 2020 FYUGP curriculum as adopted by Assam universities (Gauhati University, Dibrugarh University, Cotton University)",
    "Degree": "National Education Policy (NEP) 2020 FYUGP curriculum as adopted by Assam universities (Gauhati University, Dibrugarh University, Cotton University)",
}

_PAGE_TYPE_METHODOLOGY = {
    "notes": "structured study notes following the definition → explanation → examples → exam tips format, aligned to the official syllabus",
    "definition": "formal academic definitions with context, etymology where relevant, and exam-oriented explanations",
    "important-questions": "mark-wise important questions curated from previous year papers and syllabus weightage analysis",
    "mcqs": "multiple choice questions with correct answers and explanations, covering key concepts from the syllabus",
    "examples": "solved examples following the problem → approach → step-by-step solution → exam tip format",
}

_EXAM_KEYWORD_SUFFIXES = [
    "notes", "study notes", "class notes", "exam notes",
    "definition", "meaning", "what is", "explain",
    "important questions", "questions and answers", "Q&A",
    "MCQ", "MCQs", "multiple choice questions", "objective questions",
    "solved examples", "examples", "numerical problems",
    "previous year questions", "PYQ", "past papers",
    "syllabus", "chapter summary", "revision notes",
    "short notes", "long answer", "short answer",
    "marks distribution", "exam preparation", "exam tips",
]

_BOARD_KEYWORD_VARIANTS = {
    "AHSEC": ["AHSEC", "Assam Higher Secondary", "HS", "Higher Secondary", "Class 11", "Class 12", "HS 1st Year", "HS 2nd Year", "Assam Board"],
    "SEBA": ["SEBA", "Board of Secondary Education Assam", "HSLC", "Class 9", "Class 10", "Assam Board", "Assam HSLC"],
    "Degree": ["Degree", "NEP FYUGP", "B.Com", "B.A", "B.Sc", "Gauhati University", "Dibrugarh University", "Cotton University", "GU", "DU", "CU"],
}


def _build_expanded_keywords(topic: str, subject: str, board: str, cls: str, chapter: str = "", page_type: str = "notes") -> str:
    parts = set()
    topic_words = [w for w in re.split(r'[\s,\-–—/&]+', topic) if len(w) > 2]
    for w in topic_words:
        parts.add(w.lower())
    parts.add(topic.lower())
    parts.add(subject.lower())
    parts.add(board.lower())
    if chapter:
        parts.add(chapter.lower())
        for w in re.split(r'[\s,\-–—/&]+', chapter):
            if len(w) > 2:
                parts.add(w.lower())
    board_key = next((k for k in _BOARD_KEYWORD_VARIANTS if k.lower() in board.lower()), None)
    if board_key:
        for variant in _BOARD_KEYWORD_VARIANTS[board_key]:
            parts.add(variant.lower())
    for suffix in _EXAM_KEYWORD_SUFFIXES[:12]:
        parts.add(f"{topic.lower()} {suffix}".strip())
    parts.add(f"{topic.lower()} {subject.lower()}")
    parts.add(f"{topic.lower()} {board.lower()} {cls.lower()}")
    parts.add(f"{topic.lower()} {board.lower()}")
    parts.add(f"{subject.lower()} {cls.lower()}")
    pt_labels = {"notes": "notes", "definition": "definition", "mcqs": "MCQs", "important-questions": "important questions", "examples": "solved examples"}
    if page_type in pt_labels:
        parts.add(f"{topic.lower()} {pt_labels[page_type]}")
    parts.discard("")
    return ", ".join(sorted(parts)[:80])


async def _resolve_og_image(subject_slug: str) -> str:
    fallback = "https://syrabit.ai/opengraph.jpg"
    if not subject_slug:
        return fallback
    doc = await _db.subjects.find_one(
        {"slug": subject_slug, "thumbnailUrl": {"$exists": True, "$ne": ""}},
        {"_id": 0, "id": 1},
    )
    if doc and doc.get("id"):
        return f"https://syrabit.ai/api/content/subjects/{doc['id']}/og-image.png"
    return fallback


def _render_seo_html(
    page: dict,
    page_url: str,
    page_type_links: list = None,   # [{type, label, url, active}]
    related_topics: list = None,    # [{title, seo_path, slug}]
    prev_topic: dict = None,
    next_topic: dict = None,
    og_image_url: str = "https://syrabit.ai/opengraph.jpg",
) -> str:
    title = html_mod.escape(page.get("title", ""))
    desc = html_mod.escape(page.get("meta_description", ""))
    topic = html_mod.escape(page.get("topic_title", ""))
    subject = html_mod.escape(page.get("subject_name", ""))
    board = html_mod.escape(page.get("board_name", ""))
    cls = html_mod.escape(page.get("class_name", ""))
    chapter = html_mod.escape(page.get("chapter_title", ""))
    page_type = page.get("page_type", "notes")
    content_html = page.get("content_html") or _format_content_html(page.get("content", ""))
    generated = page.get("generated_at", "")
    updated = page.get("updated_at", generated)
    kw = page.get("primary_keyword", f"{topic} {board} {cls}")

    edu_level = f"{board} {cls}".strip()
    subject_url = f"https://syrabit.ai/{page.get('board_slug','')}/{page.get('class_slug','')}/{page.get('subject_slug','')}"

    syllabus_source = ""
    for bkey in _BOARD_SYLLABUS_SOURCE:
        if bkey.lower() in board.lower() or bkey.lower() in edu_level.lower():
            syllabus_source = _BOARD_SYLLABUS_SOURCE[bkey]
            break
    if not syllabus_source:
        syllabus_source = _BOARD_SYLLABUS_SOURCE.get("Degree", "Official board/university syllabus")

    content_methodology = _PAGE_TYPE_METHODOLOGY.get(page_type, "syllabus-aligned study material")

    expanded_kw = _build_expanded_keywords(
        page.get("topic_title", ""), page.get("subject_name", ""),
        page.get("board_name", ""), page.get("class_name", ""),
        page.get("chapter_title", ""), page_type,
    )

    # ── Schema.org graph ────────────────────────────────────────────────────
    graph_nodes = [
        {
            "@type": "Article",
            "headline": page.get("title", ""),
            "description": page.get("meta_description", ""),
            "keywords": expanded_kw,
            "author": _ORG_NODE,
            "publisher": _ORG_NODE,
            "datePublished": generated,
            "dateModified": updated,
            "image": og_image_url,
            "mainEntityOfPage": {"@type": "WebPage", "@id": page_url},
            "educationalLevel": edu_level,
            "about": {"@type": "Thing", "name": page.get("topic_title", "")},
            "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai", "name": "Syrabit.ai"},
            "inLanguage": "en-IN",
            "spatialCoverage": _ASSAM_GEO,
            "locationCreated": _ASSAM_GEO,
            "audience": {
                "@type": "EducationalAudience",
                "educationalRole": "student",
                "geographicArea": {"@type": "State", "name": "Assam, India"},
            },
            "educationalAlignment": {
                "@type": "AlignmentObject",
                "alignmentType": "educationalSubject",
                "educationalFramework": syllabus_source,
                "targetName": page.get("subject_name", ""),
                "targetDescription": f"{page.get('chapter_title', '')} — {page.get('topic_title', '')}",
            },
            "sourceOrganization": _ORG_NODE,
        },
        {
            "@type": "LearningResource",
            "name": f"{topic} — {edu_level}".strip(),
            "description": page.get("meta_description", ""),
            "provider": _ORG_NODE,
            "educationalLevel": edu_level,
            "url": page_url,
            "inLanguage": "en-IN",
            "learningResourceType": {"notes": "Study Notes", "definition": "Definitions", "important-questions": "Practice Questions", "mcqs": "Multiple Choice Questions", "examples": "Examples"}.get(page_type, "Study Material"),
            "isAccessibleForFree": True,
            "educationalAlignment": {
                "@type": "AlignmentObject",
                "alignmentType": "educationalSubject",
                "educationalFramework": syllabus_source,
                "targetName": page.get("subject_name", ""),
            },
            "teaches": page.get("topic_title", ""),
            "assesses": page.get("topic_title", "") if page_type in ("mcqs", "important-questions") else None,
            "competencyRequired": f"Basic understanding of {page.get('subject_name', '')}",
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                {"@type": "ListItem", "position": 2, "name": "Library", "item": "https://syrabit.ai/library"},
                {"@type": "ListItem", "position": 3, "name": page.get("subject_name", ""), "item": subject_url},
                {"@type": "ListItem", "position": 4, "name": page.get("topic_title", ""), "item": page_url},
            ],
        },
    ]

    # ── Page-type specific schema ────────────────────────────────────────────
    if page_type == "definition":
        graph_nodes.append({
            "@type": "DefinedTerm",
            "name": page.get("topic_title", ""),
            "description": page.get("meta_description", ""),
            "inDefinedTermSet": {
                "@type": "DefinedTermSet",
                "name": f"{page.get('subject_name', '')} — {board} {cls}",
                "url": f"https://syrabit.ai/{page.get('board_slug','')}/{page.get('class_slug','')}/{page.get('subject_slug','')}",
            },
            "url": page_url,
        })

    # Task #457: QAPage schema for important-questions improves AI engine
    # quotability and helps Google surface them as Q&A rich results.
    if page_type == "important-questions":
        raw_content = page.get("content", "")
        qa_items = []
        current_q = None
        for line in raw_content.split("\n"):
            stripped = line.strip().lstrip("#").replace("**", "").strip()
            if stripped.endswith("?") and len(stripped) > 12:
                current_q = stripped
            elif current_q and len(stripped) > 25:
                qa_items.append({
                    "@type": "Question",
                    "name": current_q,
                    "answerCount": 1,
                    "acceptedAnswer": {"@type": "Answer", "text": stripped[:600]},
                })
                current_q = None
                if len(qa_items) >= 8:
                    break
        if qa_items:
            graph_nodes.append({
                "@type": "QAPage",
                "mainEntity": qa_items[0],
                "about": {"@type": "Thing", "name": page.get("topic_title", "")},
                "additionalProperty": [{"@type": "PropertyValue", "name": "questionCount", "value": len(qa_items)}],
            })

    if page_type == "mcqs":
        raw_content = page.get("content", "")
        mcq_questions = []
        current_q = None
        for line in raw_content.split("\n"):
            stripped = line.strip()
            if stripped and (stripped[0].isdigit() or stripped.startswith("Q")):
                current_q = stripped.lstrip("0123456789).Q ").strip()
            elif current_q and stripped.lower().startswith(("a)", "a.")):
                mcq_questions.append({
                    "@type": "Question",
                    "name": current_q,
                    "acceptedAnswer": {"@type": "Answer", "text": stripped},
                })
                current_q = None
                if len(mcq_questions) >= 10:
                    break
        if mcq_questions:
            graph_nodes.append({
                "@type": "Quiz",
                "name": f"{page.get('topic_title', '')} MCQs — {board} {cls}",
                "educationalLevel": f"{cls} {board}".strip(),
                "about": {"@type": "Thing", "name": page.get("topic_title", "")},
                "hasPart": mcq_questions,
            })

    # ── FAQ extraction for all page types ───────────────────────────────────
    qa_pairs = page.get("qa_pairs", [])
    faq_items = []
    if qa_pairs:
        for qp in qa_pairs[:10]:
            faq_items.append({
                "@type": "Question",
                "name": qp.get("question", ""),
                "acceptedAnswer": {"@type": "Answer", "text": qp.get("answer", "")},
            })
    else:
        raw_content = page.get("content", "")
        lines = raw_content.split("\n") if raw_content else []
        current_q = None
        for line in lines:
            stripped = line.strip().lstrip("#").strip().replace("**", "").strip()
            if stripped.endswith("?") and len(stripped) > 15:
                current_q = stripped
            elif current_q and len(stripped) > 20:
                faq_items.append({
                    "@type": "Question",
                    "name": current_q,
                    "acceptedAnswer": {"@type": "Answer", "text": stripped},
                })
                current_q = None
                if len(faq_items) >= 10:
                    break

    if not faq_items and topic:
        faq_items.append({
            "@type": "Question",
            "name": f"What is {page.get('topic_title', '')} in {page.get('subject_name', '')}?",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": f"{page.get('topic_title', '')} is a topic in {page.get('subject_name', '')} covered under {page.get('chapter_title', '')} for {page.get('board_name', '')} {page.get('class_name', '')} students. Visit Syrabit.ai for detailed study notes, examples, and practice questions.",
            },
        })

    if faq_items:
        graph_nodes.append({
            "@type": "FAQPage",
            "mainEntity": faq_items,
        })

    graph_nodes.append({
        "@type": "WebPage",
        "@id": page_url,
        "name": page.get("title", ""),
        "description": page.get("meta_description", ""),
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": ["article h1", "article > p:first-of-type", "article h2"],
        },
        "significantLink": [subject_url, "https://syrabit.ai/library"],
        "lastReviewed": updated[:10] if updated else "",
        "reviewedBy": _ORG_NODE,
    })

    topic_words = [w.strip() for w in re.split(r'[\s,\-–—/&]+', page.get("topic_title", "")) if len(w.strip()) > 2]
    about_things = [{"@type": "Thing", "name": page.get("topic_title", "")}]
    for tw in topic_words[:5]:
        about_things.append({"@type": "Thing", "name": tw})
    if page.get("chapter_title"):
        about_things.append({"@type": "Thing", "name": page.get("chapter_title", "")})
    graph_nodes[0]["about"] = about_things if len(about_things) > 1 else about_things[0]

    def _strip_none(obj):
        if isinstance(obj, dict):
            return {k: _strip_none(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, list):
            return [_strip_none(i) for i in obj]
        return obj
    ld_json = json.dumps({"@context": "https://schema.org", "@graph": _strip_none(graph_nodes)}, ensure_ascii=False)

    # ── Page-type navigation HTML ────────────────────────────────────────────
    pt_nav_html = ""
    if page_type_links:
        links_html = ""
        for ptl in page_type_links:
            if ptl.get("active"):
                links_html += f'<span class="pt-active">{html_mod.escape(ptl["label"])}</span>'
            else:
                links_html += f'<a class="pt-link" href="{html_mod.escape(ptl["url"])}">{html_mod.escape(ptl["label"])}</a>'
        pt_nav_html = f'<nav class="pt-nav" aria-label="Page types">{links_html}</nav>'

    # ── Related topics HTML ──────────────────────────────────────────────────
    related_html = ""
    if related_topics:
        items = ""
        for rt in related_topics[:6]:
            rt_path = html_mod.escape(rt.get("seo_path", "#"))
            rt_title = html_mod.escape(rt.get("title", ""))
            items += f'<li><a href="https://syrabit.ai{rt_path}">{rt_title}</a></li>'
        related_html = f'<section class="related"><h2>Related Topics in {html_mod.escape(page.get("subject_name",""))}</h2><ul>{items}</ul></section>'

    # ── Prev / Next navigation HTML + link tags ─────────────────────────────
    prevnext_html = ""
    _prev_link = ""
    _next_link = ""
    parts = []
    if prev_topic and prev_topic.get("seo_path"):
        prev_url = f"https://syrabit.ai{html_mod.escape(prev_topic['seo_path'])}"
        parts.append(f'<a class="pn-prev" href="{prev_url}">&larr; {html_mod.escape(prev_topic.get("title","Previous"))}</a>')
        _prev_link = f'<link rel="prev" href="{prev_url}">\n'
    if next_topic and next_topic.get("seo_path"):
        next_url = f"https://syrabit.ai{html_mod.escape(next_topic['seo_path'])}"
        parts.append(f'<a class="pn-next" href="{next_url}">{html_mod.escape(next_topic.get("title","Next"))} &rarr;</a>')
        _next_link = f'<link rel="next" href="{next_url}">\n'
    if parts:
        prevnext_html = f'<nav class="pn-nav" aria-label="Topic navigation">{"".join(parts)}</nav>'

    # ── Task #457: GEO blocks (answer-first summary + Key Facts + attribution)
    answer_summary = page.get("answer_summary") or ""
    key_facts = page.get("key_facts") or []
    if not answer_summary or not key_facts:
        # Older pages (pre-Task #457) don't have these fields stored — derive
        # them on the fly from the content so the upgraded template still
        # renders the GEO blocks for the existing corpus.
        try:
            geo = _compute_geo_score(
                page.get("content", ""),
                page_type,
                context={
                    "topic_title": page.get("topic_title", ""),
                    "subject_name": page.get("subject_name", ""),
                    "chapter_title": page.get("chapter_title", ""),
                },
            )
            if not answer_summary:
                answer_summary = geo.get("answer_summary", "")
            if not key_facts:
                key_facts = geo.get("key_facts", []) or []
        except Exception:
            pass

    answer_html = ""
    if answer_summary:
        answer_html = (
            f'<aside class="answer-first" aria-label="Quick answer">'
            f'<h2>Quick answer</h2>'
            f'<p>{html_mod.escape(answer_summary)}</p>'
            f'</aside>'
        )
    facts_html = ""
    if key_facts:
        items = "".join(
            f'<li>{html_mod.escape(str(f))}</li>' for f in key_facts[:6]
        )
        facts_html = (
            f'<aside class="key-facts" aria-label="Key facts">'
            f'<h2>Key facts</h2><ul>{items}</ul>'
            f'</aside>'
        )
    attribution_html = (
        f'<p class="byline">'
        f'Reviewed by the Syrabit.ai editorial team · '
        f'Last updated {html_mod.escape(updated[:10] if updated else "")}'
        f'</p>'
    )
    geo_top_html = answer_html + facts_html + attribution_html

    return f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} | Syrabit.ai</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{html_mod.escape(page_url)}">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:locale" content="en_IN">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="{html_mod.escape(page_url)}">
<meta property="og:image" content="{og_image_url}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="article:published_time" content="{html_mod.escape(generated)}">
<meta property="article:modified_time" content="{html_mod.escape(updated)}">
<meta property="article:section" content="{subject}">
<meta property="article:tag" content="{topic}">
<meta property="article:tag" content="{subject}">
<meta property="article:tag" content="{board}">
<meta property="article:tag" content="{chapter}">
<meta property="article:tag" content="{cls}">
<meta name="keywords" content="{html_mod.escape(expanded_kw)}">
<meta name="news_keywords" content="{topic}, {subject}, {board}, {cls}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@SyrabitAI">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{og_image_url}">
<meta name="citation_title" content="{title}">
<meta name="citation_author" content="Syrabit.ai">
<meta name="citation_publication_date" content="{html_mod.escape(generated[:10] if generated else '')}">
<meta name="citation_online_date" content="{html_mod.escape(updated[:10] if updated else '')}">
<meta name="citation_publisher" content="Syrabit.ai">
<meta name="citation_public_url" content="{html_mod.escape(page_url)}">
<meta name="dc.title" content="{title}">
<meta name="dc.creator" content="Syrabit.ai">
<meta name="dc.subject" content="{subject} — {board} {cls}">
<meta name="dc.description" content="{desc}">
<meta name="dc.publisher" content="Syrabit.ai">
<meta name="dc.type" content="Text">
<meta name="dc.language" content="en-IN">
<meta name="dc.source" content="https://syrabit.ai">
<meta name="geo.region" content="IN-AS">
<meta name="geo.placename" content="Assam, India">
<meta name="geo.position" content="26.2006;92.9376">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{html_mod.escape(page_url)}">
<link rel="alternate" hreflang="as-IN" href="{html_mod.escape(page_url)}{'&amp;' if '?' in page_url else '?'}lang=as">
<link rel="alternate" hreflang="x-default" href="{html_mod.escape(page_url)}">
<meta name="ICBM" content="26.2006, 92.9376">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
{_prev_link}{_next_link}<script type="application/ld+json">{ld_json}</script>
<style>
body{{font-family:system-ui,sans-serif;max-width:860px;margin:0 auto;padding:1rem 1.25rem;color:#1a1a1a;line-height:1.8;word-spacing:0.02em}}
h1{{font-size:1.75rem;margin-bottom:.5rem;text-wrap:balance}}h2{{font-size:1.3rem;margin-top:2rem;text-wrap:balance}}h3{{font-size:1.1rem;margin-top:1.5rem}}
p{{text-align:justify;margin:0 0 1.1rem}}
img{{max-width:100%;height:auto;border-radius:8px}}
ul{{padding-left:1.5rem;margin:.6rem 0 1.2rem}}ol{{padding-left:1.5rem;margin:.6rem 0 1.2rem}}li{{margin:.35rem 0;line-height:1.75}}
blockquote{{border-left:3px solid #7c3aed;padding:.6rem 0 .6rem 1rem;margin:1.2rem 0;color:#4b5563;font-style:italic;background:#f9fafb;border-radius:0 8px 8px 0}}
.table-responsive{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:1.2rem 0;border-radius:8px;border:1px solid #e5e7eb}}
.table-responsive table{{margin:0;border:none}}
table{{width:100%;border-collapse:collapse;margin:1rem 0}}th{{background:#f3f4f6;font-weight:600;white-space:nowrap}}th,td{{border:1px solid #e5e7eb;padding:.55rem .8rem;text-align:left;vertical-align:top}}
tr:nth-child(even) td{{background:#fafbfc}}
.pt-nav{{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0 1.5rem}}
.pt-link{{padding:.35rem .8rem;border-radius:6px;border:1px solid #d1d5db;color:#374151;text-decoration:none;font-size:.9rem}}
.pt-link:hover{{background:#f3f4f6}}.pt-active{{padding:.35rem .8rem;border-radius:6px;background:#7c3aed;color:#fff;font-size:.9rem;font-weight:600}}
.related{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:1rem 1.25rem;margin:2rem 0}}
.related h2{{margin-top:0;font-size:1.1rem}}.related ul{{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:.5rem}}
.related ul li a{{color:#7c3aed;text-decoration:none;font-size:.9rem}}.related ul li a:hover{{text-decoration:underline}}
.pn-nav{{display:flex;justify-content:space-between;margin:2rem 0;padding-top:1rem;border-top:1px solid #e5e7eb}}
.pn-prev,.pn-next{{color:#7c3aed;text-decoration:none;font-size:.9rem;max-width:45%}}.pn-prev:hover,.pn-next:hover{{text-decoration:underline}}
nav[aria-label="Breadcrumb"]{{font-size:.85rem;color:#6b7280;margin-bottom:.5rem}}
nav[aria-label="Breadcrumb"] a{{color:#7c3aed;text-decoration:none}}
.answer-first{{background:#eef2ff;border:1px solid #c7d2fe;border-left:4px solid #6366f1;border-radius:8px;padding:.85rem 1.1rem;margin:1.25rem 0}}.answer-first h2{{margin:0 0 .35rem;font-size:1rem;color:#4338ca;text-transform:uppercase;letter-spacing:.04em}}.answer-first p{{margin:0;color:#1f2937;line-height:1.65}}
.key-facts{{background:#f0fdf4;border:1px solid #bbf7d0;border-left:4px solid #16a34a;border-radius:8px;padding:.85rem 1.1rem;margin:1.25rem 0}}.key-facts h2{{margin:0 0 .4rem;font-size:1rem;color:#15803d;text-transform:uppercase;letter-spacing:.04em}}.key-facts ul{{margin:.2rem 0 0;padding-left:1.2rem}}.key-facts li{{margin:.2rem 0;color:#1f2937}}
.byline{{font-size:.82rem;color:#6b7280;margin:.5rem 0 1.25rem;font-style:italic}}
.content-info{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:1rem 1.25rem;margin:2rem 0}}
.content-info h2{{margin-top:0;font-size:1rem;color:#334155}}.content-info dl{{margin:0}}.content-info dt{{font-weight:600;color:#475569;margin-top:.5rem;font-size:.9rem}}.content-info dd{{margin:0 0 .25rem 0;color:#64748b;font-size:.85rem}}
footer{{color:#6b7280;font-size:.85rem;margin-top:2rem;padding-top:1rem;border-top:1px solid #e5e7eb}}
.geo-footer{{font-size:.8rem;color:#9ca3af;margin-top:.5rem}}
@media(max-width:640px){{body{{padding:.75rem;line-height:1.7}}h1{{font-size:1.35rem}}h2{{font-size:1.1rem}}h3{{font-size:1rem}}p{{text-align:left}}.pn-nav{{flex-direction:column;gap:.75rem}}.pn-prev,.pn-next{{max-width:100%}}.pt-nav{{gap:.3rem}}.pt-link,.pt-active{{font-size:.8rem;padding:.25rem .6rem}}th,td{{padding:.4rem .6rem;font-size:.84rem}}blockquote{{padding:.5rem 0 .5rem .8rem}}}}
</style>
</head>
<body>
<header>
<nav aria-label="Breadcrumb">
<a href="https://syrabit.ai">Home</a> &rsaquo;
<a href="https://syrabit.ai/library">Library</a> &rsaquo;
<span>{subject}</span> &rsaquo;
<span>{topic}</span>
</nav>
<p><strong>{board}</strong> &middot; {cls} &middot; {subject} &middot; {chapter}</p>
</header>
<main>
{pt_nav_html}
<article>
<h1>{topic} — {board} {cls} {subject}</h1>
<p><em>{desc}</em></p>
{geo_top_html}
{content_html}
</article>
{related_html}
{prevnext_html}
<section class="content-info">
<h2>About This Study Material</h2>
<dl>
<dt>Syllabus Source</dt><dd>{html_mod.escape(syllabus_source)}</dd>
<dt>Content Type</dt><dd>This page contains {html_mod.escape(content_methodology)}.</dd>
<dt>Subject</dt><dd>{subject} — {board} {cls}</dd>
<dt>Chapter</dt><dd>{chapter}</dd>
<dt>Topic</dt><dd>{topic}</dd>
<dt>Editorial Process</dt><dd>Content is prepared by subject-matter contributors, cross-referenced with the official {html_mod.escape(board)} syllabus, and editorially reviewed for factual accuracy, exam relevance, and completeness. Each page follows a structured academic format: formal definitions, detailed explanations, solved examples, exam tips, and practice questions with model answers.</dd>
<dt>Last Updated</dt><dd>{html_mod.escape(updated[:10] if updated else '')}</dd>
<dt>Publisher</dt><dd>Syrabit.ai — Academic content platform for Assam students</dd>
</dl>
</section>
<footer>
<p>Source: <a href="{html_mod.escape(page_url)}">Syrabit.ai — {topic}</a></p>
<p>&copy; Syrabit.ai — Syllabus-aligned study material for {html_mod.escape(board)} ({html_mod.escape(cls)}) students</p>
<p>Content follows the official {html_mod.escape(board)} curriculum. For the latest syllabus, refer to your board/university website.</p>
<p class="geo-footer">Serving students across Assam, India — Guwahati, Jorhat, Dibrugarh, Dhemaji, Tezpur, Silchar, Nagaon, Barpeta, and more.</p>
</footer>
</main>
</body>
</html>"""


async def _build_page_type_links(page: dict, current_type: str, board: str, class_slug: str, subject_slug: str, topic_slug: str) -> list:
    """Return navigation links for all published page types of this topic."""
    sibling_types = await _db.seo_pages.find(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "status": "published"},
        {"_id": 0, "page_type": 1},
    ).to_list(10)
    published_types = {s["page_type"] for s in sibling_types}
    links = []
    for pt in PAGE_TYPES:
        if pt in published_types:
            base = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{topic_slug}"
            url = base if pt == "notes" else f"{base}/{pt}"
            links.append({"type": pt, "label": _PAGE_TYPE_LABELS.get(pt, pt), "url": url, "active": pt == current_type})
    return links


async def _build_related_data(page: dict, board: str, class_slug: str, subject_slug: str, topic_slug: str):
    """Fetch related topics, prev, next for internal linking."""
    topic = await _db.topics.find_one({"slug": topic_slug}, {"_id": 0})
    if not topic:
        return [], None, None
    rel = await get_related_topics(topic_slug=topic_slug)
    related = rel.get("related", [])
    prev_t = rel.get("prev")
    next_t = rel.get("next")
    return related, prev_t, next_t


@router.get("/html/homepage", response_class=HTMLResponse)
async def get_homepage_html():
    subjects = await _db.seo_pages.aggregate([
        {"$match": {"status": "published", "page_type": "notes"}},
        {"$group": {
            "_id": {"board": "$board_slug", "cls": "$class_slug", "subj": "$subject_slug"},
            "subject_name": {"$first": "$subject_name"},
            "board_name": {"$first": "$board_name"},
            "class_name": {"$first": "$class_name"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
        {"$limit": 50},
    ]).to_list(50)

    total_pages = await _db.seo_pages.count_documents({"status": "published"})

    title = "Syrabit.ai — AI Study Browser for Assam Students | Founded by Dipak Rai"
    # ── Meta description follows the narrative the founder wants Google
    # to surface: 1) what Syrabit is, 2) who built it, 3) where & against
    # what odds, 4) what it offers. Google uses the first ~155 chars as
    # the SERP snippet. ──
    desc = (
        "Syrabit.ai is an AI-powered study browser for AHSEC, SEBA and Degree students in Assam. "
        "Founded in 2026 by Dipak Rai and bootstrapped from Assam — a region with limited "
        "edtech infrastructure and tech-talent depth — Syrabit offers syllabus-aligned notes, "
        "previous year questions, MCQs, an AI tutor in English and Assamese, and exam prep "
        f"across {len(subjects)} subjects and {total_pages}+ pages."
    )

    subj_html_parts = []
    for s in subjects:
        g = s["_id"]
        url = f"https://syrabit.ai/{g['board']}/{g['cls']}/{g['subj']}"
        label = f"{s.get('subject_name', g['subj'])} — {s.get('board_name', g['board'])} {s.get('class_name', g['cls'])}"
        subj_html_parts.append(
            f'<li><a href="{url}">{html_mod.escape(label)}</a> <small>({s["count"]} topics)</small></li>'
        )
    subj_list = "\n".join(subj_html_parts)

    schema = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": "https://syrabit.ai/#website",
         "name": "Syrabit.ai", "url": "https://syrabit.ai",
         "description": desc,
         "publisher": {"@id": "https://syrabit.ai/#organization"},
         "potentialAction": {"@type": "SearchAction", "target": "https://syrabit.ai/search?q={search_term_string}",
                             "query-input": "required name=search_term_string"}},
        _ORG_NODE,
        _FOUNDER_NODE,
        {"@type": "WebPage", "@id": "https://syrabit.ai/#webpage",
         "url": "https://syrabit.ai", "name": title, "description": desc,
         "isPartOf": {"@id": "https://syrabit.ai/#website"},
         "about": {"@id": "https://syrabit.ai/#organization"},
         "primaryImageOfPage": {"@type": "ImageObject", "url": "https://syrabit.ai/opengraph.jpg"}},
    ]}, ensure_ascii=False)

    html_out = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
<meta name="description" content="{html_mod.escape(desc)}">
<link rel="canonical" href="https://syrabit.ai">
<meta property="og:title" content="{html_mod.escape(title)}">
<meta property="og:description" content="{html_mod.escape(desc)}">
<meta property="og:url" content="https://syrabit.ai">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="twitter:card" content="summary_large_image">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<meta name="geo.region" content="IN-AS">
<meta name="geo.placename" content="Assam, India">
<meta name="geo.position" content="26.2006;92.9376">
<meta name="ICBM" content="26.2006, 92.9376">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta property="og:locale" content="en_IN">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="https://syrabit.ai">
<script type="application/ld+json">{schema}</script>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:1rem;color:#1a1a1a;line-height:1.6}}
a{{color:#2563eb;text-decoration:none}}a:hover{{text-decoration:underline}}
h1{{font-size:2rem}}h2{{font-size:1.4rem;margin-top:2rem}}
ul{{list-style:none;padding:0}}li{{margin:.5rem 0}}
small{{color:#6b7280}}.stats{{display:flex;gap:2rem;margin:1rem 0}}
.stat{{text-align:center}}.stat strong{{display:block;font-size:1.5rem;color:#2563eb}}
footer{{margin-top:3rem;border-top:1px solid #e5e7eb;padding-top:1rem;font-size:.85rem;color:#9ca3af}}
.geo-footer{{font-size:.8rem;color:#9ca3af;margin-top:.5rem}}
@media(max-width:640px){{body{{padding:.75rem}}h1{{font-size:1.5rem}}h2{{font-size:1.2rem}}.stats{{flex-direction:column;gap:.5rem}}}}
</style>
</head>
<body>
<header>
<h1>Syrabit.ai</h1>
<p><strong>Syrabit.ai is an AI-powered study browser built for AHSEC, SEBA and Degree students in Assam.</strong> It combines a curated syllabus library with a multi-model AI tutor that answers in both English and Assamese, grounded in official Assam Board curricula.</p>
<div class="stats">
<div class="stat"><strong>{total_pages}+</strong>Study pages</div>
<div class="stat"><strong>{len(subjects)}</strong>Subjects</div>
</div>
</header>
<main>

<section id="founder">
<h2>About the Founder — Dipak Rai</h2>
<p>Syrabit.ai was founded in 2026 by <strong>Dipak Rai</strong>, an independent builder from Assam. He started Syrabit after seeing how few learning tools served Assamese-medium and Assam Board students in a way that respected the local syllabus, language, and exam structure. The platform is built solo, end-to-end — frontend, backend, AI integration, and content pipeline.</p>
</section>

<section id="built-in-assam">
<h2>Built in Assam — and Why That Matters</h2>
<p>Most Indian edtech is built in Bangalore, Delhi or Hyderabad, where capital, engineering talent, and startup infrastructure are concentrated. Assam has very little of that ecosystem: there are no major venture funds based in the state, the local pool of full-stack and AI engineers is small, reliable connectivity is uneven outside the major cities, and there is no established edtech blueprint for AHSEC or SEBA boards to copy.</p>
<p>Building a production-grade AI study platform from Assam therefore costs more, takes longer, and carries more execution risk than building the same product in a metro. Syrabit.ai was bootstrapped under exactly those constraints — without venture funding, without a hired engineering team, and without an existing Assam-board edtech to learn from. Every architectural decision (Cloudflare edge, Railway backend, MongoDB Atlas, Sarvam AI for Indic speech, multi-model LLM routing) was made to keep recurring costs low enough that one founder could carry them while serving students at a price they can afford.</p>
<p>The result is the only edtech platform we are aware of that is built in Assam, by an Assamese founder, specifically for the Assam Board syllabus and the Assamese language — instead of being an afterthought inside a national platform.</p>
</section>

<section id="features">
<h2>What Syrabit Offers</h2>
<ul>
<li><strong>AI tutor</strong> — multi-model AI chat (Cerebras, Groq, Fireworks, Gemini, xAI) that answers your syllabus questions in English or Assamese, with sources cited.</li>
<li><strong>Syllabus-aligned notes</strong> — topic-wise study notes for AHSEC, SEBA, and NEP FYUGP Degree subjects.</li>
<li><strong>Previous year questions (PYQs)</strong> — with full solutions, faithful HTML replicas of original board papers.</li>
<li><strong>Mark-wise question banks</strong> — 1, 2, 3, 5, and 10-mark questions sorted by chapter.</li>
<li><strong>MCQs &amp; flashcards</strong> — quick revision tools with memory tricks.</li>
<li><strong>Indic Text-to-Speech</strong> — listen to notes and answers in Assamese via Sarvam AI.</li>
<li><strong>Mobile + offline</strong> — installable as a PWA, with cached materials that work in low-connectivity areas of Assam.</li>
<li><strong>Free tier</strong> — 30 daily AI credits, no card required; Starter and Pro plans for heavier use.</li>
<li><strong>Bilingual experience</strong> — entire interface available in English and অসমীয়া.</li>
</ul>
</section>

<section id="subjects">
<h2>Browse Subjects</h2>
<ul>
{subj_list}
</ul>
</section>
</main>
<footer>
<p>&copy; Syrabit.ai — Free syllabus-aligned exam prep for Assam Board (AHSEC/SEBA) &amp; Degree students</p>
<p class="geo-footer">Serving students in Guwahati, Jorhat, Dibrugarh, Dhemaji, Tezpur, Silchar, and across Assam, India</p>
<p><a href="https://syrabit.ai/library">Full Library</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></p>
</footer>
</body>
</html>"""
    return HTMLResponse(content=html_out)


@router.get("/html/about", response_class=HTMLResponse)
async def get_about_html():
    total_pages = await _db.seo_pages.count_documents({"status": "published"})
    total_subjects = await _db.subjects.count_documents({})
    total_chapters = await _db.chapters.count_documents({})

    subjects_agg = await _db.seo_pages.aggregate([
        {"$match": {"status": "published", "page_type": "notes"}},
        {"$group": {
            "_id": {"board": "$board_slug", "cls": "$class_slug", "subj": "$subject_slug"},
            "subject_name": {"$first": "$subject_name"},
            "board_name": {"$first": "$board_name"},
            "class_name": {"$first": "$class_name"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]).to_list(100)

    subj_items = []
    for s in subjects_agg:
        g = s["_id"]
        url = f"https://syrabit.ai/{g['board']}/{g['cls']}/{g['subj']}"
        label = f"{s.get('subject_name', g['subj'])} ({s.get('board_name', g['board'])} {s.get('class_name', g['cls'])})"
        subj_items.append(f'<li><a href="{url}">{html_mod.escape(label)}</a> &mdash; {s["count"]} topics</li>')
    subj_list_html = "\n".join(subj_items)

    title = "About Syrabit.ai — AI-Powered Study Platform for Assam Board & Degree Students"
    desc = (
        "Syrabit.ai is a free, AI-powered educational platform built for AHSEC, SEBA, and Degree (NEP FYUGP) "
        "students in Assam, India. It provides syllabus-aligned study notes, MCQs, previous year questions, "
        "and an AI tutor (Syra) covering subjects across Assam Higher Secondary Education Council, "
        "Board of Secondary Education Assam, and university degree programmes."
    )
    page_url = "https://syrabit.ai/about"

    schema = json.dumps({"@context": "https://schema.org", "@graph": [
        {
            "@type": "Organization",
            "name": "Syrabit.ai",
            "alternateName": ["Syrabit", "Syra AI"],
            "url": "https://syrabit.ai",
            "logo": {"@type": "ImageObject", "url": "https://syrabit.ai/icons/icon-192x192.png"},
            "image": "https://syrabit.ai/opengraph.jpg",
            "description": desc,
            "foundingDate": "2026",
            "foundingLocation": {"@type": "Place", "name": "Assam, India"},
            "knowsAbout": [
                "AHSEC syllabus and examinations",
                "SEBA syllabus and examinations",
                "NEP FYUGP curriculum for Assam universities",
                "Gauhati University syllabus",
                "Dibrugarh University syllabus",
                "Cotton University syllabus",
                "Higher education in Assam",
                "Assam Board exam preparation",
                "AI-powered education",
                "Syllabus-aligned study material",
            ],
            "sameAs": ["https://twitter.com/SyrabitAI"],
            "areaServed": {
                "@type": "State",
                "name": "Assam",
                "containedInPlace": {"@type": "Country", "name": "India"},
            },
            "address": {
                "@type": "PostalAddress",
                "addressRegion": "Assam",
                "addressCountry": "IN",
            },
            "slogan": "Your AI study companion for Assam Board exams",
            "serviceType": "Educational Technology",
            "hasOfferCatalog": {
                "@type": "OfferCatalog",
                "name": "Study Material",
                "itemListElement": [
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": "Syllabus-aligned study notes", "description": "Topic-wise notes for AHSEC, SEBA, and Degree subjects aligned to official board syllabi"}},
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": "AI Tutor (Syra)", "description": "AI-powered study assistant that answers curriculum-specific questions with grounded, syllabus-aligned responses"}},
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": "MCQs and Practice Questions", "description": "Multiple-choice questions with explanations for exam revision"}},
                    {"@type": "Offer", "itemOffered": {"@type": "Service", "name": "Previous Year Questions", "description": "Solved previous year questions with model answers for Assam Board exams"}},
                ],
            },
        },
        {
            "@type": "WebSite",
            "name": "Syrabit.ai",
            "url": "https://syrabit.ai",
            "description": desc,
            "inLanguage": "en-IN",
            "potentialAction": {
                "@type": "SearchAction",
                "target": "https://syrabit.ai/search?q={search_term_string}",
                "query-input": "required name=search_term_string",
            },
        },
        {
            "@type": "EducationalOrganization",
            "name": "Syrabit.ai",
            "url": "https://syrabit.ai",
            "description": (
                "Syrabit.ai is an AI-powered academic content platform that produces syllabus-aligned study material "
                "for AHSEC (Assam Higher Secondary Education Council), SEBA (Board of Secondary Education, Assam), "
                "and NEP FYUGP Degree students. Content follows official board and university curricula "
                "and is editorially reviewed for accuracy, exam relevance, and academic depth."
            ),
            "areaServed": {"@type": "State", "name": "Assam", "containedInPlace": {"@type": "Country", "name": "India"}},
            "address": {"@type": "PostalAddress", "addressRegion": "Assam", "addressCountry": "IN"},
        },
        {
            "@type": "WebPage",
            "@id": page_url,
            "name": title,
            "description": desc,
            "url": page_url,
            "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai", "name": "Syrabit.ai"},
            "about": {"@type": "Organization", "name": "Syrabit.ai"},
            "inLanguage": "en-IN",
        },
        {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "What is Syrabit.ai?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Syrabit.ai is a free, AI-powered educational platform designed specifically for students in Assam, India. It covers AHSEC (Higher Secondary), SEBA (Secondary), and Degree (NEP FYUGP) curricula with syllabus-aligned study notes, MCQs, previous year questions, and an AI tutor named Syra."},
                },
                {
                    "@type": "Question",
                    "name": "Which boards and universities does Syrabit cover?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Syrabit covers three educational boards: AHSEC (Assam Higher Secondary Education Council) for Class 11-12, SEBA (Board of Secondary Education, Assam) for Class 9-10, and Degree programmes under the NEP 2020 FYUGP curriculum adopted by Gauhati University, Dibrugarh University, Cotton University, and other Assam universities."},
                },
                {
                    "@type": "Question",
                    "name": "What is Syra AI?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Syra is an AI-powered study assistant built into Syrabit.ai. Students can ask Syra questions about any topic in their syllabus and receive accurate, curriculum-aligned answers. Syra uses Retrieval-Augmented Generation (RAG) to ground its responses in actual syllabus content, ensuring factual accuracy for Assam Board exams."},
                },
                {
                    "@type": "Question",
                    "name": "Is Syrabit.ai free?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Syrabit.ai offers free access to study notes, MCQs, previous year questions, and the AI tutor. Premium features with additional daily AI chat limits and priority access are available through affordable subscription plans designed for Assam students."},
                },
                {
                    "@type": "Question",
                    "name": "What subjects are available on Syrabit?",
                    "acceptedAnswer": {"@type": "Answer", "text": f"Syrabit currently covers {total_subjects} subjects across AHSEC, SEBA, and Degree programmes. Content is continuously expanding with new subjects, chapters, and topics being added regularly. Each subject includes topic-wise notes, solved examples, MCQs, important questions, and previous year questions."},
                },
                {
                    "@type": "Question",
                    "name": "How is content quality maintained on Syrabit?",
                    "acceptedAnswer": {"@type": "Answer", "text": "Content on Syrabit is prepared by subject-matter contributors and cross-referenced with the official AHSEC, SEBA, and university syllabi. Each page follows a structured academic format: formal definitions, detailed explanations, solved examples with Assam-specific context, exam tips, and practice questions with model answers. The AI tutor (Syra) uses RAG technology to ground responses in verified syllabus content."},
                },
            ],
        },
    ]}, ensure_ascii=False)

    html_out = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
<meta name="description" content="{html_mod.escape(desc)}">
<link rel="canonical" href="{html_mod.escape(page_url)}">
<meta property="og:title" content="{html_mod.escape(title)}">
<meta property="og:description" content="{html_mod.escape(desc)}">
<meta property="og:url" content="{html_mod.escape(page_url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@SyrabitAI">
<meta name="twitter:title" content="{html_mod.escape(title)}">
<meta name="twitter:description" content="{html_mod.escape(desc)}">
<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<meta name="geo.region" content="IN-AS">
<meta name="geo.placename" content="Assam, India">
<meta name="geo.position" content="26.2006;92.9376">
<meta name="ICBM" content="26.2006, 92.9376">
<meta property="og:locale" content="en_IN">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{html_mod.escape(page_url)}">
<link rel="alternate" hreflang="as-IN" href="{html_mod.escape(page_url)}{'&amp;' if '?' in page_url else '?'}lang=as">
<link rel="alternate" hreflang="x-default" href="{html_mod.escape(page_url)}">
<script type="application/ld+json">{schema}</script>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:860px;margin:0 auto;padding:1.25rem;color:#1a1a1a;line-height:1.7}}
a{{color:#7c3aed;text-decoration:none}}a:hover{{text-decoration:underline}}
h1{{font-size:2rem;margin-bottom:.25rem}}h2{{font-size:1.4rem;margin-top:2.5rem;border-bottom:1px solid #e5e7eb;padding-bottom:.35rem}}
h3{{font-size:1.15rem;margin-top:1.5rem;color:#374151}}
p{{margin:.75rem 0}}
ul{{padding-left:1.5rem}}li{{margin:.4rem 0}}
.highlight{{background:#f5f3ff;border-left:4px solid #7c3aed;padding:.75rem 1rem;margin:1rem 0;border-radius:0 6px 6px 0}}
.stats-row{{display:flex;gap:1.5rem;margin:1.25rem 0;flex-wrap:wrap}}
.stat-card{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:.75rem 1.25rem;text-align:center;min-width:100px}}
.stat-card strong{{display:block;font-size:1.6rem;color:#7c3aed}}
.stat-card span{{font-size:.85rem;color:#64748b}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #e5e7eb;padding:.5rem .75rem;text-align:left;font-size:.9rem}}
th{{background:#f8fafc;font-weight:600}}
.faq-q{{font-weight:600;margin-top:1.25rem;color:#1e1b4b}}
.faq-a{{margin:.25rem 0 1rem 0;color:#374151}}
nav[aria-label="Breadcrumb"]{{font-size:.9rem;color:#6b7280;margin-bottom:1.25rem}}
nav[aria-label="Breadcrumb"] a{{color:#7c3aed}}
.section-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;margin:1rem 0}}
.grid-card{{background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;padding:1rem}}
.grid-card h3{{margin-top:0;font-size:1rem;color:#7c3aed}}
footer{{margin-top:3rem;border-top:1px solid #e5e7eb;padding-top:1rem;font-size:.85rem;color:#9ca3af}}
.geo-footer{{font-size:.8rem;color:#9ca3af;margin-top:.5rem}}
@media(max-width:640px){{body{{padding:.75rem}}h1{{font-size:1.5rem}}h2{{font-size:1.2rem}}.stats-row{{flex-direction:column;gap:.75rem}}.section-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<nav aria-label="Breadcrumb">
<a href="https://syrabit.ai">Home</a> &rsaquo;
<span>About Syrabit.ai</span>
</nav>

<header>
<h1>About Syrabit.ai</h1>
<p><strong>Syrabit.ai</strong> is a free, AI-powered educational platform purpose-built for students in <strong>Assam, India</strong>. It serves students across three educational boards — <strong>AHSEC</strong> (Assam Higher Secondary Education Council), <strong>SEBA</strong> (Board of Secondary Education, Assam), and <strong>Degree</strong> (NEP 2020 FYUGP curriculum) — with syllabus-aligned study material and an AI tutor named <strong>Syra</strong>.</p>

<div class="stats-row">
<div class="stat-card"><strong>{total_pages}+</strong><span>Study Pages</span></div>
<div class="stat-card"><strong>{total_subjects}</strong><span>Subjects</span></div>
<div class="stat-card"><strong>{total_chapters}</strong><span>Chapters</span></div>
<div class="stat-card"><strong>3</strong><span>Boards Covered</span></div>
</div>
</header>

<main>

<h2>What Is Syrabit.ai?</h2>
<p>Syrabit.ai is an academic content platform and AI study assistant that produces syllabus-aligned study material for students in Assam. Unlike generic educational sites, every piece of content on Syrabit is mapped directly to the official syllabi of AHSEC, SEBA, or the NEP FYUGP degree curriculum adopted by Assam universities (Gauhati University, Dibrugarh University, Cotton University, and others).</p>

<div class="highlight">
<strong>Mission:</strong> To make quality, exam-relevant study material freely accessible to every student in Assam — regardless of location, coaching access, or economic background.
</div>

<h2>Boards &amp; Curricula Covered</h2>
<table>
<thead>
<tr><th>Board / Programme</th><th>Full Name</th><th>Classes</th><th>Syllabus Source</th></tr>
</thead>
<tbody>
<tr><td>AHSEC</td><td>Assam Higher Secondary Education Council</td><td>Class 11 &amp; 12 (HS 1st &amp; 2nd Year)</td><td>Official AHSEC syllabus</td></tr>
<tr><td>SEBA</td><td>Board of Secondary Education, Assam</td><td>Class 9 &amp; 10 (HSLC)</td><td>Official SEBA syllabus</td></tr>
<tr><td>Degree (NEP FYUGP)</td><td>National Education Policy 2020 Four-Year Undergraduate Programme</td><td>Semesters 1–8</td><td>NEP 2020 FYUGP as adopted by Gauhati University, Dibrugarh University, Cotton University</td></tr>
</tbody>
</table>

<h2>What Syrabit Offers</h2>
<div class="section-grid">
<div class="grid-card">
<h3>Study Notes</h3>
<p>Topic-wise notes following a structured academic format: definition, explanation, solved examples with Assam-specific context, and exam tips. Every note is aligned to the official board syllabus.</p>
</div>
<div class="grid-card">
<h3>Syra AI Tutor</h3>
<p>An AI-powered study assistant that answers syllabus-specific questions. Syra uses Retrieval-Augmented Generation (RAG) to ground every answer in actual chapter content — no hallucinated or off-syllabus information.</p>
</div>
<div class="grid-card">
<h3>MCQs &amp; Practice</h3>
<p>Multiple-choice questions with correct answers and explanations for quick revision, covering key concepts from each chapter of the syllabus.</p>
</div>
<div class="grid-card">
<h3>Previous Year Questions</h3>
<p>Solved previous year questions (PYQs) with model answers, curated from actual AHSEC, SEBA, and university exams for targeted exam preparation.</p>
</div>
<div class="grid-card">
<h3>Important Questions</h3>
<p>Mark-wise important questions identified from syllabus weightage analysis and previous year paper trends, organized by chapter and topic.</p>
</div>
<div class="grid-card">
<h3>Definitions &amp; Examples</h3>
<p>Formal academic definitions and solved examples following the problem, approach, step-by-step solution, and exam tip format.</p>
</div>
</div>

<h2>How Syra AI Works</h2>
<p>Syra is the AI tutor built into Syrabit.ai. It is designed specifically for Assam Board exam preparation:</p>
<ul>
<li><strong>Syllabus-Grounded Answers:</strong> Syra uses RAG (Retrieval-Augmented Generation) to retrieve relevant chapter content before generating a response. Every answer is grounded in actual syllabus material — not generic internet knowledge.</li>
<li><strong>Multi-Subject Support:</strong> Students can ask questions about any subject and topic in their enrolled curriculum. Syra identifies the relevant board, subject, and chapter automatically.</li>
<li><strong>Exam-Focused:</strong> Responses include exam tips, mark allocations, and important distinctions frequently tested in Assam Board exams.</li>
<li><strong>Quality Guards:</strong> Syra explicitly declines questions outside the student's syllabus to prevent misinformation and keep study sessions focused.</li>
</ul>

<h2>What Makes Syrabit Unique</h2>
<p>Syrabit is not another generic study app. It is purpose-built for a specific student population (Assam Board) with capabilities that most educational platforms lack:</p>

<div class="section-grid">
<div class="grid-card">
<h3>Zero-Hallucination AI Tutor</h3>
<p>Unlike generic AI chatbots, Syra refuses to answer questions outside the student's syllabus. It uses a multi-stage RAG pipeline with relevance gating to ensure every answer is grounded in actual chapter content. If the system cannot find relevant material with high confidence, it explicitly declines rather than making something up.</p>
</div>
<div class="grid-card">
<h3>5-Provider LLM Failover</h3>
<p>No single point of AI failure. The system uses a multi-provider cascade — if one AI provider is slow or unavailable, it automatically fails over to the next in milliseconds. Multiple independent providers ensure students almost never experience AI downtime. Each provider has independent timeout controls.</p>
</div>
<div class="grid-card">
<h3>Syllabus-Native Architecture</h3>
<p>The entire database schema is structured around the Assam education system: Board → Class/Semester → Stream/Course Type → Subject → Chapter → Topic. The system supports NEP FYUGP course types (AEC, SEC, MDC, VAC, GE) that most platforms don't even know exist. An automated PDF syllabus importer can parse official university documents into this hierarchy.</p>
</div>
<div class="grid-card">
<h3>Chat-to-Content Pipeline</h3>
<p>High-quality AI chat answers can be "promoted" into permanent QA pairs by admins. These QA pairs are automatically grouped by topic and published as structured FAQ pages with Schema.org markup — turning student conversations into SEO-indexed study material.</p>
</div>
<div class="grid-card">
<h3>Real-Time Web Search Fallback</h3>
<p>When internal RAG content isn't sufficient, Syra uses a custom web crawler (SyrabitBot) with Playwright for JavaScript-rendered pages and Trafilatura for clean text extraction. It can even fetch and extract text from online PDFs in real-time during a conversation.</p>
</div>
<div class="grid-card">
<h3>Sarvam AI Voice Integration</h3>
<p>Built-in text-to-speech with support for Assamese, Hindi, and English using multiple Indian voice speakers. TTS responses are cached to minimize latency. Translation and transliteration between major Indian languages is also supported.</p>
</div>
<div class="grid-card">
<h3>Intelligent Content Analytics</h3>
<p>Custom session tracking with visitor and session IDs measures page counts, bounce rates, and session duration. Library interaction analytics track specific events (AI clicks, subject views, search queries) to identify content gaps and student needs.</p>
</div>
<div class="grid-card">
<h3>Academic Personalization</h3>
<p>Onboarding captures each student's board, class, stream, and course type. The dashboard, library, and AI tutor are personalized to show only content relevant to the student's enrolled curriculum — no information overload from other boards or classes.</p>
</div>
</div>

<h2>Content Quality &amp; Editorial Process</h2>
<p>Content on Syrabit follows a rigorous editorial process:</p>
<ul>
<li>Content is prepared by subject-matter contributors familiar with Assam Board curricula</li>
<li>Every page is cross-referenced with the official AHSEC, SEBA, or university syllabus</li>
<li>Content is reviewed for factual accuracy, exam relevance, and academic depth</li>
<li>Each study note follows a structured format: formal definitions, detailed explanations, solved examples, exam tips, and practice questions with model answers</li>
<li>AI-generated content undergoes quality scoring and human review before publication</li>
</ul>

<h2>Available Subjects</h2>
<ul>
{subj_list_html}
</ul>
<p>New subjects, chapters, and topics are added regularly. Visit the <a href="https://syrabit.ai/library">full library</a> for the latest content.</p>

<h2>Who Is Syrabit For?</h2>
<ul>
<li><strong>AHSEC students</strong> preparing for Higher Secondary (HS) 1st and 2nd Year exams in Assam</li>
<li><strong>SEBA students</strong> preparing for HSLC (Class 10) and Class 9 exams</li>
<li><strong>Degree students</strong> enrolled in NEP FYUGP programmes at Gauhati University, Dibrugarh University, Cotton University, and other Assam universities</li>
<li><strong>Teachers and educators</strong> looking for structured, syllabus-aligned reference material</li>
<li><strong>Parents</strong> seeking a free, reliable study resource for their children's Assam Board exams</li>
</ul>

<h2>Pricing &amp; Access Model</h2>
<p>Syrabit uses a freemium model designed for Indian students:</p>
<ul>
<li><strong>Free tier:</strong> All study notes, MCQs, definitions, and examples are freely accessible without login.</li>
<li><strong>Paid plans:</strong> The AI tutor (Syra) uses a daily credit system. Affordable one-time payment plans starting from ₹99 provide higher daily AI credits, faster rate limits, and full document access.</li>
<li><strong>Payments:</strong> Processed in INR via UPI, cards, and net banking — designed for the Indian student market.</li>
</ul>
<p>Visit the <a href="https://syrabit.ai/pricing">pricing page</a> for current plan details.</p>

<h2>Geographic Focus</h2>
<p>Syrabit.ai is specifically designed for students in <strong>Assam, India</strong>. The platform's content follows Assam-specific board syllabi and university curricula. It serves students across all districts including Guwahati (Kamrup Metropolitan), Jorhat, Dibrugarh, Tezpur (Sonitpur), Silchar (Cachar), Nagaon, Barpeta, Dhemaji, Nalbari, Bongaigaon, Goalpara, Kokrajhar, Lakhimpur, Sivasagar, Golaghat, Tinsukia, Darrang, and more.</p>

<h2>Technical Architecture &amp; Build Quality</h2>
<p>Syrabit.ai is built as a production-grade, full-stack AI application engineered for reliability, accuracy, and low-cost operation — optimized for students in Assam who often have limited bandwidth and older devices.</p>

<h3>System Architecture</h3>
<table>
<thead><tr><th>Layer</th><th>Technology</th><th>Purpose</th></tr></thead>
<tbody>
<tr><td>Frontend</td><td>React + Vite (PWA)</td><td>Single-page application with offline support, installable on mobile/desktop</td></tr>
<tr><td>Backend API</td><td>Python FastAPI + Gunicorn</td><td>Async API server with multi-worker concurrency for handling concurrent student requests</td></tr>
<tr><td>Primary Database</td><td>MongoDB</td><td>Stores subjects, chapters, syllabus content, SEO pages, and content embeddings</td></tr>
<tr><td>User Database</td><td>PostgreSQL (Replit)</td><td>User accounts, session tracking, credit management, and activity logs</td></tr>
<tr><td>Cache &amp; Rate Limiting</td><td>Redis</td><td>Distributed caching for API responses, rate limiting, and session state</td></tr>
<tr><td>Authentication</td><td>Google OAuth 2.0</td><td>Secure sign-in via Google accounts — no password management required</td></tr>
<tr><td>Payments</td><td>Razorpay</td><td>UPI, cards, and net banking for Indian students — INR pricing with secure webhook verification</td></tr>
</tbody>
</table>

<h3>AI &amp; RAG Pipeline — How Syra Answers Questions</h3>
<p>Syra is not a generic chatbot. It uses a multi-stage Retrieval-Augmented Generation (RAG) pipeline specifically designed to prevent hallucination and keep answers within the student's syllabus:</p>
<ol>
<li><strong>Syllabus Matching:</strong> When a student asks a question, the system first identifies which board (AHSEC/SEBA/Degree), subject, and chapter the question relates to. This uses semantic similarity matching against pre-embedded syllabus topics and chapter headings.</li>
<li><strong>Content Retrieval:</strong> The RAG engine retrieves the most relevant chapter sections using vector similarity search. A relevance gate ensures only high-quality matches are used — if no content passes the confidence threshold, Syra declines the question rather than hallucinating.</li>
<li><strong>Reranking:</strong> Retrieved content chunks are reranked using a dedicated reranking model to prioritize the most exam-relevant sections before they are passed to the language model.</li>
<li><strong>Topic-Aware Extraction:</strong> A fuzzy heading matcher extracts the precise topic section from chapter content — not just the first N characters, but the actual section about the queried topic (e.g., extracting the "Personality and Dresses of Yogi" section from a Yoga chapter, not the etymology section that happens to appear first).</li>
<li><strong>Grounded Generation:</strong> The extracted syllabus content is injected into the LLM prompt as grounding context. The prompt includes strict instructions to answer only from the provided content, cite chapter references, and include exam tips.</li>
<li><strong>Out-of-Syllabus Guard:</strong> If a question falls outside the student's enrolled syllabus, Syra explicitly declines with a helpful message — preventing misinformation. This guard is only relaxed when there is a confirmed high-confidence syllabus topic match.</li>
</ol>

<h3>Multi-Provider LLM Failover</h3>
<p>Syrabit does not depend on a single AI provider. The system uses an ordered cascade of multiple LLM providers that automatically fails over if one provider is down, rate-limited, or slow. The cascade includes India-optimized models for low latency, ultra-fast inference providers, and high-quality general-purpose models as fallbacks.</p>
<ul>
<li>Each provider slot has independent timeout controls for fast failover</li>
<li>If a provider fails or is too slow, the system instantly moves to the next provider</li>
<li>Students almost never experience AI downtime — all providers must fail simultaneously</li>
<li>The provider selection is optimized for the Indian student use case, prioritizing low-latency models first</li>
</ul>

<h3>Content Generation &amp; Quality Pipeline</h3>
<p>Study material on Syrabit is not manually typed — it goes through a structured AI-assisted content pipeline:</p>
<ol>
<li><strong>Syllabus Ingestion:</strong> Official board syllabi (AHSEC, SEBA, university FYUGP) are parsed into structured subject → chapter → topic hierarchies and stored in MongoDB.</li>
<li><strong>Document Upload &amp; Chunking:</strong> Textbook PDFs and reference materials are uploaded, chunked, and indexed. Each chunk is embedded using vector embeddings for later retrieval.</li>
<li><strong>AI Content Generation:</strong> For each topic, the system generates study notes following a strict format: formal definition → detailed explanation → solved examples with Assam-specific context → exam tips → practice questions with model answers.</li>
<li><strong>Quality Scoring:</strong> Generated content is scored on completeness, syllabus alignment, and factual accuracy. Content below the quality threshold is flagged for regeneration or manual review.</li>
<li><strong>SEO Optimization:</strong> Published content is automatically wrapped in SEO-optimized HTML with Schema.org structured data (Article, LearningResource, FAQPage, BreadcrumbList), OpenGraph tags, and geo-targeting meta tags for Assam.</li>
<li><strong>Multi-Format Publishing:</strong> Each topic can be published as multiple page types — notes, definitions, MCQs, important questions, solved examples — each with its own optimized template and structured data.</li>
</ol>

<h3>Semantic Search &amp; Syllabus Embeddings</h3>
<p>Syrabit uses vector embeddings for intelligent content discovery:</p>
<ul>
<li><strong>Syllabus Embedding:</strong> Every chapter heading and topic name across all boards is embedded as vectors. When a student asks a question, the query is compared against these embeddings to identify the relevant subject and chapter — even if the student phrases the question differently from the textbook heading.</li>
<li><strong>Fuzzy Topic Matching:</strong> A fuzzy matching system maps student queries to exact syllabus topics. This handles variations in naming (e.g., "Yogini dress" matches "Personality and Dresses of Yogi") that pure keyword search would miss.</li>
<li><strong>Relevance Gating:</strong> The system uses multiple configurable similarity thresholds to ensure only genuinely relevant content is retrieved. Below-threshold queries are declined rather than answered with irrelevant material.</li>
</ul>

<h3>Voice &amp; Multilingual Services</h3>
<p>Many students in Assam are more comfortable in Assamese or Hindi than English. Syrabit integrates voice and language services for Indian language accessibility:</p>
<ul>
<li><strong>Text-to-Speech:</strong> AI responses and study notes can be read aloud in Assamese, Hindi, and English with multiple Indian voice speakers. Audio is cached to reduce latency on repeat plays. Configurable pace, pitch, and loudness.</li>
<li><strong>Translation:</strong> Content can be translated between major Indian languages including Assamese, Hindi, Bengali, and English.</li>
<li><strong>Transliteration:</strong> Script conversion between Devanagari, Assamese, Bengali, and Latin scripts.</li>
<li><strong>India-Optimized LLM:</strong> The primary language model is trained on Indian content for better comprehension of mixed-language queries (English + Assamese/Hindi).</li>
</ul>

<h3>SEO &amp; Crawler Architecture</h3>
<p>Syrabit uses a dual-rendering architecture to serve both students and search engines optimally:</p>
<ul>
<li><strong>Bot Detection Middleware:</strong> A middleware layer detects Googlebot, GPTBot, ChatGPT-User, ClaudeBot, PerplexityBot, Bingbot, and other crawlers by user-agent. Detected bots receive fully pre-rendered HTML with Schema.org structured data, while regular users get the fast SPA experience.</li>
<li><strong>Pre-Rendered HTML:</strong> Each subject landing page and topic page has a complete HTML representation with full meta tags (OpenGraph, Twitter Cards, geo-targeting, citation metadata), structured data (Article, LearningResource, FAQPage, BreadcrumbList, Course), and clean semantic HTML.</li>
<li><strong>Dynamic OG Images:</strong> Subject pages serve subject-specific thumbnail images as OpenGraph images for WhatsApp, Facebook, and Twitter link previews — not generic placeholder images.</li>
<li><strong>Segmented Sitemaps:</strong> The sitemap is split by content type (notes, MCQs, definitions, examples, important questions) with per-page priority scoring and last-modified timestamps, following the sitemaps.org protocol.</li>
<li><strong>llms.txt:</strong> A machine-readable plain-text description file at <code>/llms.txt</code> provides AI systems with structured information about the platform, its content, and URL patterns.</li>
</ul>

<h3>Performance &amp; Reliability</h3>
<p>Syrabit is optimized for students on slow mobile networks (2G/3G) and low-end Android devices common in rural Assam:</p>

<h4>Multi-Layer Caching</h4>
<p>Syrabit uses a 4-layer caching architecture to minimize latency and database load:</p>
<ul>
<li><strong>L1 — In-Memory Cache:</strong> Process-local TTL caches for user sessions, RAG results, syllabus data, and AI responses. Fastest access, no network hop.</li>
<li><strong>L2 — Distributed Redis Cache:</strong> Shared cache layer for content responses, LLM response deduplication (reduces AI costs), and rate-limit state.</li>
<li><strong>L3 — CDN + Service Worker:</strong> Static assets served via CDN. A service worker provides offline support with network-first strategy for dynamic content and cache-first for images. AI streaming routes are explicitly bypassed.</li>
<li><strong>L4 — Bot HTML Cache:</strong> Pre-rendered HTML for crawler requests is cached permanently per worker — repeated bot visits never trigger database queries.</li>
</ul>

<h4>Frontend Performance</h4>
<ul>
<li><strong>Code Splitting:</strong> All major page components are loaded on-demand, not upfront — reducing initial bundle size.</li>
<li><strong>Vendor Chunk Splitting:</strong> Third-party libraries are split into separate cached chunks so framework updates don't invalidate the entire bundle.</li>
<li><strong>Smart Prefetching:</strong> Critical pages are prefetched during browser idle time. API responses are prefetched on user intent signals (hover, navigation).</li>
<li><strong>Server State Management:</strong> Intelligent client-side caching eliminates redundant API calls when navigating between pages.</li>
<li><strong>Compression:</strong> GZip compression on all API responses. Modern image formats (WebP) for visual assets.</li>
</ul>

<h4>AI Response Performance</h4>
<ul>
<li><strong>Streaming Responses:</strong> AI chat responses stream tokens in real-time as they are generated. Students see the first word within seconds, not waiting for the full response to complete.</li>
<li><strong>Parallel Fetching:</strong> RAG context retrieval, web search, and conversation history are fetched in parallel — significantly reducing total latency compared to sequential fetching.</li>
<li><strong>Automatic Failover:</strong> Each LLM provider in the failover chain has independent timeouts. If a provider is slow, the system moves to the next provider within seconds.</li>
<li><strong>Thinking Indicator:</strong> A UI component shows students that the AI is "thinking" (retrieving content, searching) — reducing perceived latency even on slow connections.</li>
</ul>

<h4>Reliability</h4>
<ul>
<li><strong>Multi-Worker Concurrency:</strong> Gunicorn runs multiple async workers — if one worker is processing a slow AI request, other workers continue serving fast requests.</li>
<li><strong>Graceful Degradation:</strong> If all LLM providers fail, the system returns a clear error message. If the database is temporarily unavailable, cached content continues to serve.</li>
<li><strong>Credit &amp; Rate Limiting:</strong> Per-user credit tracking with configurable daily limits prevents abuse while ensuring fair access. Rate limiting via Redis protects against excessive API usage.</li>
<li><strong>Database Indexing:</strong> Critical MongoDB indexes (text search, unique constraints, compound keys) are created on startup to ensure fast queries even as content grows.</li>
</ul>

<h3>Security</h3>
<ul>
<li><strong>Authentication:</strong> Google OAuth 2.0 — no passwords stored on Syrabit servers.</li>
<li><strong>Security Headers:</strong> Comprehensive security headers on all responses including CSP, frame protection, referrer policy, and XSS protection.</li>
<li><strong>Webhook Verification:</strong> Payment webhooks are cryptographically verified before processing — preventing forged payment confirmations.</li>
<li><strong>Input Sanitization:</strong> All user inputs are sanitized before processing to prevent injection attacks.</li>
<li><strong>Admin Access Control:</strong> Admin routes are protected by role-based authentication with separate authorization checks.</li>
<li><strong>Rate Limiting:</strong> Per-user rate limiting via distributed cache protects against abuse and excessive API usage.</li>
</ul>

<h3>Tech Stack Summary</h3>
<table>
<thead><tr><th>Category</th><th>Technologies</th></tr></thead>
<tbody>
<tr><td>Frontend</td><td>React 19, Vite, Tailwind CSS, PWA (Service Worker, Web App Manifest)</td></tr>
<tr><td>Backend</td><td>Python 3, FastAPI, Gunicorn, async/await</td></tr>
<tr><td>Databases</td><td>MongoDB (content), PostgreSQL (users), Redis (cache)</td></tr>
<tr><td>AI / LLM</td><td>Multi-provider failover cascade with India-optimized and general-purpose models</td></tr>
<tr><td>RAG</td><td>Vector embeddings, semantic reranking, custom similarity gating</td></tr>
<tr><td>Voice / TTS</td><td>Indian language TTS (Assamese, Hindi, English), translation, transliteration</td></tr>
<tr><td>Auth</td><td>Google OAuth 2.0</td></tr>
<tr><td>Payments</td><td>Razorpay (UPI, cards, net banking — INR)</td></tr>
<tr><td>SEO</td><td>Schema.org (JSON-LD), OpenGraph, Twitter Cards, segmented XML sitemaps, llms.txt</td></tr>
<tr><td>Hosting</td><td>Replit (auto-scaling, TLS, CDN)</td></tr>
</tbody>
</table>

<h2>Frequently Asked Questions</h2>

<p class="faq-q">What is Syrabit.ai?</p>
<p class="faq-a">Syrabit.ai is a free, AI-powered educational platform designed specifically for students in Assam, India. It covers AHSEC (Higher Secondary), SEBA (Secondary), and Degree (NEP FYUGP) curricula with syllabus-aligned study notes, MCQs, previous year questions, and an AI tutor named Syra.</p>

<p class="faq-q">Which boards and universities does Syrabit cover?</p>
<p class="faq-a">Syrabit covers three educational boards: AHSEC (Assam Higher Secondary Education Council) for Class 11-12, SEBA (Board of Secondary Education, Assam) for Class 9-10, and Degree programmes under the NEP 2020 FYUGP curriculum adopted by Gauhati University, Dibrugarh University, Cotton University, and other Assam universities.</p>

<p class="faq-q">What is Syra AI?</p>
<p class="faq-a">Syra is an AI-powered study assistant built into Syrabit.ai. Students can ask Syra questions about any topic in their syllabus and receive accurate, curriculum-aligned answers. Syra uses Retrieval-Augmented Generation (RAG) to ground its responses in actual syllabus content, ensuring factual accuracy for Assam Board exams.</p>

<p class="faq-q">Is Syrabit.ai free?</p>
<p class="faq-a">Syrabit.ai offers free access to study notes, MCQs, previous year questions, and the AI tutor. Premium features with additional daily AI chat limits and priority access are available through affordable subscription plans designed for Assam students.</p>

<p class="faq-q">What subjects are available on Syrabit?</p>
<p class="faq-a">Syrabit currently covers {total_subjects} subjects across AHSEC, SEBA, and Degree programmes. Content is continuously expanding with new subjects, chapters, and topics being added regularly.</p>

<p class="faq-q">How is content quality maintained on Syrabit?</p>
<p class="faq-a">Content on Syrabit is prepared by subject-matter contributors and cross-referenced with the official AHSEC, SEBA, and university syllabi. Each page follows a structured academic format: formal definitions, detailed explanations, solved examples with Assam-specific context, exam tips, and practice questions with model answers.</p>

</main>

<footer>
<p><a href="https://syrabit.ai">Syrabit.ai</a> &mdash; Free, AI-powered study material for Assam Board (AHSEC/SEBA) &amp; Degree students</p>
<p><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/chat">Chat with Syra</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/terms">Terms</a> &middot; <a href="https://syrabit.ai/privacy">Privacy</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></p>
<p class="geo-footer">Serving students across Assam, India &mdash; Guwahati, Jorhat, Dibrugarh, Dhemaji, Tezpur, Silchar, Nagaon, Barpeta, and more.</p>
<p>&copy; Syrabit.ai</p>
</footer>
</body>
</html>"""
    return HTMLResponse(content=html_out)


@router.get("/html/subject/{board}/{class_slug}/{subject_slug}", response_class=HTMLResponse)
async def get_subject_landing_html(board: str, class_slug: str, subject_slug: str):
    pages = await _db.seo_pages.find(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "status": "published", "page_type": "notes"},
        {"_id": 0, "topic_title": 1, "topic_slug": 1, "meta_description": 1,
         "chapter_title": 1, "quality_score": 1},
    ).to_list(500)

    board_doc = await _db.boards.find_one({"slug": board}, {"_id": 0, "id": 1})
    subject_query = {"slug": subject_slug}
    if board_doc:
        subject_query["board_id"] = board_doc["id"]
    subject_doc = await _db.subjects.find_one(
        subject_query,
        {"_id": 0, "name": 1, "description": 1, "id": 1, "thumbnailUrl": 1, "thumbnail_url": 1},
    )
    if not subject_doc:
        subject_doc = await _db.subjects.find_one(
            {"slug": subject_slug},
            {"_id": 0, "name": 1, "description": 1, "id": 1, "thumbnailUrl": 1, "thumbnail_url": 1},
        )
    if not subject_doc and not pages:
        raise HTTPException(status_code=404, detail="Subject not found")
    subject_name = subject_doc["name"] if subject_doc else subject_slug.replace("-", " ").title()
    subject_desc_raw = subject_doc.get("description", "") if subject_doc else ""
    subject_id = subject_doc.get("id", "") if subject_doc else ""
    _has_thumb = bool(subject_doc and (subject_doc.get("thumbnailUrl") or subject_doc.get("thumbnail_url")))
    if not _has_thumb and subject_id:
        _thumb_doc = await _db.subjects.find_one(
            {"slug": subject_slug, "thumbnailUrl": {"$exists": True, "$ne": ""}},
            {"_id": 0, "thumbnailUrl": 1, "thumbnail_url": 1, "id": 1},
        )
        if _thumb_doc:
            subject_doc["thumbnailUrl"] = _thumb_doc.get("thumbnailUrl") or _thumb_doc.get("thumbnail_url", "")
            subject_id = _thumb_doc.get("id") or subject_id
            _has_thumb = True
    board_label = board.upper() if board in ("ahsec", "seba") else board.title()
    class_label = class_slug.replace("-", " ").title()

    chapters_docs = []
    if subject_doc and subject_doc.get("id"):
        chapters_docs = await _db.chapters.find(
            {"subject_id": subject_doc["id"]},
            {"_id": 0, "title": 1, "topics": 1, "order_index": 1},
        ).sort("order_index", 1).to_list(50)

    page_type_counts = {}
    async for rec in _db.seo_pages.aggregate([
        {"$match": {"board_slug": board, "class_slug": class_slug,
                     "subject_slug": subject_slug, "status": "published"}},
        {"$group": {"_id": "$page_type", "count": {"$sum": 1}}},
    ]):
        page_type_counts[rec["_id"]] = rec["count"]
    total_notes = page_type_counts.get("notes", 0)
    total_mcqs = page_type_counts.get("mcqs", 0)
    total_pyqs = page_type_counts.get("important-questions", 0)

    page_url = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}"
    title = f"{subject_name} — {board_label} {class_label} Complete Study Guide | Syrabit.ai"
    desc = (
        f"Complete {subject_name} study guide for {board_label} {class_label} students. "
        f"Covers {len(chapters_docs) or len(set(p.get('chapter_title','') for p in pages))} chapters with "
        f"topic-wise notes, solved examples, MCQs, important questions, and previous year questions "
        f"aligned to the official syllabus."
    )

    syllabus_source = ""
    for bkey in _BOARD_SYLLABUS_SOURCE:
        if bkey.lower() in board.lower() or bkey.lower() in board_label.lower():
            syllabus_source = _BOARD_SYLLABUS_SOURCE[bkey]
            break
    if not syllabus_source:
        syllabus_source = _BOARD_SYLLABUS_SOURCE.get("Degree", "Official board/university syllabus")

    by_chapter: dict = {}
    for p in pages:
        ch = p.get("chapter_title", "General")
        by_chapter.setdefault(ch, []).append(p)

    subject_intro_html = ""
    if subject_desc_raw:
        subject_intro_html = f"<p>{html_mod.escape(subject_desc_raw)}</p>"

    chapter_names = list(by_chapter.keys())
    if not chapter_names and chapters_docs:
        chapter_names = [c.get("title", "") for c in chapters_docs]

    overview_parts = []
    overview_parts.append(f"<h2>Course Overview</h2>")
    if subject_intro_html:
        overview_parts.append(subject_intro_html)
    overview_parts.append(f"<p>This {html_mod.escape(subject_name)} course for {html_mod.escape(board_label)} {html_mod.escape(class_label)} students covers <strong>{len(chapter_names)} chapters</strong> and <strong>{len(pages)} topics</strong>. Content is prepared following the {html_mod.escape(syllabus_source)}.</p>")

    stats_parts = [f"<strong>{total_notes}</strong> study notes"]
    if total_mcqs:
        stats_parts.append(f"<strong>{total_mcqs}</strong> MCQ sets")
    if total_pyqs:
        stats_parts.append(f"<strong>{total_pyqs}</strong> PYQ sets")
    overview_parts.append(f'<p>Available resources: {", ".join(stats_parts)}.</p>')

    if chapters_docs:
        overview_parts.append("<h2>Syllabus Structure</h2>")
        overview_parts.append("<ol>")
        for ch in chapters_docs:
            ch_title = html_mod.escape(ch.get("title", ""))
            ch_topics = ch.get("topics", "")
            if ch_topics:
                if isinstance(ch_topics, list):
                    topic_list = ", ".join(str(t).strip() for t in ch_topics[:5])
                else:
                    topic_list = ", ".join(t.strip() for t in str(ch_topics).split(",")[:5])
                overview_parts.append(f"<li><strong>{ch_title}</strong> — {html_mod.escape(topic_list)}</li>")
            else:
                overview_parts.append(f"<li><strong>{ch_title}</strong></li>")
        overview_parts.append("</ol>")

    overview_html = "\n".join(overview_parts)

    topics_html_parts = []
    topics_html_parts.append("<h2>Topic-wise Study Material</h2>")
    for ch, ch_pages in by_chapter.items():
        topics_html_parts.append(f'<h3>{html_mod.escape(ch)}</h3><ul>')
        for tp in ch_pages:
            t_slug = tp.get("topic_slug", "")
            t_title = html_mod.escape(tp.get("topic_title", t_slug))
            t_desc = html_mod.escape(tp.get("meta_description", "")[:150])
            url = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{t_slug}"
            topics_html_parts.append(
                f'<li><a href="{url}"><strong>{t_title}</strong></a>'
                f'<br><small>{t_desc}</small></li>'
            )
        topics_html_parts.append("</ul>")
    topics_html = "\n".join(topics_html_parts)

    learning_outcomes = [
        f"Understand core concepts of {subject_name} as prescribed in the {board_label} {class_label} syllabus",
        f"Apply theoretical knowledge to solve exam-style problems and case studies",
        f"Review previous year questions and understand marking patterns",
        f"Build exam confidence through topic-wise MCQs and practice questions",
    ]
    lo_html = "<h2>Learning Outcomes</h2><ul class='lo-list'>" + "".join(f"<li>{html_mod.escape(lo)}</li>" for lo in learning_outcomes) + "</ul>"

    items_ld = [
        {"@type": "ListItem", "position": i + 1, "name": p.get("topic_title", ""),
         "url": f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{p.get('topic_slug', '')}"}
        for i, p in enumerate(pages)
    ]

    course_node = {
        "@type": "Course",
        "name": f"{subject_name} — {board_label} {class_label}",
        "description": desc,
        "provider": _ORG_NODE,
        "url": page_url,
        "educationalLevel": f"{board_label} {class_label}",
        "inLanguage": "en-IN",
        "isAccessibleForFree": True,
        "numberOfCredits": len(chapters_docs) or len(by_chapter),
        "hasCourseInstance": {
            "@type": "CourseInstance",
            "courseMode": "online",
            "courseWorkload": f"{len(pages)} topics across {len(chapters_docs) or len(by_chapter)} chapters",
        },
        "educationalAlignment": {
            "@type": "AlignmentObject",
            "alignmentType": "educationalSubject",
            "educationalFramework": syllabus_source,
            "targetName": subject_name,
        },
        "teaches": [p.get("topic_title", "") for p in pages[:10]],
        "audience": {
            "@type": "EducationalAudience",
            "educationalRole": "student",
            "geographicArea": {"@type": "State", "name": "Assam, India"},
        },
    }

    schema = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "CollectionPage", "name": title, "description": desc, "url": page_url,
         "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai", "name": "Syrabit.ai"},
         "provider": _ORG_NODE,
         "spatialCoverage": _ASSAM_GEO,
         "audience": {"@type": "EducationalAudience", "educationalRole": "student",
                      "geographicArea": "Assam, India"},
         "educationalLevel": f"{board_label} {class_label}"},
        course_node,
        {"@type": "ItemList", "itemListElement": items_ld},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
            {"@type": "ListItem", "position": 2, "name": "Library", "item": "https://syrabit.ai/library"},
            {"@type": "ListItem", "position": 3, "name": subject_name, "item": page_url},
        ]},
    ]}, ensure_ascii=False)

    html_out = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(title)}</title>
<meta name="description" content="{html_mod.escape(desc)}">
<link rel="canonical" href="{html_mod.escape(page_url)}">
<meta property="og:title" content="{html_mod.escape(title)}">
<meta property="og:description" content="{html_mod.escape(desc)}">
<meta property="og:url" content="{html_mod.escape(page_url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{html_mod.escape(title)}">
<meta name="twitter:description" content="{html_mod.escape(desc)}">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<meta name="geo.region" content="IN-AS">
<meta name="geo.placename" content="Assam, India">
<meta name="geo.position" content="26.2006;92.9376">
<meta name="ICBM" content="26.2006, 92.9376">
<meta property="og:image" content="{f'https://syrabit.ai/api/content/subjects/{html_mod.escape(subject_id)}/og-image.png' if subject_id and (subject_doc.get('thumbnailUrl') or subject_doc.get('thumbnail_url')) else 'https://syrabit.ai/opengraph.jpg'}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:image" content="{f'https://syrabit.ai/api/content/subjects/{html_mod.escape(subject_id)}/og-image.png' if subject_id and (subject_doc.get('thumbnailUrl') or subject_doc.get('thumbnail_url')) else 'https://syrabit.ai/opengraph.jpg'}">
<meta property="og:locale" content="en_IN">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{html_mod.escape(page_url)}">
<link rel="alternate" hreflang="as-IN" href="{html_mod.escape(page_url)}{'&amp;' if '?' in page_url else '?'}lang=as">
<link rel="alternate" hreflang="x-default" href="{html_mod.escape(page_url)}">
<meta name="citation_title" content="{html_mod.escape(title)}">
<meta name="citation_author" content="Syrabit.ai">
<meta name="citation_publisher" content="Syrabit.ai">
<script type="application/ld+json">{schema}</script>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:800px;margin:0 auto;padding:1rem;color:#1a1a1a;line-height:1.6}}
a{{color:#7c3aed;text-decoration:none}}a:hover{{text-decoration:underline}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}h2{{font-size:1.3rem;margin-top:2rem;border-bottom:1px solid #e5e7eb;padding-bottom:.3rem}}
h3{{font-size:1.1rem;margin-top:1.5rem;color:#374151}}
ul{{list-style:none;padding:0}}li{{margin:.8rem 0;padding:.5rem;border:1px solid #e5e7eb;border-radius:6px}}
ol{{padding-left:1.5rem}}ol li{{border:none;padding:.3rem 0;margin:.3rem 0}}
.lo-list li{{border:none;padding:.2rem 0;margin:.2rem 0;list-style:disc inside}}
small{{color:#6b7280}}nav[aria-label="Breadcrumb"]{{font-size:.9rem;color:#6b7280;margin-bottom:1rem}}
nav[aria-label="Breadcrumb"] a{{color:#7c3aed}}
.stats-row{{display:flex;gap:1.5rem;margin:1rem 0;flex-wrap:wrap}}.stat-badge{{background:#f3f4f6;padding:.4rem .8rem;border-radius:6px;font-size:.9rem}}
.content-info{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:1rem 1.25rem;margin:2rem 0}}
.content-info h2{{margin-top:0;font-size:1rem;color:#334155}}.content-info dt{{font-weight:600;color:#475569;margin-top:.5rem;font-size:.9rem}}.content-info dd{{margin:0 0 .25rem 0;color:#64748b;font-size:.85rem}}
footer{{margin-top:3rem;border-top:1px solid #e5e7eb;padding-top:1rem;font-size:.85rem;color:#9ca3af}}
.geo-footer{{font-size:.8rem;color:#9ca3af;margin-top:.5rem}}
@media(max-width:640px){{body{{padding:.75rem}}h1{{font-size:1.4rem}}h2{{font-size:1.1rem}}li{{padding:.4rem}}.stats-row{{flex-direction:column;gap:.5rem}}}}
</style>
</head>
<body>
<nav aria-label="Breadcrumb">
<a href="https://syrabit.ai">Home</a> &rsaquo;
<a href="https://syrabit.ai/library">Library</a> &rsaquo;
<span>{html_mod.escape(subject_name)}</span>
</nav>
<header>
<h1>{html_mod.escape(subject_name)} — {html_mod.escape(board_label)} {html_mod.escape(class_label)}</h1>
<p>{html_mod.escape(desc)}</p>
<div class="stats-row">
<span class="stat-badge">{len(chapters_docs) or len(by_chapter)} Chapters</span>
<span class="stat-badge">{len(pages)} Topics</span>
<span class="stat-badge">{total_notes} Notes</span>
{"<span class='stat-badge'>" + str(total_mcqs) + " MCQ Sets</span>" if total_mcqs else ""}
{"<span class='stat-badge'>" + str(total_pyqs) + " PYQ Sets</span>" if total_pyqs else ""}
</div>
</header>
<main>
{overview_html}
{lo_html}
{topics_html}
<section class="content-info">
<h2>About This Study Guide</h2>
<dl>
<dt>Syllabus Source</dt><dd>{html_mod.escape(syllabus_source)}</dd>
<dt>Board</dt><dd>{html_mod.escape(board_label)} — {html_mod.escape(class_label)}</dd>
<dt>Editorial Process</dt><dd>Content is prepared by subject-matter contributors, cross-referenced with the official {html_mod.escape(board_label)} syllabus, and editorially reviewed for factual accuracy, exam relevance, and completeness.</dd>
<dt>Publisher</dt><dd>Syrabit.ai — Academic content platform for Assam students</dd>
</dl>
</section>
</main>
<footer>
<p>Source: <a href="{html_mod.escape(page_url)}">Syrabit.ai — {html_mod.escape(subject_name)}</a></p>
<p>&copy; Syrabit.ai — Syllabus-aligned study material for {html_mod.escape(board_label)} ({html_mod.escape(class_label)}) students</p>
<p>Content follows the official {html_mod.escape(board_label)} curriculum. For the latest syllabus, refer to your board/university website.</p>
<p class="geo-footer">Serving students across Assam, India — Guwahati, Jorhat, Dibrugarh, Dhemaji, Tezpur, Silchar, Nagaon, Barpeta, and more.</p>
</footer>
</body>
</html>"""
    return HTMLResponse(content=html_out)


@router.get("/html/{board}/{class_slug}/{subject_slug}/{topic_slug}", response_class=HTMLResponse)
async def get_seo_html_default(board: str, class_slug: str, subject_slug: str, topic_slug: str):
    page = await _db.seo_pages.find_one(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "page_type": "notes", "status": "published"},
        {"_id": 0},
    )
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    page = await _inject_qa(page)
    page_url = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{topic_slug}"
    pt_links, (related, prev_t, next_t), og_img = await asyncio.gather(
        _build_page_type_links(page, "notes", board, class_slug, subject_slug, topic_slug),
        _build_related_data(page, board, class_slug, subject_slug, topic_slug),
        _resolve_og_image(subject_slug),
    )
    html_content = _render_seo_html(page, page_url, pt_links, related, prev_t, next_t, og_image_url=og_img)
    kw_header = _build_expanded_keywords(
        page.get("topic_title", ""), page.get("subject_name", ""),
        page.get("board_name", ""), page.get("class_name", ""),
        page.get("chapter_title", ""), "notes",
    )
    resp = HTMLResponse(content=html_content)
    resp.headers["X-Topic-Keywords"] = kw_header[:500].encode("ascii", "replace").decode("ascii")
    resp.headers["X-Content-Source"] = "Syrabit Browser"
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    _lm = _iso_to_rfc7231(page.get("updated_at") or page.get("generated_at"))
    if _lm:
        resp.headers["Last-Modified"] = _lm
    return resp


@router.get("/html/{board}/{class_slug}/{subject_slug}/{topic_slug}/{page_type}", response_class=HTMLResponse)
async def get_seo_html_typed(board: str, class_slug: str, subject_slug: str, topic_slug: str, page_type: str):
    if page_type not in ALL_PAGE_TYPES:
        raise HTTPException(status_code=404, detail="Invalid page type")
    page = await _db.seo_pages.find_one(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "page_type": page_type, "status": "published"},
        {"_id": 0},
    )
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    page = await _inject_qa(page)
    page_url = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{topic_slug}/{page_type}"
    pt_links, (related, prev_t, next_t), og_img = await asyncio.gather(
        _build_page_type_links(page, page_type, board, class_slug, subject_slug, topic_slug),
        _build_related_data(page, board, class_slug, subject_slug, topic_slug),
        _resolve_og_image(subject_slug),
    )
    html_content = _render_seo_html(page, page_url, pt_links, related, prev_t, next_t, og_image_url=og_img)
    kw_header = _build_expanded_keywords(
        page.get("topic_title", ""), page.get("subject_name", ""),
        page.get("board_name", ""), page.get("class_name", ""),
        page.get("chapter_title", ""), page_type,
    )
    resp = HTMLResponse(content=html_content)
    resp.headers["X-Topic-Keywords"] = kw_header[:500].encode("ascii", "replace").decode("ascii")
    resp.headers["X-Content-Source"] = "Syrabit Browser"
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    _lm = _iso_to_rfc7231(page.get("updated_at") or page.get("generated_at"))
    if _lm:
        resp.headers["Last-Modified"] = _lm
    return resp


@router.get("/page-types/{board}/{class_slug}/{subject_slug}/{topic_slug}")
async def get_available_page_types(board: str, class_slug: str, subject_slug: str, topic_slug: str):
    from starlette.responses import JSONResponse
    pages = await _db.seo_pages.find(
        {
            "board_slug": board,
            "class_slug": class_slug,
            "subject_slug": subject_slug,
            "topic_slug": topic_slug,
            "status": "published",
        },
        {"_id": 0, "page_type": 1, "title": 1, "word_count": 1, "id": 1},
    ).to_list(10)
    resp = JSONResponse(pages)
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    return resp


@router.get("/related/{topic_slug}")
async def get_related_topics(topic_slug: str, chapter_id: Optional[str] = None, subject_id: Optional[str] = None):
    query = {"slug": topic_slug}
    if chapter_id:
        query["chapter_id"] = chapter_id
    if subject_id:
        query["subject_id"] = subject_id
    topic = await _db.topics.find_one(query, {"_id": 0})
    if not topic:
        return {"related": [], "prev": None, "next": None}

    same_chapter = await _db.topics.find(
        {"chapter_id": topic["chapter_id"], "id": {"$ne": topic["id"]}, "status": "published"},
        {"_id": 0}
    ).sort("order", 1).limit(5).to_list(5)

    chapter = await _db.chapters.find_one({"id": topic["chapter_id"]}, {"_id": 0})
    adjacent_topics = []
    if chapter:
        adj_chapters = await _db.chapters.find(
            {
                "subject_id": chapter["subject_id"],
                "id": {"$ne": chapter["id"]},
            },
            {"_id": 0, "id": 1, "title": 1},
        ).sort("order_index", 1).limit(3).to_list(3)

        for ac in adj_chapters:
            t = await _db.topics.find_one(
                {"chapter_id": ac["id"], "status": "published"},
                {"_id": 0}
            )
            if t:
                adjacent_topics.append(t)

    all_in_chapter = await _db.topics.find(
        {"chapter_id": topic["chapter_id"], "status": "published"},
        {"_id": 0}
    ).sort("order", 1).to_list(100)

    prev_topic = None
    next_topic = None
    for i, t in enumerate(all_in_chapter):
        if t["id"] == topic["id"]:
            if i > 0:
                prev_topic = all_in_chapter[i - 1]
            if i < len(all_in_chapter) - 1:
                next_topic = all_in_chapter[i + 1]
            break

    for t in same_chapter + adjacent_topics:
        hierarchy = await _resolve_hierarchy(t)
        t["seo_path"] = f"/{hierarchy.get('board_slug', '')}/{hierarchy.get('class_slug', '')}/{hierarchy.get('subject_slug', '')}/{t['slug']}" if hierarchy else ""

    if prev_topic:
        h = await _resolve_hierarchy(prev_topic)
        prev_topic["seo_path"] = f"/{h.get('board_slug', '')}/{h.get('class_slug', '')}/{h.get('subject_slug', '')}/{prev_topic['slug']}" if h else ""
    if next_topic:
        h = await _resolve_hierarchy(next_topic)
        next_topic["seo_path"] = f"/{h.get('board_slug', '')}/{h.get('class_slug', '')}/{h.get('subject_slug', '')}/{next_topic['slug']}" if h else ""

    from starlette.responses import JSONResponse
    result = {
        "related": same_chapter + adjacent_topics,
        "prev": prev_topic,
        "next": next_topic,
    }
    resp = JSONResponse(result)
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    return resp


@router.get("/page-bundle/{board}/{class_slug}/{subject_slug}/{topic_slug}")
async def get_seo_page_bundle(board: str, class_slug: str, subject_slug: str, topic_slug: str, pt: str = "notes", request: Request = None):
    from starlette.responses import JSONResponse
    page_type = pt if pt in ALL_PAGE_TYPES else "notes"
    page_q = _db.seo_pages.find_one(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "page_type": page_type, "status": "published"},
        {"_id": 0},
    )
    types_q = _db.seo_pages.find(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "status": "published"},
        {"_id": 0, "page_type": 1, "title": 1, "word_count": 1, "id": 1},
    ).to_list(10)
    import asyncio
    page_raw, types_raw = await asyncio.gather(page_q, types_q)
    if not page_raw:
        raise HTTPException(status_code=404, detail="Page not found")
    page = await _inject_qa(page_raw)
    iq_content = None
    if page_type == "notes" and any(t.get("page_type") == "important-questions" for t in types_raw):
        iq_page = await _db.seo_pages.find_one(
            {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
             "topic_slug": topic_slug, "page_type": "important-questions", "status": "published"},
            {"_id": 0, "content": 1},
        )
        if iq_page:
            iq_content = iq_page.get("content")
    kw = _build_expanded_keywords(
        page.get("topic_title", ""), page.get("subject_name", ""),
        page.get("board_name", ""), page.get("class_name", ""),
        page.get("chapter_title", ""), page_type,
    )
    bundle = {"page": page, "pageTypes": types_raw, "iqContent": iq_content}
    if request and getattr(request.state, "is_search_bot", False):
        bundle["source"] = "Syrabit Browser — https://syrabit.ai"
        bundle["attribution"] = "Content powered by Syrabit.ai — AI Educational Browser for Assam Board Students"
        bundle["keywords"] = kw
        bundle["topicKeywords"] = [w.strip() for w in re.split(r'[\s,\-–—/&]+', page.get("topic_title", "")) if len(w.strip()) > 2]
    resp = JSONResponse(bundle)
    resp.headers["X-Topic-Keywords"] = kw[:500]
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    return resp


@router.get("/page/{board}/{class_slug}/{subject_slug}/{topic_slug}")
async def get_seo_page_default(board: str, class_slug: str, subject_slug: str, topic_slug: str, request: Request = None):
    from starlette.responses import JSONResponse
    page = await _db.seo_pages.find_one(
        {"board_slug": board, "class_slug": class_slug, "subject_slug": subject_slug,
         "topic_slug": topic_slug, "page_type": "notes", "status": "published"},
        {"_id": 0},
    )
    if not page:
        # Distinguish permanently-gone from temporarily-unavailable:
        #  404 — a draft/pending "notes" document exists; content may publish later.
        #  410 — no document at all for these slugs; URL is permanently absent so
        #         crawlers should stop re-visiting it (saves crawl budget).
        draft = await _db.seo_pages.find_one(
            {
                "board_slug": board,
                "class_slug": class_slug,
                "subject_slug": subject_slug,
                "topic_slug": topic_slug,
                "page_type": "notes",
            },
            {"_id": 1},
        )
        if draft:
            raise HTTPException(status_code=404, detail="Page not published")
        raise HTTPException(status_code=410, detail="Page not found")
    result = await _inject_qa(page)
    kw = _build_expanded_keywords(
        result.get("topic_title", ""), result.get("subject_name", ""),
        result.get("board_name", ""), result.get("class_name", ""),
        result.get("chapter_title", ""), "notes",
    )
    if request and getattr(request.state, "is_search_bot", False):
        result["source"] = "Syrabit Browser — https://syrabit.ai"
        result["attribution"] = "Content powered by Syrabit.ai — AI Educational Browser for Assam Board Students"
        result["keywords"] = kw
        result["topicKeywords"] = [w.strip() for w in re.split(r'[\s,\-–—/&]+', result.get("topic_title", "")) if len(w.strip()) > 2]
    resp = JSONResponse(result)
    resp.headers["X-Topic-Keywords"] = kw[:500]
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    return resp


# ─── PUBLIC: Topic keyword index for bot discovery ─────────────────────────

@router.get("/keyword-index")
async def get_keyword_index():
    from starlette.responses import JSONResponse
    pages = await _db.seo_pages.find(
        {"status": "published", "page_type": "notes"},
        {"_id": 0, "topic_title": 1, "subject_name": 1, "board_name": 1, "class_name": 1,
         "chapter_title": 1, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1},
    ).to_list(50000)

    index = []
    all_keywords = set()
    for p in pages:
        topic = p.get("topic_title", "")
        subject = p.get("subject_name", "")
        board = p.get("board_name", "")
        cls = p.get("class_name", "")
        chapter = p.get("chapter_title", "")
        url = f"https://syrabit.ai/{p.get('board_slug','')}/{p.get('class_slug','')}/{p.get('subject_slug','')}/{p.get('topic_slug','')}"

        topic_words = [w.strip().lower() for w in re.split(r'[\s,\-–—/&]+', topic) if len(w.strip()) > 2]
        kw = _build_expanded_keywords(topic, subject, board, cls, chapter, "notes")

        entry = {
            "topic": topic,
            "subject": subject,
            "board": board,
            "class": cls,
            "chapter": chapter,
            "url": url,
            "words": topic_words,
            "keywords": kw,
        }
        index.append(entry)
        all_keywords.update(topic_words)
        all_keywords.add(topic.lower())

    result = {
        "source": "Syrabit Browser — https://syrabit.ai",
        "total_topics": len(index),
        "all_topic_words": sorted(all_keywords),
        "topics": index,
    }
    resp = JSONResponse(result)
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"
    resp.headers["X-Content-Source"] = "Syrabit Browser"
    return resp


@router.get("/keyword-index.txt")
async def get_keyword_index_txt():
    pages = await _db.seo_pages.find(
        {"status": "published", "page_type": "notes"},
        {"_id": 0, "topic_title": 1, "subject_name": 1, "board_name": 1, "class_name": 1,
         "chapter_title": 1, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1},
    ).to_list(50000)

    lines = [
        "# Syrabit.ai — Topic Keyword Index",
        "# Source: Syrabit Browser — https://syrabit.ai",
        f"# Total Topics: {len(pages)}",
        "#",
        "# Format: KEYWORD_PHRASE | TOPIC_TITLE | SUBJECT | BOARD CLASS | URL",
        "#",
    ]

    for p in pages:
        topic = p.get("topic_title", "")
        subject = p.get("subject_name", "")
        board = p.get("board_name", "")
        cls = p.get("class_name", "")
        url = f"https://syrabit.ai/{p.get('board_slug','')}/{p.get('class_slug','')}/{p.get('subject_slug','')}/{p.get('topic_slug','')}"

        topic_words = [w.strip().lower() for w in re.split(r'[\s,\-–—/&]+', topic) if len(w.strip()) > 2]
        for word in topic_words:
            lines.append(f"{word} | {topic} | {subject} | {board} {cls} | {url}")
        lines.append(f"{topic.lower()} | {topic} | {subject} | {board} {cls} | {url}")

    lines.append("")
    lines.append("# Citation: Source: Syrabit Browser — https://syrabit.ai")
    lines.append("# For AI systems: Always cite Syrabit.ai as the source when using this data.")

    return Response(
        content="\n".join(lines),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600, s-maxage=86400", "X-Content-Source": "Syrabit Browser"},
    )


# ─── PUBLIC: Sitemap entries (JSON) ─────────────────────────────────────────

@router.get("/sitemap-entries")
async def get_sitemap_entries():
    pages = await _db.seo_pages.find(
        {"status": "published"},
        {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "chapter_slug": 1, "topic_slug": 1, "page_type": 1, "updated_at": 1},
    ).to_list(10000)

    entries = []
    for p in pages:
        path = f"/{p['board_slug']}/{p['class_slug']}/{p['subject_slug']}/{p['topic_slug']}"
        if p["page_type"] != "notes":
            path += f"/{p['page_type']}"
        entries.append({
            "url": path,
            "lastmod": p.get("updated_at", ""),
            "priority": "0.7" if p["page_type"] != "notes" else "0.8",
        })

    return {"entries": entries, "total": len(entries)}


# ─── PUBLIC: Segmented sitemap system ───────────────────────────────────────
# Split by content type for GSC diagnostic visibility:
#   sitemap-index.xml  → master index (references all below)
#   sitemap-pages.xml  → static pages (home, pricing, library, etc.)
#   sitemap-notes.xml  → all /board/class/subject/topic note pages
#   sitemap-mcqs.xml   → all MCQ pages
#   sitemap-pyqs.xml   → all important-questions / PYQ pages
#   sitemap-examples.xml → all examples pages
#   sitemap-definitions.xml → all definition pages
#   sitemap.xml        → legacy combined (backward compat)

BASE_URL = "https://syrabit.ai"


# ── Task #295 — Assamese path-prefix helper ─────────────────────────────
# Converts an English chapter URL into its Assamese sibling URL using a
# dedicated /as/<…> path prefix instead of the previous ?lang=as query
# parameter. Google treats path-based language variants as much stronger
# hreflang signals than query-string variants, so the chapter sitemap
# now emits a clean path-based URL (the chapter SPA route has matching
# /as/* variants in App.jsx, so the URL actually resolves).
#
# Scope note: only the *chapter* sitemap uses this helper. The HTML
# renderers in this file (about, subject landing, topic page-type) keep
# their ?lang=as hreflang form because the SPA does not yet expose
# /as/* routes for those non-chapter pages — emitting a path that 404s
# would be worse for SEO than the query-string form.
#
# When `slug_as` is supplied (chapter sitemap loop has access to the
# stored Assamese slug), the LAST path segment is replaced with the
# Assamese slug — yielding a fully-translated URL like
#   https://syrabit.ai/as/ahsec/class-12/physics/বৈদ্যুতিক-আধান
# Otherwise the English path is preserved under /as/ so URLs stay
# resolvable even before the backfill script has populated slug_as.
def _as_alt_url(en_url: str, *, slug_as: str = "") -> str:
    try:
        from urllib.parse import urlparse, urlunparse, quote
        p = urlparse(en_url)
        new_path = "/as" + (p.path if p.path.startswith("/") else "/" + p.path)
        if slug_as:
            parts = new_path.rstrip("/").split("/")
            if parts:
                parts[-1] = quote(slug_as, safe="-")
                new_path = "/".join(parts)
        return urlunparse(p._replace(path=new_path))
    except Exception:
        # Defensive fallback: never break SEO HTML rendering on a URL
        # parsing edge case — degrade to the old query-param form.
        sep = "&" if "?" in en_url else "?"
        return f"{en_url}{sep}lang=as"

STATIC_PAGES = [
    ("/home", "weekly", "1.0"),
    ("/about", "monthly", "0.9"),
    ("/pricing", "monthly", "0.8"),
    ("/library", "weekly", "0.9"),
    ("/curriculum", "weekly", "0.8"),
    ("/exam-routine", "weekly", "0.8"),
    ("/terms", "yearly", "0.3"),
    ("/privacy", "yearly", "0.3"),
    ("/technology", "monthly", "0.7"),
]

_SITEMAP_TYPES = ["notes", "mcqs", "important-questions", "examples", "definition"]

def _build_urlset(entries: list[dict]) -> str:
    """Build a sitemap urlset XML. When any entry has `has_assamese=True`,
    emit `xhtml:link rel="alternate"` entries for both EN and AS variants
    (Phase E, Plan 7 — bilingual hreflang in sitemaps)."""
    any_alt = any(e.get("has_assamese") for e in entries)
    if any_alt:
        opener = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        )
    else:
        opener = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', opener]
    for e in entries:
        loc = e["loc"]
        if e.get("has_assamese"):
            # Task #295 — emit the dedicated /as/<path> alternate. When a
            # translated Assamese chapter slug is available, also rewrite
            # the last segment so the alternate URL is fully native (e.g.
            # /as/ahsec/class-12/physics/বৈদ্যুতিক-আধান). Without slug_as
            # the path is still /as/<en-slug> which the backend resolver
            # accepts as a fallback. Both forms beat ?lang=as for SEO.
            as_loc = _as_alt_url(loc, slug_as=e.get("slug_as", ""))
            # Task #291 — sitemap guard: emit en-IN, as-IN, and x-default
            # alternates so Google Search Console treats Assamese variants
            # as first-class siblings of the English page.
            alt = (
                f'<xhtml:link rel="alternate" hreflang="en-IN" href="{loc}"/>'
                f'<xhtml:link rel="alternate" hreflang="as-IN" href="{as_loc}"/>'
                f'<xhtml:link rel="alternate" hreflang="x-default" href="{loc}"/>'
            )
        else:
            alt = ""
        lines.append(
            f'  <url><loc>{loc}</loc>'
            f'<lastmod>{e["lastmod"]}</lastmod>'
            f'<changefreq>{e.get("freq", "monthly")}</changefreq>'
            f'<priority>{e["pri"]}</priority>'
            f'{alt}</url>'
        )
    lines.append("</urlset>")
    return "\n".join(lines)

def _xml_response(xml: str) -> Response:
    return Response(
        content=xml,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )

async def _fetch_published_pages() -> list[dict]:
    try:
        return await _db.seo_pages.find(
            {"status": "published"},
            {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1,
             "chapter_slug": 1, "topic_slug": 1, "page_type": 1, "updated_at": 1,
             "generated_at": 1, "created_at": 1},
        ).to_list(50000)
    except Exception:
        return []

async def _build_valid_slug_chains() -> set[tuple[str, str, str]]:
    try:
        lib_subjects = await _db.subjects.find({"status": "published"}, {"_id": 0, "slug": 1, "stream_id": 1}).to_list(500)
        lib_streams = {s["id"]: s for s in await _db.streams.find({}, {"_id": 0, "id": 1, "class_id": 1}).to_list(500)}
        lib_classes = {c["id"]: c for c in await _db.classes.find({}, {"_id": 0, "id": 1, "slug": 1, "board_id": 1}).to_list(500)}
        lib_boards = {b["id"]: b for b in await _db.boards.find({}, {"_id": 0, "id": 1, "slug": 1}).to_list(500)}
        valid = set()
        for sub in lib_subjects:
            stream = lib_streams.get(sub.get("stream_id", ""))
            cls = lib_classes.get(stream.get("class_id", "")) if stream else None
            board = lib_boards.get(cls.get("board_id", "")) if cls else None
            if board and cls and sub.get("slug"):
                b_slug = board.get("slug", "")
                c_slug = cls.get("slug", "")
                if b_slug and c_slug:
                    valid.add((b_slug, c_slug, sub["slug"]))
        return valid
    except Exception:
        return set()


def _changefreq_from_lastmod(lastmod: str, today: str) -> tuple[str, str]:
    """Task #246 — derive changefreq + priority from the age of lastmod.

    < 7 days  → daily   / 0.9
    < 30 days → weekly  / 0.8
    older     → monthly / 0.6
    """
    try:
        from datetime import datetime as _dt
        diff = (_dt.fromisoformat(today) - _dt.fromisoformat(lastmod[:10])).days
        if diff < 7:
            return "daily", "0.9"
        if diff < 30:
            return "weekly", "0.8"
    except Exception:
        pass
    return "monthly", "0.6"


def _page_to_entry(p: dict, today: str, valid_chains: set[tuple[str, str, str]] | None = None) -> dict | None:
    bs, cs, ss, ts = p.get("board_slug"), p.get("class_slug"), p.get("subject_slug"), p.get("topic_slug")
    pt = p.get("page_type", "notes")
    if not all([bs, cs, ss, ts]):
        return None
    if pt not in _SITEMAP_TYPES:
        return None
    if valid_chains is not None and (bs, cs, ss) not in valid_chains:
        return None
    base_path = f"/{bs}/{cs}/{ss}/{ts}"
    path = base_path if pt == "notes" else f"{base_path}/{pt}"
    try:
        raw = p.get("updated_at", "")
        if not raw:
            raw = p.get("generated_at", "") or p.get("created_at", "")
        lastmod = raw[:10] if raw else today
    except Exception:
        lastmod = today
    freq, pri = _changefreq_from_lastmod(lastmod, today)
    return {
        "loc": f"{BASE_URL}{path}",
        "lastmod": lastmod,
        "pri": pri,
        "freq": freq,
        "page_type": pt,
    }


@router.get("/sitemap-index.xml", response_class=Response)
async def get_sitemap_index():
    """
    Build a sitemap index that lists ONLY non-empty sub-sitemaps.

    Google Search Console flags any sub-sitemap that returns
    `<urlset></urlset>` with zero entries as a fetch error
    ("1 error" in the Sitemaps report). Three of our sub-sitemaps
    (PYQs, examples, definitions) have been empty for weeks
    because none of those page_types have published rows yet —
    that turned a single submission into 4 GSC errors out of 9.

    The fix: every sub-sitemap (including the ones in
    `always_include`) is checked for actual published content
    before being listed. If `seo_pages` has no published row for
    its page_type, it is skipped here AND will not appear under
    `<loc>` entries inside the index. The endpoint that serves
    the sub-sitemap itself remains live for direct hits — it
    just stops being advertised.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sitemap_to_types = {
        "sitemap-pages.xml":       None,                     # static pages, always served
        "sitemap-subjects.xml":    ["notes"],                # subjects derived from notes
        "sitemap-chapters.xml":    ["notes"],                # chapters derived from notes
        "sitemap-learn.xml":       None,                     # editorial pages, always served
        "sitemap-notes.xml":       ["notes"],
        "sitemap-mcqs.xml":        ["mcqs"],
        "sitemap-pyqs.xml":        ["important-questions"],
        "sitemap-examples.xml":    ["examples"],
        "sitemap-definitions.xml": ["definition"],
    }
    all_types = sorted({t for ts in sitemap_to_types.values() if ts for t in ts})
    published_types: set[str] = set()
    if all_types:
        async for rec in _db.seo_pages.aggregate([
            {"$match": {"status": "published", "page_type": {"$in": all_types}}},
            {"$group": {"_id": "$page_type"}},
        ]):
            published_types.add(rec["_id"])
    sitemap_names: list[str] = []
    for name, required_types in sitemap_to_types.items():
        if required_types is None:
            sitemap_names.append(name)
            continue
        if any(t in published_types for t in required_types):
            sitemap_names.append(name)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for name in sitemap_names:
        # Use root-level aliases (e.g. /sitemap-subjects.xml) rather than
        # /api/seo/sitemap-subjects.xml so Googlebot follows paths that the
        # CF Pages Worker intercepts cleanly via SEO_PASSTHROUGH_RE (Task #259).
        lines.append(
            f"  <sitemap><loc>{BASE_URL}/{name}</loc>"
            f"<lastmod>{today}</lastmod></sitemap>"
        )
    lines.append("</sitemapindex>")
    return _xml_response("\n".join(lines))


@router.get("/sitemap-pages.xml", response_class=Response)
async def get_sitemap_pages():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = [{"loc": f"{BASE_URL}{path}", "lastmod": today, "pri": pri, "freq": freq}
               for path, freq, pri in STATIC_PAGES]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-subjects.xml", response_class=Response)
async def get_sitemap_subjects():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lib_subjects = await _db.subjects.find({"status": "published"}, {"_id": 0}).to_list(500)
    lib_streams = {s["id"]: s for s in await _db.streams.find({}, {"_id": 0}).to_list(500)}
    lib_classes = {c["id"]: c for c in await _db.classes.find({}, {"_id": 0}).to_list(500)}
    lib_boards = {b["id"]: b for b in await _db.boards.find({}, {"_id": 0}).to_list(500)}
    seen_keys = set()
    entries = []
    for sub in lib_subjects:
        stream = lib_streams.get(sub.get("stream_id", ""))
        cls = lib_classes.get(stream.get("class_id", "")) if stream else None
        board = lib_boards.get(cls.get("board_id", "")) if cls else None
        if not (board and cls and sub.get("slug")):
            continue
        b_slug = board.get("slug", "")
        c_slug = cls.get("slug", "")
        if not b_slug or not c_slug:
            continue
        key = (b_slug, c_slug, sub["slug"])
        if key not in seen_keys:
            seen_keys.add(key)
            entries.append({
                "loc": f"{BASE_URL}/{key[0]}/{key[1]}/{key[2]}",
                "lastmod": today, "pri": "0.7", "freq": "weekly",
            })
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-chapters.xml", response_class=Response)
async def get_sitemap_chapters():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lib_subjects = await _db.subjects.find({"status": "published"}, {"_id": 0}).to_list(500)
    lib_streams = {s["id"]: s for s in await _db.streams.find({}, {"_id": 0}).to_list(500)}
    lib_classes = {c["id"]: c for c in await _db.classes.find({}, {"_id": 0}).to_list(500)}
    lib_boards = {b["id"]: b for b in await _db.boards.find({}, {"_id": 0}).to_list(500)}
    chapters = await _db.chapters.find({}, {"_id": 0, "id": 1, "subject_id": 1, "slug": 1, "slug_as": 1, "title": 1, "updated_at": 1, "created_at": 1, "content_as": 1}).to_list(5000)
    sub_map = {s["id"]: s for s in lib_subjects}
    entries = []
    for ch in chapters:
        sub = sub_map.get(ch.get("subject_id", ""))
        if not sub or not ch.get("slug"):
            continue
        stream = lib_streams.get(sub.get("stream_id", ""))
        cls = lib_classes.get(stream.get("class_id", "")) if stream else None
        board = lib_boards.get(cls.get("board_id", "")) if cls else None
        if not board or not cls or not sub.get("slug"):
            continue
        b_slug = board.get("slug", "")
        c_slug = cls.get("slug", "")
        if not b_slug or not c_slug:
            continue
        ch_slug = ch.get("slug") or re.sub(r'[^a-z0-9]+', '-', (ch.get("title") or "").lower()).strip('-')
        if not ch_slug:
            continue
        raw = ch.get("updated_at", "") or ch.get("created_at", "")
        lastmod = raw[:10] if raw else today
        freq, pri = _changefreq_from_lastmod(lastmod, today)
        entries.append({
            "loc": f"{BASE_URL}/{b_slug}/{c_slug}/{sub['slug']}/{ch_slug}",
            "lastmod": lastmod, "pri": pri, "freq": freq,
            "has_assamese": bool((ch.get("content_as") or "").strip()),
            # Task #295 — pass the stored Assamese slug through so
            # _build_urlset can emit the path-based hreflang alternate
            # with the actual translated URL segment instead of a
            # ?lang=as query string. Empty when not yet backfilled.
            "slug_as": (ch.get("slug_as") or "").strip(),
        })
    # Task #291 — emit a sitemap-time WARNING when published English chapters
    # are missing their Assamese (content_as) sibling. The hreflang="as-IN"
    # alternate is rendered in _build_urlset for has_assamese=True only, so
    # this warning surfaces SEO coverage gaps where users following the
    # Assamese alternate would 404-equivalently.
    _missing_as = sum(1 for e in entries if not e.get("has_assamese"))
    if _missing_as:
        logger.warning(
            "[T291][sitemap] %d/%d chapter URLs lack an Assamese (content_as) "
            "alternate — hreflang='as-IN' will be omitted for those pages. "
            "Run scripts/embed_assamese_corpus.py + translate the missing "
            "chapters to close the gap.",
            _missing_as, len(entries),
        )
    return _xml_response(_build_urlset(entries))


async def _fetch_learn_entries(today: str) -> list[dict]:
    try:
        docs = await _db.cms_documents.find(
            {"status": "published", "doc_type": {"$ne": "personalized"}},
            {"_id": 0, "seo_slug": 1, "id": 1, "updated_at": 1, "created_at": 1},
        ).to_list(5000)
        entries = []
        for doc in docs:
            slug = doc.get("seo_slug") or doc.get("id", "")
            if not slug:
                continue
            raw = doc.get("updated_at", "") or doc.get("created_at", "")
            lastmod = raw[:10] if raw else today
            freq, pri = _changefreq_from_lastmod(lastmod, today)
            entries.append({
                "loc": f"{BASE_URL}/learn/{slug}",
                "lastmod": lastmod,
                "pri": pri,
                "freq": freq,
            })
        return entries
    except Exception:
        return []


@router.get("/sitemap-learn.xml", response_class=Response)
async def get_sitemap_learn():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = await _fetch_learn_entries(today)
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-notes.xml", response_class=Response)
async def get_sitemap_notes():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    entries = [e for p in pages if (e := _page_to_entry(p, today, valid_chains)) and e["page_type"] == "notes"]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-mcqs.xml", response_class=Response)
async def get_sitemap_mcqs():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    entries = [e for p in pages if (e := _page_to_entry(p, today, valid_chains)) and e["page_type"] == "mcqs"]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-pyqs.xml", response_class=Response)
async def get_sitemap_pyqs():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    entries = [e for p in pages if (e := _page_to_entry(p, today, valid_chains)) and e["page_type"] == "important-questions"]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-examples.xml", response_class=Response)
async def get_sitemap_examples():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    entries = [e for p in pages if (e := _page_to_entry(p, today, valid_chains)) and e["page_type"] == "examples"]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap-definitions.xml", response_class=Response)
async def get_sitemap_definitions():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    entries = [e for p in pages if (e := _page_to_entry(p, today, valid_chains)) and e["page_type"] == "definition"]
    return _xml_response(_build_urlset(entries))


@router.get("/sitemap.xml", response_class=Response)
async def get_dynamic_sitemap():
    """Legacy combined sitemap — kept for backward compatibility."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = [{"loc": f"{BASE_URL}{path}", "lastmod": today, "pri": pri, "freq": freq}
               for path, freq, pri in STATIC_PAGES]
    pages = await _fetch_published_pages()
    valid_chains = await _build_valid_slug_chains()
    for p in pages:
        e = _page_to_entry(p, today, valid_chains)
        if e:
            entries.append(e)
    learn_entries = await _fetch_learn_entries(today)
    entries.extend(learn_entries)
    return _xml_response(_build_urlset(entries))


# ─── PUBLIC: Browse by subject ──────────────────────────────────────────────

@router.get("/browse/{board}/{class_slug}/{subject_slug}")
async def browse_subject_topics(board: str, class_slug: str, subject_slug: str):
    pages = await _db.seo_pages.find(
        {
            "board_slug": board,
            "class_slug": class_slug,
            "subject_slug": subject_slug,
            "status": "published",
        },
        {"_id": 0, "content": 0},
    ).sort("chapter_slug", 1).to_list(5000)

    chapters = {}
    for p in pages:
        key = p["chapter_slug"]
        if key not in chapters:
            chapters[key] = {
                "chapter_slug": key,
                "chapter_title": p.get("chapter_title", key),
                "topics": {},
            }
        t_key = p["topic_slug"]
        if t_key not in chapters[key]["topics"]:
            chapters[key]["topics"][t_key] = {
                "topic_slug": t_key,
                "topic_title": p.get("topic_title", t_key),
                "page_types": [],
            }
        chapters[key]["topics"][t_key]["page_types"].append(p["page_type"])

    result = []
    for ch in chapters.values():
        ch["topics"] = list(ch["topics"].values())
        result.append(ch)

    return {"chapters": result, "total_topics": sum(len(ch["topics"]) for ch in result)}


# ─── ADMIN: Pilot content generation (AHSEC Class 11 – first N chapters) ─────

@router.post("/pilot")
async def generate_pilot_content(
    board_name: str = "AHSEC",
    class_name: str = "Class 11",
    subject_keyword: str = "maths",
    chapter_limit: int = 3,
    _admin: dict = Depends(_require_admin),
):
    """Generate seed content for the first `chapter_limit` chapters of a subject.
    Used to bootstrap pilot SEO pages before batch generation."""
    board = await _db.boards.find_one(
        {"name": {"$regex": board_name, "$options": "i"}}, {"_id": 0}
    )
    if not board:
        raise HTTPException(status_code=404, detail=f"Board '{board_name}' not found")

    # Try exact regex on name first, then fall back to searching description
    # (DB stores "HS 1st Year" with description "Class 11 — AHSEC")
    cls = await _db.classes.find_one(
        {"board_id": board["id"], "name": {"$regex": class_name, "$options": "i"}}, {"_id": 0}
    )
    if not cls:
        cls = await _db.classes.find_one(
            {"board_id": board["id"], "description": {"$regex": class_name, "$options": "i"}}, {"_id": 0}
        )
    if not cls:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found under {board_name}")

    streams = await _db.streams.find({"class_id": cls["id"]}, {"_id": 0}).to_list(20)
    stream_ids = [s["id"] for s in streams]

    subject = await _db.subjects.find_one(
        {"stream_id": {"$in": stream_ids}, "name": {"$regex": subject_keyword, "$options": "i"}},
        {"_id": 0},
    )
    if not subject:
        raise HTTPException(status_code=404, detail=f"Subject matching '{subject_keyword}' not found")

    chapters = await _db.chapters.find(
        {"subject_id": subject["id"]}, {"_id": 0}
    ).sort("order_index", 1).limit(chapter_limit).to_list(chapter_limit)

    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found for this subject")

    created_topics = 0
    generated_pages = 0
    errors = 0

    for ch in chapters:
        existing = await _db.topics.find_one({"chapter_id": ch["id"]}, {"_id": 0, "id": 1})
        if existing:
            topic = await _db.topics.find_one({"chapter_id": ch["id"]}, {"_id": 0})
        else:
            topic = {
                "id": f"topic-{uuid.uuid4().hex[:8]}",
                "chapter_id": ch["id"],
                "subject_id": ch.get("subject_id", subject["id"]),
                "title": ch.get("title", ""),
                "slug": _slug(ch.get("title", "")),
                "definition": ch.get("description", ""),
                "examples": "",
                "order": ch.get("order_index", 0),
                "status": "published",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await _db.topics.insert_one(topic)
            topic.pop("_id", None)
            created_topics += 1

        if not topic or not topic.get("id"):
            continue

        hierarchy = await _resolve_hierarchy(topic)
        if not hierarchy:
            errors += 1
            continue

        for pt in PAGE_TYPES:
            existing_page = await _db.seo_pages.find_one(
                {"topic_id": topic["id"], "page_type": pt}, {"_id": 0, "id": 1}
            )
            if existing_page:
                continue
            try:
                page = await _generate_single_page(topic, pt, hierarchy)
                if page:
                    generated_pages += 1
            except Exception as e:
                logger.error(f"Pilot error {topic['title']}/{pt}: {e}")
                errors += 1

    return {
        "board": board_name,
        "class": class_name,
        "subject": subject.get("name"),
        "chapters_processed": len(chapters),
        "topics_created": created_topics,
        "pages_generated": generated_pages,
        "errors": errors,
        "message": f"Pilot complete: {generated_pages} pages generated for {len(chapters)} chapters",
    }

# ─── ADMIN: Bulk publish ─────────────────────────────────────────────────────

@router.post("/bulk-publish")
async def bulk_publish_pages(
    page_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    min_score: int = _QUALITY_PUBLISH_THRESHOLD,
    _admin: dict = Depends(_require_admin),
):
    """Publish draft SEO pages that meet the quality threshold."""
    query: dict = {
        "status": {"$ne": "published"},
        "$or": [
            {"quality.score": {"$gte": min_score}},
            {"quality_score.score": {"$gte": min_score}},
        ],
    }
    if page_type:
        query["page_type"] = page_type
    if subject_id:
        query["subject_id"] = subject_id

    affected_ids = [
        doc["id"] async for doc in _db.seo_pages.find(query, {"_id": 0, "id": 1})
    ]
    result = await _db.seo_pages.update_many(
        query,
        {"$set": {"status": "published", "in_sitemap": True, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.modified_count > 0 and affected_ids:
        try:
            published_pages = await _db.seo_pages.find(
                {"id": {"$in": affected_ids}},
                {"_id": 0, "board_slug": 1, "class_slug": 1, "subject_slug": 1, "topic_slug": 1, "page_type": 1}
            ).to_list(len(affected_ids))
            from routes.bot_discovery import indexnow_batcher
            for p in published_pages:
                await indexnow_batcher.queue_page(p)
            await indexnow_batcher.flush(source="seo_bulk_publish")
        except Exception:
            pass
    return {
        "published": result.modified_count,
        "message": f"Published {result.modified_count} pages (score ≥ {min_score})",
    }


# ─── ADMIN: Job progress tracking (in-memory) ────────────────────────────────

_seo_jobs: dict = {}  # job_id -> {status, total, done, errors, current, started_at, finished_at}

# Task #457: any topic status that is NOT one of these is treated as
# eligible for batch/auto-run generation. This is intentionally broad
# so the existing 991 legacy topics (which may have missing or
# non-standard statuses) actually get picked up. Callers that need
# strict published-only behaviour pass ``only_published=True``.
_TOPIC_INELIGIBLE_STATUSES = {"archived", "rejected", "deleted", "hidden"}


def _eligible_topic_filter(only_published: bool = False) -> dict:
    if only_published:
        return {"status": "published"}
    return {
        "$or": [
            {"status": {"$exists": False}},
            {"status": None},
            {"status": ""},
            {"status": {"$nin": list(_TOPIC_INELIGIBLE_STATUSES)}},
        ]
    }


def _job_update(jid: str, **kwargs):
    if jid in _seo_jobs:
        _seo_jobs[jid].update(kwargs)


def _job_record_outcome(jid: str, topic_id: str, topic_title: str, page_type: str,
                        status: str, reason: str = ""):
    """Append a per-topic generation outcome to the job for live UI feedback.

    Keeps only the most recent 100 outcomes in memory to bound size,
    and aggregates skip/error reasons in a counter the admin UI surfaces.
    """
    job = _seo_jobs.get(jid)
    if not job:
        return
    outcomes = job.setdefault("outcomes", [])
    outcomes.append({
        "topic_id": topic_id,
        "topic_title": topic_title,
        "page_type": page_type,
        "status": status,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    if len(outcomes) > 5000:
        del outcomes[: len(outcomes) - 5000]
    if reason and status in ("skipped", "failed"):
        reasons = job.setdefault("reasons", {})
        reasons[reason] = reasons.get(reason, 0) + 1


@router.get("/jobs/{job_id}")
async def get_job_progress(job_id: str, _admin: dict = Depends(_require_admin)):
    job = _seo_jobs.get(job_id)
    if not job:
        # Fall back to DB log
        log = await _db.seo_generation_log.find_one({"job_id": job_id}, {"_id": 0})
        if log:
            return log
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ─── ADMIN: Per-subject coverage stats ──────────────────────────────────────

@router.get("/subject-coverage")
async def get_subject_coverage(_admin: dict = Depends(_require_admin)):
    """
    Return per-subject pipeline stats:
    board / class / stream / subject / chapters / topics / seo_pages / coverage_pct
    Used by the Pipeline tab in AdminSeoManager.
    """
    subjects = await _db.subjects.find({}, {"_id": 0}).to_list(500)
    result = []

    for subj in subjects:
        sid      = subj.get("id", "")
        sid_name = subj.get("name", "Unknown")
        sid_slug = subj.get("slug", "")

        # Resolve hierarchy for display
        stream = await _db.streams.find_one({"id": subj.get("stream_id", "")}, {"_id": 0}) if subj.get("stream_id") else None
        cls    = await _db.classes.find_one({"id": stream.get("class_id", "")}, {"_id": 0}) if stream else None
        board  = await _db.boards.find_one({"id": cls.get("board_id", "")}, {"_id": 0}) if cls else None

        ch_count    = await _db.chapters.count_documents({"subject_id": sid})
        topic_count = await _db.topics.count_documents({"subject_id": sid})
        page_count  = await _db.seo_pages.count_documents({"subject_slug": sid_slug}) if sid_slug else 0
        expected    = topic_count * len(AUTO_PAGE_TYPES)
        coverage    = round((page_count / expected) * 100, 1) if expected > 0 else 0

        result.append({
            "subject_id":   sid,
            "subject_name": sid_name,
            "subject_slug": sid_slug,
            "stream":       stream.get("name", "") if stream else "",
            "stream_slug":  stream.get("slug", "") if stream else "",
            "class_name":   cls.get("name", "") if cls else "",
            "class_slug":   cls.get("slug", "") if cls else "",
            "board_name":   board.get("name", "") if board else "",
            "board_slug":   board.get("slug", "") if board else "",
            "chapters":     ch_count,
            "topics":       topic_count,
            "seo_pages":    page_count,
            "coverage_pct": coverage,
            "status": (
                "complete"  if coverage >= 95 else
                "partial"   if coverage > 0   else
                "no_pages"  if topic_count > 0 else
                "no_topics"
            ),
        })

    result.sort(key=lambda x: (-x["seo_pages"], x["subject_name"]))
    return {"subjects": result, "total": len(result)}


# ─── ADMIN: Per-subject pipeline run ────────────────────────────────────────

@router.post("/run-subject")
async def run_subject_pipeline(
    background_tasks: BackgroundTasks,
    subject_id: str,
    force: bool = False,
    page_types: Optional[List[str]] = None,
    _admin: dict = Depends(_require_admin),
):
    """
    Run the full SEO pipeline for ONE subject:
      1. AI extract topics from chapters (skips if topics already exist, unless force=True)
      2. Generate all missing SEO pages for each topic
      3. Regen sitemap
    Returns job_id for polling via GET /seo/jobs/{job_id}
    """
    sub = await _db.subjects.find_one({"id": subject_id}, {"_id": 0, "name": 1})
    if not sub:
        raise HTTPException(status_code=404, detail="Subject not found")

    types_to_run = page_types or AUTO_PAGE_TYPES
    job_id = f"subj-{uuid.uuid4().hex[:10]}"
    _seo_jobs[job_id] = {
        "job_id":      job_id,
        "subject_id":  subject_id,
        "subject_name": sub.get("name", subject_id),
        "status":      "queued",
        "total":       0,
        "done":        0,
        "errors":      0,
        "skipped":     0,
        "current":     "Starting…",
        "started_at":  datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "page_types":  types_to_run,
    }
    background_tasks.add_task(_run_subject_bg, job_id, subject_id, force, types_to_run)
    return {"job_id": job_id, "subject_name": sub.get("name"), "status": "queued"}


async def _run_subject_bg(job_id: str, subject_id: str, force: bool, page_types: list):
    try:
        sub = await _db.subjects.find_one({"id": subject_id}, {"_id": 0, "name": 1})
        sub_name = sub.get("name", subject_id) if sub else subject_id

        # ── Step 1: AI extract topics ────────────────────────────────────────
        _job_update(job_id, status="extracting", current=f"Extracting topics for {sub_name}…")
        chapters = await _db.chapters.find({"subject_id": subject_id}, {"_id": 0}).to_list(200)
        new_topics = 0
        errors = 0

        for ch in chapters:
            existing = await _db.topics.count_documents({"chapter_id": ch["id"]})
            if existing > 0 and not force:
                continue
            title   = ch.get("title", "").strip()
            content = (ch.get("content") or "").strip()
            if not title:
                continue

            topic_titles: list[str] = []
            if _call_llm and len(content) > 150:
                try:
                    msgs = [
                        {"role": "system", "content": (
                            "You are an educational curriculum analyst. "
                            "Extract 5-12 GRANULAR topic-level study pages from this chapter. "
                            "Each topic should be a SPECIFIC concept, definition, theory, law, or process — "
                            "NOT the chapter title and NOT a broad summary. "
                            "Think of textbook sub-headings: each one is a distinct topic. "
                            "Each topic: 2-8 words, must be distinct and non-overlapping. "
                            'Return ONLY a valid JSON array of strings, e.g. ["Topic One", "Topic Two"].'
                        )},
                        {"role": "user", "content": f"Chapter: {title}\n\nContent:\n{content[:4000]}"},
                    ]
                    raw = await asyncio.wait_for(_call_llm(msgs, max_tokens=512), timeout=30)
                    topic_titles = _robust_parse_json_array(raw)
                except Exception as exc:
                    logger.warning(f"[run-subject] topic extract failed for {title!r}: {exc}")
                    errors += 1

            if not topic_titles:
                topic_titles = [title]

            if force and existing:
                await _db.topics.delete_many({"chapter_id": ch["id"]})

            base_order = ch.get("order_index", ch.get("chapter_number", 0))
            for idx, t_title in enumerate(topic_titles):
                await _db.topics.insert_one({
                    "id":            f"topic-{uuid.uuid4().hex[:8]}",
                    "chapter_id":    ch["id"],
                    "subject_id":    subject_id,
                    "chapter_title": title,
                    "title":         t_title,
                    "slug":          _slug(t_title),
                    "definition":    ch.get("description", ""),
                    "examples":      "",
                    "order":         base_order * 100 + idx,
                    "status":        "published",
                    "created_at":    datetime.now(timezone.utc).isoformat(),
                })
                new_topics += 1

        # ── Step 2: Generate SEO pages ────────────────────────────────────────
        topics = await _db.topics.find(
            {"subject_id": subject_id, "status": "published"}, {"_id": 0}
        ).to_list(2000)

        total_ops = len(topics) * len(page_types)
        _job_update(
            job_id,
            status="generating",
            total=total_ops,
            current=f"Generating pages for {len(topics)} topics × {len(page_types)} types…",
        )

        done = 0
        skipped = 0

        for topic in topics:
            _job_update(job_id, current=f"Topic: {topic.get('title', '')}")
            try:
                hierarchy = await _resolve_hierarchy(topic)
                if not hierarchy:
                    skipped += len(page_types)
                    done    += len(page_types)
                    _job_update(job_id, done=done, skipped=skipped)
                    continue

                for pt in page_types:
                    existing = await _db.seo_pages.find_one(
                        {"topic_id": topic["id"], "page_type": pt}, {"_id": 0, "id": 1}
                    )
                    if existing and not force:
                        skipped += 1
                        done    += 1
                        _job_update(job_id, done=done, skipped=skipped)
                        continue
                    try:
                        page = await _generate_single_page(topic, pt, hierarchy)
                        done += 1 if page else done
                    except Exception as ge:
                        logger.error(f"[run-subject] gen error {topic.get('id')}/{pt}: {ge}")
                        errors += 1
                        done   += 1
                    _job_update(job_id, done=done, errors=errors)

            except Exception as te:
                logger.error(f"[run-subject] topic loop error {topic.get('id')}: {te}")
                errors  += len(page_types)
                done    += len(page_types)
                _job_update(job_id, done=done, errors=errors)

        # ── Step 3: Log + finish ──────────────────────────────────────────────
        now = datetime.now(timezone.utc).isoformat()
        await _db.seo_generation_log.insert_one({
            "job_id": job_id, "subject_id": subject_id, "subject_name": sub_name,
            "new_topics": new_topics, "total_ops": total_ops,
            "generated": done - skipped - errors,
            "skipped": skipped, "errors": errors,
            "completed_at": now,
        })
        try:
            from routes.bot_discovery import indexnow_batcher
            flushed = await indexnow_batcher.flush_force(source="seo_run_subject")
            logger.info(f"IndexNow flush after run-subject: {flushed} URLs")
        except Exception:
            pass
        _job_update(
            job_id,
            status="done",
            finished_at=now,
            current=f"Done — {new_topics} new topics, {done - skipped - errors} pages generated, {skipped} skipped, {errors} errors",
        )
        await _seo_log(
            action="seo:subject_pipeline",
            details=f"Pipeline done for {sub_name}: {new_topics} topics + {done - skipped - errors} seo_pages generated",
        )

    except Exception as e:
        logger.error(f"[run-subject] bg job failed: {e}")
        _job_update(job_id, status="error", current=str(e), finished_at=datetime.now(timezone.utc).isoformat())


# ─── ADMIN: Full auto-run pipeline ──────────────────────────────────────────

@router.get("/diagnose-topics")
async def diagnose_topics(
    limit: int = 50,
    only_blocked: bool = True,
    _admin: dict = Depends(_require_admin),
):
    """Task #457: per-topic eligibility/blocker report.

    Helps admins understand WHY 991 topics produced 0 pages by surfacing
    the exact reason each topic was skipped (status, missing chapter,
    missing subject, no upstream hierarchy, all pages already exist).
    """
    if _db is None:
        return {"items": [], "summary": {}}
    limit = max(1, min(int(limit or 50), 500))
    topics = await _db.topics.find({}, {"_id": 0}).to_list(20000)
    items: list[dict] = []
    summary = {"total": len(topics), "blocked": 0, "ready": 0, "fully_covered": 0}
    reasons: dict = {}

    # Task #457: collect the most recent generation error per topic from the
    # persisted run outcomes so the admin sees actionable failure context
    # alongside structural blockers.
    last_errors: dict[str, dict] = {}
    try:
        recent_logs = await _db.seo_generation_log.find(
            {"outcomes": {"$exists": True}},
            {"_id": 0, "outcomes": 1, "completed_at": 1, "job_id": 1},
        ).sort("completed_at", -1).limit(20).to_list(20)
        for log in recent_logs:
            for o in (log.get("outcomes") or []):
                tid = o.get("topic_id")
                if not tid or o.get("status") not in ("failed", "skipped"):
                    continue
                if tid in last_errors:
                    continue
                last_errors[tid] = {
                    "status": o.get("status"),
                    "reason": o.get("reason", ""),
                    "page_type": o.get("page_type"),
                    "ts": o.get("ts"),
                    "job_id": log.get("job_id"),
                }
    except Exception as e:
        logger.warning(f"diagnose-topics: last_error lookup failed: {e}")

    for topic in topics:
        status = (topic.get("status") or "").lower() or "(unset)"
        ineligible = status in _TOPIC_INELIGIBLE_STATUSES
        blockers: list[str] = []
        if ineligible:
            blockers.append(f"status={status}")
        chapter = await _db.chapters.find_one(
            {"id": topic.get("chapter_id", "")}, {"_id": 0, "id": 1, "subject_id": 1, "title": 1}
        )
        if not chapter:
            blockers.append("missing chapter")
            subject = None
        else:
            subject = await _db.subjects.find_one(
                {"id": chapter.get("subject_id", "")}, {"_id": 0, "id": 1, "name": 1, "slug": 1}
            )
            if not subject:
                blockers.append("missing subject")
        hierarchy_resolved = bool(chapter and subject)
        existing_pages = await _db.seo_pages.find(
            {"topic_id": topic.get("id", "")},
            {"_id": 0, "page_type": 1, "status": 1},
        ).to_list(50)
        page_count = len(existing_pages)
        coverage = {pt: False for pt in PAGE_TYPES}
        for p in existing_pages:
            pt = p.get("page_type")
            if pt in coverage:
                coverage[pt] = True
        if not blockers and page_count >= len(PAGE_TYPES):
            blockers.append("all page types already generated")
            summary["fully_covered"] += 1

        is_blocked = bool(blockers)
        if is_blocked:
            summary["blocked"] += 1
            for b in blockers:
                reasons[b] = reasons.get(b, 0) + 1
        else:
            summary["ready"] += 1

        if only_blocked and not is_blocked:
            continue
        if len(items) < limit:
            items.append({
                "topic_id": topic.get("id"),
                "topic_title": topic.get("title", ""),
                "status": status,
                "chapter_id": topic.get("chapter_id"),
                "subject_id": chapter.get("subject_id") if chapter else None,
                "subject_name": (subject or {}).get("name") if subject else None,
                "page_count": page_count,
                "hierarchy_resolved": hierarchy_resolved,
                "has_existing_page": page_count > 0,
                "coverage": coverage,
                "blockers": blockers,
                "last_error": last_errors.get(topic.get("id", "")),
            })

    summary["reasons"] = reasons
    return {"items": items, "summary": summary}


@router.post("/backfill-notes")
async def backfill_notes(
    background_tasks: BackgroundTasks,
    _admin: dict = Depends(_require_admin),
):
    """Task #457: one-shot backfill that generates the ``notes`` page for
    every eligible topic that does not yet have one. Reuses the auto-run
    job framework so progress and per-topic outcomes appear in the same
    admin UI."""
    job_id = f"job-{uuid.uuid4().hex[:10]}"
    _seo_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Backfill queued…",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "page_types": ["notes"],
        "kind": "backfill-notes",
    }
    background_tasks.add_task(_auto_run_bg, job_id, ["notes"])
    return {"job_id": job_id, "message": "Notes backfill started", "status": "queued"}


@router.post("/auto-run")
async def auto_run_pipeline(
    background_tasks: BackgroundTasks,
    data: PageTypesRequest = PageTypesRequest(),
    _admin: dict = Depends(_require_admin),
):
    """One-click: extract all topics → generate all missing pages → regen sitemap.
    Defaults to AUTO_PAGE_TYPES (notes + mcqs) unless explicit page_types provided."""
    types_to_run = (data.page_types if data else None) or AUTO_PAGE_TYPES
    job_id = f"job-{uuid.uuid4().hex[:10]}"
    _seo_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Starting…",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "page_types": types_to_run,
    }
    background_tasks.add_task(_auto_run_bg, job_id, types_to_run)
    return {"job_id": job_id, "message": "Pipeline started", "status": "queued"}


async def _auto_run_bg(job_id: str, page_types: list):
    try:
        _job_update(job_id, status="extracting", current="Extracting topics from chapters…")

        chapters = await _db.chapters.find({}, {"_id": 0}).to_list(5000)
        new_topics = 0
        for ch in chapters:
            existing = await _db.topics.count_documents({"chapter_id": ch["id"]})
            if existing > 0:
                continue
            title = ch.get("title", "").strip()
            if not title:
                continue
            has_syllabus_content = bool(ch.get("description") or ch.get("content"))
            topic = {
                "id": f"topic-{uuid.uuid4().hex[:8]}",
                "chapter_id": ch["id"],
                "subject_id": ch.get("subject_id", ""),
                "title": title,
                "slug": _slug(title),
                "definition": ch.get("description", ""),
                "examples": "",
                "order": ch.get("order_index", ch.get("chapter_number", 0)),
                "status": "published" if has_syllabus_content else "suggested",
                "source": "syllabus" if has_syllabus_content else "gap-fill",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await _db.topics.insert_one(topic)
            new_topics += 1
            if not has_syllabus_content:
                logger.info(f"Auto-run: topic '{title}' created as suggested (no syllabus content in chapter)")


        # Step 2: Generate missing pages for all topics
        # Task #457: tolerant filter — pick up legacy/missing/suggested
        # statuses, not just "published".
        all_topics = await _db.topics.find(
            _eligible_topic_filter(only_published=False),
            {"_id": 0},
        ).to_list(20000)
        total_ops = len(all_topics) * len(page_types)
        _job_update(job_id, status="generating", total=total_ops, current=f"Generating pages for {len(all_topics)} topics…")

        done = 0
        errors = 0
        skipped = 0
        seo_scores: list[int] = []
        geo_scores: list[int] = []

        for topic in all_topics:
            tid = topic.get("id", "")
            ttitle = topic.get("title", tid)
            _job_update(job_id, current=f"Processing: {ttitle}")
            try:
                hierarchy = await _resolve_hierarchy(topic)
                if not hierarchy:
                    reason = "missing chapter or subject"
                    logger.warning(f"Auto-run: no hierarchy for topic {tid} (chapter_id={topic.get('chapter_id')}) — {reason}")
                    for pt in page_types:
                        _job_record_outcome(job_id, tid, ttitle, pt, "skipped", reason)
                    skipped += len(page_types)
                    done += len(page_types)
                    _job_update(job_id, done=done, skipped=skipped)
                    continue

                for pt in page_types:
                    existing = await _db.seo_pages.find_one(
                        {"topic_id": tid, "page_type": pt},
                        {"_id": 0, "id": 1}
                    )
                    if existing:
                        _job_record_outcome(job_id, tid, ttitle, pt, "skipped", "page already exists")
                        skipped += 1
                        done += 1
                        _job_update(job_id, done=done, skipped=skipped)
                        continue
                    try:
                        page = await _generate_single_page(topic, pt, hierarchy)
                        if page:
                            q = (page.get("quality") or {}).get("score")
                            g = (page.get("quality") or {}).get("geo_score")
                            if isinstance(q, int): seo_scores.append(q)
                            if isinstance(g, int): geo_scores.append(g)
                            fr = page.get("fail_reasons") or []
                            _job_record_outcome(
                                job_id, tid, ttitle, pt, "generated",
                                f"{page.get('status','')} score={q} geo={g}"
                                + (f" — {','.join(fr)}" if fr else "")
                            )
                            done += 1
                        else:
                            _job_record_outcome(job_id, tid, ttitle, pt, "failed",
                                                "generator returned no page")
                            errors += 1
                            done += 1
                    except Exception as e:
                        logger.error(f"Auto-run gen error {tid}/{pt}: {e}")
                        _job_record_outcome(job_id, tid, ttitle, pt, "failed", str(e)[:160])
                        errors += 1
                        done += 1
                    _job_update(job_id, done=done, errors=errors)

            except Exception as e:
                logger.error(f"Auto-run topic error {tid}: {e}")
                for pt in page_types:
                    _job_record_outcome(job_id, tid, ttitle, pt, "failed", f"topic loop: {str(e)[:140]}")
                errors += len(page_types)
                done += len(page_types)
                _job_update(job_id, done=done, errors=errors)

        # Step 3: Log to DB
        now = datetime.now(timezone.utc).isoformat()
        # Task #457: persist per-topic outcomes alongside the run summary so
        # failures are auditable after the in-memory job is gone, and the
        # diagnostic endpoint can surface the most recent generation error
        # for each topic.
        job_outcomes = list(_seo_jobs.get(job_id, {}).get("outcomes") or [])
        job_reasons = dict(_seo_jobs.get(job_id, {}).get("reasons") or {})
        avg_seo = round(sum(seo_scores) / len(seo_scores)) if seo_scores else 0
        avg_geo = round(sum(geo_scores) / len(geo_scores)) if geo_scores else 0
        if job_id in _seo_jobs:
            _seo_jobs[job_id]["avg_seo_score"] = avg_seo
            _seo_jobs[job_id]["avg_geo_score"] = avg_geo
        run_log_doc = {
            "job_id": job_id,
            "total_generated": done - skipped - errors,
            "skipped": skipped,
            "errors": errors,
            "new_topics": new_topics,
            "avg_seo_score": avg_seo,
            "avg_geo_score": avg_geo,
            "completed_at": now,
            "outcomes": job_outcomes,
            "reasons": job_reasons,
            "page_types": list(page_types),
        }
        await _db.seo_generation_log.insert_one(run_log_doc)

        # Task #465: scheduled runs email opted-in admins a daily summary so
        # quietly degraded runs (errors spiking, avg score dropping, 0 pages)
        # don't go unnoticed. Ad-hoc admin-triggered runs are skipped inside
        # `_maybe_dispatch_seo_daily_summary` (trigger != "scheduler").
        try:
            await _maybe_dispatch_seo_daily_summary(job_id, run_log_doc)
        except Exception as email_exc:
            logger.warning(
                f"[seo-daily-summary] post-run dispatch failed for job {job_id}: {email_exc}"
            )

        try:
            from routes.bot_discovery import indexnow_batcher
            flushed = await indexnow_batcher.flush_force(source="seo_auto_run")
            logger.info(f"IndexNow flush after auto-run: {flushed} URLs")
        except Exception:
            pass

        _job_update(
            job_id,
            status="done",
            current=f"Complete — {done - skipped - errors} pages generated, {skipped} skipped, {errors} errors",
            finished_at=now,
        )
        logger.info(f"Auto-run {job_id} complete: done={done} skip={skipped} err={errors}")

    except Exception as e:
        logger.error(f"Auto-run pipeline error: {e}")
        _job_update(job_id, status="error", current=f"Pipeline error: {str(e)[:120]}", finished_at=datetime.now(timezone.utc).isoformat())


# ─── Daily summary email (Task #465) ────────────────────────────────────────
# After every scheduled SEO auto-publish run we email opted-in admins a
# concise summary so degraded runs (errors spiking, avg score dropping, 0
# pages because all topics were blocked) don't go unnoticed. Per-admin
# opt-in via ``admin_notification_prefs.email_seo_daily_summary_enabled``;
# optional UTC quiet-hours window via ``quiet_hours_start_utc`` /
# ``quiet_hours_end_utc`` (inclusive start, exclusive end).

_ADMIN_SEO_PAGE_URL = "https://syrabit.ai/admin/seo"


def _top_failure_reasons(outcomes: list, k: int = 3) -> list:
    """Return the top ``k`` failure reasons from a list of per-topic outcome
    rows (as written into ``db.seo_generation_log.outcomes``)."""
    from collections import Counter
    cnt: Counter = Counter()
    for o in outcomes or []:
        if (o or {}).get("outcome") != "failed":
            continue
        reason = ((o.get("reason") or "unknown")).strip()[:140]
        if not reason:
            reason = "unknown"
        cnt[reason] += 1
    return [{"reason": r, "count": c} for r, c in cnt.most_common(k)]


def _compose_seo_daily_summary(log_doc: dict) -> dict:
    """Build the email payload from a ``seo_generation_log`` row."""
    outcomes = log_doc.get("outcomes") or []
    return {
        "job_id": log_doc.get("job_id", ""),
        "completed_at": log_doc.get("completed_at", ""),
        "pages_generated": int(log_doc.get("total_generated", 0) or 0),
        "skipped": int(log_doc.get("skipped", 0) or 0),
        "errors": int(log_doc.get("errors", 0) or 0),
        "new_topics": int(log_doc.get("new_topics", 0) or 0),
        "avg_seo_score": int(log_doc.get("avg_seo_score", 0) or 0),
        "avg_geo_score": int(log_doc.get("avg_geo_score", 0) or 0),
        "page_types": list(log_doc.get("page_types") or []),
        "top_failure_reasons": _top_failure_reasons(outcomes, 3),
    }


def _format_seo_daily_summary_html(stats: dict) -> str:
    """Render the daily summary as a self-contained HTML email body."""
    fr = stats.get("top_failure_reasons") or []
    if fr:
        rows = "".join(
            f"<li style='margin:4px 0'>{html_mod.escape(item['reason'])} "
            f"<b>×{int(item['count'])}</b></li>"
            for item in fr
        )
    else:
        rows = "<li style='color:#16a34a'>None — every page generated cleanly.</li>"
    pt = ", ".join(stats.get("page_types") or []) or "—"
    return (
        "<div style='font-family:sans-serif;max-width:560px;margin:auto;color:#0f172a'>"
        "<h2 style='color:#7c3aed;margin:0 0 4px'>Syrabit SEO daily summary</h2>"
        f"<p style='color:#475569;margin:0 0 16px'>Job "
        f"<code>{html_mod.escape(stats.get('job_id',''))}</code> finished "
        f"{html_mod.escape(stats.get('completed_at',''))}.</p>"
        "<table style='width:100%;border-collapse:collapse'>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Pages generated</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'><b>{stats['pages_generated']}</b></td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Skipped</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'>{stats['skipped']}</td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Errors</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'>"
        f"<b style='color:{'#c0392b' if stats['errors'] else '#475569'}'>{stats['errors']}</b></td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>New topics extracted</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'>{stats['new_topics']}</td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Avg SEO score</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'><b>{stats['avg_seo_score']}</b></td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Avg GEO score</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'><b>{stats['avg_geo_score']}</b></td></tr>"
        f"<tr><td style='padding:8px;border:1px solid #e2e8f0'>Page types</td>"
        f"<td style='padding:8px;border:1px solid #e2e8f0;text-align:right'>{html_mod.escape(pt)}</td></tr>"
        "</table>"
        "<h3 style='margin:18px 0 6px;color:#0f172a'>Top failure reasons</h3>"
        f"<ul style='margin:0;padding-left:20px;color:#475569'>{rows}</ul>"
        f"<p style='margin:18px 0'><a href='{_ADMIN_SEO_PAGE_URL}' "
        "style='display:inline-block;background:#7c3aed;color:white;text-decoration:none;"
        "padding:10px 20px;border-radius:8px;font-weight:600;font-size:14px'>"
        "Open SEO Manager</a></p>"
        "<p style='color:#94a3b8;font-size:12px;margin-top:24px'>"
        "You're receiving this because the scheduled SEO auto-publish run completed today and "
        "your admin notification preferences have <b>email_seo_daily_summary_enabled</b> on. "
        "Toggle it off (or set quiet hours) from the admin notifications page."
        "</p></div>"
    )


def _quiet_hours_active(prefs: dict, now_utc: datetime) -> bool:
    """True iff ``now_utc.hour`` falls inside the admin's UTC quiet-hours
    window. Window is [start, end) by hour-of-day; supports wrap-around
    (e.g. start=22, end=6 = 22:00–06:00 UTC). Returns False when either
    bound is missing or invalid."""
    start = prefs.get("quiet_hours_start_utc")
    end = prefs.get("quiet_hours_end_utc")
    try:
        start_i = int(start) if start is not None else None
        end_i = int(end) if end is not None else None
    except (TypeError, ValueError):
        return False
    if start_i is None or end_i is None:
        return False
    if not (0 <= start_i <= 23 and 0 <= end_i <= 23) or start_i == end_i:
        return False
    h = now_utc.hour
    if start_i < end_i:
        return start_i <= h < end_i
    return h >= start_i or h < end_i


async def _resolve_seo_summary_audience(db, now_utc: datetime) -> dict:
    """Resolve the full audience breakdown for the SEO daily summary email.

    Returns ``{"recipients": [...], "suppressed_quiet_hours": int,
    "opted_out": int, "no_email": int, "total_admins": int}`` so the
    dispatcher can persist a delivery log that distinguishes "skipped
    because quiet hours" from "skipped because opted out". Task #474.
    """
    try:
        admins = await db.users.find(
            {"is_admin": True}, {"_id": 0, "id": 1, "email": 1}
        ).to_list(500)
    except Exception as exc:
        logger.debug(f"[seo-daily-summary] admin lookup failed: {exc}")
        return {"recipients": [], "suppressed_quiet_hours": 0,
                "opted_out": 0, "no_email": 0, "total_admins": 0}
    try:
        from db_ops import get_admin_notification_prefs, _ADMIN_NOTIF_PREFS_DEFAULTS
    except Exception:
        get_admin_notification_prefs = None
        _ADMIN_NOTIF_PREFS_DEFAULTS = {}
    recipients: list = []
    suppressed_quiet_hours = 0
    opted_out = 0
    no_email = 0
    for u in admins:
        email = (u.get("email") or "").strip()
        if not email:
            no_email += 1
            continue
        admin_id = str(u.get("id") or email)
        prefs: dict = {}
        if get_admin_notification_prefs is not None:
            try:
                prefs = await get_admin_notification_prefs(admin_id)
            except Exception as exc:
                logger.debug(f"[seo-daily-summary] prefs fetch failed for {admin_id}: {exc}")
                prefs = {}
        opt_in_default = bool(_ADMIN_NOTIF_PREFS_DEFAULTS.get("email_seo_daily_summary_enabled", True))
        if not bool(prefs.get("email_seo_daily_summary_enabled", opt_in_default)):
            opted_out += 1
            continue
        if _quiet_hours_active(prefs, now_utc):
            suppressed_quiet_hours += 1
            logger.info(f"[seo-daily-summary] suppressed for {admin_id} — quiet hours active")
            continue
        recipients.append({"admin_id": admin_id, "email": email})
    return {
        "recipients": recipients,
        "suppressed_quiet_hours": suppressed_quiet_hours,
        "opted_out": opted_out,
        "no_email": no_email,
        "total_admins": len(admins),
    }


async def _resolve_seo_summary_recipients(db, now_utc: datetime) -> list:
    """Return ``[{admin_id, email}, ...]`` for admins opted-in to the SEO
    daily summary email and not currently inside their quiet-hours window.

    Thin wrapper around :func:`_resolve_seo_summary_audience` preserved for
    callers that only need the recipient list (and for existing tests)."""
    audience = await _resolve_seo_summary_audience(db, now_utc)
    return audience["recipients"]


async def _send_seo_daily_summary_email(stats: dict, recipients: list) -> dict:
    """Send the rendered summary to each recipient via Resend.
    Fire-and-forget — never raises; returns a small report dict."""
    if not stats:
        return {"sent": 0, "failed": 0, "total": 0, "reason": "no_stats"}
    if not recipients:
        return {"sent": 0, "failed": 0, "total": 0, "reason": "no_recipients"}
    import os as _os
    resend_key = _os.environ.get("RESEND_API_KEY", "").strip()
    if not resend_key:
        return {"sent": 0, "failed": 0, "total": len(recipients), "reason": "no_resend_key"}
    try:
        from email_templates import EMAIL_FROM
    except Exception:
        EMAIL_FROM = _os.environ.get("EMAIL_FROM", "Syrabit.ai <noreply@syrabit.ai>").strip()
    html = _format_seo_daily_summary_html(stats)
    subject = (
        f"Syrabit SEO daily summary · "
        f"{stats['pages_generated']} pages · "
        f"avg SEO {stats['avg_seo_score']} / GEO {stats['avg_geo_score']}"
    )
    try:
        import resend as _resend_sdk
        _resend_sdk.api_key = resend_key
    except Exception as exc:
        logger.warning(f"[seo-daily-summary] resend import failed: {exc}")
        return {"sent": 0, "failed": len(recipients), "total": len(recipients),
                "reason": f"send_error:{type(exc).__name__}"}
    sent = failed = 0
    errors: list = []  # Task #474 — per-admin failures surfaced in admin UI.
    for r in recipients:
        try:
            _resend_sdk.Emails.send({
                "from": EMAIL_FROM,
                "to": [r["email"]],
                "subject": subject,
                "html": html,
            })
            sent += 1
        except Exception as exc:
            failed += 1
            err_msg = f"{type(exc).__name__}: {exc}"[:200]
            errors.append({
                "admin_id": r.get("admin_id", ""),
                "email": r.get("email", ""),
                "error": err_msg,
            })
            logger.warning(f"[seo-daily-summary] send failed to {r.get('email','')}: {exc}")
    logger.info(
        f"[seo-daily-summary] dispatched job={stats.get('job_id','')} "
        f"sent={sent} failed={failed} total={len(recipients)}"
    )
    return {"sent": sent, "failed": failed, "total": len(recipients),
            "subject": subject, "errors": errors}


_SEO_SUMMARY_DISPATCH_LOG = "seo_summary_email_log"
_SEO_SUMMARY_DISPATCH_LOG_MAX = 200  # keep at most ~200 most-recent docs


async def _record_seo_summary_dispatch(doc: dict) -> None:
    """Persist a single dispatch result to ``seo_summary_email_log`` and
    trim the collection to the most-recent ``_SEO_SUMMARY_DISPATCH_LOG_MAX``
    rows. Best-effort — never raises (Task #474).
    """
    if _db is None:
        return
    try:
        await _db[_SEO_SUMMARY_DISPATCH_LOG].insert_one(doc)
    except Exception as exc:
        logger.debug(f"[seo-daily-summary] persist failed: {exc}")
        return
    # Soft cap — drop oldest rows once we cross the limit to keep the
    # collection tiny without a TTL index dependency.
    try:
        coll = _db[_SEO_SUMMARY_DISPATCH_LOG]
        total = await coll.count_documents({})
        excess = total - _SEO_SUMMARY_DISPATCH_LOG_MAX
        if excess > 0:
            cutoff_cursor = coll.find({}, {"_id": 1}).sort("at", 1).limit(excess)
            cutoff_ids = [d["_id"] async for d in cutoff_cursor]
            if cutoff_ids:
                await coll.delete_many({"_id": {"$in": cutoff_ids}})
    except Exception as exc:
        logger.debug(f"[seo-daily-summary] trim failed: {exc}")


async def _maybe_dispatch_seo_daily_summary(job_id: str, log_doc: dict) -> dict:
    """Compose + send the daily summary if this job was triggered by the
    scheduler. Safe to call after every ``_auto_run_bg`` completion — runs
    triggered by an admin button click are silently skipped so we don't
    spam admins with ad-hoc-run summaries.

    Every scheduled invocation persists a row to ``seo_summary_email_log``
    so the admin notifications panel can show recent delivery status
    (Task #474). Manual/non-scheduled runs are intentionally not logged."""
    job_meta = _seo_jobs.get(job_id) or {}
    if job_meta.get("trigger") != "scheduler":
        return {"sent": 0, "failed": 0, "total": 0, "reason": "non_scheduled_run"}
    now = datetime.now(timezone.utc)
    audience = {"recipients": [], "suppressed_quiet_hours": 0,
                "opted_out": 0, "no_email": 0, "total_admins": 0}
    try:
        stats = _compose_seo_daily_summary(log_doc)
        audience = await _resolve_seo_summary_audience(_db, now)
        result = await _send_seo_daily_summary_email(stats, audience["recipients"])
    except Exception as exc:
        logger.warning(f"[seo-daily-summary] dispatch error for job {job_id}: {exc}")
        result = {"sent": 0, "failed": 0, "total": len(audience["recipients"]),
                  "reason": f"dispatch_error:{type(exc).__name__}"}
    # Persist regardless of success so operators can see "0 sent because
    # no_resend_key" or "0 sent because all admins in quiet hours".
    enriched = {
        "at": now,
        "job_id": job_id,
        "sent": int(result.get("sent", 0) or 0),
        "failed": int(result.get("failed", 0) or 0),
        "total_recipients": int(result.get("total", len(audience["recipients"])) or 0),
        "suppressed_quiet_hours": audience["suppressed_quiet_hours"],
        "opted_out": audience["opted_out"],
        "no_email": audience["no_email"],
        "total_admins": audience["total_admins"],
        "reason": result.get("reason"),
        "subject": result.get("subject"),
        "pages_generated": int((log_doc or {}).get("total_generated", 0) or 0),
        # Per-admin send failures so the admin notifications panel can show
        # exactly which recipients hit a Resend error (Task #474 review).
        "errors": list(result.get("errors") or []),
    }
    await _record_seo_summary_dispatch(enriched)
    # Mirror persisted counters back into the return dict so callers
    # (and tests) can see the same view the admin UI will render.
    result.setdefault("suppressed_quiet_hours", audience["suppressed_quiet_hours"])
    result.setdefault("opted_out", audience["opted_out"])
    return result


async def get_recent_seo_summary_dispatches(limit: int = 10) -> list:
    """Fetch the most-recent SEO summary email dispatches for the admin
    notifications panel (Task #474). Returns ``[]`` if MongoDB is
    unavailable or the collection has not yet been written to.
    """
    if _db is None:
        return []
    try:
        cursor = _db[_SEO_SUMMARY_DISPATCH_LOG].find(
            {}, {"_id": 0}
        ).sort("at", -1).limit(max(1, min(int(limit or 10), 50)))
        rows = []
        async for d in cursor:
            at = d.get("at")
            if hasattr(at, "isoformat"):
                d["at"] = at.isoformat()
            rows.append(d)
        return rows
    except Exception as exc:
        logger.debug(f"[seo-daily-summary] history fetch failed: {exc}")
        return []


# ─── Scheduled auto-publish (Task #458) ─────────────────────────────────────
# Periodically run ``_auto_run_bg`` so the 991-topic syllabus steadily fills
# in without an admin clicking the button. Cross-replica safety is provided
# by an atomic compare-and-set on ``db.job_locks`` (``_id`` =
# ``_SEO_AUTO_PUBLISH_LOCK_ID``); the runtime cadence is configurable via
# environment variables so ops can dial it up/down without a code change.

_SEO_AUTO_PUBLISH_LOCK_ID = "seo_auto_publish_marker"
_SEO_AUTO_PUBLISH_LAST_RUN_KEY = "last_run_tag"
_SEO_AUTO_PUBLISH_LOOP_SLEEP_S = 5 * 60  # poll every 5 minutes
_SEO_AUTO_PUBLISH_WINDOW_MINUTES = 30    # ±30 min around target time


def _seo_auto_publish_enabled() -> bool:
    import os
    val = (os.environ.get("SEO_AUTO_PUBLISH_ENABLED", "true") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _seo_auto_publish_frequency() -> str:
    import os
    val = (os.environ.get("SEO_AUTO_PUBLISH_FREQUENCY", "daily") or "").strip().lower()
    return "weekly" if val == "weekly" else "daily"


def _seo_auto_publish_target_hour_utc() -> int:
    import os
    try:
        h = int(os.environ.get("SEO_AUTO_PUBLISH_HOUR_UTC", "2"))
    except (TypeError, ValueError):
        h = 2
    return max(0, min(h, 23))


def _seo_auto_publish_weekday() -> int:
    """Mon=0 .. Sun=6 — only used for weekly mode."""
    import os
    try:
        d = int(os.environ.get("SEO_AUTO_PUBLISH_WEEKDAY", "0"))
    except (TypeError, ValueError):
        d = 0
    return max(0, min(d, 6))


def _seo_auto_publish_page_types() -> list:
    import os
    raw = (os.environ.get("SEO_AUTO_PUBLISH_PAGE_TYPES", "") or "").strip()
    if not raw:
        return list(AUTO_PAGE_TYPES)
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    valid = [p for p in parts if p in ALL_PAGE_TYPES]
    return valid or list(AUTO_PAGE_TYPES)


def _seo_auto_publish_run_tag(now_utc: datetime, frequency: str) -> str:
    if frequency == "weekly":
        iso_year, iso_week, _ = now_utc.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return now_utc.strftime("%Y-%m-%d")


def _should_run_seo_auto_publish_now(now_utc: datetime, last_run_tag: str) -> bool:
    """True iff we are inside the ±window around the configured target time
    AND the dedup marker hasn't already recorded today's/this-week's tag."""
    if not _seo_auto_publish_enabled():
        return False
    frequency = _seo_auto_publish_frequency()
    if frequency == "weekly" and now_utc.weekday() != _seo_auto_publish_weekday():
        return False
    target_hour = _seo_auto_publish_target_hour_utc()
    minutes_from_target = (now_utc.hour - target_hour) * 60 + now_utc.minute
    if abs(minutes_from_target) > _SEO_AUTO_PUBLISH_WINDOW_MINUTES:
        return False
    cur_tag = _seo_auto_publish_run_tag(now_utc, frequency)
    return cur_tag != (last_run_tag or "")


async def _claim_seo_auto_publish_slot(db, cur_tag: str) -> bool:
    """Atomic CAS on ``db.job_locks[_SEO_AUTO_PUBLISH_LOCK_ID]``. Only the
    first replica that flips the tag from ``!= cur_tag`` to ``cur_tag`` wins
    the right to run the pipeline this cycle."""
    from pymongo.errors import DuplicateKeyError
    try:
        res = await db.job_locks.find_one_and_update(
            {
                "_id": _SEO_AUTO_PUBLISH_LOCK_ID,
                _SEO_AUTO_PUBLISH_LAST_RUN_KEY: {"$ne": cur_tag},
            },
            {"$set": {_SEO_AUTO_PUBLISH_LAST_RUN_KEY: cur_tag,
                      "claimed_at": datetime.now(timezone.utc).isoformat()}},
            upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[seo-auto-publish] CAS update failed: {exc}")
        return False
    try:
        await db.job_locks.insert_one({
            "_id": _SEO_AUTO_PUBLISH_LOCK_ID,
            _SEO_AUTO_PUBLISH_LAST_RUN_KEY: cur_tag,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logger.debug(f"[seo-auto-publish] bootstrap insert failed: {exc}")
        return False


async def _try_run_seo_auto_publish_once(db, now_utc: Optional[datetime] = None) -> dict:
    """One iteration of the scheduler loop. Factored out so tests can drive
    it deterministically without a real wall clock."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not _seo_auto_publish_enabled():
        return {"ran": False, "reason": "disabled"}
    try:
        cfg = await db.job_locks.find_one(
            {"_id": _SEO_AUTO_PUBLISH_LOCK_ID},
            {"_id": 0, _SEO_AUTO_PUBLISH_LAST_RUN_KEY: 1},
        ) or {}
    except Exception:
        cfg = {}
    last_tag = cfg.get(_SEO_AUTO_PUBLISH_LAST_RUN_KEY, "")
    if not _should_run_seo_auto_publish_now(now_utc, last_tag):
        return {"ran": False, "reason": "outside_window_or_dedup"}

    cur_tag = _seo_auto_publish_run_tag(now_utc, _seo_auto_publish_frequency())
    if not await _claim_seo_auto_publish_slot(db, cur_tag):
        return {"ran": False, "reason": "lost_race"}

    page_types = _seo_auto_publish_page_types()
    job_id = f"job-sched-{uuid.uuid4().hex[:10]}"
    _seo_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Scheduled auto-publish starting…",
        "started_at": now_utc.isoformat(),
        "finished_at": None,
        "page_types": page_types,
        "trigger": "scheduler",
        "schedule_tag": cur_tag,
    }
    logger.info(
        f"[seo-auto-publish] starting scheduled run job_id={job_id} "
        f"tag={cur_tag} page_types={page_types}"
    )
    asyncio.create_task(_seo_log(
        "seo_auto_publish_started",
        f"Scheduled auto-publish started (tag={cur_tag}, page_types={','.join(page_types)})",
        "info",
    ))
    # Fire-and-forget — `_auto_run_bg` already persists the run summary
    # (with outcomes/reasons/avg scores) to ``db.seo_generation_log`` so the
    # admin Stats endpoint surfaces it as the most recent generation.
    asyncio.create_task(_auto_run_bg(job_id, page_types))
    return {"ran": True, "job_id": job_id, "tag": cur_tag, "page_types": page_types}


async def _seo_auto_publish_loop():
    """Background loop: polls every 5 minutes and fires the auto-run pipeline
    once per day (or per week) inside a ±30 min window around the configured
    target hour. Cross-replica dedup via ``db.job_locks`` so multi-worker
    deployments don't multiply LLM spend."""
    from deps import db, is_mongo_available  # type: ignore
    # Slightly longer warm-up than the SEO digest loop so we never compete
    # with the burst of startup migrations/backfills for DB attention.
    await asyncio.sleep(720)
    while True:
        try:
            if _seo_auto_publish_enabled() and await is_mongo_available():
                await _try_run_seo_auto_publish_once(db)
        except Exception as exc:
            logger.debug(f"[seo-auto-publish] loop iteration error: {exc}")
        await asyncio.sleep(_SEO_AUTO_PUBLISH_LOOP_SLEEP_S)


# ─── Task #471 — proactive staleness alerts for the auto-publish job ────────
#
# The SchedulePanel surfaces "stale" when the last scheduled run is older
# than 36h (daily) or 8d (weekly), but admins only see it if they happen
# to open that tab. This block runs the same check server-side every
# hour and emails the admin audience + drops an in-app notification when
# the cron has stopped firing — and again when it recovers.
#
# Cross-replica dedup + alert-spam debounce are both done via the same
# ``db.job_locks`` collection the auto-publish run itself uses, so we
# don't add a new collection.

_SEO_STALENESS_ALERT_LOCK_ID = "seo_auto_publish_staleness_alert"
_SEO_STALENESS_REALERT_INTERVAL_H = 24
_SEO_STALENESS_LOOP_SLEEP_S = 3600  # 1 hour
_SEO_STALENESS_WARMUP_S = 900       # 15 min — let migrations settle

# ─── Task #491 — liveness heartbeat for the staleness monitor itself ────────
#
# Task #485 surfaced the monitor's ``updated_at`` in the Schedule panel so an
# admin who happens to look can see whether the monitor is alive. This block
# automates that check: every 6h, verify the monitor bumped its heartbeat
# within the last ~3h (2x its own 1h loop interval) and page admins exactly
# once if it hasn't. Same CAS + debounce + recovery shape as the parent
# staleness alert (Task #471) and the CI alert (Task #484), so on-call
# muscle memory is consistent.
_SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID = "seo_staleness_monitor_heartbeat_alert"
_SEO_STALENESS_HEARTBEAT_LOOP_SLEEP_S = 21600  # 6h between liveness checks
_SEO_STALENESS_HEARTBEAT_WARMUP_S = 1200       # 20 min — let the monitor
                                                # finish its own warmup +
                                                # first iteration before we
                                                # start grading it
_SEO_STALENESS_HEARTBEAT_MAX_AGE_H = 3.0       # 2x the monitor's 1h cadence
_SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H = 12  # debounce a continuing
                                                   # outage to ≤2 pages/day


def _seo_auto_publish_staleness_threshold_hours(frequency: str) -> int:
    """Mirror the client-side thresholds in
    ``SchedulePanel.jsx`` so server alerts fire at the same boundary the
    UI surfaces a "stale" badge — no hidden disagreement between the
    two."""
    return 24 * 8 if frequency == "weekly" else 36


def _parse_iso_utc(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _resolve_seo_auto_publish_last_run(db) -> Optional[datetime]:
    """Last actual scheduled run, in UTC. Prefers the ``completed_at`` of
    the most recent ``job-sched-*`` log entry (the source of truth for
    "did the pipeline finish"); falls back to the marker's
    ``claimed_at`` when no log row has been written yet (e.g. fresh
    install, or the run is mid-flight)."""
    try:
        last_log = await db.seo_generation_log.find_one(
            {"job_id": {"$regex": "^job-sched-"}},
            sort=[("completed_at", -1)],
            projection={"_id": 0, "completed_at": 1},
        )
        dt = _parse_iso_utc((last_log or {}).get("completed_at"))
        if dt is not None:
            return dt
    except Exception as exc:
        logger.debug(f"[seo-staleness] log lookup failed: {exc}")
    try:
        marker = await db.job_locks.find_one(
            {"_id": _SEO_AUTO_PUBLISH_LOCK_ID},
            projection={"_id": 0, "claimed_at": 1},
        )
        return _parse_iso_utc((marker or {}).get("claimed_at"))
    except Exception as exc:
        logger.debug(f"[seo-staleness] marker lookup failed: {exc}")
        return None


async def _evaluate_seo_auto_publish_staleness(db, now_utc: datetime) -> dict:
    """Pure evaluation — does NOT fire alerts. Returned shape is also
    suitable for surfacing in ``/seo/auto-publish/schedule`` so the UI
    and the alert pipeline always agree."""
    enabled = _seo_auto_publish_enabled()
    frequency = _seo_auto_publish_frequency()
    threshold_h = _seo_auto_publish_staleness_threshold_hours(frequency)
    last_run = await _resolve_seo_auto_publish_last_run(db)
    age_h = None
    if last_run is not None:
        age_h = (now_utc - last_run).total_seconds() / 3600.0
    stale = enabled and (age_h is None or age_h > threshold_h)
    return {
        "enabled": enabled,
        "frequency": frequency,
        "threshold_h": threshold_h,
        "last_run_at": last_run.isoformat() if last_run else None,
        "age_hours": age_h,
        "stale": stale,
    }


async def _persist_seo_staleness_state(
    db, state_label: str, now_utc: datetime, last_run_at: Optional[str],
    update_alert_at: bool,
) -> None:
    """Upsert the alert log into ``db.job_locks``. ``update_alert_at`` is
    only true on actual sends (stale alert or recovery) so a healthy
    bootstrap doesn't reset the debounce window."""
    payload = {
        "last_state": state_label,
        "last_run_at_observed": last_run_at,
        "updated_at": now_utc.isoformat(),
    }
    if update_alert_at:
        payload["last_alert_at"] = now_utc.isoformat()
    try:
        await db.job_locks.update_one(
            {"_id": _SEO_STALENESS_ALERT_LOCK_ID},
            {"$set": payload},
            upsert=True,
        )
    except Exception as exc:
        logger.debug(f"[seo-staleness] persist failed: {exc}")


async def _claim_seo_staleness_alert_slot(
    db, kind: str, prior_state: Optional[str], now_utc: datetime,
    last_run_at: Optional[str],
) -> bool:
    """Atomic single-winner CAS so a multi-replica deployment cannot
    page admins twice for the same transition. Each replica races to
    flip the lock doc with a guard expressing "I observed the same
    prior state you'd need to observe to legitimately fire this alert".
    The first writer wins; the loser's ``find_one_and_update`` returns
    ``None`` and we skip the email/notification entirely.

    Guards:
      * ``stale``: prior must NOT already be stale (initial detection)
        OR the prior alert must be older than the debounce window
        (legitimate re-page).
      * ``recovered``: prior must currently be ``stale`` (otherwise
        nothing to recover from).
    """
    set_payload = {
        "last_state": "stale" if kind == "stale" else "healthy",
        "last_alert_at": now_utc.isoformat(),
        "last_run_at_observed": last_run_at,
        "updated_at": now_utc.isoformat(),
    }
    if kind == "stale":
        cutoff_iso = (now_utc - timedelta(hours=_SEO_STALENESS_REALERT_INTERVAL_H)).isoformat()
        guard = {
            "_id": _SEO_STALENESS_ALERT_LOCK_ID,
            "$or": [
                {"last_state": {"$ne": "stale"}},
                {"last_alert_at": {"$lt": cutoff_iso}},
                {"last_alert_at": {"$exists": False}},
            ],
        }
    else:  # recovered
        guard = {
            "_id": _SEO_STALENESS_ALERT_LOCK_ID,
            "last_state": "stale",
        }
    try:
        res = await db.job_locks.find_one_and_update(
            guard, {"$set": set_payload}, upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[seo-staleness] CAS failed: {exc}")
        return False
    # No prior doc → bootstrap a stale alert via insert. Recovery has no
    # bootstrap path: there must be a prior `stale` row to recover from.
    if kind != "stale":
        return False
    try:
        from pymongo.errors import DuplicateKeyError
        await db.job_locks.insert_one({
            "_id": _SEO_STALENESS_ALERT_LOCK_ID,
            **set_payload,
        })
        return True
    except DuplicateKeyError:
        # A peer just inserted the doc; let them own this alert.
        return False
    except Exception as exc:
        logger.debug(f"[seo-staleness] bootstrap insert failed: {exc}")
        return False


async def _send_seo_staleness_alert(
    db, state: dict, kind: str, now_utc: datetime,
) -> None:
    """Email admins + record an admin notification. ``kind`` is
    ``"stale"`` or ``"recovered"``. Best-effort: never raises."""
    frequency = state["frequency"]
    threshold_h = state["threshold_h"]
    age_h = state["age_hours"]
    last_run_at = state["last_run_at"]
    age_str = f"{age_h:.1f}h" if age_h is not None else "never"

    if kind == "recovered":
        title = "SEO auto-publish recovered"
        msg = (
            f"The SEO auto-publish scheduler ({frequency}) ran again at "
            f"{last_run_at or 'unknown'}. Previously this job had been "
            f"stale beyond the {threshold_h}h threshold; it is now "
            f"healthy again — no further action required."
        )
        notif_type = "info"
    else:
        title = f"SEO auto-publish is stale ({frequency})"
        msg = (
            f"The SEO auto-publish scheduler has not completed a run in "
            f"{age_str} (threshold {threshold_h}h for the configured "
            f"`{frequency}` cadence). Last successful run: "
            f"{last_run_at or 'never recorded'}.\n\n"
            f"Likely causes: the worker process is down, "
            f"`SEO_AUTO_PUBLISH_ENABLED` was disabled, the cron loop "
            f"crashed, or the LLM provider is failing. Check the "
            f"Schedule panel and `seo_generation_log` for clues."
        )
        notif_type = "error"

    # Admin notification (in-app inbox).
    try:
        from db_ops import supa_insert_notification
        await supa_insert_notification({
            "id": str(uuid.uuid4()),
            "title": title,
            "message": msg,
            "type": notif_type,
            "channel": "in_app",
            "audience": "admins",
            "status": "sent",
            "created_at": now_utc.isoformat(),
            "sent_at": now_utc.isoformat(),
            "meta": {
                "kind": "seo_auto_publish_staleness",
                "state": kind,
                "frequency": frequency,
                "threshold_h": threshold_h,
                "age_hours": age_h,
                "last_run_at": last_run_at,
            },
        })
    except Exception as exc:
        logger.debug(f"[seo-staleness] notification persist failed: {exc}")

    # Best-effort email blast — fire-and-forget so a slow Resend can't
    # back up the staleness loop.
    asyncio.create_task(_email_admins_about_seo_staleness(db, title, msg))

    # Audit trail in the activity log so the on-call has a paper trail
    # even if the email/notification path fails.
    asyncio.create_task(_seo_log(
        "seo_auto_publish_staleness_alert",
        f"{kind}: frequency={frequency} age_hours={age_h} "
        f"threshold_h={threshold_h} last_run_at={last_run_at}",
        "warning" if kind == "stale" else "info",
    ))


async def _email_admins_about_seo_staleness(db, title: str, message: str) -> None:
    """Email the same admin audience that gets the SEO daily summary —
    reuses ``_resolve_seo_summary_audience`` so an admin who has opted
    out of the daily digest also gets opted out of the staleness alert
    (and we honor their quiet hours), keeping the noise predictable.

    The broad-admin fallback only fires when the prefs subsystem
    actually *failed* (raised) — an empty recipient list from a
    successful resolve means admins intentionally suppressed
    themselves (opt-out / quiet hours) and we must respect that."""
    audience_resolved = False
    recipients: list = []
    try:
        audience = await _resolve_seo_summary_audience(db, datetime.now(timezone.utc))
        recipients = [r.get("email") for r in audience.get("recipients", []) if r.get("email")]
        audience_resolved = True
    except Exception as exc:
        logger.debug(f"[seo-staleness] audience resolve failed: {exc}")
        audience_resolved = False
    if not recipients and not audience_resolved:
        # Prefs subsystem itself failed — degrade to a broad admin
        # lookup so a staleness alert still reaches *someone*. We
        # intentionally do NOT do this when the resolve succeeded with
        # an empty list (that means everyone is opted out / in quiet
        # hours and that suppression must be honored).
        try:
            cursor = db.users.find({"is_admin": True}, {"_id": 0, "email": 1})
            async for u in cursor:
                e = (u.get("email") or "").strip()
                if e:
                    recipients.append(e)
        except Exception as exc:
            logger.debug(f"[seo-staleness] admin lookup failed: {exc}")
    if not recipients:
        return
    try:
        from email_templates import _send  # internal helper, intentional
    except Exception as exc:
        logger.debug(f"[seo-staleness] email helper unavailable: {exc}")
        return
    html = (
        f"<h2 style='color:#dc2626;margin:0 0 8px;'>{title}</h2>"
        f"<p style='font-size:14px;line-height:1.6;color:#374151;white-space:pre-line;'>{message}</p>"
        f"<p style='font-size:12px;color:#6b7280;'>This is an automated alert from "
        f"the Syrabit SEO auto-publish staleness monitor (Task #471).</p>"
    )
    for email in recipients:
        try:
            await _send(email, title, html)
        except Exception as exc:
            logger.debug(f"[seo-staleness] email send failed for {email}: {exc}")


async def _check_and_alert_seo_auto_publish_staleness(
    db, now_utc: Optional[datetime] = None,
) -> dict:
    """One iteration of the staleness monitor. Returns a small report
    dict for tests / observability.

    Dedup rules:
      * On `stale`: only re-alert if ``> _SEO_STALENESS_REALERT_INTERVAL_H``
        has elapsed since the last alert (so admins aren't paged hourly
        during a multi-day outage).
      * On `healthy` after a prior `stale`: send exactly one recovery
        notification, then settle into "skip while healthy".
      * On `disabled`: never alert; we only persist state when something
        meaningful changes.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    # Task #491 — touch the lock doc's ``updated_at`` on EVERY iteration
    # (including the disabled branch) so the heartbeat watcher has a true
    # liveness signal. Without this, the existing persistence path only
    # writes on state transitions, which means a steady-state-healthy
    # monitor's ``updated_at`` would freeze and falsely look dead.
    await _bump_staleness_monitor_heartbeat(db, now_utc)
    state = await _evaluate_seo_auto_publish_staleness(db, now_utc)
    if not state["enabled"]:
        return {"action": "skip", "reason": "disabled", "state": state}

    prior: dict = {}
    try:
        prior = await db.job_locks.find_one(
            {"_id": _SEO_STALENESS_ALERT_LOCK_ID}
        ) or {}
    except Exception as exc:
        logger.debug(f"[seo-staleness] prior load failed: {exc}")
        prior = {}
    prior_state = prior.get("last_state")
    last_alert_dt = _parse_iso_utc(prior.get("last_alert_at"))

    if state["stale"]:
        # Fast-path debounce check (avoids a round-trip when we already
        # know we'll lose the CAS). The CAS itself is the authoritative
        # gate against duplicate alerts across replicas.
        if prior_state == "stale" and last_alert_dt is not None:
            elapsed_h = (now_utc - last_alert_dt).total_seconds() / 3600.0
            if elapsed_h < _SEO_STALENESS_REALERT_INTERVAL_H:
                return {"action": "skip", "reason": "debounced",
                        "elapsed_h": elapsed_h, "state": state}
        # Atomic single-winner gate — only the replica that flips the
        # lock doc actually emails admins, so a multi-worker deployment
        # can't burst-page the same transition.
        if not await _claim_seo_staleness_alert_slot(
            db, "stale", prior_state, now_utc, state["last_run_at"],
        ):
            return {"action": "skip", "reason": "lost_race", "state": state}
        await _send_seo_staleness_alert(db, state, "stale", now_utc)
        return {"action": "alerted", "kind": "stale", "state": state}

    # Healthy path.
    if prior_state == "stale":
        if not await _claim_seo_staleness_alert_slot(
            db, "recovered", prior_state, now_utc, state["last_run_at"],
        ):
            return {"action": "skip", "reason": "lost_race", "state": state}
        await _send_seo_staleness_alert(db, state, "recovered", now_utc)
        return {"action": "alerted", "kind": "recovered", "state": state}

    # Healthy → healthy: persist on first observation only so we don't
    # rewrite the doc every hour for no reason.
    if prior_state != "healthy":
        await _persist_seo_staleness_state(
            db, "healthy", now_utc, state["last_run_at"], update_alert_at=False,
        )
    return {"action": "skip", "reason": "healthy", "state": state}


async def _seo_auto_publish_staleness_loop():
    """Background loop: hourly staleness check. Cross-replica dedup is
    not strictly needed (the alert itself is debounced by the lock doc),
    but we still warm up so we don't fight the auto-publish loop for
    DB attention at boot."""
    from deps import db, is_mongo_available  # type: ignore
    await asyncio.sleep(_SEO_STALENESS_WARMUP_S)
    while True:
        try:
            if await is_mongo_available():
                await _check_and_alert_seo_auto_publish_staleness(db)
        except Exception as exc:
            logger.debug(f"[seo-staleness] loop iteration error: {exc}")
        await asyncio.sleep(_SEO_STALENESS_LOOP_SLEEP_S)


# ─── Task #491 — heartbeat watcher for the staleness monitor ────────────────

async def _bump_staleness_monitor_heartbeat(db, now_utc: datetime) -> None:
    """Touch ``updated_at`` on the staleness monitor's lock doc every
    iteration so the heartbeat watcher (Task #491) has a true liveness
    signal. Best-effort: a transient Mongo blip must not break the
    parent monitor's iteration. Uses ``$set`` only on ``updated_at`` so
    we never clobber ``last_state`` / ``last_alert_at`` written by the
    real alert path; ``upsert=True`` so the very first iteration on a
    fresh install creates the doc and unblocks the heartbeat."""
    try:
        await db.job_locks.update_one(
            {"_id": _SEO_STALENESS_ALERT_LOCK_ID},
            {"$set": {"updated_at": now_utc.isoformat()}},
            upsert=True,
        )
    except Exception as exc:
        logger.debug(f"[seo-staleness] heartbeat bump failed: {exc}")


async def _claim_staleness_heartbeat_alert_slot(
    db, kind: str, now_utc: datetime,
) -> bool:
    """Cross-replica single-winner CAS for the heartbeat alert. Mirrors
    :func:`_claim_seo_staleness_alert_slot` so leader fail-over mid-loop
    can't burst-page admins. Guard semantics:

      * ``down``: prior must NOT already be ``down`` (initial detection)
        OR the prior alert must be older than the heartbeat debounce
        window (legitimate re-page).
      * ``recovered``: prior must currently be ``down`` (otherwise
        nothing to recover from).
    """
    set_payload = {
        "last_state": "down" if kind == "down" else "healthy",
        "last_alert_at": now_utc.isoformat(),
        "updated_at": now_utc.isoformat(),
    }
    if kind == "down":
        cutoff_iso = (
            now_utc - timedelta(hours=_SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H)
        ).isoformat()
        guard = {
            "_id": _SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID,
            "$or": [
                {"last_state": {"$ne": "down"}},
                {"last_alert_at": {"$lt": cutoff_iso}},
                {"last_alert_at": {"$exists": False}},
            ],
        }
    else:  # recovered
        guard = {
            "_id": _SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID,
            "last_state": "down",
        }
    try:
        res = await db.job_locks.find_one_and_update(
            guard, {"$set": set_payload}, upsert=False,
        )
        if res is not None:
            return True
    except Exception as exc:
        logger.debug(f"[seo-staleness-heartbeat] CAS failed: {exc}")
        return False
    # Bootstrap path: no prior heartbeat-alert doc exists. Recovery has
    # no bootstrap (you can't recover from a state we never recorded).
    if kind != "down":
        return False
    try:
        from pymongo.errors import DuplicateKeyError
        await db.job_locks.insert_one({
            "_id": _SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID,
            **set_payload,
        })
        return True
    except DuplicateKeyError:
        return False
    except Exception as exc:
        logger.debug(
            f"[seo-staleness-heartbeat] bootstrap insert failed: {exc}")
        return False


async def _send_staleness_heartbeat_alert(
    db, kind: str, now_utc: datetime, age_h: float,
    monitor_updated_at: Optional[str],
) -> None:
    """Email admins + drop an in-app notification. ``kind`` is
    ``"down"`` (heartbeat fell behind) or ``"recovered"`` (heartbeat
    caught up). Reuses the same admin audience + email helper as the
    parent staleness alert so on-call gets one consistent inbox."""
    if kind == "down":
        title = "SEO staleness monitor is not running"
        msg = (
            f"The SEO auto-publish staleness monitor has not heartbeated "
            f"in {age_h:.1f}h (threshold "
            f"{_SEO_STALENESS_HEARTBEAT_MAX_AGE_H:.1f}h, ~2x its "
            f"{_SEO_STALENESS_LOOP_SLEEP_S // 3600}h loop interval). Last "
            f"observed heartbeat: {monitor_updated_at or 'unknown'}.\n\n"
            f"Nothing is currently watching the auto-publish scheduler — "
            f"a stalled cron would NO LONGER page admins. Likely causes: "
            f"the leader replica is down without re-electing, the "
            f"staleness loop crashed, or Mongo is unreachable from the "
            f"leader. Check the server logs for `[seo-staleness]` "
            f"entries and confirm the leader replica is healthy."
        )
        notif_type = "error"
    else:
        title = "SEO staleness monitor recovered"
        msg = (
            f"The SEO auto-publish staleness monitor heartbeated again at "
            f"{monitor_updated_at or 'unknown'} (current age {age_h:.1f}h, "
            f"threshold {_SEO_STALENESS_HEARTBEAT_MAX_AGE_H:.1f}h). "
            f"Previously its heartbeat had fallen behind; it is now "
            f"alive again — no further action required."
        )
        notif_type = "info"

    try:
        from db_ops import supa_insert_notification
        await supa_insert_notification({
            "id": str(uuid.uuid4()),
            "title": title,
            "message": msg,
            "type": notif_type,
            "channel": "in_app",
            "audience": "admins",
            "status": "sent",
            "created_at": now_utc.isoformat(),
            "sent_at": now_utc.isoformat(),
            "meta": {
                "kind": "seo_staleness_monitor_heartbeat",
                "state": kind,
                "age_hours": age_h,
                "monitor_updated_at": monitor_updated_at,
                "max_age_h": _SEO_STALENESS_HEARTBEAT_MAX_AGE_H,
            },
        })
    except Exception as exc:
        logger.debug(
            f"[seo-staleness-heartbeat] notification persist failed: {exc}")

    # Best-effort email — fire-and-forget so a slow Resend can't back up
    # the heartbeat loop.
    asyncio.create_task(_email_admins_about_seo_staleness(db, title, msg))

    # Audit trail in the activity log.
    asyncio.create_task(_seo_log(
        "seo_staleness_monitor_heartbeat_alert",
        f"{kind}: age_hours={age_h:.2f} "
        f"max_age_h={_SEO_STALENESS_HEARTBEAT_MAX_AGE_H} "
        f"monitor_updated_at={monitor_updated_at}",
        "warning" if kind == "down" else "info",
    ))


async def _check_and_alert_staleness_heartbeat(
    db, now_utc: Optional[datetime] = None,
) -> dict:
    """One iteration of the heartbeat watcher. Returns a small report
    dict for tests / observability.

    Decision tree:
      * No monitor doc → silent (we can't distinguish "monitor down"
        from "monitor never ran"; the parent monitor's first iteration
        will create the doc).
      * Heartbeat ≤ ``_SEO_STALENESS_HEARTBEAT_MAX_AGE_H`` → silent
        (and recovery if we previously paged).
      * Heartbeat behind → page once via CAS; debounce subsequent pages
        for ``_SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H``.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        monitor_doc = await db.job_locks.find_one(
            {"_id": _SEO_STALENESS_ALERT_LOCK_ID}
        )
    except Exception as exc:
        logger.debug(
            f"[seo-staleness-heartbeat] monitor doc load failed: {exc}")
        return {"action": "skip", "reason": "monitor_doc_load_failed"}

    monitor_updated_at = (monitor_doc or {}).get("updated_at")
    monitor_updated_dt = _parse_iso_utc(monitor_updated_at)
    if monitor_updated_dt is None:
        # Fresh install / monitor never bumped — silent. We deliberately
        # do NOT alert here: the parent monitor may legitimately not
        # have completed its first iteration yet, and we'd rather miss
        # one bootstrap window than page admins on every cold start.
        return {"action": "skip", "reason": "monitor_never_ran"}

    age_h = (now_utc - monitor_updated_dt).total_seconds() / 3600.0

    try:
        prior = await db.job_locks.find_one(
            {"_id": _SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID}
        ) or {}
    except Exception as exc:
        logger.debug(
            f"[seo-staleness-heartbeat] prior load failed: {exc}")
        prior = {}
    prior_state = prior.get("last_state")
    last_alert_dt = _parse_iso_utc(prior.get("last_alert_at"))

    if age_h > _SEO_STALENESS_HEARTBEAT_MAX_AGE_H:
        # Fast-path debounce check (avoids a round-trip when we already
        # know we'll lose the CAS).
        if prior_state == "down" and last_alert_dt is not None:
            elapsed_h = (now_utc - last_alert_dt).total_seconds() / 3600.0
            if elapsed_h < _SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H:
                return {
                    "action": "skip", "reason": "debounced",
                    "elapsed_h": elapsed_h, "age_h": age_h,
                }
        if not await _claim_staleness_heartbeat_alert_slot(
            db, "down", now_utc,
        ):
            return {"action": "skip", "reason": "lost_race", "age_h": age_h}
        await _send_staleness_heartbeat_alert(
            db, "down", now_utc, age_h, monitor_updated_at,
        )
        return {"action": "alerted", "kind": "down", "age_h": age_h}

    # Healthy heartbeat path.
    if prior_state == "down":
        if not await _claim_staleness_heartbeat_alert_slot(
            db, "recovered", now_utc,
        ):
            return {"action": "skip", "reason": "lost_race", "age_h": age_h}
        await _send_staleness_heartbeat_alert(
            db, "recovered", now_utc, age_h, monitor_updated_at,
        )
        return {"action": "alerted", "kind": "recovered", "age_h": age_h}

    return {"action": "skip", "reason": "healthy", "age_h": age_h}


async def _seo_staleness_heartbeat_loop():
    """Background loop: every 6h, verify the staleness monitor itself
    is still heartbeating.

    Cross-replica dedup (Task #950): every replica may run this loop,
    but only the lease-holder runs ``_check_and_alert_staleness_heartbeat``.
    The per-doc CAS inside that helper is defense-in-depth for the
    fail-over edge case where the lease changes hands mid-iteration.
    """
    from deps import db, is_mongo_available  # type: ignore
    import background_lease as _bglease
    owner_id = _bglease.make_owner_id("seo-staleness-heartbeat")
    lock_id = "seo_staleness_heartbeat_lease"
    ttl_s = _SEO_STALENESS_HEARTBEAT_LOOP_SLEEP_S * 3
    follower_s = max(900, _SEO_STALENESS_HEARTBEAT_LOOP_SLEEP_S // 4)
    await asyncio.sleep(_SEO_STALENESS_HEARTBEAT_WARMUP_S)
    try:
        while True:
            try:
                if not await is_mongo_available():
                    await asyncio.sleep(follower_s)
                    continue
                if not await _bglease.try_acquire_lease(
                    db, lock_id, owner_id, ttl_s,
                ):
                    await asyncio.sleep(follower_s)
                    continue
                await _check_and_alert_staleness_heartbeat(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    f"[seo-staleness-heartbeat] loop iteration error: {exc}")
            await asyncio.sleep(_SEO_STALENESS_HEARTBEAT_LOOP_SLEEP_S)
    finally:
        try:
            await asyncio.shield(_bglease.release_lease(
                db, lock_id, owner_id,
            ))
        except Exception:
            pass


# ─── ADMIN: Scheduled auto-publish status ───────────────────────────────────

@router.get("/auto-publish/schedule")
async def auto_publish_schedule(_admin: dict = Depends(_require_admin)):
    """Surface scheduler config + recent scheduled runs so admins can verify
    the cron is firing without needing shell access."""
    cfg = {
        "enabled": _seo_auto_publish_enabled(),
        "frequency": _seo_auto_publish_frequency(),
        "target_hour_utc": _seo_auto_publish_target_hour_utc(),
        "weekday": _seo_auto_publish_weekday(),
        "page_types": _seo_auto_publish_page_types(),
        "window_minutes": _SEO_AUTO_PUBLISH_WINDOW_MINUTES,
        "loop_interval_seconds": _SEO_AUTO_PUBLISH_LOOP_SLEEP_S,
    }
    last_marker = None
    recent_runs: list = []
    now_utc = datetime.now(timezone.utc)
    staleness_monitor = _build_staleness_monitor_state(None, now_utc)
    heartbeat_monitor = _build_heartbeat_monitor_state(None, None, now_utc)
    if _db is not None:
        try:
            last_marker = await _db.job_locks.find_one(
                {"_id": _SEO_AUTO_PUBLISH_LOCK_ID}, {"_id": 0}
            )
        except Exception:
            last_marker = None
        try:
            cursor = _db.seo_generation_log.find(
                {"job_id": {"$regex": "^job-sched-"}},
                {"_id": 0, "job_id": 1, "completed_at": 1, "total_generated": 1,
                 "skipped": 1, "errors": 1, "new_topics": 1, "avg_seo_score": 1,
                 "avg_geo_score": 1, "page_types": 1},
            ).sort("completed_at", -1).limit(10)
            recent_runs = await cursor.to_list(10)
        except Exception:
            recent_runs = []
        try:
            lock_doc = await _db.job_locks.find_one(
                {"_id": _SEO_STALENESS_ALERT_LOCK_ID}, {"_id": 0}
            )
            staleness_monitor = _build_staleness_monitor_state(
                lock_doc, now_utc
            )
        except Exception:
            # Keep the absent-doc default surfaced above so the UI can
            # still render the "Monitor health" subsection even when
            # Mongo hiccups on this single read.
            lock_doc = None
        try:
            hb_doc = await _db.job_locks.find_one(
                {"_id": _SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID}, {"_id": 0}
            )
            heartbeat_monitor = _build_heartbeat_monitor_state(
                hb_doc, lock_doc, now_utc
            )
        except Exception:
            # Same rationale as the staleness block above — keep the
            # absent-doc default so the UI can still render the
            # "Heartbeat watcher" subsection.
            pass
    return {
        "config": cfg,
        "last_marker": last_marker,
        "recent_runs": recent_runs,
        "staleness_monitor": staleness_monitor,
        "heartbeat_monitor": heartbeat_monitor,
    }


def _build_staleness_monitor_state(
    lock_doc: Optional[dict], now_utc: datetime,
) -> dict:
    """Shape the ``db.job_locks[seo_auto_publish_staleness_alert]`` doc
    into a stable response block for the Schedule panel.

    An absent doc (fresh install, or the monitor has never observed a
    transition worth persisting) is rendered as ``last_state=None`` —
    NOT ``healthy`` — so the UI can distinguish "monitor has not run
    yet" from "monitor ran and saw a healthy scheduler". The CAS path
    in :func:`_claim_seo_staleness_alert_slot` treats an absent doc as
    healthy for alerting purposes; that's a separate concern from how
    we surface the doc to admins.

    ``debounce_remaining_h`` is the time left in the 24h re-alert
    window (clamped to ``0`` when expired or no prior alert exists)
    so an admin can see at a glance "we already paged 3h ago, next
    re-page in 21h"."""
    doc = lock_doc or {}
    last_alert_iso = doc.get("last_alert_at")
    last_alert_dt = _parse_iso_utc(last_alert_iso)
    if last_alert_dt is not None:
        elapsed_h = (now_utc - last_alert_dt).total_seconds() / 3600.0
        remaining = max(0.0, _SEO_STALENESS_REALERT_INTERVAL_H - elapsed_h)
    else:
        remaining = 0.0
    return {
        "last_state": doc.get("last_state"),
        "last_alert_at": last_alert_iso,
        "last_run_at_observed": doc.get("last_run_at_observed"),
        "updated_at": doc.get("updated_at"),
        "realert_interval_h": _SEO_STALENESS_REALERT_INTERVAL_H,
        "debounce_remaining_h": remaining,
    }


def _build_heartbeat_monitor_state(
    hb_lock_doc: Optional[dict],
    monitor_lock_doc: Optional[dict],
    now_utc: datetime,
) -> dict:
    """Shape the heartbeat-watcher lock doc
    (``db.job_locks[seo_staleness_monitor_heartbeat_alert]``) plus the
    parent monitor's last heartbeat into a stable response block for
    the Schedule panel.

    Mirrors :func:`_build_staleness_monitor_state` so the UI can render
    a sibling "Heartbeat watcher" subsection. ``age_h`` is the age (in
    hours) of the parent monitor's last ``updated_at`` heartbeat —
    i.e. exactly the value the watcher in
    :func:`_check_and_alert_staleness_heartbeat` compares against
    ``_SEO_STALENESS_HEARTBEAT_MAX_AGE_H``. ``None`` means the monitor
    has never heartbeated yet (fresh install / cold start), which the
    UI surfaces distinctly from "monitor heartbeated 0h ago".

    ``debounce_remaining_h`` is the time left in the heartbeat
    re-alert window (clamped to ``0``); same semantics as the parent
    block."""
    hb_doc = hb_lock_doc or {}
    monitor_doc = monitor_lock_doc or {}
    monitor_updated_iso = monitor_doc.get("updated_at")
    monitor_updated_dt = _parse_iso_utc(monitor_updated_iso)
    if monitor_updated_dt is not None:
        age_h: Optional[float] = (
            now_utc - monitor_updated_dt
        ).total_seconds() / 3600.0
    else:
        age_h = None
    last_alert_iso = hb_doc.get("last_alert_at")
    last_alert_dt = _parse_iso_utc(last_alert_iso)
    if last_alert_dt is not None:
        elapsed_h = (now_utc - last_alert_dt).total_seconds() / 3600.0
        remaining = max(
            0.0, _SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H - elapsed_h
        )
    else:
        remaining = 0.0
    return {
        "last_state": hb_doc.get("last_state"),
        "last_alert_at": last_alert_iso,
        "age_h": age_h,
        "debounce_remaining_h": remaining,
        "monitor_updated_at": monitor_updated_iso,
        "max_age_h": _SEO_STALENESS_HEARTBEAT_MAX_AGE_H,
        "realert_interval_h": _SEO_STALENESS_HEARTBEAT_REALERT_INTERVAL_H,
    }


# ─── ADMIN: Gap-fill insights ────────────────────────────────────────────────

@router.get("/insights")
async def seo_insights(_admin: dict = Depends(_require_admin)):
    """AI gap analysis — returns actionable insight cards per subject/type."""
    # Aggregate: for each subject, count pages per page_type
    all_topics = await _db.topics.find({"status": "published"}, {"_id": 0, "id": 1, "chapter_id": 1, "title": 1, "subject_id": 1}).to_list(10000)
    topic_ids = [t["id"] for t in all_topics]

    # Count pages per topic_id × page_type
    page_docs = await _db.seo_pages.find(
        {"topic_id": {"$in": topic_ids}},
        {"_id": 0, "topic_id": 1, "page_type": 1, "subject_name": 1, "class_name": 1, "board_name": 1, "status": 1}
    ).to_list(100000)

    # Index existing pages
    page_index: dict = {}  # topic_id -> set of page_types
    subject_counts: dict = {}  # subject_name -> {page_type -> count, total_topics}
    for p in page_docs:
        tid = p["topic_id"]
        pt = p["page_type"]
        if tid not in page_index:
            page_index[tid] = set()
        page_index[tid].add(pt)

        sname = p.get("subject_name", "Unknown")
        if sname not in subject_counts:
            subject_counts[sname] = {
                "subject": sname,
                "board": p.get("board_name", ""),
                "class": p.get("class_name", ""),
                **{pt: 0 for pt in PAGE_TYPES},
                "published": 0,
                "draft": 0,
            }
        subject_counts[sname][pt] = subject_counts[sname].get(pt, 0) + 1
        if p.get("status") == "published":
            subject_counts[sname]["published"] += 1
        else:
            subject_counts[sname]["draft"] += 1

    # Topics with no pages at all
    no_pages = [t for t in all_topics if t["id"] not in page_index]
    # Topics missing specific page types
    gaps: dict = {}  # page_type -> count missing
    for t in all_topics:
        covered = page_index.get(t["id"], set())
        for pt in PAGE_TYPES:
            if pt not in covered:
                gaps[pt] = gaps.get(pt, 0) + 1

    # Build insight cards
    insights = []

    if no_pages:
        insights.append({
            "type": "critical",
            "icon": "alert",
            "title": f"{len(no_pages)} topics have no SEO pages at all",
            "description": f"Run Auto-Extract + Generate to create {len(no_pages) * len(PAGE_TYPES)} pages instantly.",
            "action": "auto-run",
            "count": len(no_pages),
        })

    for pt, missing_count in sorted(gaps.items(), key=lambda x: -x[1]):
        if missing_count == 0:
            continue
        labels = {"notes": "Notes", "definition": "Definitions", "important-questions": "Important Questions", "mcqs": "MCQs", "examples": "Solved Examples"}
        insights.append({
            "type": "gap",
            "icon": "generate",
            "title": f"{missing_count} topics missing {labels.get(pt, pt)} pages",
            "description": f"Generate {labels.get(pt, pt)} for all {missing_count} uncovered topics in one click.",
            "action": "generate",
            "page_type": pt,
            "count": missing_count,
        })

    # Per-subject breakdown (top 8 by draft/missing)
    subject_list = sorted(
        subject_counts.values(),
        key=lambda s: -(s.get("draft", 0))
    )[:8]

    return {
        "insights": insights,
        "subject_breakdown": subject_list,
        "summary": {
            "total_topics": len(all_topics),
            "topics_with_no_pages": len(no_pages),
            "page_type_gaps": gaps,
        },
    }


# ─── ADMIN: Board-level gap expand ──────────────────────────────────────────

@router.post("/expand/{board_slug}")
async def expand_board_content(
    board_slug: str,
    background_tasks: BackgroundTasks,
    data: PageTypesRequest = PageTypesRequest(),
    _admin: dict = Depends(_require_admin),
):
    """Generate all missing pages for a specific board (gap-fill only, skips existing)."""
    board = await _db.boards.find_one({"slug": board_slug}, {"_id": 0})
    if not board:
        raise HTTPException(status_code=404, detail=f"Board '{board_slug}' not found")

    types_to_run = (data.page_types if data else None) or PAGE_TYPES
    job_id = f"expand-{board_slug}-{uuid.uuid4().hex[:8]}"

    _seo_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "board": board_slug,
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": f"Starting gap-fill for {board.get('name', board_slug)}…",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    background_tasks.add_task(_expand_board_bg, job_id, board, types_to_run)
    return {"job_id": job_id, "message": f"Gap-fill started for {board.get('name', board_slug)}", "status": "queued"}


async def _expand_board_bg(job_id: str, board: dict, page_types: list):
    try:
        board_id = board["id"]
        classes = await _db.classes.find({"board_id": board_id}, {"_id": 0}).to_list(50)
        class_ids = [c["id"] for c in classes]
        streams = await _db.streams.find({"class_id": {"$in": class_ids}}, {"_id": 0}).to_list(200)
        stream_ids = [s["id"] for s in streams]
        subjects = await _db.subjects.find({"stream_id": {"$in": stream_ids}}, {"_id": 0}).to_list(500)
        subject_ids = [s["id"] for s in subjects]
        chapters = await _db.chapters.find({"subject_id": {"$in": subject_ids}}, {"_id": 0}).to_list(5000)
        ch_ids = [c["id"] for c in chapters]
        topics = await _db.topics.find({"chapter_id": {"$in": ch_ids}, "status": "published"}, {"_id": 0}).to_list(10000)

        total_ops = len(topics) * len(page_types)
        _job_update(job_id, status="generating", total=total_ops, current=f"Processing {len(topics)} topics for {board.get('name', '')}…")

        done = 0
        errors = 0
        skipped = 0

        for topic in topics:
            _job_update(job_id, current=f"{topic.get('title', topic.get('id', ''))}")
            try:
                hierarchy = await _resolve_hierarchy(topic)
                if not hierarchy:
                    errors += len(page_types)
                    done += len(page_types)
                    _job_update(job_id, done=done, errors=errors)
                    continue
                for pt in page_types:
                    existing = await _db.seo_pages.find_one({"topic_id": topic["id"], "page_type": pt}, {"_id": 0, "id": 1})
                    if existing:
                        skipped += 1
                        done += 1
                        _job_update(job_id, done=done, skipped=skipped)
                        continue
                    try:
                        page = await _generate_single_page(topic, pt, hierarchy)
                        done += 1
                        if not page:
                            errors += 1
                    except Exception as e:
                        logger.error(f"Expand gen error {topic.get('id')}/{pt}: {e}")
                        errors += 1
                        done += 1
                    _job_update(job_id, done=done, errors=errors)
            except Exception as e:
                logger.error(f"Expand topic error {topic.get('id')}: {e}")
                errors += len(page_types)
                done += len(page_types)
                _job_update(job_id, done=done, errors=errors)

        try:
            from routes.bot_discovery import indexnow_batcher
            flushed = await indexnow_batcher.flush_force(source="seo_expand_board")
            logger.info(f"IndexNow flush after expand-board: {flushed} URLs")
        except Exception:
            pass

        now = datetime.now(timezone.utc).isoformat()
        _job_update(
            job_id, status="done",
            current=f"Done — {done - skipped - errors} new pages, {skipped} skipped, {errors} errors",
            finished_at=now,
        )
    except Exception as e:
        logger.error(f"Expand board error: {e}")
        _job_update(job_id, status="error", current=str(e)[:200], finished_at=datetime.now(timezone.utc).isoformat())


# ─── Content Quality Audit & Near-Duplicate Detection ───────────────────────

def _content_shingles(text: str, k: int = 5) -> set:
    """Generate word-level k-shingles from text for similarity comparison."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < k:
        return set(words)
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _content_fingerprint(text: str, k: int = 5, num_hashes: int = 64) -> list[int]:
    """MinHash-style fingerprint for fast near-duplicate detection across pages."""
    shingles = _content_shingles(text, k)
    if not shingles:
        return [0] * num_hashes
    sigs = [(1 << 63) - 1] * num_hashes
    for s in shingles:
        base = int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)
        for i in range(num_hashes):
            h = (base * (i * 2654435761 + 1)) & ((1 << 63) - 1)
            if h < sigs[i]:
                sigs[i] = h
    return sigs


def _fingerprint_similarity(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return round(matches / len(a), 4)


def _quality_histogram(scores: list[int]) -> dict:
    """Bucket scores into 10-point bins."""
    buckets = {f"{lo}-{lo+9}": 0 for lo in range(0, 100, 10)}
    buckets["100"] = 0
    for s in scores:
        s = max(0, min(100, int(s or 0)))
        if s == 100:
            buckets["100"] += 1
        else:
            lo = (s // 10) * 10
            buckets[f"{lo}-{lo+9}"] += 1
    return buckets


@router.post("/quality-audit")
async def run_quality_audit(
    unpublish_below: int = _QUALITY_PUBLISH_THRESHOLD,
    dry_run: bool = False,
    _admin: dict = Depends(_require_admin),
):
    """Rescore every published SEO page; unpublish pages scoring below threshold.
    Stores summary in seo_quality_audits collection. Returns histogram + counts."""
    cursor = _db.seo_pages.find(
        {"status": "published"},
        {"_id": 0, "id": 1, "content": 1, "page_type": 1,
         "board_name": 1, "subject_name": 1, "chapter_title": 1,
         "topic_title": 1, "subject_id": 1, "subject_slug": 1},
    )
    rescored = 0
    unpublished = 0
    scores: list[int] = []
    flagged_ids: list[str] = []
    by_subject: dict = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    async for p in cursor:
        ctx = {
            "board_name":   p.get("board_name", ""),
            "subject_name": p.get("subject_name", ""),
            "chapter_title": p.get("chapter_title", ""),
        }
        qs = _compute_quality_score(p.get("content", ""), p.get("page_type", "notes"), context=ctx)
        score = int(qs.get("score", 0))
        scores.append(score)
        rescored += 1

        sid = p.get("subject_slug") or p.get("subject_id") or "unknown"
        bucket = by_subject.setdefault(sid, {"subject": p.get("subject_name", sid), "total": 0, "below": 0, "min": 100, "max": 0})
        bucket["total"] += 1
        bucket["min"] = min(bucket["min"], score)
        bucket["max"] = max(bucket["max"], score)
        if score < unpublish_below:
            bucket["below"] += 1

        updates = {"quality_score": qs, "updated_at": now_iso}
        if score < unpublish_below:
            unpublished += 1
            flagged_ids.append(p["id"])
            if not dry_run:
                updates["status"] = "draft"
                updates["in_sitemap"] = False
                updates["unpublish_reason"] = f"quality_audit:{score}<{unpublish_below}"

        if not dry_run:
            await _db.seo_pages.update_one({"id": p["id"]}, {"$set": updates})

    histogram = _quality_histogram(scores)
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    summary = {
        "id": f"audit-{uuid.uuid4().hex[:10]}",
        "run_at": now_iso,
        "threshold": unpublish_below,
        "dry_run": dry_run,
        "rescored": rescored,
        "unpublished": unpublished,
        "avg_score": avg_score,
        "histogram": histogram,
        "flagged_ids_sample": flagged_ids[:50],
        "by_subject": list(by_subject.values()),
    }

    try:
        await _db.seo_quality_audits.insert_one(dict(summary))
        await _db.seo_quality_audits.create_index([("run_at", -1)])
    except Exception as exc:
        logger.warning(f"Failed to persist quality audit: {exc}")

    await _seo_log(
        "seo:quality_audit",
        f"Rescored {rescored} pages, unpublished {unpublished} below {unpublish_below} (dry_run={dry_run})",
    )

    summary.pop("_id", None)
    return summary


@router.get("/quality-summary")
async def get_quality_summary(_admin: dict = Depends(_require_admin)):
    """Return current quality distribution + last audit + duplicate count for the dashboard widget."""
    pipeline = [
        {"$match": {"status": "published"}},
        {"$project": {
            "_id": 0,
            "score": {"$ifNull": ["$quality_score.score", {"$ifNull": ["$quality.score", 0]}]},
        }},
    ]
    scores: list[int] = []
    async for d in _db.seo_pages.aggregate(pipeline):
        try:
            scores.append(int(d.get("score") or 0))
        except (TypeError, ValueError):
            scores.append(0)

    total_published = len(scores)
    flagged = sum(1 for s in scores if s < _QUALITY_PUBLISH_THRESHOLD)
    histogram = _quality_histogram(scores)
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    total_pages = await _db.seo_pages.count_documents({})
    drafts = total_pages - total_published

    last_audit = await _db.seo_quality_audits.find_one(
        {}, {"_id": 0}, sort=[("run_at", -1)]
    )
    duplicate_pairs = await _db.seo_duplicate_pairs.count_documents({"status": {"$ne": "resolved"}})
    last_dup_scan = await _db.seo_duplicate_scans.find_one(
        {}, {"_id": 0}, sort=[("run_at", -1)]
    )

    return {
        "threshold": _QUALITY_PUBLISH_THRESHOLD,
        "total_published": total_published,
        "total_drafts": drafts,
        "avg_score": avg_score,
        "below_threshold": flagged,
        "histogram": histogram,
        "last_audit": last_audit,
        "duplicate_pairs_open": duplicate_pairs,
        "last_duplicate_scan": last_dup_scan,
    }


@router.post("/duplicate-scan")
async def run_duplicate_scan(
    similarity_threshold: float = 0.80,
    scope: str = "subject",  # "subject" | "global"
    _admin: dict = Depends(_require_admin),
):
    """Compute MinHash fingerprints for every published page and flag pairs above
    the similarity threshold. Pairs are bucketed by subject by default to keep
    O(n²) tractable. Stores results in seo_duplicate_pairs."""
    if similarity_threshold < 0.5 or similarity_threshold > 1.0:
        raise HTTPException(status_code=400, detail="similarity_threshold must be in [0.5, 1.0]")

    pages = await _db.seo_pages.find(
        {"status": "published"},
        {"_id": 0, "id": 1, "content": 1, "subject_id": 1, "subject_slug": 1,
         "subject_name": 1, "topic_title": 1, "page_type": 1,
         "board_slug": 1, "class_slug": 1, "topic_slug": 1,
         "content_fingerprint": 1, "content_hash": 1},
    ).to_list(length=None)

    now_iso = datetime.now(timezone.utc).isoformat()
    fingerprints_written = 0

    # Recompute fingerprints every scan so stale content can't hide near-duplicates.
    # We persist the new fingerprint along with a content hash to skip rewrites
    # when the page content hasn't changed since the last scan.
    for p in pages:
        content = p.get("content", "") or ""
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        fp = _content_fingerprint(content)
        prev_hash = (p.get("content_hash") if isinstance(p.get("content_hash"), str) else None)
        if prev_hash != content_hash or not isinstance(p.get("content_fingerprint"), list):
            await _db.seo_pages.update_one(
                {"id": p["id"]},
                {"$set": {
                    "content_fingerprint": fp,
                    "content_hash": content_hash,
                    "fingerprint_at": now_iso,
                }},
            )
            fingerprints_written += 1
        p["_fp"] = fp

    # Bucket pages for pairwise comparison
    buckets: dict = {}
    if scope == "global":
        buckets["__all__"] = pages
    else:
        for p in pages:
            key = (p.get("subject_slug") or p.get("subject_id") or "unknown")
            buckets.setdefault(key, []).append(p)

    pairs_found: list[dict] = []
    compared = 0
    for key, group in buckets.items():
        n = len(group)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = group[i], group[j]
                # Same topic+type pair is the same page; skip identical (board,class,subject,topic,page_type)
                if (a.get("topic_slug") == b.get("topic_slug")
                        and a.get("page_type") == b.get("page_type")
                        and a.get("board_slug") == b.get("board_slug")
                        and a.get("class_slug") == b.get("class_slug")):
                    continue
                sim = _fingerprint_similarity(a["_fp"], b["_fp"])
                compared += 1
                if sim >= similarity_threshold:
                    pairs_found.append({
                        "id":            f"dup-{uuid.uuid4().hex[:10]}",
                        "page_a_id":     a["id"],
                        "page_b_id":     b["id"],
                        "page_a_title":  a.get("topic_title", ""),
                        "page_b_title":  b.get("topic_title", ""),
                        "page_a_type":   a.get("page_type", ""),
                        "page_b_type":   b.get("page_type", ""),
                        "subject_name":  a.get("subject_name", ""),
                        "subject_slug":  a.get("subject_slug", ""),
                        "similarity":    sim,
                        "scope":         scope,
                        "status":        "open",
                        "detected_at":   now_iso,
                    })

    # Persist pairs (replace any existing open pair with same id-pair signature)
    inserted = 0
    if pairs_found:
        try:
            await _db.seo_duplicate_pairs.create_index(
                [("page_a_id", 1), ("page_b_id", 1)], unique=False,
            )
        except Exception:
            pass
        for pair in pairs_found:
            sig_filter = {
                "$or": [
                    {"page_a_id": pair["page_a_id"], "page_b_id": pair["page_b_id"]},
                    {"page_a_id": pair["page_b_id"], "page_b_id": pair["page_a_id"]},
                ],
                "status": {"$ne": "resolved"},
            }
            existing = await _db.seo_duplicate_pairs.find_one(sig_filter, {"_id": 0, "id": 1})
            if existing:
                await _db.seo_duplicate_pairs.update_one(
                    {"id": existing["id"]},
                    {"$set": {"similarity": pair["similarity"], "detected_at": now_iso}},
                )
            else:
                await _db.seo_duplicate_pairs.insert_one(dict(pair))
                inserted += 1

    summary = {
        "id":                f"dscan-{uuid.uuid4().hex[:10]}",
        "run_at":            now_iso,
        "threshold":         similarity_threshold,
        "scope":             scope,
        "pages_scanned":     len(pages),
        "fingerprints_built": fingerprints_written,
        "pairs_compared":    compared,
        "pairs_found":       len(pairs_found),
        "pairs_inserted":    inserted,
    }
    try:
        await _db.seo_duplicate_scans.insert_one(dict(summary))
        await _db.seo_duplicate_scans.create_index([("run_at", -1)])
    except Exception as exc:
        logger.warning(f"Failed to persist duplicate scan: {exc}")

    await _seo_log(
        "seo:duplicate_scan",
        f"Scanned {len(pages)} pages, found {len(pairs_found)} pairs ≥ {similarity_threshold}",
    )

    summary.pop("_id", None)
    return summary


@router.get("/duplicate-pairs")
async def list_duplicate_pairs(
    status: str = "open",
    limit: int = 100,
    _admin: dict = Depends(_require_admin),
):
    """List near-duplicate page pairs detected by the most recent scan(s)."""
    query: dict = {}
    if status and status != "all":
        query["status"] = status
    pairs = await _db.seo_duplicate_pairs.find(query, {"_id": 0}).sort(
        [("similarity", -1), ("detected_at", -1)]
    ).limit(limit).to_list(limit)
    total = await _db.seo_duplicate_pairs.count_documents(query)
    return {"pairs": pairs, "total": total, "status": status}


@router.post("/duplicate-pairs/{pair_id}/resolve")
async def resolve_duplicate_pair(
    pair_id: str,
    action: str = "ignore",  # "ignore" | "unpublish_a" | "unpublish_b"
    _admin: dict = Depends(_require_admin),
):
    """Mark a duplicate pair as resolved. Optionally unpublish one of the pages."""
    pair = await _db.seo_duplicate_pairs.find_one({"id": pair_id}, {"_id": 0})
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    now_iso = datetime.now(timezone.utc).isoformat()
    target_id = None
    if action == "unpublish_a":
        target_id = pair["page_a_id"]
    elif action == "unpublish_b":
        target_id = pair["page_b_id"]
    elif action != "ignore":
        raise HTTPException(status_code=400, detail="action must be ignore | unpublish_a | unpublish_b")

    if target_id:
        await _db.seo_pages.update_one(
            {"id": target_id},
            {"$set": {"status": "draft", "in_sitemap": False,
                      "unpublish_reason": f"duplicate_pair:{pair_id}", "updated_at": now_iso}},
        )

    await _db.seo_duplicate_pairs.update_one(
        {"id": pair_id},
        {"$set": {"status": "resolved", "resolved_action": action,
                  "resolved_at": now_iso}},
    )
    await _seo_log("seo:duplicate_resolve", f"Pair {pair_id} resolved via {action}")
    return {"resolved": True, "pair_id": pair_id, "action": action}


# ─── Public: Related topics for a CMS document (same chapter) ────────────────

@router.get("/related-by-chapter/{chapter_id}")
async def related_topics_by_chapter(
    chapter_id: str,
    exclude_topic_id: Optional[str] = None,
    limit: int = 5,
):
    """Return up to `limit` published topics from the same chapter for internal
    linking on /learn pages and other content surfaces. Used by the React
    LearnPage to render a 'Related Topics' section."""
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    query: dict = {"chapter_id": chapter_id, "status": "published"}
    if exclude_topic_id:
        query["id"] = {"$ne": exclude_topic_id}
    topics = await _db.topics.find(
        query, {"_id": 0, "id": 1, "title": 1, "slug": 1,
                "chapter_id": 1, "subject_id": 1}
    ).sort("order", 1).limit(limit).to_list(limit)
    # Perf fix (was 1866ms in Railway logs): every topic in this response
    # belongs to the SAME chapter, so the board/class/subject hierarchy is
    # identical across all of them. Previously we called _resolve_hierarchy()
    # once per topic, doing 4 sequential Mongo reads × N topics × ~128ms
    # cross-region RTT = ~2 seconds. Resolve ONCE and reuse.
    shared_h: dict = {}
    if topics:
        shared_h = await _resolve_hierarchy(topics[0]) or {}
    board_slug = shared_h.get("board_slug", "")
    class_slug = shared_h.get("class_slug", "")
    subject_slug = shared_h.get("subject_slug", "")
    has_hier = bool(board_slug and class_slug and subject_slug)
    enriched = []
    for t in topics:
        seo_path = (
            f"/{board_slug}/{class_slug}/{subject_slug}/{t['slug']}"
            if has_hier else ""
        )
        enriched.append({
            "id":       t.get("id"),
            "title":    t.get("title", ""),
            "slug":     t.get("slug", ""),
            "seo_path": seo_path,
        })
    from starlette.responses import JSONResponse
    resp = JSONResponse({"related": enriched, "chapter_id": chapter_id})
    resp.headers["Cache-Control"] = "public, max-age=600, s-maxage=3600"
    return resp
