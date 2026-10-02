"""一次性准备脚本：下载 int8 量化版 bge-m3 ONNX 模型并组装本地运行目录。

替代下载 2.3GB 的 fp32 权重（磁盘占用约 600MB，检索维度仍为 1024，
已入库向量无需重建）。

用法：
    .venv/bin/python scripts/prepare_onnx_embedding.py
    # 国内网络可走镜像：
    HF_ENDPOINT=https://hf-mirror.com .venv/bin/python scripts/prepare_onnx_embedding.py

可选参数：
    --data-dir     应用数据目录（默认 ~/.docmind）
    --model-name   目标 embedding 模型名（默认 BAAI/bge-m3，决定组装目录名）
    --source-repo  量化模型来源仓库（默认 Xenova/bge-m3）
    --dimension    期望的向量维度（默认 1024，用于自检）
    --keep-cache   保留 HF 下载缓存（默认组装成功后自动清理）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# HF 上量化文件的候选名，按优先级排列
QUANTIZED_CANDIDATES = [
    "onnx/sentence_transformers_quantized.onnx",
    "onnx/model_quantized.onnx",
    "onnx/model_int8.onnx",
]
# 分词器/配置文件（存在即下载）
AUX_FILES = [
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "config.json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备 int8 量化 ONNX embedding 模型")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".docmind")
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument("--source-repo", default="Xenova/bge-m3")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--keep-cache", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
    except ImportError:
        print("缺少 huggingface_hub，请在 backend venv 中安装：uv sync", file=sys.stderr)
        return 1

    print(f"检查来源仓库 {args.source_repo} 的文件清单 ...")
    repo_files = set(list_repo_files(args.source_repo))
    quantized_file = next((name for name in QUANTIZED_CANDIDATES if name in repo_files), None)
    if quantized_file is None:
        print(f"仓库 {args.source_repo} 中未找到量化 ONNX 文件，候选：{QUANTIZED_CANDIDATES}", file=sys.stderr)
        return 1

    target_dir = args.data_dir / "models" / f"onnx--{args.model_name.replace('/', '--')}"
    target_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[Path] = []
    wanted = [quantized_file, *(name for name in AUX_FILES if name in repo_files)]
    for filename in wanted:
        print(f"下载 {args.source_repo}/{filename} ...")
        downloaded.append(Path(hf_hub_download(repo_id=args.source_repo, filename=filename)))

    print(f"组装运行目录 {target_dir} ...")
    onnx_source = downloaded[0]
    shutil.copyfile(onnx_source, target_dir / "model.onnx")
    for path in downloaded[1:]:
        shutil.copyfile(path, target_dir / path.name)

    print("自检：加载量化模型并验证向量质量 ...")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.onnx_embedding import ONNXEmbeddingModel

    model = ONNXEmbeddingModel(target_dir)
    probes = ["内存泄漏怎么排查", "如何排查应用内存泄漏", "今天的晚餐吃什么", "how do I debug a memory leak"]
    vectors = model.encode(probes)

    def cosine(left: int, right: int) -> float:
        import numpy as np

        a, b = vectors[left], vectors[right]
        return float(np.dot(a, b))

    dimension = vectors.shape[1]
    related, unrelated, cross_lingual = cosine(0, 1), cosine(0, 2), cosine(0, 3)
    print(f"维度: {dimension}（期望 {args.dimension}）")
    print(f"中文相似对 cos: {related:.4f}")
    print(f"中文无关对 cos: {unrelated:.4f}")
    print(f"中英跨语言对 cos: {cross_lingual:.4f}")

    if dimension != args.dimension:
        print(f"维度不匹配：得到 {dimension}，期望 {args.dimension}", file=sys.stderr)
        return 1
    if not (related > unrelated and cross_lingual > unrelated):
        print("向量质量自检未通过（相关样本应显著高于无关样本）", file=sys.stderr)
        return 1

    if not args.keep_cache:
        repo_cache_root = next(
            (parent for parent in onnx_source.parents if parent.name == f"models--{args.source_repo.replace('/', '--')}"),
            None,
        )
        if repo_cache_root is not None:
            print(f"清理 HF 下载缓存 {repo_cache_root} ...")
            shutil.rmtree(repo_cache_root)

    size_mb = sum(p.stat().st_size for p in target_dir.rglob("*") if p.is_file()) / 1024 / 1024
    print(f"完成。模型目录：{target_dir}（约 {size_mb:.0f}MB）")
    print("重启应用后在设置中点击加载嵌入模型即可，已入库向量无需重建。")
    # onnxruntime 的 C++ 对象在解释器退出阶段析构时会崩溃（known issue），
    # 此处工作已全部完成，直接结束进程绕过 teardown。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
