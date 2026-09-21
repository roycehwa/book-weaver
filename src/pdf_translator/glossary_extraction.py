from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any

from wordfreq import zipf_frequency

if TYPE_CHECKING:
    from pdf_translator.glossary_profiles import GlossaryProfilePolicy


# Phrases that are almost never useful as book-level translation glossary entries.
GENERIC_STOP_PHRASES = frozenset(
    {
        "acknowledgements",
        "acknowledgments",
        "as well as",
        "at the same time",
        "bibliography",
        "cambridge university",
        "chapter one",
        "chicago press",
        "early islam",
        "early middle",
        "east africa",
        "east african",
        "east asia",
        "eastern europe",
        "eighth century",
        "eleventh century",
        "far east",
        "federal reserve",
        "fifth century",
        "figure one",
        "first half",
        "for example",
        "foreword",
        "fourth century",
        "further reading",
        "harvard university",
        "high middle",
        "in addition",
        "in fact",
        "in other words",
        "in particular",
        "in the case",
        "index",
        "introduction",
        "islamic middle east",
        "johns hopkins",
        "land and trade",
        "land trade",
        "late antique",
        "latin america",
        "list of figures",
        "list of tables",
        "los angeles",
        "middle east",
        "middle eastern",
        "near east",
        "new york",
        "ninth century",
        "north africa",
        "north african",
        "north america",
        "on the other hand",
        "oxford university",
        "part four",
        "part one",
        "part three",
        "part two",
        "preface",
        "press university",
        "references",
        "san francisco",
        "second half",
        "second world",
        "seventh century",
        "silicon valley",
        "sixth century",
        "south america",
        "south asia",
        "south asian",
        "such as",
        "supreme court",
        "table contents",
        "table of",
        "table of contents",
        "tenth century",
        "third century",
        "third world",
        "twelfth century",
        "united kingdom",
        "united states",
        "university press",
        "wall street",
        "west africa",
        "west asia",
        "western asia",
        "western europe",
        "white house",
        "world war",
        "yale university",
    }
)

_ORDINAL_CENTURY_RE = re.compile(
    r"^(?:(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth|Eleventh|Twelfth|"
    r"Thirteenth|Fourteenth|Fifteenth|Sixteenth|Seventeenth|Eighteenth|Nineteenth|Twentieth|"
    r"\d{1,2}(?:st|nd|rd|th)?)\s+Centur(?:y|ies))$",
    re.IGNORECASE,
)
_GENERIC_COMPASS_RE = re.compile(
    r"^(?:(?:Far|Near|Middle|Upper|Lower|Central|Northern|Southern|Eastern|Western)\s+"
    r"(?:East|West|North|South|Africa|Asia|Europe|Caliphate|Mesopotamia|Iraq)|"
    r"(?:East|West|North|South|Central)\s+(?:Africa|Asia|Europe|Caliphate))$",
    re.IGNORECASE,
)
_ISOLATED_ROMAN_TOKEN_RE = re.compile(r"\b[IVX] [a-z]")
_NUMBER_WORD_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|hundred|thousand|million)\b",
    re.IGNORECASE,
)
_GENERIC_REGIONAL_MODIFIER_RE = re.compile(
    r"\b(?:Ancient|Medieval|Early|Late|Imperial|Lower|Upper|Central)\s+"
    r"(?:Near Eastern|Middle Eastern|East African|West African|North African|South Asian)\b",
    re.IGNORECASE,
)
_TRAILING_CONNECTOR_RE = re.compile(
    r"\b(?:of|and|the|in|on|for|from|with|to|at|by|or|as|an|a)\s*$",
    re.IGNORECASE,
)

def is_generic_stop_phrase(normalised_phrase: str) -> bool:
    """Return True if the normalised phrase is in the generic stop list.

    The list is intentionally a hand-curated set of phrases that
    frequently appear in book corpora but should never be treated as
    book-level glossary entries (publishing boilerplate, generic
    geographic regions, ordinal centuries). It does NOT enforce any
    occurrence or chapter-count floor: a 600-page monograph and a
    100-page children's book use the same list, because "early
    Islam" or "land and trade" are equally non-terms regardless of
    how often they appear.
    """
    if not normalised_phrase:
        return False
    return normalised_phrase.strip().lower() in GENERIC_STOP_PHRASES


