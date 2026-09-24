"""AST-aware chunker using tree-sitter for Python; fallback for other files."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# -- Tree-sitter bootstrap (handles API differences across versions) ---------

_parser = None
_TS_AVAILABLE = False

try:
    import tree_sitter_python as tsp  # type: ignore
    from tree_sitter import Language, Parser  # type: ignore

    try:
        _PY_LANG = Language(tsp.language())
    except TypeError:
        _PY_LANG = tsp.language()

    try:
        _parser = Parser(_PY_LANG)
    except TypeError:
        _parser = Parser()
        _parser.set_language(_PY_LANG)

    _TS_AVAILABLE = True
except Exception:
    logger.debug("tree-sitter not available, using fallback chunker")
    _PY_LANG = None


@dataclass
class Chunk:
    content: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str  # "function", "class", "module", "text"
    name: str = ""
    language: str = "python"
    metadata: dict = field(default_factory=dict)


class TreeSitterChunker:
    """Chunks Python source into AST nodes (functions/classes); other files by size."""

    PY_NODE_TYPES = {"function_definition", "class_definition"}

    def __init__(self, max_chars: int = 1500, overlap: int = 200):
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk_file(self, path: Path) -> list[Chunk]:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        ext = Path(path).suffix.lower()
        if ext == ".py" and _TS_AVAILABLE:
            return self._chunk_python(text, str(path))
        return self._chunk_text(text, str(path), language=self._ext_lang(ext))

    def chunk_text(self, text: str, file_path: str = "<string>", language: str = "python") -> list[Chunk]:
        if language == "python" and _TS_AVAILABLE:
            return self._chunk_python(text, file_path)
        return self._chunk_text(text, file_path, language=language)

    # -- Python AST chunking ------------------------------------------------

    def _chunk_python(self, source: str, file_path: str) -> list[Chunk]:
        tree = _parser.parse(source.encode("utf-8"))
        root = tree.root_node
        chunks: list[Chunk] = []
        consumed_spans: list[tuple[int, int]] = []

        top_nodes = [n for n in root.children if n.type in self.PY_NODE_TYPES]

        if not top_nodes:
            return self._chunk_text(source, file_path, language="python")

        for node in top_nodes:
            start_byte = node.start_byte
            end_byte = node.end_byte
            content = source[start_byte:end_byte]
            name = self._extract_name(node, source)
            chunks.append(
                Chunk(
                    content=content.strip(),
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    node_type=("function" if node.type == "function_definition" else "class"),
                    name=name,
                    language="python",
                )
            )
            consumed_spans.append((node.start_byte, node.end_byte))

        # Capture module-level code (imports, constants, etc.) between consumed nodes
        consumed_spans.sort()
        prev_end = 0
        module_fragments: list[str] = []
        module_start_line = 1
        for start_byte, end_byte in consumed_spans:
            fragment = source[prev_end:start_byte]
            if fragment.strip():
                lines = fragment.count("\n")
                module_fragments.append(fragment.strip())
            prev_end = end_byte
        trailing = source[prev_end:]
        if trailing.strip():
            module_fragments.append(trailing.strip())

        if module_fragments:
            module_text = "\n\n".join(module_fragments)
            for sub in self._split_by_size(module_text, self.max_chars, self.overlap):
                chunks.append(
                    Chunk(
                        content=sub,
                        file_path=file_path,
                        start_line=module_start_line,
                        end_line=module_start_line + sub.count("\n"),
                        node_type="module",
                        name="",
                        language="python",
                    )
                )

        return self._merge_small(chunks)

    # -- Text fallback -------------------------------------------------------

    def _chunk_text(self, source: str, file_path: str, language: str = "text") -> list[Chunk]:
        results: list[Chunk] = []
        for i, piece in enumerate(self._split_by_size(source, self.max_chars, self.overlap)):
            results.append(
                Chunk(
                    content=piece,
                    file_path=file_path,
                    start_line=source[: source.index(piece)].count("\n") + 1 if piece in source else 1,
                    end_line=source[: source.index(piece)].count("\n") + 1 + piece.count("\n") if piece in source else 1 + piece.count("\n"),
                    node_type="text",
                    name="",
                    language=language,
                )
            )
        return results

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _extract_name(node, source: str) -> str:
        for child in node.children:
            if child.type == "identifier":
                return source[child.start_byte : child.end_byte]
        return ""

    @staticmethod
    def _split_by_size(text: str, max_chars: int, overlap: int) -> list[str]:
        if len(text) <= max_chars:
            return [text] if text.strip() else []
        pieces: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            piece = text[start:end]
            if piece.strip():
                pieces.append(piece)
            if end >= len(text):
                break
            start = end - overlap
            if start <= 0:
                start = end
        return pieces

    def _merge_small(self, chunks: list[Chunk]) -> list[Chunk]:
        """Merge consecutive small chunks of the same type to reduce fragment count."""
        if not chunks:
            return chunks
        merged: list[Chunk] = []
        for ch in chunks:
            if (
                merged
                and merged[-1].node_type == ch.node_type
                and len(merged[-1].content) + len(ch.content) < self.max_chars
            ):
                prev = merged[-1]
                prev.content = prev.content + "\n\n" + ch.content
                prev.end_line = ch.end_line
            else:
                merged.append(ch)
        return merged

    @staticmethod
    def _ext_lang(ext: str) -> str:
        mapping = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".md": "markdown", ".txt": "text", ".rst": "rst",
            ".java": "java", ".go": "go", ".rs": "rust", ".c": "c", ".cpp": "cpp",
        }
        return mapping.get(ext, "text")
