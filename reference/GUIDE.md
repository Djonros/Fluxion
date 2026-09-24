# Подробное руководство: обучение и использование Seq2Seq

---

## ЧАСТЬ 1. ОБУЧЕНИЕ МОДЕЛИ

### Шаг 0. Установка зависимостей

```bash
pip install torch sentencepiece
```

Проверка установки:

```python
python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA:', torch.cuda.is_available())"
```

Если CUDA `False` — обучение будет на CPU (медленнее, но работает).


### Шаг 1. Подготовка данных

Нужны параллельные тексты — по одному предложению на строку.
Файлы исходного и целевого языка должны **совпадать по количеству строк**
и быть **выровнены** (строка N в `.src` = перевод строки N в `.trg`).

Создайте структуру папок:

```
проект/
├── model.py
├── train.py
├── beam_search.py
├── tokenize.py
├── example.py
└── data/
    ├── train.src      # обучающая выборка, исходный язык
    ├── train.trg      # обучающая выборка, целевой язык
    ├── valid.src      # валидационная выборка, исходный язык
    └── valid.trg      # валидационная выборка, целевой язык
```

Пример содержимого (перевод EN -> RU):

```
data/train.src:
Hello world
How are you?
Good morning

data/train.trg:
Привет мир
Как дела?
Доброе утро
```

ОБЯЗАТЕЛЬНО: количество строк в `train.src` == `train.trg`,
и `valid.src` == `valid.trg`.

Минимальный размер: от 10 000 пар предложений для заметного результата.
Для хорошего качества: 100 000+ пар.


### Шаг 2. Обучение BPE-токенизатора (один раз)

Токенизатор разбивает слова на подслова (subwords), чтобы не было
проблемы с незнакомыми словами. Обучается один раз на корпусе.

Создайте файл `prepare.py`:

```python
from tokenize import SentencePieceTokenizer

# Обучаем токенизатор для исходного языка
src_tok = SentencePieceTokenizer(model_prefix='src_sp')
src_tok.train('data/train.src')
print(f"Source vocab size: {src_tok.vocab_size}")

# Обучаем токенизатор для целевого языка
trg_tok = SentencePieceTokenizer(model_prefix='trg_sp')
trg_tok.train('data/train.trg')
print(f"Target vocab size: {trg_tok.vocab_size}")
```

Запуск:

```bash
python prepare.py
```

Результат — 4 файла:

```
src_sp.model   # модель токенизатора (нужна для работы)
src_sp.vocab   # словарь
trg_sp.model
trg_sp.vocab
```

Проверка токенизатора:

```python
from tokenize import SentencePieceTokenizer

tok = SentencePieceTokenizer(model_prefix='src_sp')
tok.load()

text = "Hello world"
print("IDs:    ", tok.encode(text))
print("Pieces: ", tok.encode_as_pieces(text))
print("Decode: ", tok.decode(tok.encode(text)))
```


### Шаг 3. Настройка параметров модели

Откройте `train.py`, найдите класс `Config`:

```python
@dataclass
class Config:
    input_dim: int = 10000       # размер словаря ИСТОЧНИКА (замените!)
    output_dim: int = 10000      # размер словаря ЦЕЛИ (замените!)
    emb_dim: int = 256           # размер эмбеддингов
    enc_hid_dim: int = 512       # скрытый размер энкодера
    dec_hid_dim: int = 512       # скрытый размер декодера
    n_layers: int = 2            # слоёв LSTM в энкодере и декодере
    enc_dropout: float = 0.5     # dropout в энкодере
    dec_dropout: float = 0.5     # dropout в декодере
    pad_idx: int = 0             # ID pad-токена (НЕ МЕНЯТЬ — задан SentencePiece)
    sos_idx: int = 2             # ID <sos> (НЕ МЕНЯТЬ)
    eos_idx: int = 3             # ID <eos> (НЕ МЕНЯТЬ)
    learning_rate: float = 1e-3  # скорость обучения
    weight_decay: float = 1e-5   # L2-регуляризация
    clip: float = 1.0            # gradient clipping
    n_epochs: int = 20           # число эпох
    teacher_forcing_ratio: float = 0.5  # доля ground-truth при обучении
    use_layer_norm: bool = True
    lr_scheduler_factor: float = 0.5     # во сколько раз уменьшать LR
    lr_scheduler_patience: int = 2       # ждать N эпох без улучшения
    checkpoint_dir: str = "checkpoints"
    freeze_embeddings: bool = True
```

Рекомендации по подбору:

