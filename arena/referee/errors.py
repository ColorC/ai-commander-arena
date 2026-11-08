"""The one exception the HTTP front turns into an answer: status code, message and extra fields."""


class ApiError(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra
