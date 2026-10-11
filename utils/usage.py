"""
Token usage, including prompt caching.

LangChain reports `input_tokens` as the total input (cached + uncached), with the cached
parts in `input_token_details`. Cached tokens are billed differently (Anthropic prompt
caching, 5-minute cache):
  - reading from the cache costs 0.1x the normal input price
  - writing to the cache costs 1.25x
so "billed input tokens" below is the input cost expressed in normal-price tokens.
"""

CACHE_READ_PRICE = 0.10
CACHE_WRITE_PRICE = 1.25


def summarize_usage(usage_metadata: dict) -> dict:
    """Sum a get_usage_metadata_callback() result over all models used."""
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for usage in usage_metadata.values():
        details = usage.get("input_token_details") or {}
        total["input_tokens"] += usage.get("input_tokens", 0) or 0
        total["output_tokens"] += usage.get("output_tokens", 0) or 0
        total["cache_read_tokens"] += details.get("cache_read") or 0
        total["cache_write_tokens"] += ((details.get("cache_creation") or 0)
                                        + (details.get("ephemeral_5m_input_tokens") or 0)
                                        + (details.get("ephemeral_1h_input_tokens") or 0))
    total["billed_input_tokens"] = billed_input_tokens(total)
    return total


def billed_input_tokens(usage: dict) -> float:
    """Input cost in normal-price tokens: uncached + 0.1 x cache reads + 1.25 x cache writes."""
    read, write = usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0)
    uncached = usage.get("input_tokens", 0) - read - write
    return uncached + CACHE_READ_PRICE * read + CACHE_WRITE_PRICE * write