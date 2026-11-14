"""Does a chat message ask to change files? Rules, no model: checked before the message is sent.

With the agent off, a request like «создай директории backend/, frontend/… добавь .gitignore» went to
the chat model, which can only answer in words: Fluxion "worked idle" (2026-11-14). The window asks
first: «Для этой задачи нужно менять файлы — включите агента».

A request is "files" when it has an action on files (create, add, fix, change, delete, rename, move,
write into, refactor…) and something of a project to act on (a file, a folder, code, a module, a test,
a path or a file name with an extension, README, .gitignore…). A question about how to do it
(«как создать файл», «объясни, что такое .gitignore», «why does main.py fail?») is not.
"""
from __future__ import annotations

import re

# the verb of a request to act (Russian imperative and «нужно/надо + infinitive», English)
_ACTION = re.compile(
    r"(?<![\w])("
    r"созда(?:й|йте|ть)|сдела(?:й|йте|ть)|добав(?:ь|ьте|ить)|допиш(?:и|ите)|дописать|"
    r"исправ(?:ь|ьте|ить)|поправ(?:ь|ьте|ить)|почин(?:и|ите|ить)|"
    r"измен(?:и|ите|ить)|поменя(?:й|йте|ть)|замен(?:и|ите|ить)|обнов(?:и|ите|ить)|"
    r"удал(?:и|ите|ить)|переименуй(?:те)?|переименовать|перенес(?:и|ите|ти)|перемест(?:и|ите|ить)|"
    r"перепиш(?:и|ите)|переписать|напиш(?:и|ите)|написать|запиш(?:и|ите)|записать|"
    r"сгенерируй(?:те)?|сгенерировать|рефактор\w*|отрефактор\w*|внес(?:и|ите|ти)\s+правк|"
    r"разлож(?:и|ите|ить)|вынес(?:и|ите|ти)|"
    r"create|make|add|fix|edit|change|update|modify|delete|remove|rename|move|write|generate|refactor|"
    r"scaffold|implement|set\s+up"
    r")(?![\w])",
    re.IGNORECASE,
)

# what the action is about: things of a project on disk
_OBJECT = re.compile(
    r"(?<![\w])("
    r"файл\w*|папк\w*|директори\w*|каталог\w*|модул\w*|класс\w*|функци\w*|метод\w*|код\w*|"
    r"скрипт\w*|тест\w*|проект\w*|структур\w*|репозитори\w*|конфиг\w*|readme|gitignore|"
    r"file|files|folder|folders|director(?:y|ies)|module|class|function|method|code|script|tests?|"
    r"project|structure|repo|repository|config"
    r")(?![\w])"
    # a path or a file name: backend/, src\app, main.py, .gitignore, docs/architecture.md
    r"|(?:^|[\s«\"'(`])(?:\.?[\w-]+[\\/])+(?=[\s,.;:»\"')`]|$)"
    r"|(?:^|[\s«\"'(`])(?:[\w-]+[\\/])*\.?[\w-]+\.(?:py|js|ts|tsx|jsx|json|ya?ml|toml|ini|cfg|md|txt|html|css|sql|sh|bat|ps1|"
    r"c|cpp|h|hpp|cs|java|go|rs|kt|php|rb|xml|csv)(?![\w])"
    r"|(?:^|[\s«\"'(`])\.(?:gitignore|env|editorconfig|dockerignore)(?![\w])",
    re.IGNORECASE,
)

# the message asks how / what / why: an explanation, not a change
_QUESTION_START = re.compile(
    r"^\s*(как|каким\s+образом|что\s+такое|что\s+значит|что\s+делает|почему|зачем|объясни\w*|расскажи\w*|"
    r"подскажи\w*|можно\s+ли|стоит\s+ли|чем\s+отличается|в\s+чём|в\s+чем|"
    r"how|what|why|when|where|which|explain|describe|tell\s+me|is\s+it|should\s+i|can\s+i|does|do\s+i)\b",
    re.IGNORECASE,
)
# «нужно/надо/необходимо создать…» is a request, not a question
_NEED = re.compile(r"(?<![\w])(нужно|надо|необходимо|требуется|прошу|please|need\s+to|i\s+want\s+you\s+to)(?![\w])",
                   re.IGNORECASE)
# the request asks for code in the answer, to copy: the chat does that well
_IN_ANSWER = re.compile(r"(?<![\w])(пример\w*|покажи|в\s+ответе|example|show\s+me)(?![\w])", re.IGNORECASE)


def needs_files(text: str) -> bool:
    """True when *text* asks to create or change files or folders of the project."""
    text = (text or "").strip()
    if len(text) < 6 or text.startswith("/"):  # «/компас …», «/расчёт …»: commands of their own
        return False
    first = text.splitlines()[0]
    if (_QUESTION_START.match(first) or first.rstrip().endswith("?")) and not _NEED.search(first):
        return False
    if _IN_ANSWER.search(first) and not _NEED.search(first):
        return False
    return bool(_ACTION.search(text)) and bool(_OBJECT.search(text))
