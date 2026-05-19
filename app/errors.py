class AppError(Exception):
    def __init__(self, message: str, code: str = "APP_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


class AuthError(AppError):
    def __init__(self, message: str = "Требуется вход") -> None:
        super().__init__(message, "AUTH_REQUIRED")


class ValidationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "VALIDATION_ERROR")


class DatabaseError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "DATABASE_ERROR")


class ConfigError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "CONFIG_ERROR")


def parse_limit(value: str | None, *, default: int = 500, maximum: int = 5000) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise AppError(f"Неверный параметр limit: {value!r}") from None
    if n < 1:
        raise AppError("Параметр limit должен быть не меньше 1")
    return min(n, maximum)