DOMAIN_MARKERS = frozenset(
    {
        "act",
        "policy",
        "capitalism",
        "corporation",
        "corporate",
        "shareholder",
        "primacy",
        "board",
        "governance",
        "economic",
        "economy",
        "finance",
        "financial",
        "regulation",
        "regulatory",
        "federal",
        "commission",
        "institution",
        "stakeholder",
        "equity",
        "market",
        "labor",
        "union",
        "tax",
        "fiscal",
        "monetary",
        "democracy",
        "republic",
        "amendment",
        "statute",
        "legislation",
    }
)

INSTITUTION_MARKERS = frozenset(
    {
        "university",
        "press",
        "institute",
        "foundation",
        "association",
        "commission",
        "department",
        "agency",
        "corporation",
        "company",
        "bank",
        "council",
        "committee",
        "office",
        "bureau",
    }
)

EVENT_MARKERS = frozenset(
    {
        "revolution",
        "war",
        "campaign",
        "massacre",
        "famine",
        "dynasty",
        "movement",
        "reform",
        "opening",
    }
)

PERSON_NAME_RE = re.compile(r"^[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,2}$")
CONCEPT_HEAD_WORDS = frozenset(
    {
        "analysis",
        "account",
        "approach",
        "bibliography",
        "evidence",
        "fact",
        "history",
        "ideology",
        "investigation",
        "investigations",
        "landscape",
        "landscapes",
        "man",
        "meditation",
        "meditations",
        "phenomenology",
        "production",
        "reader",
        "research",
        "review",
        "series",
        "sources",
        "study",
        "survey",
        "system",
        "systems",
        "teleology",
        "turn",
    }
)
PHRASE_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z'’-]*(?:\s+|(?=[,.;:!?]|$))){2,5}"
)
CONNECTOR_PHRASE_RE = re.compile(
    r"\b("
    r"(?:[A-Z][A-Za-z'-]*\s+){1,4}"
    r"(?:of|and|the)\s+"
    r"(?:[A-Z][A-Za-z'-]*(?:\s+|(?=[,.;:!?]|$))){1,4}"
    r")"
)
INDEX_ENTRY_RE = re.compile(r"^([A-Z][^,\n;]{2,80}?)(?:,\s*\d)", re.MULTILINE)
DOMAIN_WORD_RE = re.compile(r"\b([A-Za-z]{4,})\b")
QUOTED_TERM_RE = re.compile(r'"([^"]{3,60})"')
DEFINITION_TERM_RE = re.compile(
    r"\b([A-Z][A-Za-z'’-]+(?:\s+[A-Za-z'’-]+){0,3})\s+"
    r"(?:is defined as|are defined as|means|denotes|refers to)\b",
    re.IGNORECASE,
)

EVIDENCE_MULTIWORD_PHRASE = "multiword_phrase"
EVIDENCE_CONNECTOR_PHRASE = "connector_phrase"
EVIDENCE_QUOTED = "quoted"
EVIDENCE_INDEX = "index"
EVIDENCE_DEFINITION = "definition"
EVIDENCE_DOMAIN_FREQUENCY = "domain_frequency"

STRONG_SINGLE_WORD_EVIDENCE = frozenset(
    {EVIDENCE_QUOTED, EVIDENCE_INDEX, EVIDENCE_DEFINITION},
)
MULTIWORD_TERMHOOD_EVIDENCE = frozenset(
    {
        EVIDENCE_MULTIWORD_PHRASE,
        EVIDENCE_CONNECTOR_PHRASE,
        EVIDENCE_INDEX,
        EVIDENCE_QUOTED,
        EVIDENCE_DEFINITION,
    },
)


def _normalize_phrase(value: str) -> str:
    return " ".join(value.split())


def canonical_source_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    # Split glued Roman numerals only when there are 2+ numeral chars (e.g. IIandhis),
    # never treat leading I/V/X in words like Islam or Iraq as numerals.
    normalized = re.sub(r"\b([IVX]{2,6})(and|or|the|his|her|was|is|in|of)\b", r"\1 \2", normalized)
    normalized = re.sub(r"\b([IVX]{2,6})([a-z]{2,})\b", r"\1 \2", normalized)
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    return _normalize_phrase(normalized).rstrip(" '\".,;:!?…")


def canonical_source_key(value: str) -> str:
    return canonical_source_term(value).casefold()


def _phrase_words(phrase: str) -> list[str]:
    return [word for word in re.split(r"\s+", phrase.strip()) if word]


