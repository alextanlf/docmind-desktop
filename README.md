# DocMind

DocMind is a local macOS knowledge workspace. It imports URL, PDF, and UTF-8 Markdown sources into Yuque, keeps a local SQLite/Chroma index, and answers questions with trusted source citations.

## Prerequisites

- macOS, Node.js 20 or later, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/)
- A Yuque account for real-service use
- A model API key for one of the DeepSeek, Qwen, OpenAI, or custom OpenAI-compatible presets

For ordinary development on a trusted machine, follow the local runtime workflow below. For restricted validation environments where Electron downloads or launches are intentionally blocked, install JavaScript packages without postinstall scripts:

```bash
npm install --ignore-scripts
```

That restricted setup supports static checks and unit tests only. It cannot start `npm run dev` or run Electron E2E; complete those checks later on a trusted runtime.

The first-run dialog saves non-secret model settings locally and stores the API key only in the macOS Keychain. The key is never written to SQLite, diagnostics, IPC values, or logs. Choose a preset (DeepSeek, Qwen, OpenAI, or Custom), save a valid key, then test the model connection.

The first real import requires explicit embedding preparation. It downloads about 400 MB for `BAAI/bge-base-zh-v1.5`; it is never downloaded by setup or ordinary backend tests. Run the visible Yuque login flow from the app and complete it in the browser window before creating a repository or importing a source.

## 本地运行

### 首次安装

首次安装、lockfile 变化或后端虚拟环境缺失时，在仓库根目录执行：

```bash
npm install
./scripts/setup-backend.sh
```

运行需要 Node.js 20+、npm、Python 3.11+、`uv` 和可用的 `backend/.venv`。`setup-backend.sh` 只同步后端依赖；它不会下载 Electron、Playwright Chromium、embedding 模型或 Ollama，也不会启动 Ollama。

### 日常启动

依赖准备好后，日常只需：

```bash
npm run dev
```

启动脚本会再次检查 `node`、`npm`、`python3`、`uv`、Electron 依赖、后端 venv 和固定端口 `127.0.0.1:18900`。端口被其他服务占用时会拒绝启动，不会连接未知服务。Electron 为本次进程生成随机会话令牌，并在后端通过令牌保护的健康检查成功、renderer 加载完成后才显示窗口；Ollama 不会成为 Electron 的 child process。

### 数据目录与测试隔离

普通运行使用 Electron 的 `app.getPath("userData")` 作为权威数据目录，并通过 `DOCMIND_DATA_DIR` 传给 FastAPI；SQLite、Chroma、缓存、浏览器 profile 和诊断文件都应写入该目录。正常启动不会迁移、删除或重置已有 userData。

本地 runtime smoke 使用新建的临时目录作为 `DOCMIND_DATA_DIR` 和 Electron userData，测试结束只清理本次明确创建的临时目录，不触碰开发者的真实数据、Keychain、浏览器 profile 或文档。测试记录只包含 health、rendererLoaded、backendExited、portReleased 四项结果，不包含会话令牌、密钥或用户路径。

### 按需准备的外部依赖

- 第一次真实语雀导入前，按需完成可见的语雀登录，并在后端环境中安装 Playwright Python 包和 **Playwright Chromium**：`cd backend && uv run playwright install chromium`。缺少 Chromium 不阻止基础窗口启动，语雀操作会给出明确依赖错误。
- 第一次需要语义检索的真实导入前，按需准备 embedding 模型。该模型不会被 `npm install`、`setup-backend.sh` 或 `npm run dev` 隐式下载。
- Ollama 由用户自行安装和运行，只能通过 loopback HTTP 使用。Ollama 未运行时，`cloud_only` 仍可启动；选择 `local_only` 时会显示本地服务不可用的分层错误。云端模型的 API key 仍只存储在 macOS Keychain，设置页可以在未配置 key 时打开。

### 停止与常见诊断

关闭 Electron 窗口或使用应用退出操作即可停止。退出顺序是清理 proxy 流、向本次 Electron 创建的后端发送 `SIGTERM`，超时后才对同一个 child 发送 `SIGKILL`；不会使用 `killall` 或终止无关进程。需要确认端口已释放时，可执行：

```bash
lsof -nP -iTCP:18900 -sTCP:LISTEN
```

命令无输出表示没有监听者；若仍有监听者，先确认是否为本次启动的后端，再处理占用进程，不要广泛杀进程。

