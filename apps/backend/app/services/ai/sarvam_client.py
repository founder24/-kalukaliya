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

# Compiled regex for detecting the start of section 4+ (synthesis / drafting
# sections that are pure meta-analysis and should not be shown to users).
# Sections 2-3 contain the core educational content (definition + types/examples);
# section 4 onwards is synthesis / drafting meta-analysis.
_re_cutoff = re.compile(r'\n[ \t]*[4-9][\.\s]|\n[ \t]*1\d[\.\s]')

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


def _has_assamese(text: str) -> bool:
    return any(ord(c) in _ASSAMESE_RANGE for c in text)


_re_section_header = re.compile(r'^\d+[\.\)]\s+\S')

def _clean_educational_sections(text: str) -> str:
    """Strip meta-analysis lines and section headers from model output.

    Removes:
    - Numbered section headers (e.g. "2. Analyze the Core Question")
    - Lines containing known debug/meta phrases
    - Bullet-prefixed meta-analysis lines
    - Bold question sub-bullets
    """
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip numbered section headers (e.g. "2. Analyze...", "3. Synthesize...")
        if _re_section_header.match(stripped):
            continue
        if any(phrase in stripped for phrase in _META_PHRASES):
            continue
        if _re_meta_line.match(stripped):
            continue
        # Also skip bold-question sub-bullets (e.g. "    *   **How to ...?**")
        if stripped.endswith("?**") and stripped.startswith("*"):
            continue
        cleaned.append(line)
    # Collapse runs of 3+ blank lines to at most 2
    result = re.sub(r'\n{3,}', '\n\n', "\n".join(cleaned))
    return result


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
        self.api_key = settings.SARVAM_API_KEY
        self.base_url = settings.SARVAM_BASE_URL
        self.model = settings.SARVAM_MODEL
        self._client = httpx.AsyncClient(
            # sarvam-30b / sarvam-105b are reasoning models — they can take
            # 30-90 s to produce a long translation; raise the read timeout.
            timeout=httpx.Timeout(120.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self):
        """Close the HTTP client (call on app shutdown)"""
        await self._client.aclose()

    async def generate(
        self, system_prompt: str, user_message: str, stream: bool = False
    ) -> str:
        """Generate response using Sarvam AI (sarvam-m model).

        Retry policy:
          - 429 Too Many Requests → wait 3 s then retry once
          - 500 / 502 / 503 from Sarvam → wait 2 s then retry once
        httpx.HTTPStatusError is re-raised after exhaustion so chat.py can
        return an appropriate response to the client.
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
                    # enable_thinking=False: sarvam-30b streams the answer in
                    # reasoning_content (fast TTFB ~150ms); content is always
                    # empty for this model regardless of the setting.
                    "enable_thinking": False,
                    "max_tokens": 1200,
                    "stream": stream,
                },
            )
            response.raise_for_status()
            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                msg = data["choices"][0]["message"]
                # Use content only — the final answer from the model.
                # reasoning_content is the internal thinking chain; do not
                # surface it to users.
                content = msg.get("content") or ""
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
        is_assamese = _has_assamese(system_prompt)

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
            # enable_thinking=False: answer streams in reasoning_content fast
            # (~150ms TTFB); content field is always empty for sarvam-30b.
            "enable_thinking": False,
            # Assamese mode: system prompt is in Assamese so the model reasons
            # entirely in Assamese — generous budget for the full reasoning chain.
            # English mode: sections 2-3 are sufficient, 1500 tokens is plenty.
            "max_tokens": 4000 if is_assamese else 1500,
            "stream": True,
        }

        # sarvam-30b always streams its answer in reasoning_content.
        # It always starts with a numbered analysis chain:
        #   1. Deconstruct the User's Request  ← skip entirely
        #   2. Analyze the Core Question       ← English: show this
        #   3. Synthesize / Draft              ← English: cut here; Assamese: all English
        #   4. Final Draft / Polish            ← Assamese answer often starts here
        #   [unnumbered]                       ← final formatted answer
        #
        # Strategy:
        #  • English mode: skip section 1, stream section 2, stop at section 3.
        #    Section 2 contains the full educational answer for English requests.
        #  • Assamese mode: buffer the ENTIRE response (the model reasons in English
        #    throughout sections 1-5) then call _extract_assamese_answer() at the
        #    end to pull out only the Assamese-script content.
        #
        # Both modes share the same preamble-detection state machine; they diverge
        # only once the preamble (section 1) has been detected and stripped.

        # ── State for <think> block stripping (applied to `content` field) ──
        in_think_block = False
        buffer = ""
        think_started = False

        # ── State for numbered-section preamble filter ────────────────────────
        preamble_filter_active = None  # None=undecided, True=filtering, False=passthrough
        preamble_done = False
        preamble_buf = ""   # accumulates tokens until preamble decision is made

        # ── State for post-preamble content handling ──────────────────────────
        # edu_buf holds the content AFTER section 1 has been stripped.
        # For English: we stream it incrementally (line-boundary aligned),
        #   stopping when section 3 starts.
        # For Assamese: we buffer the whole thing and process at stream end.
        edu_buf = ""
        edu_cutoff_done = False  # True once English cutoff point reached

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
                                # No preamble — passthrough immediately (still clean meta lines)
                                preamble_done = True
                                if not is_assamese:
                                    cleaned_pass = _clean_educational_sections(preamble_buf)
                                    if cleaned_pass.strip():
                                        yield cleaned_pass
                                else:
                                    edu_buf += preamble_buf
                                preamble_buf = ""
                            elif preamble_filter_active:
                                m = _re_section.search(preamble_buf)
                                if m:
                                    preamble_done = True
                                    remainder = preamble_buf[m.start():]
                                    preamble_buf = ""
                                    if is_assamese:
                                        # Assamese: buffer everything for end-of-stream extraction
                                        edu_buf += remainder
                                    else:
                                        # English: strip the section header line itself
                                        # (e.g. "2. Analyze the Core Question") before yielding.
                                        # m.start() points to the \n before "2. …", so remainder
                                        # starts with "\n2. Analyze…\n<content>". We skip to the
                                        # newline after the header line so only the content yields.
                                        nl_after_header = remainder.find('\n', 1)
                                        if nl_after_header >= 0:
                                            remainder = remainder[nl_after_header:]
                                        else:
                                            remainder = ""
                                        # Run through the cleaner in case the first content lines
                                        # still contain meta-analysis noise.
                                        remainder = _clean_educational_sections(remainder)
                                        if remainder.strip():
                                            yield remainder
                                elif len(preamble_buf) > 3000:
                                    # Safety: never hold back more than 3 KB
                                    preamble_done = True
                                    if is_assamese:
                                        edu_buf += preamble_buf
                                    else:
                                        cleaned_overflow = _clean_educational_sections(preamble_buf)
                                        if cleaned_overflow.strip():
                                            yield cleaned_overflow
                                    preamble_buf = ""

                        # ── Phase 2: post-preamble content handling ───────────
                        else:
                            chunk_text = preamble_buf
                            preamble_buf = ""
                            edu_buf += chunk_text

                            if not is_assamese:
                                # English: stream section 2, stop at section 3
                                if not edu_cutoff_done:
                                    m3 = _re_cutoff.search(edu_buf)
                                    if m3:
                                        edu_cutoff_done = True
                                        final_text = _clean_educational_sections(
                                            edu_buf[: m3.start()]
                                        )
                                        edu_buf = ""
                                        if final_text.strip():
                                            yield final_text
                                    elif len(edu_buf) > 400:
                                        # Yield complete lines, keep 200-char window
                                        search_in = edu_buf[:-200]
                                        last_nl = search_in.rfind('\n')
                                        if last_nl >= 0:
                                            safe = edu_buf[: last_nl + 1]
                                            edu_buf = edu_buf[last_nl + 1:]
                                            cleaned = _clean_educational_sections(safe)
                                            if cleaned.strip():
                                                yield cleaned
                            # Assamese: just keep accumulating in edu_buf (no yield)
                        continue

                    # ── content field path (fallback for other models) ────────
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
                    buffer = ""
                    yield content

            # ── End-of-stream flush ──────────────────────────────────────────
            if preamble_buf and not preamble_done:
                # Preamble never resolved — yield whatever we accumulated
                if not is_assamese:
                    yield preamble_buf
                else:
                    edu_buf += preamble_buf

            if edu_buf:
                if is_assamese:
                    # Extract the Assamese-script content from the full buffer
                    result = _extract_assamese_answer(edu_buf)
                    if result.strip():
                        yield result
                elif not edu_cutoff_done:
                    # English: no section 3 was found — yield remaining section 2
                    cleaned = _clean_educational_sections(edu_buf)
                    if cleaned.strip():
                        yield cleaned

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
