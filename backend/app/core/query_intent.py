"""提问意图分类：区分「局部型」与「全局型」提问。

两类问题的检索前提完全不同：

- **局部型**（术语、事实、细节，如「什么是 X」「X 的作用」）问的是某个片段，
  提问里带着能对上原文的内容词，向量 + 词法混合检索适用。
- **全局型**（总结、贡献、方法概述，如「这篇论文提出了什么方法」）问的是**整篇文档的性质**。
  提问里的词（「这篇论文」「提出了什么方法」）在文档里不存在，也不可能存在 ——
  任何单个段落都不是它的答案。实测中英文同样失败：
  `What is the main contribution of this paper?` 的向量 top1 落在致谢段，
  `What method does this paper propose?` 落在表格段，区分度只有 0.02~0.08。
  这类问题应当**跳过相似度排序**，直接注入文档概览。

**为什么不看检索分数**：全局型提问与「与知识库无关的提问」的区分度区间重叠
（实测全局型 0.020~0.083，无关提问 0.021~0.055），单靠分数无法区分二者。
因此本模块只做词面判断，不依赖任何检索结果、模型或网络。
"""

from __future__ import annotations

import re
from typing import Literal

QueryIntent = Literal["local", "global"]

# 全局意图词：问「整篇怎么样」而不是「某处是什么」。
_GLOBAL_INTENT_PATTERNS = (
    # 中文
    r"总结|概述|摘要|概括|归纳|综述",
    r"主要(?:内容|观点|结论|工作|贡献|发现|讲|说)",
    r"贡献|创新(?:点|性)?|亮点|价值",
    r"讲了什么|说了什么|写的什么|内容是|讲的啥|说的啥",
    r"提出(?:了)?(?:什么|啥|哪些)?(?:方法|方案|框架|模型|算法|贡献|创新)",
    r"(?:方法|方案|框架|模型|算法|结论|思路)(?:是|为)(?:什么|啥)",
    r"有(?:什么|啥)(?:结论|贡献|创新|方法)",
    r"整体|全文|通篇|大致",
    # 英文
    r"\bsummar(?:y|ize|ise|izing|ising)\b",
    r"\boverview\b|\babstract\b|\bgist\b|\btl;?dr\b",
    r"\bmain\s+(?:idea|point|contribution|finding|takeaway|result|method)",
    r"\bcontribut(?:ion|e|es)\b|\bnovelt(?:y|ies)\b",
    r"\bwhat\s+(?:is|are|does)\b[^?]*\b(?:paper|document|article|file)\b",
    r"\bwhat\s+[a-z]+\s+(?:does|do|is|are|did)\b[^?]*\b(?:paper|document|article|report)\b",
    r"\bwhat\b[^?]*\babout\b",
    r"\btell\s+me\s+about\b",
    r"\bexplain\b[^?]*\b(?:paper|document|article|report)\b",
    r"\b(?:propose|present|introduce)s?\b[^?]*\b(?:method|approach|framework|model)\b",
    r"\bin\s+general\b|\boverall\b",
)

# 指代「当前这份文档/知识库」的词。是全局意图的重要佐证，
# 也是把「这篇论文提出了什么方法」与「macbook 的 blender 如何移动视角」分开的关键。
_DOCUMENT_REFERENCE_PATTERNS = (
    r"这(?:篇|份|个|本)|那(?:篇|份|个|本)|本(?:篇|文|文档|知识库)|该(?:篇|文|文档|知识库)|此(?:篇|文|文档)",
    r"论文|文献|文章|文档|资料|知识库|报告|材料",
    r"\b(?:this|these|the|that)\s+(?:paper|document|article|file|report|pdf)\b",
    r"\b(?:here|above|below)\b",
)

# 内容锚点：英文/数字标识（型号名、术语、缩写）。有它说明提问指向具体对象，
# 即使出现了全局意图词（如「总结一下 Radar-APLANC 的三阶段流程」）也属于局部型。
_CONTENT_ANCHOR_RE = re.compile(r"[A-Za-z]{2,}|\d{2,}")

_GLOBAL_INTENT_RE = re.compile("|".join(_GLOBAL_INTENT_PATTERNS), re.IGNORECASE)
_DOCUMENT_REFERENCE_RE = re.compile("|".join(_DOCUMENT_REFERENCE_PATTERNS), re.IGNORECASE)


def classify_query_intent(query: str) -> QueryIntent:
    """判定提问是「全局型」还是「局部型」。

    判据（只看词面，不看检索分数）：

    1. 没有全局意图词 → 局部型（无论是否指代文档）。
    2. 有全局意图词且指代了当前文档 → 全局型。
    3. 有全局意图词、未指代文档、且不含任何内容锚点 → 全局型
       （如「帮我总结一下」，在已选定知识库的会话里明显是在问整篇）。
    4. 有全局意图词但带内容锚点 → 局部型
       （如「总结一下 Radar-APLANC 的三阶段流程」，问的是特定片段）。
    """
    text = " ".join(query.split())
    if not text:
        return "local"
    if _GLOBAL_INTENT_RE.search(text) is None:
        return "local"
    if _DOCUMENT_REFERENCE_RE.search(text) is not None:
        return "global"
    if _CONTENT_ANCHOR_RE.search(text) is None:
        return "global"
    return "local"


def is_global_query(query: str) -> bool:
    return classify_query_intent(query) == "global"
