from pydantic import BaseModel, Field

PASSWORD_MIN_LENGTH = 8


class PasswordSetup(BaseModel):
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=1024)


class PasswordLogin(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class AuthState(BaseModel):
    password_set: bool
    authenticated: bool