| Параметр | Маленький корпус (<50k) | Средний (50k-500k) | Большой (>500k) |
|----------|------------------------|--------------------|-----------------|
| emb_dim | 128 | 256 | 300-512 |
| enc/dec_hid_dim | 256 | 512 | 1024 |
| n_layers | 1-2 | 2 | 2-3 |
| dropout | 0.5 | 0.5 | 0.3 |
| n_epochs | 20-30 | 15-20 | 10-15 |
| batch_size | 32 | 64-128 | 128-256 |


### Шаг 4. Запуск обучения

В файле `example.py` в блоке `__main__` раскомментируйте строку:

```python
if __name__ == '__main__':
    model, src_tok, trg_tok, device, config = run_training()
```

Запуск:

```bash
python example.py
```

Что происходит во время обучения (вывод в консоль):

```
Device: cuda
Обучаемых параметров: 12,847,392
Epoch: 01 | Time: 3m 42s
        Train Loss: 5.234 | Train PPL: 187.342
         Val. Loss: 4.891 |  Val. PPL: 133.184
Epoch: 02 | Time: 3m 39s
        Train Loss: 4.102 | Train PPL:  60.421
         Val. Loss: 3.856 |  Val. PPL:  47.341
...
```

- **Loss** — чем меньше, тем лучше (ошибка)
- **PPL** (perplexity) — экспонента от loss; чем ближе к 1, тем лучше
- После каждой эпохи, если val loss улучшился — сохраняется checkpoint
  в `checkpoints/best_model.pt`
- Если val loss не улучшается 2 эпохи — LR уменьшается в 2 раза


### Шаг 5. Контроль обучения

Если loss не падает:
  - уменьшите learning_rate (1e-4)
  - проверьте данные (выровнены ли пары?)
  - увеличьте размер корпуса

Если loss падает, но val loss растёт (переобучение):
  - увеличьте dropout (0.6)
  - уменьшите размер модели
  - добавьте больше данных

Если не хватает памяти (CUDA OOM):
  - уменьшите batch_size (32 -> 16)
  - уменьшите hid_dim (512 -> 256)
  - уменьшите max_len в датасете

Обучение можно прервать в любой момент — лучший checkpoint уже сохранён.


### Шаг 6. Что получилось после обучения

```
проект/
├── checkpoints/
│   └── best_model.pt    # ЛУЧШАЯ модель (сохраняется автоматически)
├── src_sp.model         # токенизатор источника
├── src_sp.vocab
├── trg_sp.model         # токенизатор цели
└── trg_sp.vocab
```

best_model.pt содержит:
  - веса модели (model_state_dict)
  - состояние оптимизатора (optimizer_state_dict)
  - номер эпохи
  - val loss

---
---

## ЧАСТЬ 2. ИСПОЛЬЗОВАНИЕ (ИНФЕРЕНС)

### Шаг 1. Загрузка обученной модели

Создайте файл `inference.py`:

```python
import torch
from model import Encoder, Decoder, Attention, Seq2Seq
from tokenize import SentencePieceTokenizer
from beam_search import beam_search_decode
from train import Config, build_model


def load_trained_model(checkpoint_path, config, device):
    """Загружает обученную модель из checkpoint."""
    model = build_model(config, device)

    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()  # ОБЯЗАТЕЛЬНО: режим оценки (выключает dropout)

    print(f"Модель загружена из {checkpoint_path}")
    print(f"Val loss: {checkpoint['valid_loss']:.3f}")
    return model


# Конфиг ДОЛЖЕН совпадать с тем, на котором обучали!
config = Config(
    input_dim=10000,       # те же значения, что при обучении
    output_dim=10000,
    emb_dim=256,
    enc_hid_dim=512,
    dec_hid_dim=512,
    n_layers=2,
    enc_dropout=0.5,       # при eval dropout всё равно выключен
    dec_dropout=0.5,
    pad_idx=0,             # SentencePiece: pad=0, bos=2, eos=3
    sos_idx=2,
    eos_idx=3,
    use_layer_norm=True,
)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Загрузка
model = load_trained_model('checkpoints/best_model.pt', config, device)

# Загрузка токенизаторов
src_tok = SentencePieceTokenizer(model_prefix='src_sp')
src_tok.load()
trg_tok = SentencePieceTokenizer(model_prefix='trg_sp')
trg_tok.load()
```


ВАЖНО: параметры в Config при загрузке должны СОВПАДАТЬ
с теми, что были при обучении (emb_dim, hid_dim, n_layers и т.д.).
Иначе — ошибка загрузки весов. dropout можно не менять — при
model.eval() он выключается автоматически.


### Шаг 2. Перевод одного предложения

