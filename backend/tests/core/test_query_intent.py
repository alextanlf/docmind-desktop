from __future__ import annotations

import pytest

from app.core.query_intent import classify_query_intent, is_global_query

# 全部取自实机数据：知识库「123」中那篇 Radar-APLANC 论文的真实提问，
# 以及该用户历史里真实被拒的无关提问。
GLOBAL_QUERIES = [
    "这篇论文提出了什么方法",
    "总结一下这篇论文",
    "论文的主要贡献是什么",
    "这篇文章讲了什么",
    "这篇论文的方法是什么",
    "这篇文章的创新点",
    "这个知识库里都讲了什么",
    "帮我总结一下",
    "summarize this paper",
    "What is the main contribution of this paper?",
    "What method does this paper propose?",
    "What is this paper about?",
    "Please summarize the document",
    "这个文档大致讲了什么",
]

LOCAL_QUERIES = [
    # 带内容锚点的局部型提问
    "什么是Radar-APLANC",
    "Radar-APLANC是什么",
    "Radar-APLANC的训练流程是怎样的",
    "heartbeat extractor 的作用是什么",
    "APLANC 和 Equipleth RF 的对比结果",
    # 有全局意图词但指向特定片段 → 仍属局部型
    "总结一下 Radar-APLANC 的三阶段流程",
    "概括一下 Algorithm 1 的输入输出",
    # 与知识库无关的提问（必须保持局部型，否则会被误当作文档概览请求）
    "macbook的blender如何移动视角",
    "今天上海的天气怎么样",
    "Python 里怎么读 CSV 文件",
]

EMPTY_QUERIES = ["", "   ", "\n\t "]


@pytest.mark.parametrize("query", GLOBAL_QUERIES)
def test_global_queries_are_classified_global(query: str) -> None:
    assert classify_query_intent(query) == "global", query
    assert is_global_query(query) is True


@pytest.mark.parametrize("query", LOCAL_QUERIES)
def test_local_queries_are_classified_local(query: str) -> None:
    assert classify_query_intent(query) == "local", query
    assert is_global_query(query) is False


@pytest.mark.parametrize("query", EMPTY_QUERIES)
def test_blank_queries_default_to_local(query: str) -> None:
    assert classify_query_intent(query) == "local"


def test_intent_classification_needs_no_retrieval_input() -> None:
    """意图判定只看词面，不接收任何检索分数或模型输出。"""
    import inspect

    signature = inspect.signature(classify_query_intent)

    assert list(signature.parameters) == ["query"]


def test_whitespace_variants_classify_the_same() -> None:
    assert classify_query_intent("  这篇论文   提出了什么方法  ") == "global"
    assert classify_query_intent("What  is   this paper about?") == "global"
