from __future__ import annotations

import os
from typing import Any


DEFAULT_MODEL_PATH = "Qwen/Qwen2.5-7B-Instruct"


class TransformersQwenBackend:
    """Windows/NVIDIA-compatible local Qwen backend built on Transformers."""

    def __init__(self, model_path: str | None = None) -> None:
        self.model_path = model_path or os.getenv("AUDIT_OCR_TRANSFORMERS_MODEL", DEFAULT_MODEL_PATH)
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._torch: Any | None = None
        self._device: str | None = None

    def _get_model(self) -> tuple[Any, Any, Any, str]:
        if self._model is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

            self._torch = torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            load_kwargs: dict[str, Any] = {
                "torch_dtype": dtype,
                "low_cpu_mem_usage": True,
            }
            if self._device == "cuda":
                # Equivalent in intent to the original MLX 4-bit model, but
                # supported by Windows/NVIDIA through BitsAndBytes.
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_compute_dtype=dtype,
                )
                load_kwargs["device_map"] = "auto"
            self._model = AutoModelForCausalLM.from_pretrained(self.model_path, **load_kwargs)
            if self._device != "cuda":
                self._model.to(self._device)
            self._model.eval()
        return self._model, self._tokenizer, self._torch, self._device

    def call(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        debug_label: str = "llm",
    ) -> str:
        if system is None:
            from src.llm_extractor import SYSTEM_PROMPT

            system = SYSTEM_PROMPT
        model, tokenizer, torch, device = self._get_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

        # Keep the existing --debug-llm output convention.
        from src import llm_extractor

        llm_extractor._debug_write(f"{debug_label}_prompt", formatted)
        inputs = tokenizer(formatted, return_tensors="pt").to(device)
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = generated[0, inputs["input_ids"].shape[1] :]
        response = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
        llm_extractor._debug_write(f"{debug_label}_response", response)
        return response
