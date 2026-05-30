"""
Token Budget Management for LLM Context Window
Uses character-based estimation (accurate enough for budget planning)
"""

# LIMITATION: Uses character-based heuristic estimation. For precise counting
# with specific models (GPT, Gemini), integrate tiktoken or the model-specific tokenizer.
# Current accuracy: ~80% for English, ~70% for Assamese/Indic scripts.


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text.
    English: ~1 token per 4 characters
    Assamese/Unicode: ~1 token per 2 characters
    """
    if not text:
        return 0
    # Count non-ASCII characters (likely Assamese/Indic)
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - non_ascii
    return (ascii_chars // 4) + (non_ascii // 2) + 1


def truncate_chunks_to_budget(
    chunks: list[dict],
    max_tokens: int = 3000,
    title_key: str = "title",
    content_key: str = "content",
) -> list[dict]:
    """
    Truncate context chunks to fit within token budget.
    Keeps chunks in order, truncating or dropping later ones as needed.
    """
    result = []
    tokens_used = 0

    for chunk in chunks:
        chunk_text = f"{chunk.get(title_key, '')}: {chunk.get(content_key, '')}"
        chunk_tokens = estimate_tokens(chunk_text)

        if tokens_used + chunk_tokens <= max_tokens:
            result.append(chunk)
            tokens_used += chunk_tokens
        else:
            # Try to include a truncated version
            remaining_budget = max_tokens - tokens_used
            if remaining_budget > 50:  # Only include if meaningful content fits
                # Truncate content to fit
                available_chars = remaining_budget * 4  # Rough reverse estimation
                truncated_chunk = chunk.copy()
                truncated_chunk[content_key] = (
                    chunk.get(content_key, "")[:available_chars] + "..."
                )
                result.append(truncated_chunk)
            break

    return result
