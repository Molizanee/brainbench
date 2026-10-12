from app.api.errors import status_for
from app.core.errors import Conflict, DomainError, NotFound, Unauthorized


def test_domain_errors_map_to_statuses() -> None:
    assert status_for(NotFound("x")) == 404
    assert status_for(Conflict("x")) == 409
    assert status_for(Unauthorized("x")) == 401
    assert status_for(DomainError("x")) == 400
