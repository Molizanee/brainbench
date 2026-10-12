from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Environment variables are matched case-insensitively.

    Secrets created at runtime (vault key, password hash, session secret) never live here:
    they are in the data directory or the database.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "BrainBench"

    # Environment-specific values (compose sets working defaults for every one of them).
    brainbench_data: Path = Path("/data")
    database_url: str = "postgresql+asyncpg://brainbench:brainbench@localhost:5432/brainbench"
    brainbench_oauth_callbacks: bool = False
    brainbench_port: int = 3000
    brainbench_bind: str = "127.0.0.1"
    brainbench_public_url: str | None = None
    brainbench_data_host: Path | None = None

    # Constants with sane defaults; env can override.
    session_cookie_name: str = "brainbench_session"
    session_max_age_s: int = 60 * 60 * 24 * 30
    password_min_length: int = 8
    anatomy_repo_url: str = "https://github.com/wheresryan22/anatomy.git"
    anatomy_pin_file: Path = Path("/app/infra/anatomy.pin")
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    sandbox_probe_image: str = "brainbench/manim"
    worker_idle_sleep_s: float = 5.0
    job_concurrency: int = 2
    render_concurrency: int = 1

    @property
    def jobs_dir(self) -> Path:
        return self.brainbench_data / "jobs"

    @property
    def assets_dir(self) -> Path:
        return self.brainbench_data / "assets"

    @property
    def models_dir(self) -> Path:
        return self.brainbench_data / "models"

    @property
    def skills_dir(self) -> Path:
        return self.brainbench_data / "skills"

    @property
    def vault_key_path(self) -> Path:
        return self.brainbench_data / "vault.key"

    @property
    def public_url(self) -> str:
        return self.brainbench_public_url or f"http://localhost:{self.brainbench_port}"


settings = Settings()
