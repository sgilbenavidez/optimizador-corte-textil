class DomainError(Exception):
    """Base error for an expected domain rejection."""


class NotFoundError(DomainError):
    pass


class ValidationError(DomainError):
    pass

