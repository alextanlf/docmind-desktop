from __future__ import annotations

from pathlib import Path
from typing import Any


class ONNXEmbeddingModel:
    """int8 量化 ONNX 版 bge-m3 编码器（磁盘占用约 600MB，替代 2.3GB 的 fp32 权重）。

    只加载量化 ONNX 图和分词器，不经过 sentence-transformers / PyTorch 权重，
    但保持与 sentence-transformers 相同的 `encode` 调用面，供
    `BGEEmbeddingProvider._encode` 无差别使用。
    """

    def __init__(self, model_dir: str | Path, *, max_seq_length: int = 8192, batch_size: int = 16) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        self.model_dir = Path(model_dir)
        onnx_path = self.model_dir / "model.onnx"
        if not onnx_path.is_file():
            raise FileNotFoundError(f"quantized embedding model not found: {onnx_path}")

        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), local_files_only=True)
        self.session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.max_seq_length = max_seq_length
        self.batch_size = batch_size
        self._input_names = {item.name for item in self.session.get_inputs()}

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> Any:
        """返回 shape 为 (len(texts), dimension) 的 numpy 数组。"""
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        batches: list[Any] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [str(text) for text in texts[start : start + self.batch_size]]
            encoded = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_seq_length,
                return_tensors="np",
            )
            feed = {
                name: encoded[name]
                for name in ("input_ids", "attention_mask", "token_type_ids")
                if name in self._input_names and name in encoded
            }
            outputs = self.session.run(None, feed)
            batch_vectors = np.asarray(outputs[0])
            if batch_vectors.ndim == 3:
                # bge-m3 dense 向量 = CLS token 池化。
                # 必须逐批池化成 2 维后再拼接：每批各自 padding 到该批最长序列，
                # 不同批的序列长度不同（如 1242 与 264），直接 concatenate(axis=0)
                # 会因第 1 维不一致而失败。批内长度一致，所以池化可以安全地逐批进行。
                batch_vectors = batch_vectors[:, 0]
            batches.append(batch_vectors)
        merged = np.concatenate(batches, axis=0)
        if merged.ndim != 2:
            raise ValueError(f"unexpected ONNX embedding output shape: {merged.shape}")
        if normalize_embeddings:
            norms = np.linalg.norm(merged, axis=1, keepdims=True)
            merged = merged / np.clip(norms, 1e-12, None)
        return merged
