# DocMind

DocMind is a local macOS knowledge workspace. It imports URL, PDF, and UTF-8 Markdown sources into Yuque, keeps a local SQLite/Chroma index, and answers questions with trusted source citations.

## Prerequisites

- macOS, Node.js 20 or later, Python 3.11 or later, and [uv](https://docs.astral.sh/uv/)
- A Yuque account for real-service use
- A model API key for one of the DeepSeek, Qwen, OpenAI, or custom OpenAI-compatible presets

For ordinary development on a trusted machine, install the backend and the Electron runtime, then start the app:

```bash
./scripts/setup-backend.sh
npm install
npm run dev
```

For restricted validation environments where Electron downloads or launches are intentionally blocked, install JavaScript packages without postinstall scripts:

```bash
npm install --ignore-scripts
```

That restricted setup supports static checks and unit tests only. It cannot start `npm run dev` or run Electron E2E; complete those checks later on a trusted runtime.

The first-run dialog saves non-secret model settings locally and stores the API key only in the macOS Keychain. The key is never written to SQLite, diagnostics, IPC values, or logs. Choose a preset (DeepSeek, Qwen, OpenAI, or Custom), save a valid key, then test the model connection.

The first real import requires explicit embedding preparation. It downloads about 400 MB for `BAAI/bge-base-zh-v1.5`; it is never downloaded by setup or ordinary backend tests. Run the visible Yuque login flow from the app and complete it in the browser window before creating a repository or importing a source.

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

首次运行：`npm install`、`./scripts/setup-backend.sh`，之后使用 `npm run dev`。开发服务仅监听本机 `127.0.0.1:18900`，每次启动生成新的会话令牌；Ollama 需由用户自行安装并运行，不会由启动脚本下载或托管。可选本地归档默认不构建，显式设置 `DOCMIND_BUILD_LOCAL_ARCHIVE=1` 才会执行，并且不包含签名或公证。
