"""账号脱敏。

🔴 为什么放这里而不是各 provider 自己的模块：`_mask_account` 此前在
`app/yuque/gateway.py`、`app/yuque/api_gateway.py`、`app/feishu/provider.py`
各有一份逐字相同的实现，`app/remote/fake.py` 还内联了第四份字面量。
它是「平台无关的展示层脱敏」，按项目约束不该由平台模块各自持有，
新增 provider 时也不该再抄一遍。
"""

from __future__ import annotations


def mask_account(value: str | None) -> str | None:
    """把账号脱敏成 `a***z` 形态，供 UI 展示。

    长度 ≤ 2 时全部打码：两字符账号若保留首尾，等于没脱敏。
    """
    if not value:
        return None
    compact = value.strip()
    if len(compact) <= 2:
        return "*" * len(compact)
    return f"{compact[0]}***{compact[-1]}"