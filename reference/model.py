import random

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence


class Encoder(nn.Module):
    """Bidirectional LSTM-энкодер с packing padded sequences."""

    def __init__(self, input_dim, emb_dim, enc_hid_dim, dec_hid_dim,
                 n_layers, dropout, pad_idx, pretrained_embeddings=None,
                 freeze_embeddings=True):
        super().__init__()
        self.n_layers = n_layers
        self.enc_hid_dim = enc_hid_dim

        self.embedding = nn.Embedding(input_dim, emb_dim, padding_idx=pad_idx)
        if pretrained_embeddings is not None:
            self.embedding.weight.data.copy_(pretrained_embeddings)
            self.embedding.weight.requires_grad = not freeze_embeddings

        self.rnn = nn.LSTM(
            emb_dim,
            enc_hid_dim,
            n_layers,
            dropout=dropout if n_layers > 1 else 0,
            bidirectional=True,
        )
        self.fc_hidden = nn.Linear(enc_hid_dim * 2, dec_hid_dim)
        self.fc_cell = nn.Linear(enc_hid_dim * 2, dec_hid_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, src, src_lengths):
        #src = [src_len, batch_size]

        embedded = self.dropout(self.embedding(src))
        #embedded = [src_len, batch_size, emb_dim]

        packed = pack_padded_sequence(
            embedded, src_lengths.cpu(), enforce_sorted=False
        )
        packed_outputs, (hidden, cell) = self.rnn(packed)
        outputs, _ = pad_packed_sequence(packed_outputs)
        #outputs = [src_len, batch_size, enc_hid_dim * 2]

        hidden = self._reshape_hidden(hidden)
        cell = self._reshape_hidden(cell)
        #hidden = [n_layers, batch_size, enc_hid_dim * 2]

        hidden = torch.tanh(self.fc_hidden(hidden))
        cell = torch.tanh(self.fc_cell(hidden))
        #hidden = [n_layers, batch_size, dec_hid_dim]

        return outputs, hidden, cell

    def _reshape_hidden(self, h):
        #h = [n_layers * 2, batch_size, enc_hid_dim]
        h = h.contiguous()
        h = h.view(self.n_layers, 2, h.shape[1], h.shape[2])
        #h = [n_layers, 2(dir), batch_size, enc_hid_dim]
        h = torch.cat([h[:, 0, :, :], h[:, 1, :, :]], dim=2)
        #h = [n_layers, batch_size, enc_hid_dim * 2]
        return h


class Attention(nn.Module):
    """Bahdanau (additive) attention."""

    def __init__(self, enc_hid_dim, dec_hid_dim):
        super().__init__()
        self.attn = nn.Linear(enc_hid_dim * 2 + dec_hid_dim, dec_hid_dim)
        self.v = nn.Linear(dec_hid_dim, 1, bias=False)

    def forward(self, hidden, encoder_outputs, mask):
        #hidden = [batch_size, dec_hid_dim]
        #encoder_outputs = [src_len, batch_size, enc_hid_dim * 2]
        #mask = [batch_size, src_len]

        src_len = encoder_outputs.shape[0]

        hidden_expanded = hidden.unsqueeze(0).expand(src_len, -1, -1)
        #hidden_expanded = [src_len, batch_size, dec_hid_dim]

        energy = torch.tanh(
            self.attn(torch.cat((hidden_expanded, encoder_outputs), dim=2))
        )
        #energy = [src_len, batch_size, dec_hid_dim]

        attention = self.v(energy).squeeze(2)
        #attention = [src_len, batch_size]
        attention = attention.permute(1, 0)
        #attention = [batch_size, src_len]

        attention = attention.masked_fill(mask == 0, -1e10)

        return F.softmax(attention, dim=1)


