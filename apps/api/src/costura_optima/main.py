from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from costura_optima.api.routes import router
from costura_optima.domain.errors import NotFoundError, ValidationError
from costura_optima.settings import get_settings
from costura_optima.operational import configure_logging, request_id_context


def problem(request: Request, status_code: int, title: str, detail: str, error_code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, media_type="application/problem+json", content={
        "type": f"https://costura-optima.local/problems/{error_code.lower()}", "title": title,
        "status": status_code, "detail": detail, "instance": request.url.path,
        "error_code": error_code, "request_id": getattr(request.state, "request_id", None),
    })


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
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
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def operational_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied[:128] if supplied and all(ch.isalnum() or ch in "-_." for ch in supplied) else str(uuid4())
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        try:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.max_request_bytes:
                return problem(request, 413, "Solicitud demasiado grande", "El payload supera el límite permitido.", "PAYLOAD_TOO_LARGE")
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            return response
        finally:
            request_id_context.reset(token)

    @application.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, error: NotFoundError) -> JSONResponse:
        return problem(request, 404, "Recurso no encontrado", str(error), "NOT_FOUND")

    @application.exception_handler(ValidationError)
    async def validation_handler(request: Request, error: ValidationError) -> JSONResponse:
        return problem(request, 422, "Solicitud inválida", str(error), "VALIDATION_ERROR")

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, _: RequestValidationError) -> JSONResponse:
        return problem(request, 422, "Solicitud inválida", "Revise los campos enviados e inténtelo de nuevo.", "INVALID_INPUT")

    @application.exception_handler(Exception)
    async def unexpected_handler(request: Request, _: Exception) -> JSONResponse:
        return problem(request, 500, "Error interno", "No fue posible completar la operación.", "INTERNAL_ERROR")

    application.include_router(router)
    return application


app = create_app()
