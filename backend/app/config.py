from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    brickognize_url: str = "https://api.brickognize.com/predict/"
    brickognize_sets_url: str = "https://api.brickognize.com/predict/sets/"
    rebrickable_api_key: str | None = None
    # The AI asked when Brickognize is unsure about a set or a part: "infomaniak", "claude"
    # or "none". Unset: Infomaniak if its token is set, else Claude if its key is set.
    ai_provider: str | None = None
    infomaniak_ai_token: str | None = None
    infomaniak_ai_product_id: str | None = None
    ai_model: str = "google/gemma-4-31B-it"  # Infomaniak's model
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5-5"
    confidence_threshold: float = 0.5
    data_dir: Path = Path("./data")
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "brick_tidy.sqlite3"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "crops"

    @property
    def rebrickable_dir(self) -> Path:
        return self.data_dir / "rebrickable"
