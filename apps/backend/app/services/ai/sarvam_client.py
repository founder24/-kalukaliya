import httpx
import json
import asyncio
import logging
import re
from typing import AsyncGenerator

from app.config import settings
from app.core.circuit_breaker import (
    sarvam_circuit_breaker,
    CircuitBreakerError,
    CircuitState,
)

logger = logging.getLogger(__name__)

# Compiled regex for detecting the start of section 2 in sarvam-30b's
# "Deconstruct" preamble pattern ("\n2.  " or "\n2 " etc.)
_re_section = re.compile(r'\n[ \t]*2[\.\s]')

# Regex for any numbered section start — used by the end-of-stream extractor
# to locate the last reasoning section and skip to the answer after it.
_re_any_section = re.compile(r'\n\d+\.\s+\S')

# Phrases that appear on meta-analysis lines inside educational section 2.
# Lines containing any of these are stripped from the output.
_META_PHRASES = (
    "How to make it concise",
    "How to make this concise",
    "How to phrase",
    "How to structure",
    "How to present",
    "Let me think",
    "Let me combine",
    "Let me refine",
    "Let's go with",
    "Let's try",
    "Let me try",
    "I need to combine",
    "I can't list every",
    "I'll use the most",
    "I'll list the types",
    "Good, but",
    "This is good, but",
    "This is good.",
    "Good. Let",
    # Debug/routing metadata that must never reach users
    "Prompt policy",
    "QAroute=",
    "QA route",
    "Analyze the Core Question",
    "Deconstruct the User",
    "Synthesize the Answer",
    "Synthesize and Draft",
    "Draft the Answer",
    "Polish the Answer",
    # Post-answer meta sentences the model appends in the final block
    "The final response is ready",
    "This looks like the best possible response",
    "It's structured, factual, and follows",
    "the user's specific and strict rules",
    "Final check:",
    "This is the best possible",
    "This response is complete",
    "This is my final answer",
)

# Regex patterns for meta-analysis lines (applied in addition to _META_PHRASES).
_re_meta_line = re.compile(
    r'^\s*\*+\s*'                         # bullet prefix
    r'('
    r"Let('s| me)\s"                       # "Let's" / "Let me"
    r"|I('ll| need to)\s"                  # "I'll" / "I need to"
    r"|Good,?\s*(but|let|so)"             # "Good, but" etc.
    r"|This is (good|okay|correct)"       # "This is good"
    r"|\*?Draft \d+"                       # "*Draft N:" labels
    r"|\*?Critique \d+"                    # "*Critique N:" labels
    r"|\*?Version \d+"                     # "*Version N:" labels
    r")",
    re.IGNORECASE,
)

# Assamese/Bengali Unicode block (shared script for Assamese)
_ASSAMESE_RANGE = range(0x0980, 0x0A00)

# Greeting / chatter pattern compiled once at module level (used inside
# stream_generate to short-circuit the reasoning phase for trivial inputs).
_GREETING_STREAM_RE = re.compile(
    r"^(hi+|he+y+|he+llo+|thanks?|thank\s+you|ok+a*y*|o+k+|bye+"
    r"|good\s+(?:morning|evening|night|day|afternoon)|how\s+are\s+you"
    r"|who\s+are\s+you|what\s+can\s+you\s+do|nice|great|sure|got\s+it"
    r"|understood|noted|alright|cool|perfect)[\s!?.,'\u0964]*$",
    re.IGNORECASE,
)


def _has_assamese(text: str) -> bool:
    return any(ord(c) in _ASSAMESE_RANGE for c in text)


