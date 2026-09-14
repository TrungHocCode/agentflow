"""Application service for registration, login and current-user lookup."""

import uuid
from datetime import datetime, timezone

from app.modules.identity.models import AuthResponse, LoginRequest, RegisterRequest, UserRecord
from app.modules.identity.ports import UserRepository
from app.modules.identity.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.shared.errors import ApplicationError, ValidationError


class IdentityService:
    def __init__(self, repository: UserRepository, signing_secret: str, token_ttl: int) -> None:
        self.repository = repository
        self.signing_secret = signing_secret
        self.token_ttl = token_ttl

    async def register(self, request: RegisterRequest) -> AuthResponse:
        email = str(request.email).lower()
        if await self.repository.get_by_email(email) is not None:
            raise ApplicationError("An account with this email already exists.", code="email_exists")
        now = datetime.now(timezone.utc)
        user = UserRecord(
            id=str(uuid.uuid4()),
            email=email,
            display_name=request.display_name,
            created_at=now,
            updated_at=now,
        )
        created = await self.repository.create(user, hash_password(request.password))
        return self._auth_response(created)

    async def login(self, request: LoginRequest) -> AuthResponse:
        entry = await self.repository.get_by_email(str(request.email).lower())
        if entry is None or not verify_password(request.password, entry[1]):
            raise ApplicationError("Invalid email or password.", code="invalid_credentials")
        user = await self.repository.touch_last_login(entry[0].id) or entry[0]
        if not user.is_active:
            raise ApplicationError("Invalid email or password.", code="invalid_credentials")
        return self._auth_response(user)

    async def current_user(self, token: str) -> UserRecord:
        claims = decode_access_token(token, self.signing_secret)
        user = await self.repository.get(str(claims["sub"]))
        if user is None or not user.is_active:
            raise ValidationError("Authenticated user is not available.", code="invalid_token")
        return user

    def _auth_response(self, user: UserRecord) -> AuthResponse:
        return AuthResponse(
            access_token=create_access_token(user.id, self.signing_secret, self.token_ttl),
            expires_in=self.token_ttl,
            user=user,
        )
