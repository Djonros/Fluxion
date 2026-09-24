"""
Полный пример использования Seq2Seq с attention, BPE-токенизацией,
обучением и инференсом через beam search.

Установка зависимостей:
    pip install torch sentencepiece

Подготовьте данные:
    - data/train.src — исходные предложения (по одному на строку)
    - data/train.trg — целевые предложения (по одному на строку)
    - data/valid.src, data/valid.trg — аналогично для валидации
"""

import torch
from torch.utils.data import Dataset, DataLoader

from model import Encoder, Decoder, Attention, Seq2Seq
from beam_search import beam_search_decode
from tokenize import SentencePieceTokenizer
from train import Config, build_model, train, load_checkpoint


# ============================================================
# 1. ОБУЧЕНИЕ ТОКЕНАЙЗЕРА (запускается один раз)
# ============================================================
def train_tokenizers():
    src_tok = SentencePieceTokenizer(model_prefix='src_sp')
    trg_tok = SentencePieceTokenizer(model_prefix='trg_sp')

    src_tok.train('data/train.src')
    trg_tok.train('data/train.trg')

    print(f"Source vocab: {src_tok.vocab_size}")
    print(f"Target vocab: {trg_tok.vocab_size}")
    return src_tok, trg_tok


# ============================================================
# 2. DATASET
# ============================================================
class TranslationDataset(Dataset):
    def __init__(self, src_path, trg_path, src_tok, trg_tok, max_len=100):
        self.src_tok = src_tok
        self.trg_tok = trg_tok
        self.max_len = max_len
        self.data = []

        with open(src_path, encoding='utf-8') as f_src, \
             open(trg_path, encoding='utf-8') as f_trg:
            for src_line, trg_line in zip(f_src, f_trg):
                src_line = src_line.strip()
                trg_line = trg_line.strip()
                if not src_line or not trg_line:
                    continue
                src_ids = src_tok.encode(src_line, add_special_tokens=True)
                trg_ids = trg_tok.encode(trg_line, add_special_tokens=True)
                if len(src_ids) <= max_len and len(trg_ids) <= max_len:
                    self.data.append((src_ids, trg_ids))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        src_ids, trg_ids = self.data[idx]
        return torch.tensor(src_ids), torch.tensor(trg_ids)


def make_collate_fn(pad_idx):
    from torch.nn.utils.rnn import pad_sequence

    def collate(batch):
        srcs, trgs = zip(*batch)
        src_lengths = torch.tensor([len(s) for s in srcs])
        srcs = pad_sequence(srcs, padding_value=pad_idx)
        trgs = pad_sequence(trgs, padding_value=pad_idx)
        return srcs, src_lengths, trgs

    return collate


# ============================================================
# 3. ПОЛНЫЙ ПРИМЕР: ОБУЧЕНИЕ
# ============================================================
def run_training():
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")

    # Шаг 1: токенизаторы (расскомментируйте при первом запуске)
    # src_tok, trg_tok = train_tokenizers()
    src_tok = SentencePieceTokenizer(model_prefix='src_sp')
    trg_tok = SentencePieceTokenizer(model_prefix='trg_sp')
    src_tok.load()
    trg_tok.load()

    # Шаг 2: датасеты и data loaders
    train_ds = TranslationDataset('data/train.src', 'data/train.trg',
                                  src_tok, trg_tok)
    valid_ds = TranslationDataset('data/valid.src', 'data/valid.trg',
                                  src_tok, trg_tok)

    collate = make_collate_fn(pad_idx=src_tok.pad_id)

    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True,
                              collate_fn=collate)
    valid_loader = DataLoader(valid_ds, batch_size=64, shuffle=False,
                              collate_fn=collate)

    # Шаг 3: конфигурация
    config = Config(
        input_dim=src_tok.vocab_size,
        output_dim=trg_tok.vocab_size,
        emb_dim=256,
        enc_hid_dim=512,
        dec_hid_dim=512,
        n_layers=2,
        enc_dropout=0.5,
        dec_dropout=0.5,
        pad_idx=src_tok.pad_id,
        sos_idx=src_tok.sos_id,
        eos_idx=src_tok.eos_id,
        learning_rate=1e-3,
        clip=1.0,
        n_epochs=20,
    )

    # Шаг 4: обучение
    model = train(config, train_loader, valid_loader, device)
    return model, src_tok, trg_tok, device, config


# ============================================================
# 4. ИНФЕРЕНС: ПЕРЕВОД ОДНОГО ПРЕДЛОЖЕНИЯ
# ============================================================
def translate(model, src_tok, trg_tok, sentence, device, beam_width=5):
    """Перевод одного предложения через beam search."""
    model.eval()

    # Токенизация входа
    src_ids = src_tok.encode(sentence, add_special_tokens=True)
    src_tensor = torch.tensor(src_ids, device=device).unsqueeze(1)
    #src_tensor = [src_len, 1]
    src_lengths = torch.tensor([len(src_ids)], device=device)

    # Beam search
    token_ids = beam_search_decode(
        model, src_tensor, src_lengths,
        beam_width=beam_width,
        max_len=100,
    )

    # Декодирование в текст
    text = trg_tok.decode(token_ids)
    return text


# ============================================================
# 5. ЗАГРУЗКА СОХРАНЁННОЙ МОДЕЛИ
# ============================================================
def load_model(checkpoint_path, config, device):
    """Загружает модель из checkpoint без оптимизатора."""
    model = build_model(config, device)

    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()

    print(f"Модель загружена. Val loss: {checkpoint['valid_loss']:.3f}")
    return model


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    # --- Вариант A: обучение с нуля ---
    # model, src_tok, trg_tok, device, config = run_training()

    # --- Вариант B: загрузка готовой модели ---
    # config = Config(
    #     input_dim=10000, output_dim=10000,
    #     pad_idx=0, sos_idx=2, eos_idx=3,
    # )
    # model = load_model('checkpoints/best_model.pt', config, device)

    # --- Перевод ---
    # result = translate(model, src_tok, trg_tok, "Hello world", device)
    # print(result)

    print("Расскомментируйте нужные строки в __main__ и подготовьте данные в data/")
