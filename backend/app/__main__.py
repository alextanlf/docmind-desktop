from __future__ import annotations

import uvicorn

from app.config import get_settings
from app.main import create_app


def main() -> None:
    settings = get_settings()
    if settings.host != "127.0.0.1" or settings.port != 18900:
        raise RuntimeError("DocMind 后端只能绑定到 127.0.0.1:18900")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
