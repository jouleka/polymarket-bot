"""Untrusted-content sanitizer (indirect-prompt-injection defense).

Sits OUTSIDE the model. Strips invisible/format characters that hide injection
payloads (zero-width, bidi overrides, control chars) and wraps content in a
delimiter the content cannot itself contain (spotlighting / breakout defense).
"""

import unicodedata

_DEFAULT_MARKER = "⟦UNTRUSTED⟧"
_KEEP_CONTROL = {"\n", "\t"}


def strip_dangerous_chars(text):
    """Drop control chars (category Cc, except newline/tab) and all format chars
    (category Cf: zero-width spaces/joiners, bidi overrides, BOM, soft hyphen)."""
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cc" and ch not in _KEEP_CONTROL:
            continue
        if category == "Cf":
            continue
        out.append(ch)
    return "".join(out)


def sanitize(text, marker=_DEFAULT_MARKER):
    """Strip dangerous chars, neutralize any embedded delimiter, then wrap.

    The marker strip runs to a fixed point: a single pass can leave the
    surrounding glyphs rejoined into a fresh marker, so repeat until none remain.
    """
    cleaned = strip_dangerous_chars(text)
    while marker in cleaned:
        cleaned = cleaned.replace(marker, "")
    return f"{marker}\n{cleaned}\n{marker}"


def neutralize(text, marker=_DEFAULT_MARKER):
    """For non-content PLUMBING fields (ids, links): strip control / format chars
    (including the newline/tab that ``strip_dangerous_chars`` keeps) AND any embedded
    spotlight marker, WITHOUT wrapping -- so a feed-controlled id or link can never
    forge the UNTRUSTED delimiter or smuggle an invisible-char payload onto a field a
    downstream consumer might read. Deterministic, so it is safe as a dedup key."""
    cleaned = strip_dangerous_chars(text).replace("\n", "").replace("\t", "")
    while marker in cleaned:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()
