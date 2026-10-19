"""Context formatting, citation parsing and grounding checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..retrieval.hybrid import RetrievedChunk

NOT_FOUND_MESSAGE = "I could not find this in the indexed code."
_NOT_FOUND = re.compile(
    r"(could ?n[o']t find|cannot find|can't find|unable to (?:find|locate)|"
    r"no (?:relevant )?information (?:about|on|regarding)|"
    r"(?:excerpts?|context|code)(?: provided| shown| given)? (?:does|do) not (?:contain|include|mention|show))",
    re.IGNORECASE,
)
_CITATION = re.compile(r"(?<!\w)\[(\d{1,3}(?:\s*(?:,|-|–)\s*\d{1,3})*)\]")
_FENCED = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_INLINE = re.compile(r"`[^`\n]*`")
_PATH_MENTION = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z]{1,5})(?::(\d+)(?:-(\d+))?)?")
KNOWN_EXTENSIONS = {
    "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "md", "json", "yaml", "yml", "toml", "txt", "html", "css",
    "scss", "vue", "svelte", "go", "rs", "java", "kt", "rb", "php", "sh", "sql", "cfg", "ini", "c", "h", "cpp",
    "cs", "swift", "xml",
}


@dataclass
class ContextBlock:
    number: int
    chunk: RetrievedChunk
    start_line: int
    end_line: int
    content: str

    def header(self) -> str:
        symbol = f" — {self.chunk.symbol_type} {self.chunk.symbol}" if self.chunk.symbol else ""
        via = f" (related: {self.chunk.via})" if self.chunk.via else ""
        return f"[{self.number}] {self.chunk.file_path}:{self.start_line}-{self.end_line}{symbol}{via}"

    def source(self) -> dict[str, Any]:
        return {
            "n": self.number, "chunk_id": self.chunk.id, "file_path": self.chunk.file_path,
            "start_line": self.start_line, "end_line": self.end_line, "symbol": self.chunk.symbol,
            "symbol_type": self.chunk.symbol_type, "language": self.chunk.language, "via": self.chunk.via,
            "score": round(self.chunk.score, 5), "ranks": self.chunk.ranks, "content": self.content,
        }


def build_context(chunks: list[RetrievedChunk], char_budget: int, max_chunk_chars: int = 4000) -> list[ContextBlock]:
    """Number retrieved chunks for the prompt, trimming long ones line by line to fit the budget."""
    blocks: list[ContextBlock] = []
    used = 0
    for chunk in chunks:
        lines = chunk.content.split("\n")
        end_line = chunk.end_line
        text = chunk.content
        limit = min(max_chunk_chars, char_budget - used)
        if limit < 300:
            break
        if len(text) > limit:
            kept, size = [], 0
            for line in lines:
                if size + len(line) + 1 > limit:
                    break
                kept.append(line)
                size += len(line) + 1
            if not kept:
                continue
            text = "\n".join(kept)
            end_line = chunk.start_line + len(kept) - 1
        blocks.append(ContextBlock(number=len(blocks) + 1, chunk=chunk, start_line=chunk.start_line,
                                   end_line=end_line, content=text))
        used += len(text)
    return blocks


def format_context(blocks: list[ContextBlock]) -> str:
    parts = []
    for block in blocks:
        fence = "```" + (block.chunk.language if block.chunk.language not in ("text", "") else "")
        parts.append(f"{block.header()}\n{fence}\n{block.content}\n```")
    return "\n\n".join(parts)


def _strip_code(text: str) -> str:
    return _INLINE.sub(" ", _FENCED.sub(" ", text))


def parse_citations(answer: str, blocks: list[ContextBlock]) -> tuple[list[dict[str, Any]], list[int]]:
    """Map [n] markers to context blocks. Returns (citations in first-use order, invalid numbers)."""
    by_number = {b.number: b for b in blocks}
    cited: list[int] = []
    invalid: list[int] = []
    for match in _CITATION.finditer(_strip_code(answer)):
        numbers: list[int] = []
        for part in re.split(r"\s*,\s*", match.group(1)):
            if re.search(r"[-–]", part):
                lo, hi = (int(x) for x in re.split(r"\s*[-–]\s*", part)[:2])
                if 0 < hi - lo < 20:
                    numbers += list(range(lo, hi + 1))
                else:
                    numbers += [lo, hi]
            else:
                numbers.append(int(part))
        for number in numbers:
            if number in by_number:
                if number not in cited:
                    cited.append(number)
            elif number not in invalid:
                invalid.append(number)
    citations = []
    for number in cited:
        block = by_number[number]
        citations.append({
            "n": number, "chunk_id": block.chunk.id, "file_path": block.chunk.file_path,
            "start_line": block.start_line, "end_line": block.end_line, "symbol": block.chunk.symbol,
        })
    return citations, invalid


# Sentence boundaries: end punctuation followed by whitespace (so "api.ts" or "Cart.total" never split) or newlines.
_SENTENCE_BREAK = re.compile(r"((?<=[.!?])\s+|\n+)")
_BACKTICK_IDENT = re.compile(r"`([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)(?:\(\))?`")


def align_citations(answer: str, blocks: list[ContextBlock]) -> tuple[str, list[str]]:
    """Re-point citation numbers that a small model slipped by one excerpt.

    A marker is moved only when its sentence names an identifier in backticks, the cited excerpt
    does not contain that identifier anywhere, and exactly one other excerpt defines it. Returns the
    corrected answer and a note per correction (shown to the user as a warning).
    """
    by_number = {b.number: b for b in blocks}
    notes: list[str] = []

    def short(symbol: str | None) -> str:
        return (symbol or "").split(".")[-1]

    def fix_sentence(sentence: str) -> str:
        names = set(_BACKTICK_IDENT.findall(sentence))
        if not names or not _CITATION.search(sentence):
            return sentence
        candidates = {b.number for b in blocks
                      if b.chunk.symbol and (b.chunk.symbol in names or short(b.chunk.symbol) in names)}

        def replace(match: re.Match) -> str:
            numbers = re.split(r"\s*,\s*", match.group(1))
            if len(numbers) != 1 or not numbers[0].isdigit():
                return match.group(0)
            number = int(numbers[0])
            block = by_number.get(number)
            if block is None or any(name.split(".")[-1] in block.content for name in names):
                return match.group(0)  # the cited code does mention what the sentence talks about
            others = candidates - {number}
            if len(others) != 1:
                return match.group(0)
            target = others.pop()
            notes.append(f"Citation [{number}] was changed to [{target}] to match "
                         f"`{by_number[target].chunk.symbol}` named in the sentence.")
            return f"[{target}]"

        return _CITATION.sub(replace, sentence)

    def fix_prose(text: str) -> str:
        parts = _SENTENCE_BREAK.split(text)  # separators are kept at odd indexes
        return "".join(part if index % 2 else fix_sentence(part) for index, part in enumerate(parts))

    pieces: list[str] = []
    position = 0
    for match in _FENCED.finditer(answer):  # never touch code blocks
        pieces.append(fix_prose(answer[position:match.start()]))
        pieces.append(match.group(0))
        position = match.end()
    pieces.append(fix_prose(answer[position:]))
    return "".join(pieces), notes


def path_mentions(answer: str) -> list[dict[str, Any]]:
    """File paths (optionally with :line or :start-end) that the answer mentions in its text."""
    found: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for match in _PATH_MENTION.finditer(answer):
        path = match.group(1).rstrip(".")  # a sentence-ending period is not part of the path
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in KNOWN_EXTENSIONS:
            continue
        start = int(match.group(2)) if match.group(2) else None
        end = int(match.group(3)) if match.group(3) else start
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        found.append({"path": path, "start_line": start, "end_line": end})
    return found


def unknown_paths(answer: str, known_paths: list[str]) -> list[str]:
    """Directory paths mentioned in the answer that match no indexed file (exactly or as a suffix).

    Bare names ("Node.js", "settings.py") are too ambiguous to flag; a path with a folder is a
    concrete claim about where code lives.
    """
    known = set(known_paths)
    missing: list[str] = []
    for mention in path_mentions(answer):
        path = mention["path"].removeprefix("./")
        if "/" not in path or path in known or path in missing:
            continue
        if any(p.endswith("/" + path) for p in known):
            continue
        missing.append(path)
    return missing


def is_not_found(answer: str) -> bool:
    """True when the model said the context does not contain the answer."""
    head = answer.strip().lstrip("#*>_ ").strip()[:240]
    return NOT_FOUND_MESSAGE.lower().rstrip(".") in answer.lower() or bool(_NOT_FOUND.search(head))
