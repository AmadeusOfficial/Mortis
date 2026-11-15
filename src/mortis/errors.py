class UserError(Exception):
    """能直接给用户看的错误，带建议的 HTTP 状态码。"""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
