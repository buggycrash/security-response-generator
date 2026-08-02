"""Normalize model output to plain ASCII text, stripping Markdown syntax.

The prompt already instructs the model to produce plain ASCII text when
--format text is used, but some evidence/GRC systems reject a submission
outright if it contains any non-ASCII character or leftover formatting.
This is a code-level safety net so compliance doesn't depend on the model
following instructions perfectly.
"""

import re
import unicodedata

_UNICODE_SUBSTITUTIONS = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "–": "-",
    "—": "-",
    "―": "-",
    "…": "...",
    "•": "-",
    "◦": "-",
    "▪": "-",
    "‣": "-",
    " ": " ",
}

_MARKDOWN_PATTERNS = [
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),  # headers
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),  # bold **
    (re.compile(r"__([^_]+)__"), r"\1"),  # bold __
    (re.compile(r"\*([^*]+)\*"), r"\1"),  # italic *
    (re.compile(r"(?<!\w)_([^_]+)_(?!\w)"), r"\1"),  # italic _
    (re.compile(r"`([^`]*)`"), r"\1"),  # inline code
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\1 (\2)"),  # links
]


def normalize_to_ascii(text: str) -> str:
    """Strip Markdown syntax and non-ASCII characters from model output."""
    for unicode_char, replacement in _UNICODE_SUBSTITUTIONS.items():
        text = text.replace(unicode_char, replacement)

    text = text.replace("```", "")

    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)

    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