def _metadata_exclusions(book: dict[str, Any]) -> set[str]:
    exclusions: set[str] = set()
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    for key in ("title", "subtitle", "author", "publisher", "series"):
        raw = metadata.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        cleaned = _normalize_phrase(raw.strip(" ."))
        if cleaned:
            exclusions.add(cleaned)
        for part in re.split(r"[:—–\-|]", raw):
            part_clean = _normalize_phrase(part.strip(" ."))
            if len(part_clean) >= 4:
                exclusions.add(part_clean)
    for chapter in book.get("chapters", []):
        title = str(chapter.get("title") or "").strip()
        if not title or title.lower().startswith("untitled"):
            continue
        cleaned = _normalize_phrase(title)
        if cleaned:
            exclusions.add(cleaned)
        # also exclude each word-trail of the title so e.g. a chapter
        # titled "Notes on Transcription" still blocks the substring
        # "Notes on Transcription" from becoming a candidate.
        for part in re.split(r"[:—–\-|]", title):
            part_clean = _normalize_phrase(part.strip(" ."))
            if len(part_clean) >= 4:
                exclusions.add(part_clean)
    return exclusions


def _count_occurrences(text: str, phrase: str) -> int:
    if not phrase:
        return 0
    pattern = re.escape(phrase)
    return len(re.findall(rf"(?<!\w){pattern}(?!\w)", text, flags=re.IGNORECASE))


def _classify_term(phrase: str) -> str:
    lowered = phrase.lower()
    words = _phrase_words(phrase)
    if any(marker in lowered for marker in EVENT_MARKERS):
        return "event"
    if any(marker in lowered for marker in (" act", "act ", "policy", "primacy", "capitalism", "regulation")):
        return "policy_term"
    if any(marker in lowered for marker in INSTITUTION_MARKERS):
        return "institution"
    concept_head = words[-1].casefold().strip("'’-") if words else ""
    concept_morphology = concept_head.endswith(
        ("ism", "ology", "ics", "ity", "tion", "ment", "ness", "graphy", "nomy")
    )
    if (
        PERSON_NAME_RE.fullmatch(phrase)
        and len(words) <= 3
        and concept_head not in CONCEPT_HEAD_WORDS
        and not concept_morphology
    ):
        return "person"
    return "concept"


def _domain_marker_hits(phrase: str, markers: frozenset[str]) -> list[str]:
    lowered = phrase.lower()
    return sorted(marker for marker in markers if marker in lowered)


def _is_valid_phrase(phrase: str) -> bool:
    if any(char in phrase for char in ".;:!?"):
        return False
    words = _phrase_words(phrase)
    if len(words) < 2:
        return False
    stop_edges = {"the", "in", "on", "how", "a", "and", "of", "to", "for"}
    if words[0].lower() in stop_edges or words[-1].lower() in stop_edges:
        return False
    return True


def _is_fragment_phrase(phrase: str) -> bool:
    return phrase.startswith(
        (
            "The ",
            "In ",
            "On ",
            "How ",
            "A ",
            "That ",
            "Within ",
            "Between ",
            "From ",
            "For ",
            "After ",
            "Before ",
            "During ",
            "Since ",
            "Until ",
            "When ",
            "While ",
            "Many ",
            "Making of ",
            "Despite ",
        )
    )


_CLAUSE_LEADING_WORDS = frozenset(
    {
        "after",
        "although",
        "and",
        "as",
        "because",
        "before",
        "but",
        "if",
        "nor",
        "once",
        "or",
        "since",
        "so",
        "than",
        "though",
        "unless",
        "until",
        "when",
        "where",
        "whereas",
        "whether",
        "while",
        "yet",
    }
)
_POSSESSIVE_DETERMINERS = frozenset({"her", "his", "its", "my", "our", "their", "your"})
_COORDINATION_DETERMINERS = frozenset({"both", "either", "neither"})
_AUXILIARY_VERBS = frozenset(
    {
        "am",
        "are",
        "be",
        "been",
        "being",
        "can",
        "could",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "is",
        "may",
        "might",
        "must",
        "shall",
        "should",
        "was",
        "were",
        "will",
        "would",
    }
)
_WH_WORDS = frozenset({"how", "what", "when", "where", "which", "who", "whom", "whose", "why"})
_PHRASAL_VERB_PARTICLES = frozenset(
    {"about", "after", "along", "around", "away", "back", "down", "off", "on", "out", "over", "through", "up"}
)
_GRAMMATICAL_FRAGMENT_LEADS = frozenset(
    {"how", "insofar", "like", "there", "what", "when", "where", "which", "who", "whom", "whose", "why"}
)
_TRAILING_PRONOUNS = frozenset({"he", "her", "him", "i", "it", "me", "she", "them", "they", "us", "we", "you"})

