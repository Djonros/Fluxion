"""Build a trl ``SFTTrainer`` that works with old and current trl.

trl 0.12+ moved ``max_seq_length`` (later ``max_length``),
``dataset_text_field`` and ``packing`` from ``SFTTrainer(...)`` into
``SFTConfig`` and renamed ``tokenizer`` to ``processing_class``. The old call
failed right after the base model had been loaded (trl 1.x in the training
environment). The arguments are matched against the installed signatures.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _params(callable_obj) -> set[str]:
    try:
        return set(inspect.signature(callable_obj).parameters)
    except (TypeError, ValueError):
        return set()


def sft_config_kwargs(
    config_params: set[str],
    training_args: dict[str, Any],
    *,
    max_seq_length: int,
    packing: bool,
    text_field: str = "text",
) -> dict[str, Any]:
    """Map Fluxion's settings onto the parameters of the installed SFTConfig."""
    kwargs = dict(training_args)
    # transformers 5 dropped warmup_ratio: warmup_steps < 1 is a ratio there.
    if (
        "warmup_ratio" in kwargs
        and "warmup_ratio" not in config_params
        and "warmup_steps" in config_params
        and "warmup_steps" not in kwargs
    ):
        kwargs["warmup_steps"] = kwargs.pop("warmup_ratio")
    if "max_length" in config_params:
        kwargs["max_length"] = max_seq_length
    elif "max_seq_length" in config_params:
        kwargs["max_seq_length"] = max_seq_length
    kwargs["dataset_text_field"] = text_field
    kwargs["packing"] = packing
    dropped = sorted(key for key in kwargs if key not in config_params)
    if dropped:
        logger.info("SFTConfig: unsupported options skipped: %s", ", ".join(dropped))
    return {key: value for key, value in kwargs.items() if key in config_params}


def build_sft_trainer(
    *,
    model,
    tokenizer,
    train_dataset,
    eval_dataset,
    training_args: dict[str, Any],
    max_seq_length: int,
    packing: bool,
):
    """Create ``trl.SFTTrainer`` for the installed trl version."""
    import trl
    from trl import SFTTrainer

    config_class = getattr(trl, "SFTConfig", None)
    trainer_params = _params(SFTTrainer.__init__)
    if config_class is None:  # trl < 0.9: everything goes to the trainer
        from transformers import TrainingArguments

        return SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            args=TrainingArguments(**training_args),
            max_seq_length=max_seq_length,
            dataset_text_field="text",
            packing=packing,
        )
    config = config_class(
        **sft_config_kwargs(
            _params(config_class),
            training_args,
            max_seq_length=max_seq_length,
            packing=packing,
        )
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "args": config,
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
    }
    if "processing_class" in trainer_params:
        kwargs["processing_class"] = tokenizer
    else:
        kwargs["tokenizer"] = tokenizer
    return SFTTrainer(**kwargs)