class Decoder(nn.Module):
    """LSTM-декодер с attention и опциональным LayerNorm."""

    def __init__(self, output_dim, emb_dim, enc_hid_dim, dec_hid_dim,
                 n_layers, dropout, pad_idx, attention,
                 use_layer_norm=True, pretrained_embeddings=None,
                 freeze_embeddings=True):
        super().__init__()
        self.output_dim = output_dim
        self.n_layers = n_layers
        self.attention = attention
        self.use_layer_norm = use_layer_norm

        self.embedding = nn.Embedding(output_dim, emb_dim, padding_idx=pad_idx)
        if pretrained_embeddings is not None:
            self.embedding.weight.data.copy_(pretrained_embeddings)
            self.embedding.weight.requires_grad = not freeze_embeddings

        self.rnn = nn.LSTM(
            enc_hid_dim * 2 + emb_dim,
            dec_hid_dim,
            n_layers,
            dropout=dropout if n_layers > 1 else 0,
        )

        self.layer_norm = nn.LayerNorm(dec_hid_dim) if use_layer_norm else None

        self.fc_out = nn.Linear(enc_hid_dim * 2 + dec_hid_dim + emb_dim, output_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, input_token, hidden, cell, encoder_outputs, mask):
        #input_token = [batch_size]
        #hidden = [n_layers, batch_size, dec_hid_dim]
        #cell = [n_layers, batch_size, dec_hid_dim]
        #encoder_outputs = [src_len, batch_size, enc_hid_dim * 2]
        #mask = [batch_size, src_len]

        input_token = input_token.unsqueeze(0)
        #input_token = [1, batch_size]

        embedded = self.dropout(self.embedding(input_token))
        #embedded = [1, batch_size, emb_dim]

        attn_weights = self.attention(hidden[-1], encoder_outputs, mask)
        #attn_weights = [batch_size, src_len]

        attn_weights_expanded = attn_weights.unsqueeze(1)
        #attn_weights_expanded = [batch_size, 1, src_len]

        encoder_outputs_perm = encoder_outputs.permute(1, 0, 2)
        #encoder_outputs_perm = [batch_size, src_len, enc_hid_dim * 2]

        context = torch.bmm(attn_weights_expanded, encoder_outputs_perm)
        #context = [batch_size, 1, enc_hid_dim * 2]
        context = context.permute(1, 0, 2)
        #context = [1, batch_size, enc_hid_dim * 2]

        rnn_input = torch.cat((embedded, context), dim=2)
        #rnn_input = [1, batch_size, emb_dim + enc_hid_dim * 2]

        output, (hidden, cell) = self.rnn(rnn_input, (hidden, cell))
        #output = [1, batch_size, dec_hid_dim]

        if self.use_layer_norm:
            output = self.layer_norm(output)

        output_cat = torch.cat((output, context, embedded), dim=2).squeeze(0)
        #output_cat = [batch_size, dec_hid_dim + enc_hid_dim * 2 + emb_dim]

        prediction = self.fc_out(output_cat)
        #prediction = [batch_size, output_dim]

        return prediction, hidden, cell, attn_weights


class Seq2Seq(nn.Module):
    """Seq2Seq модель с attention, masking и packing."""

    def __init__(self, encoder, decoder, device, pad_idx, sos_idx, eos_idx):
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.device = device
        self.pad_idx = pad_idx
        self.sos_idx = sos_idx
        self.eos_idx = eos_idx

    def create_mask(self, src):
        #src = [src_len, batch_size]
        mask = (src != self.pad_idx).permute(1, 0)
        #mask = [batch_size, src_len]
        return mask

    def forward(self, src, src_lengths, trg, teacher_forcing_ratio=0.5):
        #src = [src_len, batch_size]
        #trg = [trg_len, batch_size]

        batch_size = src.shape[1]
        trg_len = trg.shape[0]
        trg_vocab_size = self.decoder.output_dim

        outputs = torch.zeros(trg_len, batch_size, trg_vocab_size).to(self.device)

        mask = self.create_mask(src)

        encoder_outputs, hidden, cell = self.encoder(src, src_lengths)

        input_token = trg[0, :]

        for t in range(1, trg_len):
            output, hidden, cell, _ = self.decoder(
                input_token, hidden, cell, encoder_outputs, mask
            )
            outputs[t] = output
            teacher_force = random.random() < teacher_forcing_ratio
            top1 = output.argmax(1)
            input_token = trg[t] if teacher_force else top1

        return outputs

    @torch.no_grad()
    def greedy_decode(self, src, src_lengths, max_len):
        """Жадный декодер для инференса (batch_size=1)."""
        self.eval()

        mask = self.create_mask(src)
        encoder_outputs, hidden, cell = self.encoder(src, src_lengths)

        tokens = [self.sos_idx]
        input_token = torch.tensor([self.sos_idx], device=self.device)

        for _ in range(max_len):
            output, hidden, cell, _ = self.decoder(
                input_token, hidden, cell, encoder_outputs, mask
            )
            top1 = output.argmax(1)
            tokens.append(top1.item())
            input_token = top1
            if top1.item() == self.eos_idx:
                break

        return tokens