_INCOMPLETE_TRAILING_MODIFIERS = frozenset(
    {
        "advisory",
        "central",
        "contemporary",
        "cultural",
        "eastern",
        "economic",
        "general",
        "international",
        "key",
        "local",
        "modern",
        "national",
        "northern",
        "physical",
        "political",
        "social",
        "southern",
        "western",
    }
)


def _word_casefold(word: str) -> str:
    return word.casefold().strip("'’-")


def termhood_structural_rejection(phrase: str) -> str | None:
    """Reject title-cased sentence fragments that are not book-level terms."""
    normalized = _normalize_phrase(phrase)
    if not normalized:
        return None
    if re.search(r"\b[A-Za-z]{2,}-[A-Za-z]{2,}\b", normalized):
        return None
    words = _phrase_words(normalized)
    if len(words) < 2:
        return None

    first = _word_casefold(words[0])
    last = _word_casefold(words[-1])
    lowered_words = [_word_casefold(word) for word in words]

    if first in _POSSESSIVE_DETERMINERS:
        return "sentence_fragment"
    if first in _COORDINATION_DETERMINERS:
        return "sentence_fragment"
    if first in _GRAMMATICAL_FRAGMENT_LEADS:
        return "sentence_fragment"
    if first in _AUXILIARY_VERBS:
        return "sentence_fragment"
    if first in _WH_WORDS and any(word in _AUXILIARY_VERBS for word in lowered_words[1:]):
        return "sentence_fragment"
    if len(words) == 2 and first in _CLAUSE_LEADING_WORDS:
        return "sentence_fragment"
    if len(words) == 2 and last in _PHRASAL_VERB_PARTICLES:
        return "sentence_fragment"
    if last in _TRAILING_PRONOUNS:
        return "sentence_fragment"
    return None


def _is_glossary_phrase_candidate(phrase: str) -> bool:
    if not _is_valid_phrase(phrase):
        return False
    return termhood_structural_rejection(phrase) is None


def candidate_integrity_rejection(phrase: str) -> str | None:
    normalized = _normalize_phrase(phrase)
    if not normalized:
        return "empty phrase"
    if re.match(r"^(?:this|these|those|our)\s", normalized, re.IGNORECASE):
        return "demonstrative_phrase"
    if re.match(r"^(?:during|since|among|within|across|under)\s", normalized, re.IGNORECASE) and not re.match(r"^under secretary\b", normalized, re.IGNORECASE):
        return "clause_fragment"
    if any(marker in normalized for marker in ("*", "[", "]", "(", ")")):
        return "markup_contamination"
    if normalized.startswith(("Journal of ", "International Journal of ", "Glossary of ")):
        return "bibliographic_label"
    if re.search(r"(?<![A-Za-z'’])\b[IVX]\s+[a-z]{2,}\b", normalized):
        return "malformed_token_boundary"
    if _is_fragment_phrase(normalized):
        return "clause_fragment"
    words = _phrase_words(normalized)
    if len(words) == 1 and _word_casefold(words[0]) in (
        _CLAUSE_LEADING_WORDS
        | _AUXILIARY_VERBS
        | _GRAMMATICAL_FRAGMENT_LEADS
        | _POSSESSIVE_DETERMINERS
        | _TRAILING_PRONOUNS
    ):
        return "grammatical_word"
    if words and words[-1].casefold().strip("'’-") in _INCOMPLETE_TRAILING_MODIFIERS:
        return "incomplete_trailing_modifier"
    if _ORDINAL_CENTURY_RE.fullmatch(normalized):
        return "generic century phrase"
    if _GENERIC_COMPASS_RE.fullmatch(normalized):
        return "generic geographic phrase"
    termhood_reason = termhood_structural_rejection(normalized)
    if termhood_reason is not None:
        return termhood_reason
    return None


