from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    brickognize_url: str = "https://api.brickognize.com/predict/"
    rebrickable_api_key: str | None = None
    llm_api_key: str | None = None
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
