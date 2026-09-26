"""Semantic-ish similarity search over incident history and knowledge sections.

Uses a hybrid TF-IDF (word 1-2 grams + character n-grams) so that differently
worded tickets ("resource busy" vs "ORA-00054", "0C7" vs "S0C7") still match,
without needing a GPU or an embedding service. Swap `_Index` for an embedding
store later without changing callers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import config
from .knowledge import Document, HistoricIncident, Section

OTHER_APP_PENALTY = 0.6
EXACT_CODE_SCORE = 0.9


class _Index:
    def __init__(self, texts: list[str]):
        self.word = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english",
                                    token_pattern=r"(?u)\b[\w$#.-]*\w\b")
        self.char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.w = self.word.fit_transform(texts)
        self.c = self.char.fit_transform(texts)

    def score(self, query: str) -> np.ndarray:
        w = cosine_similarity(self.word.transform([query]), self.w)[0]
        c = cosine_similarity(self.char.transform([query]), self.c)[0]
        # Character n-grams share many trigrams even for unrelated text; weight
        # them lower and subtract their typical noise floor.
        return 0.65 * w + 0.35 * np.clip(c - 0.10, 0, None) / 0.9


@dataclass
class IncidentMatch:
    incident: HistoricIncident
    score: float


@dataclass
class SectionMatch:
    section: Section
    score: float


class KnowledgeIndex:
    def __init__(self, documents: list[Document], history: list[HistoricIncident]):
        self.documents = {d.doc_id: d for d in documents}
        self.sections = [s for d in documents for s in d.sections]
        self.history = history
        self._inc_index = _Index([h.search_text for h in history]) if history else None
        self._sec_index = _Index([f"{s.doc_title}. {s.heading}. {s.text}" for s in self.sections]) \
            if self.sections else None

    def similar_incidents(self, query: str, application: str, groups: list[str],
                          k: int = config.TOP_INCIDENTS) -> list[IncidentMatch]:
        if not self._inc_index:
            return []
        scores = self._inc_index.score(query)
        q = query.lower()
        out = []
        for inc, s in zip(self.history, scores):
            if inc.application not in groups:           # permission filter (FR-5)
                continue
            if inc.error_code and inc.error_code.lower() in q:
                s = max(s, EXACT_CODE_SCORE)            # identical error code is the strongest signal
            if inc.application != application:
                s *= OTHER_APP_PENALTY
            out.append(IncidentMatch(inc, float(s)))
        out.sort(key=lambda m: m.score, reverse=True)
        return out[:k]

    def relevant_sections(self, query: str, application: str, spaces: list[str],
                          k: int = config.TOP_SECTIONS) -> list[SectionMatch]:
        if not self._sec_index:
            return []
        scores = self._sec_index.score(query)
        out = []
        for sec, s in zip(self.sections, scores):
            if sec.space not in spaces:                 # permission filter (FR-5)
                continue
            if sec.application != application:
                s *= OTHER_APP_PENALTY
            if s >= config.MIN_DOC_SCORE:
                out.append(SectionMatch(sec, float(s)))
        out.sort(key=lambda m: m.score, reverse=True)
        return out[:k]