def has_independent_termhood(
    phrase: str,
    *,
    evidence_kinds: frozenset[str] | set[str],
) -> bool:
    """Frequency and chapter spread may rank only after termhood is established."""
    words = _phrase_words(_normalize_phrase(phrase))
    if len(words) >= 2:
        return bool(evidence_kinds & MULTIWORD_TERMHOOD_EVIDENCE)
    if len(words) == 1:
        if evidence_kinds & {EVIDENCE_INDEX, EVIDENCE_DEFINITION}:
            return True
        # Quotation alone is useful only for lexically uncommon words. Common
        # prose words are often quoted rhetorically or inside citations and do
        # not, by themselves, create a translation-consistency decision.
        return EVIDENCE_QUOTED in evidence_kinds and zipf_frequency(words[0], "en") <= 4.0
    return False


def score_glossary_candidate(
    phrase: str,
    *,
    occurrences: int,
    chapter_count: int,
    body_chapter_count: int = 0,
    exclusions: set[str],
    in_index: bool,
    evidence_kinds: frozenset[str] | set[str] | None = None,
    policy: GlossaryProfilePolicy | None = None,
) -> tuple[float, list[str], bool]:
    """Return (score, reasons, rejected)."""
    from pdf_translator.glossary_profiles import GLOSSARY_PROFILES, SOCIAL_ECON_PHILOSOPHY

    active_policy = policy or GLOSSARY_PROFILES[SOCIAL_ECON_PHILOSOPHY]
    normalized = _normalize_phrase(phrase)
    words = _phrase_words(normalized)
    kinds = frozenset(evidence_kinds or ())
    min_len = 3 if len(words) == 1 and kinds & STRONG_SINGLE_WORD_EVIDENCE else 5
    if len(words) < active_policy.min_word_count or len(normalized) < min_len:
        return 0.0, [], True
    if not has_independent_termhood(normalized, evidence_kinds=kinds):
        return 0.0, ["缺乏独立术语证据（需引号、索引、定义或多词专名）"], True
    if normalized in exclusions or normalized in GENERIC_STOP_PHRASES:
        return 0.0, [f"排除：与书名/作者/出版社或通用地名重复（{normalized}）"], True

    reasons: list[str] = []
    score = 0.0
    profile_boosts: list[str] = []

    if occurrences >= 8:
        score += 6.0
        reasons.append(f"全文高频出现 {occurrences} 次")
    elif occurrences >= 4:
        score += 4.0
        reasons.append(f"多次出现 {occurrences} 次")
    elif occurrences >= 2:
        score += 2.0
        reasons.append(f"出现 {occurrences} 次")
    else:
        score -= 2.0
        reasons.append("仅出现 1 次，可能是偶发短语")

    if chapter_count >= 4:
        score += 5.0
        reasons.append(f"跨 {chapter_count} 章反复使用")
    elif chapter_count >= 2:
        score += 3.0
        reasons.append(f"出现在 {chapter_count} 章")
    else:
        score -= 1.5
        reasons.append("仅出现在单一章节")

    markers = _domain_marker_hits(normalized, active_policy.domain_markers)
    if markers:
        score += min(4.0, len(markers) * 1.5)
        reasons.append("含领域词：" + "、".join(markers[:4]))
        profile_boosts.append("domain_marker")

    if len(words) == 1:
        if EVIDENCE_QUOTED in kinds:
            score += 2.0
            reasons.append("正文引号标明的术语")
            profile_boosts.append("quoted_term")
        if EVIDENCE_INDEX in kinds:
            score += 1.5
            profile_boosts.append("index_single")
        if EVIDENCE_DEFINITION in kinds:
            score += 2.0
            reasons.append("显式定义表述")
            profile_boosts.append("definition")
    elif len(words) >= 3:
        score += 2.0
        reasons.append("多词专名，比两词通用短语更具体")
    elif len(words) == 2:
        term_type = _classify_term(normalized)
        if term_type == "person" and active_policy.person_name_penalty > 0:
            score -= active_policy.person_name_penalty
            reasons.append("两词人名：除非全书核心人物，否则优先级较低")
        elif term_type == "person":
            profile_boosts.append("person_allowed")
        elif term_type == "event":
            score += 1.5
            reasons.append("历史/事件类短语加权")
            profile_boosts.append("event_boost")
        else:
            score -= 0.5

    if in_index:
        score += active_policy.index_parse_weight
        reasons.append("出现在索引/术语章节")
        profile_boosts.append("index")

    if _is_fragment_phrase(normalized) and not in_index:
        if active_policy.fragment_prefix_penalty >= 3.0:
            return 0.0, ["句首碎片短语，不进入候选列表"], True
        score -= active_policy.fragment_prefix_penalty
        reasons.append("句首碎片短语，优先级降低")
        profile_boosts.append("fragment_penalty")

    if normalized.endswith((" Press", " University", " Institute")):
        score -= 4.0
        reasons.append("更像出版/机构名，通常不作全书术语统一")

    if len(words) == 2 and not markers and occurrences <= 3 and chapter_count <= 2:
        score -= 3.0
        reasons.append("宽泛两词短语，缺乏全书术语特征")

    rejected = score < active_policy.min_accept_score
    if rejected:
        reasons.append("综合得分过低，不进入候选列表")
    return score, reasons, rejected


