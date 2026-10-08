"""Fine-tuning: QLoRA config, dataset loading, training, merge, GGUF export."""
from .qlora_config import QLoRASettings, VRAMPreset, DEFAULT_TARGET_MODULES_LOW, DEFAULT_TARGET_MODULES_STD
from .dataset_loader import (
    DatasetFormatError,
    check_dataset,
    format_chatml,
    load_jsonl,
    prepare_dataset,
    pack_sequences,
    train_val_split,
)
from .marketplace import (
    DEFAULT_REGISTRY_DIR,
    AdapterInfo,
    AdapterRegistry,
    AdapterError,
    AdapterNotFoundError,
    DuplicateAdapterError,
    InvalidAdapterError,
)

__all__ = [
    "QLoRASettings",
    "VRAMPreset",
    "DEFAULT_TARGET_MODULES_LOW",
    "DEFAULT_TARGET_MODULES_STD",
    "DatasetFormatError",
    "check_dataset",
    "format_chatml",
    "load_jsonl",
    "prepare_dataset",
    "pack_sequences",
    "train_val_split",
    "DEFAULT_REGISTRY_DIR",
    "AdapterInfo",
    "AdapterRegistry",
    "AdapterError",
    "AdapterNotFoundError",
    "DuplicateAdapterError",
    "InvalidAdapterError",
]
