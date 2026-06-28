"""
Greeting RAG — pre-embedded instant response bank for casual greetings.

Eliminates LLM calls for casual greetings (hi, hello, নমস্কাৰ, etc.)
by matching the user query against a curated bank of greeting patterns
using:

  Tier 1 — Regex/exact fast-path (~0 ms): deterministic lookup for known
            patterns. No embedding API call needed.
  Tier 2 — Embedding path (~0 ms after warmup): cosine similarity against
            pre-computed bge-m3 vectors for semantic edge-cases.

Both tiers return a randomly-sampled response from the matching intent's
response pool, providing variety while maintaining instant latency.

Performance:
  Before: greeting → LLM call → 1–3 s TTFB
  After:  greeting → pre-stored response → < 5 ms TTFB
"""

import asyncio
import logging
import random
import re
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Cosine similarity threshold for embedding-based greeting match.
# Set deliberately high (0.78) so educational queries are never accidentally
# short-circuited by the greeting response bank.
_GREETING_THRESHOLD = 0.78


# ---------------------------------------------------------------------------
# Curated greeting bank
# Schema: {
#   "intent": str,
#   "queries": list[str],      — representative phrases (English + Assamese)
#   "response_en": list[str],  — response pool for English
#   "response_as": list[str],  — response pool for Assamese
# }
# Multiple variants per intent → random sampling prevents repetitive replies.
# ---------------------------------------------------------------------------