def extract_candidate_phrases(text: str) -> list[str]:
    seen: dict[str, int] = {}
    for match in PHRASE_RE.finditer(text):
        phrase = _normalize_phrase(match.group(0))
        if phrase.startswith("The "):
            phrase = phrase[4:]
        if len(_phrase_words(phrase)) < 2 or not _is_glossary_phrase_candidate(phrase):
            continue
        seen[phrase] = seen.get(phrase, 0) + 1
    return list(seen.keys())


def extract_connector_phrases(text: str) -> list[str]:
    seen: set[str] = set()
    for match in CONNECTOR_PHRASE_RE.finditer(text):
        phrase = _normalize_phrase(match.group(1))
        if phrase.startswith("The "):
            phrase = phrase[4:]
        words = _phrase_words(phrase)
        content_words = [word for word in words if word.lower() not in {"of", "and", "the"}]
        if len(content_words) >= 2 and _is_glossary_phrase_candidate(phrase):
            seen.add(phrase)
    return sorted(seen)


def extract_domain_single_words(text: str, markers: frozenset[str], *, min_occurrences: int = 2) -> list[str]:
    """Surface marker hits for diagnostics only; not independent termhood evidence."""
    counts: dict[str, int] = {}
    for match in DOMAIN_WORD_RE.finditer(text):
        word = match.group(1)
        if word.lower() not in markers:
            continue
        start = match.start()
        if start > 0 and text[start - 1] in ".!?\n":
            continue
        if word[0].islower():
            continue
        counts[word] = counts.get(word, 0) + 1
    return sorted(word for word, count in counts.items() if count >= min_occurrences)


def extract_definition_terms(text: str) -> list[str]:
    phrases: set[str] = set()
    for match in DEFINITION_TERM_RE.finditer(text):
        phrase = _normalize_phrase(match.group(1).strip(" ."))
        if len(phrase) < 3:
            continue
        words = _phrase_words(phrase)
        if len(words) >= 2 and _is_glossary_phrase_candidate(phrase):
            phrases.add(phrase)
        elif len(words) == 1 and len(phrase) >= 4:
            phrases.add(phrase)
    return sorted(phrases)


def extract_quoted_terms(text: str) -> list[str]:
    phrases: set[str] = set()
    for match in QUOTED_TERM_RE.finditer(text):
        phrase = _normalize_phrase(match.group(1).strip(" ."))
        if len(phrase) < 3:
            continue
        words = _phrase_words(phrase)
        if len(words) >= 2 and _is_glossary_phrase_candidate(phrase):
            phrases.add(phrase)
        elif len(words) == 1 and len(phrase) >= 4:
            phrases.add(phrase)
    return sorted(phrases)


def extract_index_phrases(text: str) -> list[str]:
    phrases: set[str] = set()
    for match in INDEX_ENTRY_RE.finditer(text):
        phrase = _normalize_phrase(match.group(1).strip(" ."))
        word_count = len(_phrase_words(phrase))
        if word_count >= 2 and len(phrase) >= 5 and _is_glossary_phrase_candidate(phrase):
            phrases.add(phrase)
        elif word_count == 1 and len(phrase) >= 4:
            phrases.add(phrase)
    for chunk in re.split(r"[,;\n]", text):
        chunk = _normalize_phrase(chunk.strip(" ."))
        if not chunk or len(chunk) < 5 or not _is_glossary_phrase_candidate(chunk):
            continue
        if PERSON_NAME_RE.fullmatch(chunk) or len(_phrase_words(chunk)) >= 2:
            if chunk[0].isupper():
                phrases.add(chunk)
    return sorted(phrases)
