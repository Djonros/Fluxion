import sentencepiece as spm


class SentencePieceTokenizer:
    """Обёртка над SentencePiece для BPE subword-токенизации.

    BPE (Byte Pair Encoding) эффективнее пословной токенизации:
    - нет проблем с OOV (незнакомыми словами);
    - лучше работает с богатыми морфологией и опечатками.

    Установка: pip install sentencepiece

    Специальные токены:
        pad_id=0, unk_id=1, bos_id=2 (<sos>), eos_id=3 (<eos>)
    """

    def __init__(self, model_prefix, train_vocab_size=8000,
                 model_type='bpe', character_coverage=0.9995):
        self.model_prefix = model_prefix
        self.train_vocab_size = train_vocab_size
        self.model_type = model_type
        self.character_coverage = character_coverage
        self.sp = None

    def train(self, corpus_file):
        """Обучает BPE-модель на текстовом корпусе.

        Args:
            corpus_file: путь к текстовому файлу (одна строка = одно предложение).
        """
        spm.SentencePieceTrainer.train(
            input=corpus_file,
            model_prefix=self.model_prefix,
            vocab_size=self.train_vocab_size,
            model_type=self.model_type,
            character_coverage=self.character_coverage,
            pad_id=0,
            unk_id=1,
            bos_id=2,
            eos_id=3,
        )
        self.load()

    def load(self):
        """Загружает обученную модель."""
        self.sp = spm.SentencePieceProcessor()
        self.sp.load(f"{self.model_prefix}.model")

    def encode(self, text, add_special_tokens=False):
        """Кодирует строку в список ID токенов.

        Args:
            text: входной текст.
            add_special_tokens: если True, добавляет <sos> и <eos>.

        Returns:
            list[int] — список ID.
        """
        if self.sp is None:
            self.load()

        ids = self.sp.encode(text)

        if add_special_tokens:
            ids = [self.sos_id] + ids + [self.eos_id]

        return ids

    def decode(self, ids):
        """Декодирует список ID обратно в строку."""
        if self.sp is None:
            self.load()
        return self.sp.decode(ids)

    def encode_as_pieces(self, text):
        """Возвращает subword-фрагменты вместо ID."""
        if self.sp is None:
            self.load()
        return self.sp.encode_as_pieces(text)

    @property
    def vocab_size(self):
        if self.sp is None:
            self.load()
        return self.sp.get_piece_size()

    @property
    def pad_id(self):
        return 0

    @property
    def unk_id(self):
        return 1

    @property
    def sos_id(self):
        return 2

    @property
    def eos_id(self):
        return 3
