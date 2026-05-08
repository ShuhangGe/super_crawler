from __future__ import annotations

import re
from collections import Counter


SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "tool_request": re.compile(r"\b(is there|any|what'?s)\s+(a\s+)?(tool|app|service|way)\b", re.I),
    "workaround": re.compile(r"\b(workaround|hack|spreadsheet|manual|copy and paste|deal with)\b", re.I),
    "complaint": re.compile(r"\b(tired of|why is it so hard|hate|frustrat|annoying|pain|struggle)\b", re.I),
    "alternative": re.compile(r"\b(alternative to|replace|switch from|better than)\b", re.I),
    "wish": re.compile(r"\b(i wish|wish there was|does anyone else)\b", re.I),
    "best_way": re.compile(r"\b(best way|how do people|how are you all)\b", re.I),
}

GEO_PATTERNS: dict[str, re.Pattern[str]] = {
    "United States": re.compile(r"\b(usa|u\.s\.|united states|american|usd|\$)\b", re.I),
    "United Kingdom": re.compile(r"\b(uk|britain|england|gbp|£|nhs|gcse)\b", re.I),
    "Canada": re.compile(r"\b(canada|canadian|cad)\b", re.I),
    "Australia": re.compile(r"\b(australia|aussie|aud)\b", re.I),
    "European Union": re.compile(r"\b(eu|euro|eur|gdpr|€)\b", re.I),
    "Germany": re.compile(r"\b(germany|german|deutschland)\b", re.I),
}

AUDIENCE_HINTS: dict[str, re.Pattern[str]] = {
    "parents": re.compile(r"\b(parent|kids|children|school|toddler|baby)\b", re.I),
    "students": re.compile(r"\b(student|college|university|homework|class)\b", re.I),
    "freelancers": re.compile(r"\b(freelance|client|invoice|contractor)\b", re.I),
    "pet owners": re.compile(r"\b(dog|cat|pet|vet)\b", re.I),
    "small business owners": re.compile(r"\b(small business|shop owner|customers|inventory)\b", re.I),
    "developers": re.compile(r"\b(api|code|developer|github|deploy|bug)\b", re.I),
}

STOPWORDS = {
    "about",
    "after",
    "again",
    "anyone",
    "because",
    "being",
    "better",
    "could",
    "does",
    "from",
    "have",
    "help",
    "just",
    "like",
    "need",
    "people",
    "really",
    "should",
    "some",
    "that",
    "there",
    "this",
    "tired",
    "using",
    "want",
    "what",
    "when",
    "where",
    "which",
    "with",
    "without",
    "would",
}


def matched_signal_patterns(text: str) -> list[str]:
    return [name for name, pattern in SIGNAL_PATTERNS.items() if pattern.search(text)]


def infer_geo(text: str, subreddit: str = "") -> list[str]:
    haystack = f"{subreddit} {text}"
    regions = [region for region, pattern in GEO_PATTERNS.items() if pattern.search(haystack)]
    subreddit_lower = subreddit.lower()
    if "askuk" in subreddit_lower and "United Kingdom" not in regions:
        regions.append("United Kingdom")
    if "personalfinancecanada" in subreddit_lower and "Canada" not in regions:
        regions.append("Canada")
    if "aus" in subreddit_lower and "Australia" not in regions:
        regions.append("Australia")
    return regions


def infer_audience(text: str) -> list[str]:
    audiences = [name for name, pattern in AUDIENCE_HINTS.items() if pattern.search(text)]
    return audiences or ["general consumers"]


def keywords(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    terms = [word for word in words if word not in STOPWORDS]
    return [word for word, _ in Counter(terms).most_common(limit)]


def normalize_requirement(text: str) -> str:
    terms = keywords(text, limit=6)
    return " ".join(sorted(terms))


def simple_similarity(left: str, right: str) -> float:
    left_terms = set(keywords(left, limit=20))
    right_terms = set(keywords(right, limit=20))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def make_requirement_title(title: str, body: str) -> str:
    terms = keywords(f"{title} {body}", limit=5)
    if not terms:
        return title[:90] or "Unclear Reddit requirement"
    phrase = " ".join(terms)
    return f"Users need a better way to handle {phrase}"
