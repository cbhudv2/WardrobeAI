class WardrobeAIError(RuntimeError):
    code = "application_error"


class NotFoundError(WardrobeAIError):
    code = "not_found"


class ConflictError(WardrobeAIError):
    code = "conflict"


class AuthorizationError(WardrobeAIError):
    code = "unauthorized"


class ConfirmationError(WardrobeAIError):
    code = "invalid_confirmation"


class CooldownError(WardrobeAIError):
    code = "cooldown_violation"


class WeatherUnavailable(WardrobeAIError):
    code = "weather_unavailable"


class ModelUnavailable(WardrobeAIError):
    code = "model_unavailable"