def _clean_educational_sections(text: str) -> str:
    """Strip meta-analysis lines from model output.

    Removes:
    - Lines containing known debug/meta phrases (QAroute=, Prompt policy, etc.)
    - Bullet-prefixed meta-analysis lines (Let me…, I'll…, Good but…)
    - Bold question sub-bullets

    NOTE: Does NOT strip all numbered list items — "1. Photosynthesis…" is
    valid educational content.  The numbered reasoning section *headers*
    (e.g. "2. Analyze the Core Question") are caught by _META_PHRASES.
    """
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if any(phrase in stripped for phrase in _META_PHRASES):
            continue
        if _re_meta_line.match(stripped):
            continue
        # Skip bold-question sub-bullets (e.g. "    *   **How to ...?**")
        if stripped.endswith("?**") and stripped.startswith("*"):
            continue
        cleaned.append(line)
    # Collapse runs of 3+ blank lines to at most 2
    result = re.sub(r'\n{3,}', '\n\n', "\n".join(cleaned))
    return result


def _extract_assamese_translation(rc: str) -> str:
    """Extract the final Assamese translation from sarvam-30b reasoning_content.

    For translation tasks the model:
    1. Reasons word-by-word in English (noise — mixed EN+AS)
    2. Wraps the final assembled translation in double-quoted strings at the end

    Strategy (in order of confidence):
    A) Last double-quoted string that is >50% Assamese chars (final clean answer).
    B) Last paragraph (separated by blank lines) that is >60% Assamese chars.
    C) Fall back to _extract_assamese_answer() line-by-line extraction.
    """
    if not rc:
        return ""

    def _assamese_ratio(s: str) -> float:
        chars = [c for c in s if not c.isspace()]
        if not chars:
            return 0.0
        return sum(1 for c in chars if '\u0980' <= c <= '\u09ff') / len(chars)

    candidates: list[str] = []

    # Strategy C: line-by-line collection of every Assamese line (most complete).
    # We always run this because it captures multi-paragraph translations.
    line_based = _extract_assamese_answer(rc)
    if line_based.strip():
        candidates.append(line_based.strip())

    # Strategy A: last double-quoted block ≥ 30 chars that is >50% Assamese.
    # The model often wraps its final assembled output in quotes at the end of
    # the "Final Output" section — this can be cleaner than Strategy C for
    # short inputs where the reasoning embeds many draft fragments.
    quoted = re.findall(r'"([^"]{30,})"', rc, re.DOTALL)
    as_quoted = [q.strip() for q in quoted if _assamese_ratio(q) > 0.5]
    if as_quoted:
        candidates.append(as_quoted[-1])

    # Strategy B: last paragraph that is >60% Assamese
    paragraphs = [p.strip() for p in rc.split('\n\n') if p.strip()]
    as_paragraphs = [p for p in paragraphs if _assamese_ratio(p) > 0.6 and len(p) > 30]
    if as_paragraphs:
        candidates.append(max(as_paragraphs, key=len))

    if not candidates:
        return ""

    # Return the LONGEST candidate — more words == more complete translation.
    # This avoids Strategy A accidentally returning a short concluding sentence
    # when Strategy C already found the full multi-paragraph translation.
    return max(candidates, key=lambda s: len(s.split()))


def _extract_assamese_answer(text: str) -> str:
    """
    Extract the final Assamese-language answer from sarvam-30b's reasoning chain.

    For Assamese requests the model reasons entirely in English across all numbered
    sections and embeds Assamese translations as draft bullet points. We collect
    ONLY lines that contain Assamese script, clean them, and deduplicate — keeping
    the last occurrence of each unique sentence (latest draft wins).
    """
    raw_assamese: list[str] = []
    for line in text.split("\n"):
        if not _has_assamese(line):
            continue
        # Strip leading bullets and draft labels, keep the Assamese content
        clean = line.strip()
        clean = re.sub(r'^\*+\s*', '', clean)           # remove bullets
        clean = re.sub(                                  # remove *Draft N:* prefix
            r'^\*?(Draft \d+|Version \d+|Final version'
            r'|পৰিষ্কাৰ|Final Draft|Final Answer):\*?\s*',
            '', clean, flags=re.IGNORECASE,
        )
        clean = re.sub(r'^\*', '', clean).strip()       # remove stray italic *
        if clean:
            raw_assamese.append(clean)

    if not raw_assamese:
        return ""

    # Deduplicate: keep the LAST occurrence of each unique first-30-char key
    # (later drafts are more refined than earlier ones)
    seen: dict[str, int] = {}
    for i, line in enumerate(raw_assamese):
        key = line[:40]
        seen[key] = i  # overwrite → last occurrence wins

    ordered = [raw_assamese[i] for i in sorted(set(seen.values()))]
    return "\n".join(ordered)


def _extract_english_answer(text: str) -> str:
    """
    Extract the final English answer from sarvam-30b's full reasoning buffer.

    The model always outputs numbered meta-reasoning sections first:
        2. Brainstorm / Analyze the Core Question   ← meta, skip
        3. Structuring the Answer / Synthesize      ← meta, skip
        [blank line]
        [actual clean answer in plain prose]        ← keep THIS

    Strategy: find the last numbered-section marker, advance past the section
    body (first double-newline after it), and return the content that follows.
    Falls back to cleaning the entire text if the pattern is not found.
    """
    last_sec_start = -1
    for m in _re_any_section.finditer(text):
        last_sec_start = m.start()

    if last_sec_start == -1:
        # No numbered sections detected — clean and return as-is.
        return _clean_educational_sections(text)

    remainder = text[last_sec_start:]
    sep = remainder.find('\n\n')
    if sep == -1:
        # No blank-line separator — clean and return everything.
        return _clean_educational_sections(text)

    # ── Find the actual answer boundary ────────────────────────────────────
    # We need the FIRST double-newline in `remainder` that is followed by
    # non-indented, non-bullet content — this marks the start of the real
    # answer block.  A plain find('\n\n') finds the wrong spot when the last
    # numbered section has internal paragraph breaks between "Option" bullets.
    #
    # Lookahead: next char is NOT whitespace, *, ", or a digit (section number).
    _re_boundary = re.compile(r'\n\n(?=[^\s*"\d])')
    bm = _re_boundary.search(remainder)

    if bm:
        answer = remainder[bm.end():].strip()
    else:
        answer = ""

    # ── Fallback: quoted-string extractor ───────────────────────────────────
    # For greetings / edge-cases the model embeds its final draft as a quoted
    # string inside the last numbered section and never writes an unnumbered
    # block.  Extract the last quoted string ≥30 chars from the last section.
    if not answer:
        quotes = re.findall(r'"([^"]{30,400})"', remainder)
        if quotes:
            answer = quotes[-1].strip()
        else:
            # Hard fallback: return the entire text cleaned.
            return _clean_educational_sections(text)

    # ── Inline stop markers ─────────────────────────────────────────────────
    # The model sometimes appends "Attempt N:" or "Draft:" labels inline
    # (no newline) after the clean answer text.  Truncate at the first one.
    _INLINE_STOPS = (
        '*   *Draft:',
        '*   *Final check:',
        '*   *Attempt',
        '*Draft:',
        '*Attempt ',
        '    *   *Draft',
        '    *   *Final',
        '    *   *Attempt',
        # Inline meta sentences that follow the real answer without a newline
        'This systematic process ensures',
        'Therefore, the final response',
        'Therefore, my final response',
        'This ensures all constraints',
        'This response meets all',
        'The response is complete',
    )
    for stop in _INLINE_STOPS:
        idx = answer.find(stop)
        if idx > 0:
            answer = answer[:idx].rstrip()

    return _clean_educational_sections(answer)


def _strip_think_block(text: str | None) -> str:
    """Remove <think>...</think> reasoning blocks from Sarvam model output.

    sarvam-30b / sarvam-105b are reasoning models: they may return
    reasoning_content separately and leave content=null.  This function
    is null-safe — returns "" if text is None or empty.
    """
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


