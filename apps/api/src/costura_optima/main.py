from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from costura_optima.api.routes import router
from costura_optima.domain.errors import NotFoundError, ValidationError
from costura_optima.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Catálogo versionado, nesting geométrico validado y planificación asíncrona de producción.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    @application.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            media_type="application/problem+json",
            content={"type": "about:blank", "title": "Recurso no encontrado", "status": 404, "detail": str(error)},
        )

    @application.exception_handler(ValidationError)
    async def validation_handler(_: Request, error: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            media_type="application/problem+json",
            content={"type": "about:blank", "title": "Orden inválida", "status": 422, "detail": str(error)},
        )

    application.include_router(router)
    return application


app = create_app()
