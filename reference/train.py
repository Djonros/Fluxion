import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.optim as optim

from model import Encoder, Decoder, Attention, Seq2Seq


@dataclass
class Config:
    input_dim: int = 10000
    output_dim: int = 10000
    emb_dim: int = 256
    enc_hid_dim: int = 512
    dec_hid_dim: int = 512
    n_layers: int = 2
    enc_dropout: float = 0.5
    dec_dropout: float = 0.5
    pad_idx: int = 0
    sos_idx: int = 1
    eos_idx: int = 2
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    clip: float = 1.0
    n_epochs: int = 20
    teacher_forcing_ratio: float = 0.5
    use_layer_norm: bool = True
    lr_scheduler_factor: float = 0.5
    lr_scheduler_patience: int = 2
    checkpoint_dir: str = "checkpoints"
    freeze_embeddings: bool = True


def initialize_weights(m):
    for name, param in m.named_parameters():
        if param.requires_grad:
            if 'weight' in name:
                nn.init.normal_(param.data, mean=0, std=0.01)
            elif 'bias' in name:
                nn.init.constant_(param.data, 0)


def build_model(config, device, pretrained_src_emb=None, pretrained_trg_emb=None):
    attn = Attention(config.enc_hid_dim, config.dec_hid_dim)

    encoder = Encoder(
        input_dim=config.input_dim,
        emb_dim=config.emb_dim,
        enc_hid_dim=config.enc_hid_dim,
        dec_hid_dim=config.dec_hid_dim,
        n_layers=config.n_layers,
        dropout=config.enc_dropout,
        pad_idx=config.pad_idx,
        pretrained_embeddings=pretrained_src_emb,
        freeze_embeddings=config.freeze_embeddings,
    )

    decoder = Decoder(
        output_dim=config.output_dim,
        emb_dim=config.emb_dim,
        enc_hid_dim=config.enc_hid_dim,
        dec_hid_dim=config.dec_hid_dim,
        n_layers=config.n_layers,
        dropout=config.dec_dropout,
        pad_idx=config.pad_idx,
        attention=attn,
        use_layer_norm=config.use_layer_norm,
        pretrained_embeddings=pretrained_trg_emb,
        freeze_embeddings=config.freeze_embeddings,
    )

    model = Seq2Seq(
        encoder, decoder, device,
        config.pad_idx, config.sos_idx, config.eos_idx
    )
    model = model.to(device)

    model.apply(initialize_weights)

    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Обучаемых параметров: {num_params:,}")

    return model


def train_epoch(model, iterator, optimizer, criterion, clip,
                teacher_forcing_ratio, device):
    model.train()
    epoch_loss = 0

    for src, src_lengths, trg in iterator:
        src = src.to(device)
        src_lengths = src_lengths.to(device)
        trg = trg.to(device)

        optimizer.zero_grad()

        output = model(src, src_lengths, trg, teacher_forcing_ratio)
        #output = [trg_len, batch_size, output_dim]

        output_dim = output.shape[-1]
        output = output[1:].reshape(-1, output_dim)
        trg = trg[1:].reshape(-1)

        loss = criterion(output, trg)
        loss.backward()

        #Gradient clipping — критично для RNN
        torch.nn.utils.clip_grad_norm_(model.parameters(), clip)

        optimizer.step()

        epoch_loss += loss.item()

    return epoch_loss / len(iterator)


def evaluate(model, iterator, criterion, device):
    model.eval()
    epoch_loss = 0

    with torch.no_grad():
        for src, src_lengths, trg in iterator:
            src = src.to(device)
            src_lengths = src_lengths.to(device)
            trg = trg.to(device)

            output = model(src, src_lengths, trg, teacher_forcing_ratio=0)
            #output = [trg_len, batch_size, output_dim]

            output_dim = output.shape[-1]
            output = output[1:].reshape(-1, output_dim)
            trg = trg[1:].reshape(-1)

            loss = criterion(output, trg)
            epoch_loss += loss.item()

    return epoch_loss / len(iterator)


def epoch_time(start_time, end_time):
    elapsed = end_time - start_time
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    return mins, secs


def train(config, train_iter, valid_iter, device,
          pretrained_src_emb=None, pretrained_trg_emb=None):
    model = build_model(config, device, pretrained_src_emb, pretrained_trg_emb)

    optimizer = optim.Adam(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    criterion = nn.CrossEntropyLoss(ignore_index=config.pad_idx)

    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode='min',
        factor=config.lr_scheduler_factor,
        patience=config.lr_scheduler_patience,
    )

    checkpoint_dir = Path(config.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    best_valid_loss = float('inf')

    for epoch in range(config.n_epochs):
        start_time = time.time()

        train_loss = train_epoch(
            model, train_iter, optimizer, criterion,
            config.clip, config.teacher_forcing_ratio, device
        )

        valid_loss = evaluate(model, valid_iter, criterion, device)

        scheduler.step(valid_loss)

        end_time = time.time()
        mins, secs = epoch_time(start_time, end_time)

        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'valid_loss': valid_loss,
            }, checkpoint_dir / 'best_model.pt')

        print(f"Epoch: {epoch+1:02} | Time: {mins}m {secs}s")
        print(f"\tTrain Loss: {train_loss:.3f} | Train PPL: {math.exp(train_loss):7.3f}")
        print(f"\t Val. Loss: {valid_loss:.3f} |  Val. PPL: {math.exp(valid_loss):7.3f}")

    return model


def load_checkpoint(model, optimizer, checkpoint_path, device):
    checkpoint = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    return checkpoint['epoch'], checkpoint['valid_loss']


def load_pretrained_embeddings(path, vocab, emb_dim):
    """Загружает предобученные векторы (GloVe / FastText / Word2Vec).

    Args:
        path: путь к файлу с векторами (формат: 'word v1 v2 ...').
        vocab: dict {word: index}.
        emb_dim: размерность эмбеддингов.

    Returns:
        torch.FloatTensor [vocab_size, emb_dim].
    """
    vectors = torch.randn(len(vocab), emb_dim) * 0.01
    found = 0

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip().split(' ')
            word = parts[0]
            if word in vocab:
                idx = vocab[word]
                vec = torch.tensor(
                    [float(x) for x in parts[1:]], dtype=torch.float32
                )
                if vec.shape[0] == emb_dim:
                    vectors[idx] = vec
                    found += 1

    print(f"Найдено эмбеддингов: {found}/{len(vocab)} ({100*found/len(vocab):.1f}%)")
    return vectors


if __name__ == '__main__':
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    config = Config()

    # Замените на реальные data-итераторы
    # train_iter, valid_iter = ...
    # model = train(config, train_iter, valid_iter, device)
    print("Подготовьте data-итераторы и вызовите train(config, train_iter, valid_iter, device)")
