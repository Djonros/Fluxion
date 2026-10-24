"""Dataset from the user's documents (finetune/doc_dataset.py)."""
from __future__ import annotations

import json

import pytest

from finetune import doc_dataset as dd
from finetune.dataset_loader import check_dataset, load_jsonl

PARAGRAPH = (
    "Сварные швы конструкций выполняют по ГОСТ 14771-76. Катет углового шва принимают "
    "равным наименьшей толщине свариваемых элементов, но не более 12 мм. "
)


class FakeBackend:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, messages, gen=None):
        self.calls.append((messages, gen))
        reply = self.replies.pop(0) if self.replies else '{"pairs": []}'
        if isinstance(reply, Exception):
            raise reply
        return reply


def pairs_json(*items):
    return json.dumps({"pairs": [{"question": q, "answer": a} for q, a in items]}, ensure_ascii=False)


def test_split_fragments_respects_limit_and_drops_short():
    text = "\n\n".join([PARAGRAPH * 3] * 10 + ["Оглавление"])
    fragments = dd.split_fragments(text, max_chars=1000)
    assert fragments and all(len(f) <= 1000 for f in fragments)
    assert all(len(f) >= dd.MIN_FRAGMENT_CHARS for f in fragments)
    assert not any(f == "Оглавление" for f in fragments)


def test_split_fragments_cuts_long_paragraph():
    fragments = dd.split_fragments(PARAGRAPH * 50, max_chars=800)
    assert len(fragments) > 3 and all(len(f) <= 800 for f in fragments)


def test_parse_pairs_tolerates_noise_and_drops_bad_items():
    raw = "Вот ответ:\n" + pairs_json(
        ("Какой катет углового шва?", "Равен наименьшей толщине, но не более 12 мм."),
        ("?", "слишком коротко"),
    )
    pairs = dd.parse_pairs(raw, "doc.txt")
    assert [p.question for p in pairs] == ["Какой катет углового шва?"]
    assert pairs[0].source == "doc.txt"
    assert dd.parse_pairs("не JSON") == []


def test_dedupe_ignores_case_and_punctuation():
    a = dd.QAPair("Какой катет шва?", "ответ " * 5)
    b = dd.QAPair("какой  катет шва", "другой ответ " * 3)
    assert dd.dedupe([a, b]) == [a]


def test_build_pairs_and_save_make_a_trainable_dataset(tmp_path):
    (tmp_path / "нормы.txt").write_text("\n\n".join([PARAGRAPH * 4] * 2), encoding="utf-8")
    (tmp_path / "пусто.txt").write_text("титул", encoding="utf-8")
    (tmp_path / "картинка.png").write_bytes(b"x")
    backend = FakeBackend([
        pairs_json(("Какой катет углового шва?", "Равен наименьшей толщине, но не более 12 мм.")),
        RuntimeError("timeout"),
        pairs_json(("По какому ГОСТ выполняют швы?", "По ГОСТ 14771-76 для дуговой сварки в защитном газе.")),
    ])
    files = dd.collect_files([tmp_path])
    assert [f.name for f in files] == ["нормы.txt", "пусто.txt"]
    logs = []
    pairs = dd.build_pairs(files, backend, per_fragment=2, log=logs.append)
    assert backend.calls and backend.calls[0][1].json_schema == dd.SCHEMA
    assert len(pairs) >= 1
    assert any("пусто.txt" in line for line in logs)

    pairs.append(dd.QAPair("Снятая пара?", "не должна попасть в датасет", keep=False))
    out = tmp_path / "out" / "ds.jsonl"
    assert dd.save_jsonl(pairs, out) == len(pairs) - 1
    assert check_dataset(out) == ""
    rows = load_jsonl(out)
    assert len(rows) == len(pairs) - 1


def test_build_pairs_stops_and_limits(tmp_path):
    (tmp_path / "a.md").write_text("\n\n".join([PARAGRAPH * 8] * 6), encoding="utf-8")
    backend = FakeBackend([])
    dd.build_pairs(dd.collect_files([tmp_path]), backend, max_fragments=2)
    assert len(backend.calls) == 2
    backend = FakeBackend([])
    dd.build_pairs(dd.collect_files([tmp_path]), backend, stop_requested=lambda: True)
    assert backend.calls == []


def test_no_text_and_dead_model_raise(tmp_path):
    with pytest.raises(dd.DocDatasetError):
        dd.build_pairs([], FakeBackend([]))
    (tmp_path / "a.txt").write_text("\n\n".join([PARAGRAPH * 8] * 6), encoding="utf-8")
    with pytest.raises(dd.DocDatasetError):
        dd.build_pairs(dd.collect_files([tmp_path]), FakeBackend([RuntimeError("нет связи")] * 10))


def test_draft_round_trip(tmp_path):
    pairs = [dd.QAPair("Вопрос один?", "Ответ " * 5, "a.txt", keep=False)]
    path = tmp_path / "sub" / "draft.json"
    dd.save_draft(pairs, path)
    assert dd.load_draft(path) == pairs