| 现象                                     | 启动行为                                  | 最小排查动作                                                |
| ---------------------------------------- | ----------------------------------------- | ----------------------------------------------------------- |
| 缺少 Node/npm/Python/uv 或 Electron 依赖 | 启动前停止并指出缺少项                    | 安装对应依赖后重试；Electron 依赖缺失时运行 `npm install`   |
| `backend/.venv` 缺失或 setup 失败        | 自动尝试 setup，失败返回非零              | 重新运行 `./scripts/setup-backend.sh` 并检查 Python/uv 环境 |
| `18900` 被占用                           | 拒绝连接未知服务                          | 用上面的 `lsof` 查看监听者，关闭确认无关的服务后重试        |
| 后端健康超时或提前退出                   | 不显示窗口并报告脱敏的后端错误            | 检查后端依赖和端口；不要记录令牌或完整用户路径              |
| renderer 加载失败                        | 不显示空白成功窗口                        | 确认构建产物存在并重试 `npm run desktop:build`              |
| Playwright Chromium 缺失                 | 基础应用继续运行                          | 按需执行 `cd backend && uv run playwright install chromium` |
| Ollama 未运行                            | `cloud_only` 正常；本地模式提示服务不可用 | 用户自行启动 Ollama，并确认仅监听 loopback 地址             |

`npm run desktop:build` 只编译 electron-vite 并验证 main/preload/renderer 静态产物；它不是可分发 `.app`，也不会内置 Python、后端依赖、Playwright 浏览器、embedding 模型或 Ollama。

### 本机归档边界

本阶段默认不生成 `.app` 或 `.dmg`，验收状态为 **OPTIONAL / NOT BUILT**。如未来显式启用本机归档 gate，产物也只供创建它的同一台可信 Mac 本地或内部实验使用，并依赖外部后端路径和本机依赖；归档不得包含 API key、会话令牌、浏览器 profile、SQLite 或真实文档。

归档不会获得 Developer ID 签名或 notarization，也不做自动更新、跨平台发布或 Gatekeeper 绕过。未签名、未 notarize 的文件可能在下载位置或另一台 Mac 上被 Gatekeeper 拦截，不能承诺其他用户下载后正常安装打开；正式公开分发需要单独立项。

## Sources And Limits

- HTTP(S) article URL, subject to safe-address checks, at most 5 redirects, a 10-second connection timeout, a 30-second read timeout, and a 20 MB converted Markdown limit
- UTF-8 Markdown file up to 20 MB
- PDF file up to 100 MB

The renderer stages local files through the desktop process; absolute source paths do not enter backend previews, persisted citations, or chat output.

## Testing

Run the full developer sequence with:

```bash
npm run verify
```

It runs backend tests and Ruff, Vitest, TypeScript, ESLint, Prettier, and Electron E2E. E2E requires a separately installed browser dependency:

```bash
cd backend && uv run playwright install chromium
```

`DOCMIND_FAKE_SERVICES=1` enables deterministic, in-memory Keychain, embedding, Yuque, and LLM replacements only outside production. It seeds no user data, makes no Yuque request, and does not download a model. The backend vertical integration test uses this mode. Electron E2E uses the same fake mode with a temporary data directory and the fixed local backend address `127.0.0.1:18900`.

Real-service validation is separate from fake-service evidence. After configuring credentials in the app and completing the visible Yuque login, use this smoke checklist:

1. Import one supported URL.
2. Import one supported PDF.
3. Import one UTF-8 Markdown file.
4. Ask one question and open its citation.
5. Create, edit, and delete one document with title confirmation.

Record each item as `PASS`, `FAIL`, or `NOT RUN`. Do not treat fake-service results as real-service validation.

## Phase 1 Boundaries

Phase 1 excludes collaborative sharing, cloud synchronization, automatic background Yuque synchronization, multi-user accounts, arbitrary local filesystem access from the renderer, and uncited answers outside retrieved evidence. These exclusions are binding for Phase 1; they are not silently enabled by configuration or treated as supported workflows.

# 本地开发启动

完整的首次安装、日常启动、诊断和归档边界请参阅上面的“本地运行”章节。可选归档 gate 使用 `DOCMIND_BUILD_LOCAL_ARCHIVE=1`，默认保持 `OPTIONAL / NOT BUILT`，且不包含签名或公证。
