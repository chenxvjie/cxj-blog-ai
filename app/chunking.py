import hashlib
import logging
import re
from dataclasses import dataclass

import jieba
from markdown_it import MarkdownIt

jieba.setLogLevel(logging.ERROR)
STOP = {"的", "了", "是", "在", "和", "与", "如何", "什么", "怎么", "为什么", "请", "一个", "这个", "那", "有", "吗", "呢", "我", "你", "它", "可以"}


def terms(text: str) -> list[str]:
    return list(dict.fromkeys(t.lower() for t in jieba.cut(text) if re.fullmatch(r"[\w-]+", t) and t not in STOP and len(t) > 1))


def digest(title: str, slug: str, content: str) -> str:
    return hashlib.sha256((title + "\0" + slug + "\0" + content).encode()).hexdigest()


@dataclass
class Chunk:
    heading: str
    content: str


def split_markdown(markdown: str, max_chars: int = 1200) -> list[Chunk]:
    lines = markdown.splitlines()
    headings: list[str] = []
    chunks: list[Chunk] = []
    buffer: list[str] = []

    def flush():
        if buffer:
            chunks.append(Chunk(" / ".join(headings), "\n\n".join(buffer)))
            buffer.clear()

    tokens = MarkdownIt("commonmark").enable("table").parse(markdown)
    for index, token in enumerate(tokens):
        if token.level != 0 or token.map is None or token.type.endswith("_close"):
            continue
        if token.type == "heading_open":
            flush()
            level = int(token.tag[1:])
            headings[:] = headings[:level-1]
            headings.append(tokens[index+1].content)
            continue
        block = "\n".join(lines[token.map[0]:token.map[1]]).strip()
        if not block:
            continue
        # Normal code blocks/tables stay intact. Oversized blocks are split explicitly.
        for start in range(0, len(block), max_chars):
            part = block[start:start+max_chars]
            if sum(map(len, buffer)) + len(part) + len(buffer)*2 > max_chars:
                flush()
            buffer.append(part)
    flush()
    return chunks