class SarvamAIClient:
    """Sarvam AI Client for Assamese/Indic content"""

    def __init__(self):
        # NOTE: do NOT cache SARVAM_API_KEY here.
        # The singleton is created at module import time — before FastAPI's
        # lifespan runs and Secret Manager loads the key into settings.
        # api_key is a property that reads from settings lazily at request
        # time so it always sees the Secret-Manager-loaded value.
        self.base_url = settings.SARVAM_BASE_URL
        self.model = settings.SARVAM_MODEL
        self._client = httpx.AsyncClient(
            # sarvam-30b / sarvam-105b are reasoning models — they can take
            # 30-90 s to produce a long translation; raise the read timeout.
            timeout=httpx.Timeout(120.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def api_key(self) -> str | None:
        """Read lazily from settings so Secret Manager has time to load it."""
        return settings.SARVAM_API_KEY

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str,
        stream: bool = False, is_assamese: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a non-streaming response using Sarvam AI.

        Args:
            system_prompt: System instruction.
            user_message:  User input / content to translate or process.
            stream:        Whether to stream (default False).
            is_assamese:   Hint that the expected output is Assamese.
                           Used only to widen max_tokens; both EN and AS
                           responses come back in the content field when
                           enable_thinking=False.

        Retry policy:
          - 429 Too Many Requests → wait 3 s then retry once
          - 500 / 502 / 503 from Sarvam → wait 2 s then retry once
        httpx.HTTPStatusError is re-raised after exhaustion.
        """
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        async def _do_generate():
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                    # enable_thinking=False: sarvam-30b puts the final clean
                    # answer in the content field (confirmed by live tests).
                    # reasoning_content holds the internal thinking chain and
                    # must NOT be used as the answer for non-streaming calls.
                    "enable_thinking": False,
                    # Assamese output needs more tokens (script is denser)
                    "max_tokens": max_tokens if max_tokens is not None else (2048 if is_assamese else 1200),
                    "stream": stream,
                },
            )
            response.raise_for_status()
            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                msg = data["choices"][0]["message"]
                # sarvam-30b behaviour is inconsistent: sometimes the clean
                # answer is in content, sometimes content is null and the
                # answer is embedded in reasoning_content.
                content = (msg.get("content") or "").strip()
                if not content:
                    rc = (msg.get("reasoning_content") or "").strip()
                    if rc:
                        content = (
                            _extract_assamese_translation(rc)
                            if is_assamese
                            else _extract_english_answer(rc)
                        )
                return content
            return ""

        _last_http_exc: httpx.HTTPStatusError | None = None

        for attempt in range(2):
            try:
                result = await sarvam_circuit_breaker.call(_do_generate)
                return _strip_think_block(result)
            except CircuitBreakerError as e:
                raise RuntimeError(f"Sarvam AI unavailable: {e}")
            except httpx.HTTPStatusError as e:
                _last_http_exc = e
                status = e.response.status_code
                if status in (429, 500, 502, 503) and attempt == 0:
                    wait = 3 if status == 429 else 2
                    body = ""
                    try:
                        body = e.response.text[:200]
                    except Exception:
                        pass
                    logger.warning(
                        f"Sarvam API HTTP {status} on attempt 1; retrying after {wait}s | body={body}"
                    )
                    await asyncio.sleep(wait)
                    continue
                body = ""
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                logger.error(
                    f"Sarvam API HTTP {status} "
                    f"({'retries exhausted' if attempt > 0 else 'non-retryable'}) "
                    f"| model={self.model} | body={body}"
                )
                raise
            except Exception as e:
                logger.error(f"Sarvam AI error: {str(e)}")
                raise RuntimeError(f"Sarvam AI service failed: {e}")

        raise _last_http_exc or RuntimeError("Sarvam AI generate: exhausted retries")

    async def stream_generate(
        self,
        system_prompt: str,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response using Sarvam AI OpenAI-compatible SSE endpoint.

        Parses chunked SSE lines in the format:
            data: {"choices": [{"delta": {"content": "..."}}]}
            data: [DONE]

        Yields text content deltas as they arrive.
        Strips <think>...</think> reasoning blocks from output.
        """
        if not self.api_key:
            raise RuntimeError("Sarvam AI not configured (SARVAM_API_KEY is empty)")

        if sarvam_circuit_breaker.state == CircuitState.OPEN:
            raise RuntimeError("Sarvam AI unavailable (circuit open)")

        # Detect response language from the system prompt so we can apply the
        # right post-processing strategy and set an appropriate token budget.
        # Safe default ensures is_assamese is always bound even if _has_assamese raises.
        is_assamese = False
        is_assamese = _has_assamese(system_prompt)

        # Greeting / chatter detection (inline — avoids circular import).
        # Short, non-Assamese messages that match the greeting pattern get a
        # reduced token budget and skip the reasoning phase entirely, cutting
        # TTFB from ~7-8 s down to ~150 ms for responses like "Hi there!".
        _msg_stripped = user_message.strip()
        _non_ws_len = len(re.sub(r"\s+", "", _msg_stripped))
        is_greeting = (
            _non_ws_len <= 30
            and not _has_assamese(_msg_stripped)
            and bool(_GREETING_STREAM_RE.match(_msg_stripped))
        ) or (
            # Ultra-short messages (≤5 non-ws chars, no Assamese) are always chatter
            _non_ws_len <= 5 and not _has_assamese(_msg_stripped)
        )
        if is_greeting:
            logger.info(
                "sarvam_greeting_mode",
                extra={"msg_len": _non_ws_len, "enable_thinking": False, "max_tokens": 300},
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            # Greeting / chatter: skip reasoning phase entirely — small budget,
            # fast direct answer (TTFB ~150 ms instead of 7-8 s).
            #
            # English mode: enable_thinking=True separates the model's reasoning
            # into reasoning_content (hidden) from the clean final answer in
            # content (streamed directly).  No extraction logic required.
            #
            # Assamese mode: keep enable_thinking=False — the model reasons in
            # English in reasoning_content and embeds Assamese draft lines there;
            # _extract_assamese_answer() collects those at end-of-stream.
            "enable_thinking": False if is_greeting else (not is_assamese),
            "max_tokens": 300 if is_greeting else (4000 if is_assamese else 2000),
            "stream": True,
        }

        # Response strategy:
        #  • English (enable_thinking=True):
        #      reasoning_content = internal thinking — buffered to edu_buf, not yielded.
        #      content           = clean final answer — streamed directly token-by-token.
        #      At end-of-stream: if no content arrived, fall back to _extract_english_answer(edu_buf).
        #
        #  • Assamese (enable_thinking=False):
        #      reasoning_content = full English reasoning + embedded Assamese draft lines.
        #      content           = empty.
        #      At end-of-stream: _extract_assamese_answer(edu_buf) collects Assamese lines.

        # ── State for <think> block stripping (applied to `content` field) ──
        in_think_block = False
        buffer = ""
        think_started = False

        # ── State for numbered-section preamble filter (reasoning_content) ───
        preamble_filter_active = None  # None=undecided, True=filtering, False=passthrough
        preamble_done = False
        preamble_buf = ""   # accumulates tokens until preamble decision is made

        # ── State for post-preamble content handling ──────────────────────────
        # edu_buf accumulates the ENTIRE reasoning_content stream (both modes).
        # At end-of-stream, the appropriate extractor runs on the full buffer:
        #   English  → _extract_english_answer()  (fallback only)
        #   Assamese → _extract_assamese_answer()
        edu_buf = ""
        content_was_yielded = False  # True once any content-field token is streamed

        # ── State for content-field preamble sniff ────────────────────────────
        # When enable_thinking=True the model SHOULD put reasoning into
        # reasoning_content and emit only the clean answer in content.
        # However some model responses (or billing-degraded fallback paths)
        # emit the full numbered chain-of-thought directly in content, leaking
        # internal reasoning text to the user.  We sniff the first 150 chars
        # and, if a numbered-section pattern is detected, redirect the entire
        # content stream to edu_buf for EOS extraction instead of yielding it.
        content_sniff_buf = ""      # accumulates until sniff decision
        content_sniff_done = False  # True once we've committed to a path
        content_redirected = False  # True → content field ➜ edu_buf (not yielded)

        try:
            async with self._client.stream(
                "POST", url, headers=headers, json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    reasoning_content = delta.get("reasoning_content") or ""
                    content = delta.get("content") or ""

                    if not reasoning_content and not content:
                        continue

                    # ── reasoning_content path (primary for sarvam-30b) ───────
                    if reasoning_content and not content:
                        preamble_buf += reasoning_content

                        # ── Phase 1: detect & skip the "1. Deconstruct" preamble
                        if not preamble_done:
                            if preamble_filter_active is None and len(preamble_buf) >= 60:
                                stripped = preamble_buf.lstrip()
                                preamble_filter_active = stripped.startswith("1.")

                            if preamble_filter_active is False:
                                # No numbered preamble detected — buffer everything;
                                # the extractor will find the answer at end-of-stream.
                                preamble_done = True
                                edu_buf += preamble_buf
                                preamble_buf = ""
                            elif preamble_filter_active:
                                m = _re_section.search(preamble_buf)
                                if m:
                                    preamble_done = True
                                    remainder = preamble_buf[m.start():]
                                    preamble_buf = ""
                                    # Both modes: buffer for end-of-stream extraction.
                                    edu_buf += remainder
                                elif len(preamble_buf) > 3000:
                                    # Safety: never hold back more than 3 KB.
                                    preamble_done = True
                                    edu_buf += preamble_buf
                                    preamble_buf = ""

                        # ── Phase 2: post-preamble content handling ───────────
                        else:
                            chunk_text = preamble_buf
                            preamble_buf = ""
                            # Both modes: accumulate into edu_buf; no incremental
                            # yield — the extractor runs at end-of-stream only.
                            edu_buf += chunk_text
                        continue

                    # ── content field path (with numbered-section preamble guard) ─
                    # Fast-path: already decided to redirect content → edu_buf.
                    if content_redirected:
                        edu_buf += content
                        buffer = ""
                        continue

                    # Sniff phase: buffer first 150 chars to detect whether this
                    # response erroneously emits its reasoning chain in `content`.
                    if not content_sniff_done:
                        content_sniff_buf += content
                        # 60 chars is enough to detect a numbered chain-of-thought
                        # preamble ("1. Deconstruct...") while minimising TTFB for
                        # normal English answers. Previously 150 chars, which added
                        # a visible pause after the reasoning phase finished.
                        if len(content_sniff_buf) < 60:
                            continue  # keep accumulating
                        # Enough data — make the decision.
                        content_sniff_done = True
                        _sniff_stripped = content_sniff_buf.lstrip()
                        if re.match(r'^[123]\.\s+\S', _sniff_stripped):
                            # Numbered chain-of-thought detected in content field.
                            # Redirect everything to edu_buf; EOS extractor will
                            # recover the clean answer and prevent leaking reasoning.
                            content_redirected = True
                            edu_buf += content_sniff_buf
                            content_sniff_buf = ""
                            buffer = ""
                            continue
                        # Not a preamble — flush the sniff buffer through normal path.
                        # Note: current `content` chunk is already inside content_sniff_buf,
                        # so we set buffer to the sniff buf and skip adding content again.
                        buffer = content_sniff_buf
                        content_sniff_buf = ""
                        # Fall through to think-block check / yield below.
                    else:
                        buffer += content

                    # Check if we are entering a think block
                    if not think_started and buffer.lstrip().startswith("<think>"):
                        in_think_block = True
                        think_started = True

                    if in_think_block:
                        # Check if think block has ended
                        if "</think>" in buffer:
                            # Strip out the think block and yield the rest
                            after_think = buffer.split("</think>", 1)[1]
                            buffer = ""
                            in_think_block = False
                            if after_think.strip():
                                yield after_think
                        # While in think block, don't yield anything
                        continue

                    # Not in a think block, yield content as it arrives
                    to_yield = buffer
                    buffer = ""
                    if to_yield:
                        content_was_yielded = True
                        yield to_yield

            # ── End-of-stream flush ──────────────────────────────────────────
            if preamble_buf and not preamble_done:
                # Preamble never resolved — move into edu_buf for extraction.
                edu_buf += preamble_buf
                preamble_buf = ""

            # Flush content sniff buffer if the response was shorter than 150 chars
            # (sniff decision never fired mid-stream).
            if content_sniff_buf and not content_sniff_done:
                content_sniff_done = True
                _sniff_stripped = content_sniff_buf.lstrip()
                if re.match(r'^[123]\.\s+\S', _sniff_stripped):
                    content_redirected = True
                    edu_buf += content_sniff_buf
                else:
                    # Short clean response — yield it directly.
                    if content_sniff_buf.strip():
                        content_was_yielded = True
                        yield content_sniff_buf
                content_sniff_buf = ""

            if edu_buf and not content_was_yielded:
                # English: content-field streaming took care of the answer.
                # Only run extraction if the content field was empty (fallback).
                if is_assamese:
                    result = _extract_assamese_answer(edu_buf)
                else:
                    result = _extract_english_answer(edu_buf)
                if result.strip():
                    yield result

            if buffer and not in_think_block:
                yield buffer

            sarvam_circuit_breaker._on_success()
        except Exception as e:
            sarvam_circuit_breaker._on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                body = ""
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                logger.error(
                    f"Sarvam stream HTTP error: {e.response.status_code} | model={self.model} | body={body}"
                )
                raise RuntimeError(
                    f"Sarvam stream failed: HTTP {e.response.status_code}"
                )
            logger.error(f"Sarvam stream error: {str(e)}")
            raise RuntimeError(f"Sarvam stream failed: {e}")

    async def stream_generate_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        max_retries: int = 1,
        retry_delay: float = 2.0,
    ) -> AsyncGenerator[str, None]:
        """
        Stream with retry logic for resilience.

        - On 5xx or timeout: retries up to max_retries times
        - On 4xx (400/401/402/403/404): raises immediately — permanent errors,
          retry is pointless and would trip the circuit breaker on every request.
        - If all retries exhausted, raises to let caller handle fallback
        - HF-078: If chunks were already sent to client, cannot retry
        """
        last_error: Exception | None = None
        chunks_yielded = False

        for attempt in range(max_retries + 1):
            try:
                async for chunk in self.stream_generate(system_prompt, user_message):
                    chunks_yielded = True
                    yield chunk
                return  # Success - exit after full stream
            except RuntimeError as e:
                last_error = e
                # HF-078: If chunks were already sent to client, cannot retry
                if chunks_yielded:
                    raise
                # 4xx errors are permanent client errors — never retry.
                # The error message is "Sarvam stream failed: HTTP 4XX".
                err_str = str(e)
                is_client_error = "HTTP 4" in err_str or "circuit open" in err_str
                if is_client_error:
                    logger.warning(
                        f"Sarvam stream permanent error (no retry): {e}"
                    )
                    break
                if attempt < max_retries:
                    logger.warning(
                        f"Sarvam stream attempt {attempt + 1} failed: {e}, "
                        f"retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    break

        # All retries exhausted
        raise last_error or RuntimeError("Sarvam stream failed after retries")


# Singleton instance
sarvam_client = SarvamAIClient()


async def generate_with_sarvam(
    system_prompt: str, user_message: str, stream: bool = False
) -> str:
    """Convenience function for Sarvam AI generation"""
    return await sarvam_client.generate(
        system_prompt=system_prompt, user_message=user_message, stream=stream
    )
