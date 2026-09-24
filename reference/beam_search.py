import torch
import torch.nn.functional as F


@torch.no_grad()
def beam_search_decode(model, src, src_lengths, beam_width=5, max_len=50,
                       length_penalty=0.6):
    """Beam search декодирование для Seq2Seq модели с attention.

    Args:
        model: обученная Seq2Seq модель.
        src: тензор [src_len, 1] (batch_size строго 1).
        src_lengths: тензор [1] — длины исходных последовательностей.
        beam_width: количество лучей.
        max_len: максимальная длина вывода.
        length_penalty: степень нормализации по длине (0.6 — типичное значение).

    Returns:
        list[int] — токены лучшей гипотезы (без <sos>, с <eos>).
    """
    model.eval()
    device = model.device

    assert src.shape[1] == 1, "Beam search поддерживает только batch_size=1"

    vocab_size = model.decoder.output_dim

    encoder_outputs, hidden, cell = model.encoder(src, src_lengths)
    #encoder_outputs = [src_len, 1, enc_hid_dim * 2]
    #hidden = [n_layers, 1, dec_hid_dim]
    #cell = [n_layers, 1, dec_hid_dim]

    mask = model.create_mask(src)

    # Дублируем состояния для каждого луча
    encoder_outputs = encoder_outputs.expand(-1, beam_width, -1)
    hidden = hidden.expand(-1, beam_width, -1).contiguous()
    cell = cell.expand(-1, beam_width, -1).contiguous()
    mask = mask.expand(beam_width, -1)

    # beams хранит последовательности токенов
    beams = torch.full(
        (beam_width, 1), model.sos_idx, dtype=torch.long, device=device
    )
    # scores хранит накопленный log-probability
    scores = torch.tensor(
        [0.0] + [float('-inf')] * (beam_width - 1), device=device
    )

    finished = []

    for step in range(max_len):
        last_tokens = beams[:, -1]

        output, hidden, cell, _ = model.decoder(
            last_tokens, hidden, cell, encoder_outputs, mask
        )
        #output = [beam_width, vocab_size]

        log_probs = F.log_softmax(output, dim=1)

        candidate_scores = scores.unsqueeze(1) + log_probs
        candidate_scores = candidate_scores.view(-1)
        #candidate_scores = [beam_width * vocab_size]

        top_scores, top_indices = candidate_scores.topk(beam_width)

        beam_indices = top_indices // vocab_size
        token_indices = top_indices % vocab_size

        beams = torch.cat([
            beams[beam_indices],
            token_indices.unsqueeze(1),
        ], dim=1)
        scores = top_scores

        # Переупорядочиваем скрытые состояния для новых лучей
        hidden = hidden[:, beam_indices, :].contiguous()
        cell = cell[:, beam_indices, :].contiguous()

        # Проверяем завершённые лучи (предсказали <eos>)
        for i in range(beam_width):
            if token_indices[i].item() == model.eos_idx:
                length = step + 1
                norm_score = scores[i].item() / (length ** length_penalty)
                finished.append((norm_score, beams[i].clone().tolist()))

        if len(finished) >= beam_width:
            break

    if not finished:
        best_beam = beams[0].tolist()
        return best_beam[1:]

    finished.sort(key=lambda x: x[0], reverse=True)
    best = finished[0][1]
    return best[1:]
