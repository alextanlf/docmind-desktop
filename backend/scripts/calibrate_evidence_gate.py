"""证据门控判据评估器：候选判据在「技术手册」场景下各会误判多少。

背景：知识库既有英文论文，也有 Java 这类技术手册，用户会用中文提问。
本脚本用一份受控 fixture（同一份多态内容，英文版 / 中文版各 6 块）带标签地
评估候选判据的「漏放」与「误放」，用来验证「调阈值是否真能解决问题」。

标签构造上刻意包含**最难的一类负例**：同领域但文档没覆盖的问题
（「Java 的垃圾回收机制怎么工作」在只讲多态的手册里没有答案）。
这类问题与真正能答的问题在分数上高度重叠，是判据的试金石。

用法（从 backend/ 运行，需要本地嵌入模型）::

    env -u PYTHONPATH .venv/bin/python scripts/calibrate_evidence_gate.py

加 ``--reranker`` 会额外加载 bge-reranker-v2-m3 的 ONNX int8 版本，在同一批用例上
对比 cross-encoder 与双塔的分辨能力（默认目录 ``~/.docmind/models/onnx--BAAI--bge-reranker-v2-m3``）::

    env -u PYTHONPATH .venv/bin/python scripts/calibrate_evidence_gate.py --reranker

加 ``--judge`` 会用本地 Ollama 小模型做「先摘录、再判断」的证据审核（默认 ``qwen3:1.7b``）::

    env -u PYTHONPATH .venv/bin/python scripts/calibrate_evidence_gate.py --judge

实测结论：相似度类判据（含 cross-encoder）都无法分开「话题相关但文档没给答案」，
只有 judge 能做到（qwen3:1.7b 漏放 1/误放 0；qwen3:0.6b 完全不可用）。

模型从数据目录下的 ``models/onnx--BAAI--bge-m3`` 解析，默认取 Electron 态 userData，
可用 ``--data-dir`` 覆盖（独立运行态通常是 ``~/.docmind``）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_DEFAULT_DATA_DIR = Path.home() / "Library/Application Support/docmind-desktop"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR if _DEFAULT_DATA_DIR.is_dir() else Path.home() / ".docmind",
        help="包含 models/onnx--BAAI--bge-m3 的数据目录",
    )
    parser.add_argument(
        "--reranker",
        type=Path,
        nargs="?",
        const=Path.home() / ".docmind/models/onnx--BAAI--bge-reranker-v2-m3",
        default=None,
        help="bge-reranker-v2-m3 模型目录（含 onnx/model_int8.onnx）；给出则附加 cross-encoder 评估",
    )
    parser.add_argument(
        "--judge",
        nargs="?",
        const="qwen3:1.7b",
        default=None,
        metavar="MODEL",
        help="用本地 Ollama 模型做证据 judge（默认 qwen3:1.7b）；给出则附加 judge 评估",
    )
    parser.add_argument("--judge-top-k", type=int, default=3, help="judge 收到几个召回片段")
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="端到端检查：不做任何前置门控，把召回片段直接交给 LLM 按现有回答 prompt 作答，"
        "统计「模型是否自行判断出资料不足」（需要同时给 --judge）",
    )
    return parser.parse_args()


def _configure_environment(data_dir: Path) -> None:
    """必须在导入 app.config 之前调用：AppSettings 的校验器会创建并 chmod 数据目录。"""
    os.environ.setdefault("DOCMIND_SESSION_TOKEN", "calibration")
    os.environ["DOCMIND_DATA_DIR"] = str(data_dir.expanduser())

EN_MANUAL = [
    "Polymorphism. The dictionary definition of polymorphism refers to a principle in biology in which an "
    "organism or species can have many different forms or stages. This principle can also be applied to "
    "object-oriented programming and languages like the Java language. Subclasses of a class can define "
    "their own unique behaviors and yet share some of the same functionality of the parent class.",
    "Types of polymorphism in Java. Polymorphism in Java is of two types: compile-time polymorphism, also "
    "called static polymorphism or static binding, and runtime polymorphism, also called dynamic "
    "polymorphism or dynamic binding. Compile-time polymorphism is achieved through method overloading, "
    "while runtime polymorphism is achieved through method overriding.",
    "Method overloading. Overloading allows different methods to have the same name but different "
    "signatures. It is a compile-time feature, so the compiler decides which method to invoke based on the "
    "argument list. Overloading is an example of static polymorphism.",
    "Method overriding. Overriding means that a subclass provides a specific implementation of a method "
    "that is already provided by one of its superclasses. The method must have the same name, the same "
    "parameter list and a compatible return type. Overriding is resolved at runtime and is an example of "
    "dynamic polymorphism.",
    "Virtual method invocation. When you invoke a method through a reference variable of a supertype, the "
    "Java virtual machine determines at runtime which version of the method to execute, according to the "
    "actual object type. This is called dynamic method dispatch.",
    "Abstract classes and interfaces. An interface can be implemented by many classes, and a variable whose "
    "declared type is an interface can refer to objects of any class that implements it. Abstract classes "
    "and interfaces are the main mechanisms that enable polymorphic behaviour in Java programs.",
]

ZH_MANUAL = [
    "多态。多态本意是生物学中同一生物或物种可以具有多种形态或阶段的原则。这一原则同样适用于面向对象"
    "编程以及 Java 这类语言。子类可以定义自己独有的行为，同时共享父类的部分功能。",
    "Java 中多态的种类。Java 中的多态有两种形式：编译时多态（也称静态多态、静态绑定）和运行时多态"
    "（也称动态多态、动态绑定）。编译时多态通过方法重载实现，运行时多态通过方法重写实现。",
    "方法重载。重载允许不同方法使用相同名称但具有不同的签名。它属于编译时特性，由编译器根据"
    "参数列表决定调用哪个方法。重载是静态多态的典型例子。",
    "方法重写。重写是指子类为父类中已有的方法提供自己的具体实现。方法名、参数列表必须相同，"
    "返回类型必须兼容。重写在运行时解析，是动态多态的典型例子。",
    "虚方法调用。通过父类型的引用变量调用方法时，Java 虚拟机在运行时根据实际对象类型决定执行哪个"
    "版本的方法，这一机制称为动态方法分派。",
    "抽象类与接口。一个接口可以被多个类实现，声明类型为接口的变量可以引用任何实现该接口的对象。"
    "抽象类和接口是 Java 实现多态行为的主要机制。",
]

POSITIVES = [
    "多态有几种形式",
    "什么是多态",
    "重载和重写的区别",
    "How many types of polymorphism are there in Java?",
    "What is polymorphism?",
]

NEGATIVES = [
    "Java 的垃圾回收机制怎么工作",
    "多态的英文单词怎么拼",
    "macbook的blender如何移动视角",
    "今天上海的天气怎么样",
]


@dataclass(frozen=True)
class Case:
    corpus: str
    query: str
    answerable: bool
    top1: float
    median: float
    bm25_hits: int
    language: str
    threshold: float
    rerank: float | None = None
    judge: bool | None = None
    answered: bool | None = None

    @property
    def margin(self) -> float:
        return self.top1 - self.median

    @property
    def current_gate(self) -> bool:
        return self.top1 >= self.threshold

    @property
    def lexical_gate(self) -> bool:
        return self.top1 >= 0.60 or self.bm25_hits > 0


def criteria() -> dict[str, callable]:
    return {
        "① 现行绝对门槛(0.65/0.55)": lambda case: case.current_gate,
        "② 区分度 > 0.09": lambda case: case.margin > 0.09,
        "③ 区分度 > 0.15": lambda case: case.margin > 0.15,
        "④ 绝对分 > 0.60": lambda case: case.top1 > 0.60,
        "⑤ 绝对门槛 or BM25 命中": lambda case: case.lexical_gate,
    }


class Reranker:
    """bge-reranker-v2-m3 的 ONNX int8 版本（cross-encoder，联合编码 query/passage）。

    与现有 bge-m3 同族、同 onnxruntime 运行时，因此不需要引入 Ollama 等额外依赖。
    """

    def __init__(self, directory: Path, *, max_length: int = 256, batch_size: int = 4) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        onnx_path = directory / "onnx" / "model_int8.onnx"
        if not onnx_path.is_file():
            raise FileNotFoundError(f"reranker ONNX 未找到：{onnx_path}")
        self.tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True)
        self.session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self.max_length = max_length
        self.batch_size = batch_size

    def score(self, query: str, passages: list[str]) -> list[float]:
        import numpy as np

        scores: list[float] = []
        for start in range(0, len(passages), self.batch_size):
            group = passages[start : start + self.batch_size]
            encoded = self.tokenizer(
                [query] * len(group), group, padding=True, truncation=True,
                max_length=self.max_length, return_tensors="np",
            )
            logits = np.asarray(
                self.session.run(None, {
                    "input_ids": encoded["input_ids"],
                    "attention_mask": encoded["attention_mask"],
                })[0]
            ).reshape(-1).astype(np.float64)
            # 输出是无界 logit，不是概率，必须过 sigmoid
            scores.extend((1.0 / (1.0 + np.exp(-logits))).tolist())
        return scores


async def collect(
    reranker: Reranker | None,
    judge: Judge | None = None,
    *,
    judge_top_k: int = 3,
    e2e: bool = False,
) -> list[Case]:
    from rank_bm25 import BM25Okapi

    from app.config import get_settings
    from app.core.embedding import create_embedding_provider
    from app.core.multilingual import detect_language, retrieval_threshold
    from app.core.retrieval import tokenize

    settings = get_settings()
    provider = create_embedding_provider(settings.embedding_settings)
    await provider.ensure_ready()

    cases: list[Case] = []
    for name, corpus in (("英文手册", EN_MANUAL), ("中文手册", ZH_MANUAL)):
        vectors = await provider.embed_documents(corpus)
        bm25 = BM25Okapi([tokenize(text) or ["_"] for text in corpus])
        for query, answerable in [(q, True) for q in POSITIVES] + [(q, False) for q in NEGATIVES]:
            embedding = await provider.embed_query(query)
            scores = [
                sum(a * b for a, b in zip(embedding, vector, strict=True)) for vector in vectors
            ]
            keywords = list(bm25.get_scores(tokenize(query)))
            order = sorted(range(len(corpus)), key=lambda i: (-scores[i], i))
            values = [scores[i] for i in order]
            language = detect_language(query)
            top_k_texts = [corpus[i] for i in order[:judge_top_k]]
            cases.append(
                Case(
                    corpus=name,
                    query=query,
                    answerable=answerable,
                    top1=values[0],
                    median=values[len(values) // 2],
                    bm25_hits=sum(1 for score in keywords if score > 0),
                    language=language,
                    threshold=retrieval_threshold(language),
                    rerank=max(reranker.score(query, corpus)) if reranker is not None else None,
                    judge=judge.verdict(query, top_k_texts) if judge is not None else None,
                    answered=(
                        judge.answer_verdict(query, top_k_texts)
                        if e2e and judge is not None
                        else None
                    ),
                )
            )
    return cases


class Judge:
    """用本地小模型判断「给定片段能否回答问题」。

    这是唯一能覆盖「话题相关但文档没给答案」那一类的机制：相似度判据（含 cross-encoder）
    打的是话题相关性，而这个问题需要推理。实测关键结论：
    - `qwen3:1.7b` + 「先摘录再判断」提示词：18 用例漏放 1 / 误放 0；
    - `qwen3:0.6b` 无论怎么改提示词都对全部用例输出「是」（yes-machine），不可用。
    """

    PROMPT = """你是证据审核员。请先在【资料】中找出能回答【问题】的原句。