GREETING_BANK = [
    {
        "intent": "hello",
        "queries": [
            "hi", "hello", "hey", "heyy", "hii", "hello there",
            "hey there", "hi there", "greetings", "howdy",
            "নমস্কাৰ", "হেলো", "হেলৌ", "হাই", "নমস্কাৰে",
        ],
        "response_en": [
            "Hey! 👋 I'm Syrabit AI — your study assistant for AHSEC, SEBA, and Degree exams. What topic can I help you with today?",
            "Hello! I'm Syrabit AI, ready to help with your board exam prep. Which subject or chapter would you like to explore?",
            "Hi there! Ask me anything from your AHSEC, SEBA, or Degree syllabus — notes, definitions, important questions, you name it!",
            "Hey! Great to see you. I'm Syrabit AI — powered by your curriculum. What are you studying today?",
        ],
        "response_as": [
            "নমস্কাৰ! 👋 মই Syrabit AI — আপোনাৰ AHSEC, SEBA আৰু Degree পৰীক্ষাৰ অধ্যয়ন সহায়ক। আজি কোন বিষয়ত সহায় লাগিব?",
            "নমস্কাৰ! মই Syrabit AI। আপোনাৰ পাঠ্যক্ৰমৰ যিকোনো বিষয়ৰ বিষয়ে জিজ্ঞাসা কৰক।",
            "আদৰণি! মই Syrabit AI। AHSEC, SEBA বা Degree পৰীক্ষাৰ যিকোনো প্ৰশ্নৰ উত্তৰ দিবলৈ সাজু। 📚",
        ],
    },
    {
        "intent": "good_morning",
        "queries": [
            "good morning", "gm", "morning", "good morning!",
            "শুভ ৰাতিপুৱা", "ৰাতিপুৱা", "শুভ প্ৰভাত",
        ],
        "response_en": [
            "Good morning! ☀️ Hope you're ready for a productive study session. What topic shall we tackle first?",
            "Good morning! The best time to learn is now. What chapter or subject are you working on today?",
            "Morning! Let's make this session count. Ask me anything from your syllabus!",
        ],
        "response_as": [
            "শুভ ৰাতিপুৱা! ☀️ আশা কৰোঁ আজি পঢ়া-শুনা ভাল হ'ব। কোন বিষয়ৰ পৰা আৰম্ভ কৰিব বিচাৰে?",
            "শুভ প্ৰভাত! পঢ়াৰ বাবে সাজু? আপোনাৰ পাঠ্যক্ৰমৰ যিকোনো প্ৰশ্ন কৰক।",
        ],
    },
    {
        "intent": "good_evening",
        "queries": [
            "good evening", "evening", "good afternoon", "afternoon",
            "শুভ গধূলি", "গধূলি", "শুভ আবেলি", "আবেলি",
        ],
        "response_en": [
            "Good evening! 🌆 Perfect time for a study session. What are you revising today?",
            "Evening! Let's make the most of this study time. What topic can I help you with?",
            "Good afternoon! Ready to continue studying? Ask me anything from your syllabus.",
        ],
        "response_as": [
            "শুভ গধূলি! 🌆 অধ্যয়নৰ বাবে সুন্দৰ সময়। আজি কোন বিষয় পুনৰাবৃত্তি কৰিব বিচাৰে?",
            "গধূলি ভাল হওক! পঢ়াৰ বাবে সাজু? কি জানিব বিচাৰে?",
        ],
    },
    {
        "intent": "good_night",
        "queries": [
            "good night", "gn", "night", "good night!",
            "শুভ নিশা", "শুভৰাত্ৰি", "নিশা ভাল হওক",
        ],
        "response_en": [
            "Good night! 🌙 Rest well — a fresh mind learns better. See you tomorrow!",
            "Good night! Sweet dreams. Come back whenever you're ready to continue. 🌙",
        ],
        "response_as": [
            "শুভ নিশা! 🌙 ভালকৈ জিৰণি লওক — সতেজ মনে ভালকৈ শিকিব পাৰে। কাইলৈ পুনৰ আহিব!",
            "শুভৰাত্ৰি! বিশ্ৰাম লওক। পুনৰ পঢ়িবলৈ মন হ'লে আহিব।",
        ],
    },
    {
        "intent": "how_are_you",
        "queries": [
            "how are you", "how r u", "how are u", "how do you do",
            "how's it going", "whats up", "what's up", "sup",
            "কেনে আছা", "কেমন আছ", "কেনে আছে", "ভাল আছানে",
        ],
        "response_en": [
            "I'm doing great and fully charged to help! 😊 Ask me anything about your AHSEC, SEBA, or Degree syllabus.",
            "All systems go! I'm Syrabit AI, ready to help with your exam prep. What topic shall we dive into?",
            "Feeling fantastic and ready to help you study! What chapter or subject can I assist with?",
        ],
        "response_as": [
            "মই একেবাৰে ভালেই আছোঁ আৰু সাহায্য কৰিবলৈ সাজু! 😊 আপোনাৰ AHSEC, SEBA বা Degree পাঠ্যক্ৰমৰ যিকোনো প্ৰশ্ন কৰক।",
            "মই ভাল আছোঁ! আপুনি কেনে আছে? পঢ়া-শুনাৰ কিবা সহায় লাগিব নেকি?",
        ],
    },
    {
        "intent": "who_are_you",
        "queries": [
            "who are you", "what are you", "tell me about yourself",
            "what is syrabit", "who is syrabit", "what can you do",
            "what do you do", "your capabilities", "about you",
            "তুমি কোন", "আপুনি কোন", "Syrabit কি", "তুমি কি কৰিব পাৰা",
        ],
        "response_en": [
            "I'm Syrabit AI — your personal study assistant for Assam Board exams! 📚\n\nI can:\n• Explain any topic from your AHSEC, SEBA, or Degree curriculum\n• Answer in both English and Assamese\n• Provide source-cited answers with important questions and past papers\n\nJust ask me anything from your syllabus!",
            "I'm Syrabit AI, built specifically for Assam Board students. I'm trained on your actual curriculum — AHSEC, SEBA, and Degree — and understand both English and Assamese. Ask me any topic, chapter, or exam question!",
        ],
        "response_as": [
            "মই Syrabit AI — অসম বৰ্ড পৰীক্ষাৰ বাবে আপোনাৰ ব্যক্তিগত অধ্যয়ন সহায়ক! 📚\n\nমই পাৰোঁ:\n• AHSEC, SEBA বা Degree পাঠ্যক্ৰমৰ যিকোনো বিষয় ব্যাখ্যা কৰিব\n• ইংৰাজী আৰু অসমীয়া দুয়োটা ভাষাত উত্তৰ দিব\n• উৎস উল্লেখসহ গুৰুত্বপূৰ্ণ প্ৰশ্নৰ উত্তৰ দিব\n\nআপোনাৰ পাঠ্যক্ৰমৰ যিকোনো বিষয় সোধক!",
        ],
    },
    {
        "intent": "thanks",
        "queries": [
            "thanks", "thank you", "thank u", "ty", "thx", "thankyou",
            "thanks a lot", "many thanks", "thank you so much", "thnx",
            "ধন্যবাদ", "থেংকু", "আভাৰী", "বহুত ধন্যবাদ",
        ],
        "response_en": [
            "You're welcome! 😊 Feel free to ask anything else about your studies.",
            "Happy to help! Got more questions? I'm always here for your exam prep.",
            "Anytime! Keep the questions coming — that's how we master the syllabus together. 📚",
            "Glad I could help! What would you like to study next?",
        ],
        "response_as": [
            "আপোনাক স্বাগতম! 😊 অধ্যয়নৰ বিষয়ে আৰু কিবা জানিব বিচাৰে নেকি?",
            "সহায় কৰিব পাৰি বুলি ভাল লাগিল! আৰু কিবা প্ৰশ্ন আছে নেকি?",
            "যিকোনো সময়ত সুধিব! পাঠ্যক্ৰমৰ আৰু কোন বিষয় পঢ়িব বিচাৰে?",
        ],
    },
    {
        "intent": "bye",
        "queries": [
            "bye", "goodbye", "good bye", "see you", "see ya",
            "cya", "take care", "later", "ttyl",
            "বিদায়", "বাই", "যাওঁ", "পিছত লগ পাম",
        ],
        "response_en": [
            "Goodbye! 👋 All the best with your studies. Come back whenever you need help!",
            "See you later! Keep up the revision — you've got this! 💪",
            "Bye! Take care and good luck with your exams. I'll be here whenever you need me. 👋",
        ],
        "response_as": [
            "বিদায়! 👋 পঢ়া-শুনাত শুভেচ্ছা। সহায় লাগিলে পুনৰ আহিব!",
            "পিছত লগ পাম! পুনৰাবৃত্তি জাৰি ৰাখক — আপুনি পাৰিব! 💪",
        ],
    },
    {
        "intent": "ok_acknowledgement",
        "queries": [
            "ok", "okay", "sure", "got it", "understood", "noted",
            "alright", "cool", "nice", "great", "awesome", "perfect",
            "ঠিক আছে", "বেছি ভাল", "বুজিলোঁ", "ভাল", "ঠিকেই",
        ],
        "response_en": [
            "Got it! What would you like to explore next? Ask me any topic from your syllabus.",
            "Sure! What else can I help you study today?",
            "Great! Feel free to ask me anything — notes, definitions, important questions, past papers. 📚",
        ],
        "response_as": [
            "ঠিক আছে! এতিয়া কি পঢ়িব বিচাৰে? আপোনাৰ পাঠ্যক্ৰমৰ যিকোনো বিষয় সোধক।",
            "ভাল! আৰু কি জানিব বিচাৰে?",
            "বুজিলোঁ! পাঠ্যক্ৰমৰ যিকোনো প্ৰশ্ন কৰক। 📚",
        ],
    },
]


