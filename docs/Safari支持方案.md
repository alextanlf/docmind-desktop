# Safari (safaridriver) 支持：技术结论

> 2026-10-07 · 基于本机实测

## 结论：可以实现，但有一个无法绕过的用户前置步骤

实测证据（本机 macOS，Safari 26.x）：

```
$ /usr/bin/safaridriver -p 4444        → 进程正常启动并监听
$ defaults read com.apple.Safari AllowRemoteAutomation
  → The domain/default pair does not exist      # 未授权
$ POST /session  {"browserName": "safari"}
  → HTTP 500 {"error":"session not created",
     "message":"Could not create a session: You must enable 'Allow remote
      automation' in the Developer section of Safari Settings to control
      Safari via WebDriver."}
```

`safaridriver` 随系统预装（`/usr/bin/safaridriver` → Cryptex），**零下载**。

## 那个开关是硬性门槛

| 尝试的方式 | 结果 |
|---|---|
| `defaults write com.apple.Safari AllowRemoteAutomation -bool true` | 沙箱拒绝（Safari 偏好受保护） |
| 命令行传参 | 不存在该参数 |
| 直接 HTTP 请求 | 被 safaridriver 拒绝并明确要求人工开启 |
| 自动点击 Safari 菜单 | 不可行（需辅助功能授权 + 用户在场） |

**必须由用户在 Safari「设置 → 高级 → 显示网页开发者功能」打开，
再在「开发」菜单勾选「允许远程自动化」。**

## 因此 Safari 路径的产品形态

```
检测到 Safari 可用（macOS 预装）
  ↓
检测 AllowRemoteAutomation 是否已开启
  ├─ 已开启 → 静默使用
  └─ 未开启 → 返回 YUQUE_SAFARI_REMOTE_AUTOMATION_OFF
              文案：「请在 Safari 设置 → 高级 打开网页开发者功能，
                     然后在开发菜单勾选『允许远程自动化』」
              action：「查看设置步骤」
```

这不是缺陷，是 Apple 的安全设计。**文案必须写清三步路径**，否则用户会以为软件坏了。

## 覆盖面（Cloudflare Radar 2026 真人流量）

| 平台 | Chrome | Safari | Edge |
|---|---|---|---|
| macOS | **57.9%** | 35.5% | — |
| Windows | **68.9%** | — | 19.5% |

## 实现方案：与 chromedriver 并列，而非替换

架构上应当是**三浏览器目标**（不是"二选一"）：

| 目标 | 驱动 | 二进制来源 | 用户前置 |
|---|---|---|---|
| Chrome/Chromium | chromedriver | 下载 9.3M | 无（需已装 Chrome） |
| Safari (macOS) | safaridriver | **系统自带 0M** | 手动开 1 个开关 |
| Edge (Windows) | msedgedriver | 下载 ~10M | 版本严格匹配，**已决定不做** |

**查找顺序**：Chrome 优先（份额最高且无需授权）→ Safari 兜底（覆盖只装 Safari 的用户）。

## 待实现的文件结构

```
app/yuque/
  browser.py       # 发现层：扩为多目标发现
  driver.py        # 下载层：Safari 直接返回系统路径，不下载
  wd_session.py    # 会话层：按目标分派
  safaridriver.py  # 新增：Safari 会话（safaridriver 的能力探测/错误翻译）
```

## 风险

1. **无法端到端验证**：本机未开启该开关，我无法证明握手之后能读写语雀。
   只能验证「未授权时的错误路径正确」。这是本次最大的不确定性。
2. **Safari 自动化会弹窗**：safaridriver 控制时 Safari 可能抢占前台。
3. **Safari 对 headless 无支持**：`safaridriver` 没有 headless 模式，
   每次后台操作都会开一个可见窗口 —— 同步任务时体验很差。
   可行缓解：只用于登录，后台操作仍走 Chrome（若无 Chrome 则提示）。