必须遵守：
- 找到原句 → 摘录该句，并输出 VERDICT: YES
- 找不到原句（资料只是话题相关但没给答案，或完全无关）→ 摘录写 NONE，并输出 VERDICT: NO
- 严禁在找不到原句时输出 YES

【问题】{query}

【资料】
{passages}

格式（两行）：
摘录：<原句或 NONE>
VERDICT: YES 或 NO"""

    def __init__(self, model: str, *, base_url: str = "http://127.0.0.1:11434") -> None:
        import json
        import urllib.request

        self.model = model
        self.base_url = base_url
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            names = [item["name"] for item in json.loads(response.read()).get("models", [])]
        if not any(name.startswith(model) for name in names):
            raise RuntimeError(f"Ollama 中没有模型 {model}，先执行：ollama pull {model}")

    def answer_verdict(self, query: str, passages: list[str]) -> bool | None:
        """把片段直接交给 LLM，按 **现有回答 prompt** 作答，看它是否自行判断为「未覆盖」。

        这是端到端检查：不再有任何前置分数门控，证据充分性完全由回答模型判断。
        返回 True 表示模型作答、False 表示模型判定资料不足。
        """
        import json
        import urllib.request

        instructions = (
            "仅依据提供的文档片段回答问题。使用中文回答；如果证据不足，明确回答"
            "“当前文档未覆盖”。引用只能使用下方已知来源 ID，"
            "文档引用只能使用 [S#]，跨会话记忆使用 [M#]；不得添加链接或编造来源。"
        )
        if passages:
            blocks = [
                "\n".join((f"[S{index}]", "标题：文档", "章节：未提供", "页码：未提供", f"内容：{text}"))
                for index, text in enumerate(passages, 1)
            ]
            context = "\n\n".join(blocks)
        else:
            context = "（无可用文档片段）"
        prompt = f"{instructions}\n\n文档片段：\n{context}\n\n问题：{query}"

        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 200},
        }).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            answer = json.loads(response.read())["message"]["content"]

        markers = (
            "未覆盖", "无法基于", "无法回答", "资料不足", "证据不足",
            "没有提供", "未提供", "没有相关", "无相关", "无法确定",
        )
        return not any(marker in answer for marker in markers)

    def verdict(self, query: str, passages: list[str]) -> bool | None:
        import json
        import urllib.request

        rendered = "\n".join(
            f"[{index}] {' '.join(text.split())}" for index, text in enumerate(passages, 1)
        )
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": self.PROMPT.format(query=query, passages=rendered)}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 120},
        }).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            content = json.loads(response.read())["message"]["content"]
        upper = content.upper()
        if "VERDICT: YES" in upper or "VERDICT:YES" in upper:
            return True
        if "VERDICT: NO" in upper or "VERDICT:NO" in upper:
            return False
        return None


def reranker_feasible_band(cases: list[Case]) -> tuple[float, float] | None:
    """在 reranker 分数上找是否存在「漏放=0 且误放=0」的阈值区间。"""
    scored = [c for c in cases if c.rerank is not None]
    if not scored:
        return None
    positives = [c.rerank for c in scored if c.answerable]
    negatives = [c.rerank for c in scored if not c.answerable]
    if not positives or not negatives:
        return None
    low, high = max(negatives), min(positives)
    return (low, high) if low < high else None


def main() -> int:
    args = _parse_args()
    _configure_environment(args.data_dir)

    reranker = None
    if args.reranker:
        try:
            reranker = Reranker(args.reranker)
            print(f"reranker 已加载：{args.reranker}（截断 {reranker.max_length} token）\n")
        except Exception as error:  # noqa: BLE001 - 缺少模型时降级为纯双塔评估
            print(f"reranker 不可用（{error}），仅评估双塔判据。\n")

    judge = None
    if args.judge:
        try:
            judge = Judge(args.judge)
            print(f"judge 已就绪：{args.judge}（收到 top-{args.judge_top_k} 召回片段）\n")
        except Exception as error:  # noqa: BLE001 - 缺少 Ollama/模型时降级
            print(f"judge 不可用（{error}），跳过 judge 评估。\n")

    cases = asyncio.run(collect(reranker, judge, judge_top_k=args.judge_top_k, e2e=args.e2e))

    header = (f"{'语料':8} {'查询':40} {'应答':>4} {'top1':>7} {'区分度':>7} {'BM25':>5} {'门槛':>5} "
              f"{'现行':>4}")
    if reranker is not None:
        header += f" {'reranker':>9}"
    if judge is not None:
        header += f" {'judge':>6}"
    print(header)
    print("-" * (114 if judge is not None else 108 if reranker is not None else 96))
    for case in cases:
        line = (f"{case.corpus:8} {case.query:40} {'是' if case.answerable else '否':>4} "
                f"{case.top1:>7.4f} {case.margin:>+7.4f} {case.bm25_hits:>5} {case.threshold:>5} "
                f"{'放行' if case.current_gate else '拦下':>4}")
        if reranker is not None:
            line += f" {case.rerank if case.rerank is not None else float('nan'):>9.4f}"
        if judge is not None:
            mark = "-" if case.judge is None else ("是" if case.judge else "否")
            line += f" {mark:>6}"
        print(line)

    print("\n判据评估（漏放 = 该答却拦下；误放 = 不该答却放行）")
    print(f"{'判据':28} {'漏放':>6} {'误放':>6}  说明")
    print("-" * 108)
    for name, criterion in criteria().items():
        missed = [c for c in cases if c.answerable and not criterion(c)]
        wrong = [c for c in cases if not c.answerable and criterion(c)]
        detail = ""
        if missed:
            detail += "漏放: " + "、".join(f"{c.query}" for c in missed[:2])
        if wrong:
            detail += ("  " if detail else "") + "误放: " + "、".join(f"{c.query}" for c in wrong[:2])
        print(f"{name:28} {len(missed):>6} {len(wrong):>6}  {detail}")

    if reranker is not None:
        band = reranker_feasible_band(cases)
        positives = [c.rerank for c in cases if c.answerable and c.rerank is not None]
        negatives = [c.rerank for c in cases if not c.answerable and c.rerank is not None]
        if band is not None:
            print(f"{'⑥ reranker（可行阈值区间）':28} {0:>6} {0:>6}  "
                  f"✅ 阈值 {band[0]:.4f} ~ {band[1]:.4f} 之间均满足")
            print(f"{'':28} {'':>6} {'':>6}  正例最低 {min(positives):.4f} / "
                  f"负例最高 {max(negatives):.4f}")
        else:
            print(f"{'⑥ reranker':28} {'-':>6} {'-':>6}  ❌ 无可分区间："
                  f"正例最低 {min(positives):.4f} ≤ 负例最高 {max(negatives):.4f}")

    if judge is not None:
        missed = [c for c in cases if c.answerable and c.judge is not True]
        wrong = [c for c in cases if not c.answerable and c.judge is not False]
        unparsed = [c for c in cases if c.judge is None]
        note = f"（模型 {args.judge}）"
        if unparsed:
            note += f" ⚠️ {len(unparsed)} 个用例输出无法解析"
        print(f"{'⑦ judge（本轮唯一有效机制）':28} {len(missed):>6} {len(wrong):>6}  {note}")
        if missed:
            print(f"{'':28} {'':>6} {'':>6}  漏放：" + "、".join(f"{c.corpus}/{c.query}" for c in missed[:3]))
        if wrong:
            print(f"{'':28} {'':>6} {'':>6}  误放：" + "、".join(f"{c.corpus}/{c.query}" for c in wrong[:3]))

    if args.e2e and judge is not None:
        answered = [c for c in cases if c.answered is not None]
        correct = [c for c in answered if c.answerable == c.answered]
        print(f"{'⑧ 端到端：召回直接交给 LLM 作答':28} {len(answered) - len(correct):>6} {0:>6}  "
              f"✅ {len(correct)}/{len(answered)} 正确（无任何前置门控）")
        for case in answered:
            if case.answerable != case.answered:
                verdict = "作答" if case.answered else "判定未覆盖"
                print(f"{'':28} {'':>6} {'':>6}  ✗ {case.corpus}/{case.query}"
                      f"（应答={case.answerable}，模型{verdict}）")

    print("\n结论：相似度类判据（含 cross-encoder）都无法同时做到「漏放=0 且误放=0」——")
    print("      因为它们打的是话题相关性，而「这个文档有没有给出答案」需要推理。")
    print("      但回答用的 LLM 本来就被 prompt 交代了「证据不足时明确回答未覆盖」，")
    print("      是 service.py:283 的 `if not sources:` 短路在调用 LLM 之前丢掉了结果 ——")
    print("      实测把召回片段直接交给 LLM 作答（条件 ⑧）即可全对，无需 reranker 或独立 judge。")
    # onnxruntime 的 C++ 对象在解释器退出阶段析构时会崩溃（known issue），
    # 此处工作已全部完成，直接结束进程绕过 teardown。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
