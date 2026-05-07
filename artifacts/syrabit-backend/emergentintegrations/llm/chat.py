"""
Universal LLM adapter — supports Groq and OpenAI providers.
Automatically selects the right client based on provider setting.
Routes through Cloudflare AI Gateway when configured.
"""
import logging

from config import (
    mark_cf_gateway_down, get_provider_base_url,
    byok_headers, BYOK_PLACEHOLDER,
)

_log = logging.getLogger(__name__)

# Suppress verbose httpx / openai request logs — they can echo auth headers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


class _MaskedKey:
    """Wraps an API key so it is never printed or repr'd in plain text."""
    __slots__ = ("_key",)

    def __init__(self, key: str):
        self._key = key

    def __str__(self) -> str:
        k = self._key
        return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else "***"

    def __repr__(self) -> str:
        return f"<MaskedKey {self!s}>"

    def reveal(self) -> str:
        return self._key


class UserMessage:
    def __init__(self, text: str):
        self.text = text


class LlmChat:
    def __init__(self, api_key: str, session_id: str = "", system_message: str = ""):
        self._masked = _MaskedKey(api_key)
        self.session_id = session_id
        self.system_message = system_message
        self._provider = "groq"
        self._model = "llama-3.1-8b-instant"

    @property
    def api_key(self) -> str:
        return self._masked.reveal()

    def __repr__(self) -> str:
        return (f"LlmChat(provider={self._provider!r}, model={self._model!r}, "
                f"key={self._masked!s})")

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self._provider = provider or "groq"
        self._model = model or "llama-3.1-8b-instant"
        return self

    async def send_message(self, message: UserMessage) -> str:
        messages = []
        if self.system_message:
            messages.append({"role": "system", "content": self.system_message})
        messages.append({"role": "user", "content": message.text})

        if self._provider == "openai":
            return await self._call_openai(messages)
        elif self._provider == "fireworksai":
            return await self._call_fireworks(messages)
        else:
            return await self._call_groq(messages)

    async def send_messages(self, messages: list) -> str:
        if self._provider == "openai":
            return await self._call_openai(messages)
        elif self._provider == "fireworksai":
            return await self._call_fireworks(messages)
        else:
            return await self._call_groq(messages)

    async def stream_messages(self, messages: list, max_tokens: int = 2048):
        if self._provider == "openai":
            async for token in self._stream_openai(messages, max_tokens):
                yield token
        elif self._provider == "fireworksai":
            async for token in self._stream_fireworks(messages, max_tokens):
                yield token
        else:
            async for token in self._stream_groq(messages, max_tokens):
                yield token

    def _cf_cache_headers(self) -> dict | None:
        # Delegates to config.byok_headers() — includes:
        #   cf-aig-byok-key:true      (CF MAY substitute the stored key upstream)
        #   cf-aig-cache-ttl:<N>      (cache hint)
        #   cf-aig-authorization:…    (only if Authenticated Gateway is on)
        #
        # The decision of whether to clear the SDK's auto-attached
        # ``Authorization: Bearer <self.api_key>`` is per-instance, derived
        # from the api_key the caller is about to send (FIXED 2026-04-26
        # after architect review surfaced a BYOK regression):
        #   • self.api_key == BYOK_PLACEHOLDER ("x") → BYOK runtime, CF
        #     must substitute the stored key upstream → CLEAR Authorization
        #     so CF doesn't forward "Bearer x" (which 401s upstream).
        #   • self.api_key is a REAL provider key → keep Authorization so
        #     CF forwards it to the upstream provider. The original bug
        #     (default cleared) produced 400 "Missing or invalid
        #     Authorization header" from Google Gemini whenever the CF
        #     dashboard's BYOK binding was missing or stale.
        # Returns None when the gateway is down so the SDK omits
        # extra_headers entirely.
        clear = (self.api_key == BYOK_PLACEHOLDER)
        h = byok_headers(clear_upstream_auth=clear)
        return h or None

    @staticmethod
    def _is_cf_conn_err(exc: Exception) -> bool:
        err = str(exc).lower()
        return "connect" in err or "timeout" in err or "unreachable" in err or "dns" in err

    def _mark_cf_down(self, exc: Exception) -> None:
        if self._is_cf_conn_err(exc):
            mark_cf_gateway_down()
            _log.warning(f"Cloudflare AI Gateway connection error — direct fallback for 5 min: {type(exc).__name__}")

    async def _call_groq(self, messages: list) -> str:
        from groq import AsyncGroq
        base = get_provider_base_url("groq")
        kwargs = {"api_key": self.api_key, "max_retries": 0, "timeout": 8.0}
        if base:
            kwargs["base_url"] = base
        client = AsyncGroq(**kwargs)
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=2048,
                extra_headers=self._cf_cache_headers(),
            )
        except Exception as e:
            if base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = AsyncGroq(api_key=self.api_key, max_retries=0, timeout=8.0)
                response = await client.chat.completions.create(
                    model=self._model, messages=messages, max_tokens=2048,
                )
            else:
                raise
        return response.choices[0].message.content or ""

    async def _stream_groq(self, messages: list, max_tokens: int = 2048):
        from groq import AsyncGroq
        base = get_provider_base_url("groq")
        kwargs = {"api_key": self.api_key, "max_retries": 0, "timeout": 8.0}
        if base:
            kwargs["base_url"] = base
        client = AsyncGroq(**kwargs)
        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                temperature=0.1,
                top_p=0.95,
            )
        except Exception as e:
            if base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = AsyncGroq(api_key=self.api_key, max_retries=0, timeout=8.0)
                stream = await client.chat.completions.create(
                    model=self._model, messages=messages, max_tokens=max_tokens,
                    stream=True, temperature=0.1, top_p=0.95,
                )
            else:
                raise
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def _call_openai(self, messages: list) -> str:
        import openai
        base = get_provider_base_url("openai")
        kwargs = {"api_key": self.api_key}
        if base:
            kwargs["base_url"] = base
        client = openai.AsyncOpenAI(**kwargs)
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                extra_headers=self._cf_cache_headers(),
            )
        except openai.APIConnectionError as e:
            if base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = openai.AsyncOpenAI(api_key=self.api_key)
                response = await client.chat.completions.create(
                    model=self._model, messages=messages,
                )
            else:
                raise
        return response.choices[0].message.content or ""

    async def _stream_openai(self, messages: list, max_tokens: int = 1024):
        import openai
        base = get_provider_base_url("openai")
        kwargs = {"api_key": self.api_key}
        if base:
            kwargs["base_url"] = base
        client = openai.AsyncOpenAI(**kwargs)
        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                temperature=0.1,
                top_p=0.95,
            )
        except openai.APIConnectionError as e:
            if base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = openai.AsyncOpenAI(api_key=self.api_key)
                stream = await client.chat.completions.create(
                    model=self._model, messages=messages, max_tokens=max_tokens,
                    stream=True, temperature=0.1, top_p=0.95,
                )
            else:
                raise
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def _call_fireworks(self, messages: list) -> str:
        import openai
        direct_base = "https://api.fireworks.ai/inference/v1"
        base = get_provider_base_url("fireworksai") or direct_base
        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=base)
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                extra_headers=self._cf_cache_headers(),
            )
        except openai.APIConnectionError as e:
            if base != direct_base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = openai.AsyncOpenAI(api_key=self.api_key, base_url=direct_base)
                response = await client.chat.completions.create(
                    model=self._model, messages=messages,
                )
            else:
                raise
        return response.choices[0].message.content or ""

    async def _stream_fireworks(self, messages: list, max_tokens: int = 1024):
        import openai
        direct_base = "https://api.fireworks.ai/inference/v1"
        base = get_provider_base_url("fireworksai") or direct_base
        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=base)
        try:
            stream = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                stream=True,
                temperature=0.1,
                top_p=0.95,
            )
        except openai.APIConnectionError as e:
            if base != direct_base and self._is_cf_conn_err(e):
                self._mark_cf_down(e)
                client = openai.AsyncOpenAI(api_key=self.api_key, base_url=direct_base)
                stream = await client.chat.completions.create(
                    model=self._model, messages=messages, max_tokens=max_tokens,
                    stream=True, temperature=0.1, top_p=0.95,
                )
            else:
                raise
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    # Task #491 — legacy SLM call/stream helpers removed.
    async def _noop_legacy_slm_removed(self, *args, **kwargs):
        async for _ in []:  # pragma: no cover
            yield None
        if False:
            delta = None
            if delta and delta.content:
                yield delta.content

    async def _call_emergent(self, messages: list) -> str:
        import openai, os
        base_url = os.environ.get('EMERGENT_BASE_URL', 'https://api.emergent.sh/v1').strip()
        client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
        )
        response = await client.chat.completions.create(
            model=self._model,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    async def _stream_emergent(self, messages: list, max_tokens: int = 1024):
        import openai, os
        base_url = os.environ.get('EMERGENT_BASE_URL', 'https://api.emergent.sh/v1').strip()
        client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
        )
        stream = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            temperature=0.1,
            top_p=0.95,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
