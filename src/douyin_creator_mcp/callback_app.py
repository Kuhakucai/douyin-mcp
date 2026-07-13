"""HTTPS callback ASGI app for Douyin OAuth."""

from __future__ import annotations

from typing import Any

from .errors import CONFIGURATION_ERROR, AppError
from .responses import response_from_exception, success_response
from .server import ServiceContainer, build_container


def create_app(services: ServiceContainer | None = None) -> Any:
    try:
        from fastapi import FastAPI
    except ImportError as exc:
        raise AppError(
            CONFIGURATION_ERROR,
            "fastapi is not installed. Run: python -m pip install -e .",
            retryable=False,
        ) from exc

    services = services or build_container()
    app = FastAPI(title="douyin-creator-mcp-callback")

    @app.get("/oauth/douyin/callback")
    def douyin_oauth_callback(code: str, state: str) -> dict[str, Any]:
        try:
            return success_response(**services.auth_service.handle_callback(code, state))
        except Exception as exc:
            return response_from_exception(exc)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise AppError(
            CONFIGURATION_ERROR,
            "uvicorn is not installed. Run: python -m pip install -e .",
            retryable=False,
        ) from exc
    services = build_container()
    app = create_app(services)
    uvicorn.run(app, host=services.settings.mcp_host, port=services.settings.mcp_port)


if __name__ == "__main__":
    main()
