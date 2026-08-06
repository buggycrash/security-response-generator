"""Bridge colloquial security terms to the NIST SP 800-53 vocabulary they map to.

Customer standards and the NIST baseline consistently use NIST's own umbrella
terms (e.g. "authenticator", "system component"), but analysts often ask
about the same concept using a more common term (e.g. "password", "server").
Retrieval embeds queries verbatim with no lexical matching, so a colloquial
query can miss chunks that only contain the NIST term. `expand_query` appends
any matched NIST term(s) to the query text before it's embedded, without
altering the original wording shown to the analyst or the generation model.
"""

import re

# Colloquial/common term -> NIST SP 800-53 vocabulary term(s) it maps to.
# Deliberately small and hand-maintained; covers common gaps, not exhaustive.
TERM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "password": ("authenticator",),
    "passphrase": ("authenticator",),
    "pin": ("authenticator",),
    "mfa": ("multifactor authentication",),
    "2fa": ("multifactor authentication",),
    "two-factor": ("multifactor authentication",),
    "login": ("authentication",),
    "logon": ("authentication",),
    "sign-in": ("authentication",),
    "lockout": ("unsuccessful logon attempts",),
    "server": ("system component",),
    "vm": ("system component",),
    "virtual machine": ("system component",),
    "container": ("system component",),
    "workstation": ("system component",),
    "laptop": ("system component",),
    "firewall": ("boundary protection",),
    "waf": ("boundary protection",),
    "antivirus": ("malicious code protection",),
    "anti-malware": ("malicious code protection",),
    "patch": ("flaw remediation",),
    "patching": ("flaw remediation",),
    "hotfix": ("flaw remediation",),
    "backup": ("system backup",),
    "encryption": ("cryptographic protection",),
    "encrypt": ("cryptographic protection",),
    "badge": ("physical access control", "physical access device"),
    "key card": ("physical access control", "physical access device"),
    "vulnerability scan": ("vulnerability monitoring and scanning",),
    "pen test": ("vulnerability monitoring and scanning",),
    "background check": ("personnel screening",),
    "onboarding": ("account management",),
    "offboarding": ("account management",),
    "vendor": ("external system services",),
    "third-party": ("external system services",),
    "disaster recovery": ("contingency planning",),
}

_TERM_PATTERNS = {
    term: re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in TERM_SYNONYMS
}


def expand_query(text: str) -> str:
    """Append any matched NIST vocabulary term(s) to `text` for embedding.

    Matching is case-insensitive and word-bounded. Matched NIST terms are
    deduplicated and appended in a fixed order; `text` itself is never
    altered or reordered, so callers that also display `text` to a human or
    the model can keep using the original value separately.
    """
    matched: list[str] = []
    seen: set[str] = set()
    for term, nist_terms in TERM_SYNONYMS.items():
        if not _TERM_PATTERNS[term].search(text):
            continue
        for nist_term in nist_terms:
            if nist_term not in seen:
                seen.add(nist_term)
                matched.append(nist_term)

    if not matched:
        return text
    return f"{text} {' '.join(matched)}"
