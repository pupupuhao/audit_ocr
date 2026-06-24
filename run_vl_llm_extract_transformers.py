"""Windows/NVIDIA Transformers variant of run_vl_llm_extract.py.

This launcher leaves the original MLX implementation untouched. Run it with
any Windows Conda environment that provides CUDA PyTorch, Transformers, and
BitsAndBytes support.
"""

from src import llm_extractor
from src.transformers_llm_backend import TransformersQwenBackend


_backend = TransformersQwenBackend()
llm_extractor.call_llm = _backend.call

import run_vl_llm_extract


if __name__ == "__main__":
    run_vl_llm_extract.main()
