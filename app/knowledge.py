from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.config import settings


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    raw = TOKEN_RE.findall(text)
    tokens: list[str] = list(raw)
    chinese = [t for t in raw if len(t) == 1 and "\u4e00" <= t <= "\u9fff"]
    tokens.extend("".join(pair) for pair in zip(chinese, chinese[1:]))
    return tokens


SYNONYMS = {
    "邮费": ["运费", "快递费", "包邮", "配送"],
    "运费": ["邮费", "快递费", "包邮"],
    "退款": ["退钱", "退货", "售后"],
    "退货": ["退款", "退换", "售后"],
    "物流": ["快递", "到哪", "配送", "发货"],
    "快递": ["物流", "到哪", "配送"],
    "积分": ["抵扣", "积分抵运费"],
    "会员": ["金卡", "银卡", "免运费"],
}


def expand_query(query: str) -> str:
    extra: list[str] = []
    for key, values in SYNONYMS.items():
        if key in query:
            extra.extend(values)
    return query + (" " + " ".join(extra) if extra else "")


@dataclass
class Chunk:
    id: str
    doc_id: str
    title: str
    source: str
    category: str
    content: str
    score: float = 0.0


def _parse_markdown(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    doc_title = path.stem
    current_title = doc_title
    sections: list[tuple[str, str]] = []
    buffer: list[str] = []

    for line in text.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            if buffer and "".join(buffer).strip():
                sections.append((current_title, "\n".join(buffer).strip()))
            current_title = line[3:].strip()
            buffer = []
        else:
            buffer.append(line)
    if buffer and "".join(buffer).strip():
        sections.append((current_title, "\n".join(buffer).strip()))

    chunks: list[Chunk] = []
    for idx, (title, body) in enumerate(sections):
        content = f"【文档】{doc_title}\n【章节】{title}\n【正文】{body}"
        chunks.append(
            Chunk(
                id=f"{path.stem}-{idx}",
                doc_id=path.stem,
                title=title,
                source=path.name,
                category=path.stem,
                content=content,
            )
        )
    return chunks


class LocalKnowledgeBase:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.knowledge_dir
        self.chunks: list[Chunk] = []
        self.bm25: BM25Okapi | None = None
        self._load()

    def _load(self) -> None:
        chunks: list[Chunk] = []
        for path in sorted(self.root.glob("*.md")):
            chunks.extend(_parse_markdown(path))
        self.chunks = chunks
        corpus = [tokenize(expand_query(c.content)) for c in chunks]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    @staticmethod
    def _char_vec(text: str) -> dict[str, float]:
        chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
        grams = chars + ["".join(p) for p in zip(chars, chars[1:])]
        vec: dict[str, float] = {}
        for gram in grams:
            vec[gram] = vec.get(gram, 0.0) + 1.0
        return vec

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(v * b.get(k, 0.0) for k, v in a.items())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def retrieve(self, query: str, category: str | None = None, top_k: int = 8) -> list[Chunk]:
        if self.bm25 is None or not self.chunks:
            return []
        expanded = expand_query(query)
        q_tokens = tokenize(expanded)
        bm25_scores = list(self.bm25.get_scores(q_tokens))
        q_vec = self._char_vec(expanded)

        candidates = []
        for idx, chunk in enumerate(self.chunks):
            if category and chunk.category != category:
                continue
            dense = self._cosine(q_vec, self._char_vec(chunk.content))
            # 关键词/标题命中做轻量 rerank；不是 BGE，但保证本地 demo 可解释。
            keyword = 1.0 if any(t in chunk.content or t in chunk.title for t in q_tokens if len(t) >= 2) else 0.0
            score = 0.55 * bm25_scores[idx] + 0.35 * dense + 0.10 * keyword
            if category and query and any(k in query for k in ("邮费", "运费", "包邮")):
                score += 0.05
            chunk.score = float(score)
            candidates.append(chunk)

        # RRF：BM25 rank + dense rank，避免只依赖单一分数
        bm25_rank = {c.id: i for i, c in enumerate(sorted(candidates, key=lambda x: x.score, reverse=True))}
        dense_rank = {
            c.id: i
            for i, c in enumerate(
                sorted(candidates, key=lambda x: self._cosine(q_vec, self._char_vec(x.content)), reverse=True)
            )
        }
        for c in candidates:
            c.score = 1.0 / (60 + bm25_rank[c.id] + 1) + 1.0 / (60 + dense_rank[c.id] + 1)

        candidates.sort(key=lambda x: x.score, reverse=True)
        # 政策题经常有多个同文档条件；保留最多 8 条，商品 5 条。
        limit = 8 if (category == "shipping" or top_k > 5) else top_k
        return candidates[:limit]


_kb: LocalKnowledgeBase | None = None


def get_kb() -> LocalKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = LocalKnowledgeBase()
    return _kb
