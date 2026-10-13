"""远端目录诊断：确认「按目录移除」看到的层级与远端真实层级一致。

为什么需要这个脚本：目录信息来自两条我无法在开发环境里验证的来源 ——

* 语雀 API Token：``GET /repos/:ns/:slug/toc``（节点带 ``parent_uuid``/``depth``）
* 语雀网页登录：页面内嵌的 ``window.appData`` 里的目录树
* 飞书：``/open-apis/wiki/v2/spaces/:id/nodes`` 的父子链

三者的字段形状都随平台版本变过，而**猜错的后果不是报错，是层级看起来像棵树却漏
文档** —— 在"清理处理过的内容"这个场景里，漏掉等于没清。所以这里把原始数据与
推导出的路径一起打出来，让形状可以被核对，而不是靠信任。

用法（必须从 backend/ 运行）::

    env -u PYTHONPATH .venv/bin/python scripts/inspect_remote_tree.py \
        --data-dir ~/Library/Application\\ Support/docmind-desktop \
        --repo-list

    env -u PYTHONPATH .venv/bin/python scripts/inspect_remote_tree.py \
        --data-dir ~/Library/Application\\ Support/docmind-desktop \
        --repo "SwiftUI"

加 ``--raw`` 会额外打印 provider 返回的原始目录节点（语雀 TOC 用），用于核对字段名。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import AppSettings
from app.remote.registry import ProviderRegistry
from app.storage.database import Database
from app.storage.repositories import DocumentStore, RepositoryStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", required=True, type=Path, help="DocMind 数据目录")
    parser.add_argument("--repo", help="知识库名称或 id")
    parser.add_argument("--repo-list", action="store_true", help="仅列出知识库后退出")
    parser.add_argument("--raw", action="store_true", help="额外打印 provider 的原始目录节点")
    return parser.parse_args()


async def _providers(settings: AppSettings, database: Database) -> ProviderRegistry:
    """按生产装配建一份 registry（需要凭据才能列远端仓库）。"""
    from app.main import create_app

    app = create_app(settings)
    # 只借它的 registry：create_app 不在 lifespan 里就不会连数据库/起调度器，
    # 诊断脚本只需要 provider 这一层。
    return app.state.remote_registry if hasattr(app.state, "remote_registry") else ProviderRegistry()


async def main() -> int:
    args = parse_args()
    settings = AppSettings(data_dir=args.data_dir)
    database = Database(f"sqlite+pysqlite:///{args.data_dir / 'database' / 'docmind.sqlite3'}")
    store = RepositoryStore(database)
    documents = DocumentStore(database)

    if args.repo_list or not args.repo:
        print("知识库列表：")
        for record in store.list():
            marker = record.provider or "本地"
            print(f"  {record.name!r}  id={record.id}  来源={marker}")
        return 0

    record = next(
        (item for item in store.list() if item.name == args.repo or item.id == args.repo),
        None,
    )
    if record is None:
        print(f"未找到知识库 {args.repo!r}")
        return 1
    if not record.provider or not record.remote_id:
        print(f"{record.name!r} 是本地知识库，没有远端目录。")
        return 0

    print(f"知识库: {record.name!r}  来源={record.provider}  远端 id={record.remote_id}")

    registry = await _providers(settings, database)
    provider = registry.get(record.provider)
    capabilities = provider.identity.capabilities
    print(f"provider 声明 document_paths = {capabilities.document_paths}")
    if not capabilities.document_paths:
        print("→ 这个 provider 不提供目录层级，界面会平铺显示。")
        return 0

    if args.raw and hasattr(provider, "api_gateway"):
        gateway = provider.api_gateway
        if hasattr(gateway, "fetch_toc"):
            rows = await gateway.fetch_toc(record.remote_id)
            print(f"\n原始目录节点（{len(rows)} 条）:")
            for row in rows:
                print(
                    "  uuid={uuid} type={type} parent={parent_uuid} depth={depth} "
                    "title={title} url={url} slug={slug}".format(
                        uuid=row.get("uuid"),
                        type=row.get("type"),
                        parent_uuid=row.get("parent_uuid"),
                        depth=row.get("depth"),
                        title=row.get("title"),
                        url=row.get("url"),
                        slug=row.get("slug"),
                    )
                )

    remote_documents = await provider.list_documents(record.remote_id)
    print(f"\n远端文档 {len(remote_documents)} 篇，推导出的目录路径：")
    for document in remote_documents:
        path = " / ".join(document.path) if document.path else "(根目录)"
        print(f"  {path}  ←  {document.title}  [{document.remote_id}]")

    with_path = sum(1 for document in remote_documents if document.path)
    if remote_documents and with_path == 0:
        print(
            "\n⚠ 所有文档的路径都是空的：要么它们确实都在根目录，要么目录来源的形状"
            "与代码预期不符。请用 --raw 对比原始节点字段。"
        )

    local = documents.list_for_repository(record.id)
    print(f"\n本地已导入 {len(local)} 篇，其中带路径 "
          f"{sum(1 for item in local if item.remote_path_json not in ('', '[]'))} 篇。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