```python
def translate(model, src_tok, trg_tok, sentence, device, beam_width=5):
    """Перевод одного предложения."""
    model.eval()

    # 1. Токенизация входа (добавляем <sos> и <eos>)
    src_ids = src_tok.encode(sentence, add_special_tokens=True)
    print(f"Tokens: {src_tok.encode_as_pieces(sentence)}")
    print(f"IDs:    {src_ids}")

    # 2. Преобразование в тензор [seq_len, 1] (batch_size=1)
    src_tensor = torch.tensor(src_ids, device=device).unsqueeze(1)
    src_lengths = torch.tensor([len(src_ids)], device=device)

    # 3. Beam search декодирование
    token_ids = beam_search_decode(
        model,
        src_tensor,
        src_lengths,
        beam_width=beam_width,   # больше = точнее, но медленнее
        max_len=100,             # максимум токенов на выходе
    )

    # 4. Декодирование токенов обратно в текст
    translation = trg_tok.decode(token_ids)
    return translation


# Использование:
sentence = "Hello, how are you?"
result = translate(model, src_tok, trg_tok, sentence, device, beam_width=5)
print(f"Перевод: {result}")
```


### Шаг 3. Перевод целого файла

```python
def translate_file(model, src_tok, trg_tok, input_path, output_path,
                   device, beam_width=5):
    """Перевод всех строк файла."""
    model.eval()

    with open(input_path, encoding='utf-8') as f_in, \
         open(output_path, 'w', encoding='utf-8') as f_out:

        for i, line in enumerate(f_in):
            line = line.strip()
            if not line:
                f_out.write('\n')
                continue

            translation = translate(
                model, src_tok, trg_tok, line, device, beam_width
            )
            f_out.write(translation + '\n')

            if (i + 1) % 100 == 0:
                print(f"Переведено {i + 1} строк...")

    print(f"Готово! Результат в {output_path}")


# Использование:
translate_file(
    model, src_tok, trg_tok,
    input_path='data/test.src',
    output_path='data/test.translated.trg',
    device=device,
    beam_width=5,
)
```


### Шаг 4. Интерактивный режим (чат)

```python
def interactive_translate(model, src_tok, trg_tok, device):
    """Ввод текста с клавиатуры, вывод перевода."""
    print("=== Интерактивный перевод ===")
    print("Введите текст (или 'выход' для завершения):\n")

    while True:
        text = input(">>> ").strip()
        if text.lower() in ('выход', 'exit', 'quit', 'q'):
            break
        if not text:
            continue

        translation = translate(
            model, src_tok, trg_tok, text, device, beam_width=5
        )
        print(f"Перевод: {translation}\n")


# Использование:
interactive_translate(model, src_tok, trg_tok, device)
```


### Шаг 5. Настройка beam search

```python
# beam_width (ширина луча):
#   1   — жадный режим (быстро, менее точно)
#   5   — стандарт (рекомендуется)
#   10  — точнее, но в 2 раза медленнее
#   20+ — для максимального качества

# length_penalty (нормализация по длине):
#   0.0 — предпочитает короткие фразы
#   0.6 — стандарт (сбалансировано)
#   1.0 — предпочитает длинные фразы

# max_len:
#   Достаточно установить в 2x от средней длины целевого предложения
```


---
---

## ЧАСТЬ 3. ТИПИЧНЫЕ ПРОБЛЕМЫ

### Проблема: RuntimeError при загрузке модели

```
Error(s) in loading state_dict: size mismatch for encoder.embedding.weight
```

Причина: Config при загрузке не совпадает с обученной моделью.
Решение: используйте те же emb_dim, hid_dim, n_layers, vocab sizes.
input_dim = src_tok.vocab_size, output_dim = trg_tok.vocab_size.


### Проблема: CUDA out of memory

```
RuntimeError: CUDA out of memory.
```

Решение:
  - уменьшите batch_size
  - уменьшите hid_dim (512 -> 256)
  - переводите по одному предложению (batch_size=1 при инференсе)


### Проблема: перевод — бессмысленный текст

Причины:
  - мало данных (нужно 50k+ пар минимум)
  - мало эпох (loss всё ещё высокий)
  - данные не выровнены (проверьте, что пары соответствуют)
  - токенизатор обучен на слишком малом корпусе


### Проблема: модель слишком медленно переводит

  - используйте beam_width=1 (жадный режим)
  - используйте model.greedy_decode() вместо beam search
  - проверьте, что используете GPU (torch.cuda.is_available() == True)


---
---

## ЧАСТЬ 4. КОРОТКАЯ ШПАРГАЛКА

# ОБУЧЕНИЕ (один раз):
#   1. Положить данные в data/
#   2. python prepare.py              # обучить токенизаторы
#   3. Поправить Config в example.py  # размеры словарей
#   4. python example.py              # обучить модель
#   5. Получить checkpoints/best_model.pt

# ИСПОЛЬЗОВАНИЕ (много раз):
#   1. Загрузить модель + токенизаторы
#   2. Вызвать translate(model, src_tok, trg_tok, text, device)
#   3. Получить перевод