class GreetingRAG:
    """
    Pre-embedded greeting response bank for instant replies.

    Two-tier lookup:
      1. fast_match()           — regex/exact, ~0 ms, no network
      2. match_by_embedding()   — cosine similarity, ~0 ms after warmup
    """

    def __init__(self):
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._queries: list[str] = []
        self._intent_indices: list[int] = []
        self._vectors: Optional[np.ndarray] = None  # shape (N, 1024)

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._vectors is not None and len(self._vectors) > 0

    async def initialize(self) -> None:
        """
        Pre-embed all greeting queries via CF Workers AI bge-m3.
        Called once at app startup. Thread-safe via asyncio.Lock.
        Subsequent calls are no-ops.
        """
        async with self._init_lock:
            if self._initialized:
                return
            try:
                from app.services.ai.embedder import embed_batch_chunked

                all_queries: list[str] = []
                all_intent_indices: list[int] = []

                for intent_idx, entry in enumerate(GREETING_BANK):
                    for q in entry["queries"]:
                        all_queries.append(q)
                        all_intent_indices.append(intent_idx)

                logger.info(
                    f"GreetingRAG: embedding {len(all_queries)} queries "
                    f"across {len(GREETING_BANK)} intents via CF bge-m3…"
                )
                vectors = await embed_batch_chunked(all_queries, batch_size=50)
                self._queries = all_queries
                self._intent_indices = all_intent_indices
                self._vectors = np.array(vectors, dtype=np.float32)
                self._initialized = True
                logger.info(
                    f"GreetingRAG ready: {len(all_queries)} queries embedded, "
                    f"shape={self._vectors.shape}"
                )
            except Exception as exc:
                logger.warning(
                    f"GreetingRAG warmup failed — fast-path only will function: {exc}"
                )

    # ------------------------------------------------------------------
    # Tier 1: exact / prefix match (no network, ~0 ms)
    # ------------------------------------------------------------------

    def fast_match(self, message: str, lang: str) -> Optional[str]:
        """
        Exact or prefix match against every query in the greeting bank.

        Normalises the input (lowercase, strip trailing punctuation) then
        checks each bank query for an exact match or as a leading token.
        Returns a randomly-sampled response or None when unmatched.
        """
        normalized = message.strip().lower()
        normalized = re.sub(r"[\s!?.,\u0964\u0021\u09F7]+$", "", normalized)
        if not normalized:
            return None

        for entry in GREETING_BANK:
            for q in entry["queries"]:
                q_lower = q.lower()
                if normalized == q_lower or normalized.startswith(q_lower + " "):
                    return _pick_response(entry, lang)
        return None

    # ------------------------------------------------------------------
    # Tier 2: cosine similarity against pre-embedded bank (~0 ms post-warmup)
    # ------------------------------------------------------------------

    def match_by_embedding(
        self, query_embedding: list[float], lang: str
    ) -> Optional[str]:
        """
        Vectorised cosine-similarity lookup against the pre-embedded bank.

        Returns a randomly-sampled response for the best-matching intent if
        the top score is ≥ _GREETING_THRESHOLD (0.78), otherwise None.
        """
        if not self.is_ready:
            return None

        q = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return None
        q_normalized = q / q_norm

        bank_norms = np.linalg.norm(self._vectors, axis=1, keepdims=True)
        bank_norms = np.where(bank_norms == 0, 1.0, bank_norms)
        bank_normalized = self._vectors / bank_norms
        sims = bank_normalized @ q_normalized  # (N,)

        best_idx = int(np.argmax(sims))
        best_score = float(sims[best_idx])
        if best_score < _GREETING_THRESHOLD:
            return None

        intent_idx = self._intent_indices[best_idx]
        entry = GREETING_BANK[intent_idx]
        logger.debug(
            f"GreetingRAG embedding match: intent={entry['intent']} "
            f"score={best_score:.3f} query={self._queries[best_idx]!r}"
        )
        return _pick_response(entry, lang)

    # ------------------------------------------------------------------
    # Combined lookup (fast path first, embedding fallback)
    # ------------------------------------------------------------------

    def lookup(
        self,
        message: str,
        lang: str,
        query_embedding: Optional[list[float]] = None,
    ) -> Optional[str]:
        """
        Try fast-path first, then embedding match if an embedding is supplied.

        Args:
            message:         Raw (sanitized) user message.
            lang:            Resolved language code ('en' or 'as').
            query_embedding: Optional pre-computed bge-m3 vector (1024-dim).
                             When None, only the fast-path is attempted.

        Returns:
            A pre-stored response string, or None if no greeting matched.
        """
        response = self.fast_match(message, lang)
        if response:
            return response
        if query_embedding is not None:
            response = self.match_by_embedding(query_embedding, lang)
        return response


def _pick_response(entry: dict, lang: str) -> str:
    """Randomly sample from the response pool for the target language."""
    if lang == "as":
        pool = entry.get("response_as") or entry.get("response_en", [])
    else:
        pool = entry.get("response_en", [])
    if not pool:
        return "Hello! Ask me anything about your syllabus. 📚"
    return random.choice(pool)


# Module-level singleton — imported by chat.py and main.py
greeting_rag = GreetingRAG()
