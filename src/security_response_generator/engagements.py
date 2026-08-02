"""Engagement lifecycle and active-engagement state."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from security_response_generator import config

DEMO_SLUG = "demo"
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Engagement:
    slug: str
    customer_name: str
    root: Path

    @property
    def customer_standards_dir(self) -> Path:
        return self.root / "customer_standards"

    @property
    def private_context_dir(self) -> Path:
        return self.root / "private_context"

    @property
    def chroma_dir(self) -> Path:
        return self.root / "chroma_db"

    @property
    def manifest_path(self) -> Path:
        return self.chroma_dir / "manifest.json"

    @property
    def responses_dir(self) -> Path:
        return self.root / "responses"

    @property
    def is_demo(self) -> bool:
        return self.slug == DEMO_SLUG

    @property
    def response_customer_name(self) -> str:
        return "DEMO" if self.is_demo else self.customer_name


def validate_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not _SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Engagement names may contain only lowercase letters, numbers, and single hyphens."
        )
    return normalized


def engagement_path(slug: str) -> Path:
    return config.ENGAGEMENTS_DIR / slug


def load_engagement(slug: str) -> Engagement:
    slug = validate_slug(slug)
    root = engagement_path(slug)
    metadata_path = root / "engagement.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Engagement '{slug}' does not exist.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return Engagement(slug=slug, customer_name=metadata["customer_name"], root=root)


def active_engagement() -> Engagement:
    slug = DEMO_SLUG
    if config.ACTIVE_ENGAGEMENT_PATH.is_file():
        slug = config.ACTIVE_ENGAGEMENT_PATH.read_text(encoding="utf-8").strip()
    return load_engagement(slug)


def set_active_engagement(slug: str) -> Engagement:
    engagement = load_engagement(slug)
    config.ACTIVE_ENGAGEMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.ACTIVE_ENGAGEMENT_PATH.write_text(f"{engagement.slug}\n", encoding="utf-8")
    return engagement


def create_engagement(slug: str, customer_name: str | None = None) -> Engagement:
    slug = validate_slug(slug)
    if slug == DEMO_SLUG:
        raise ValueError("'demo' is reserved for the built-in demonstration engagement.")
    root = engagement_path(slug)
    if root.exists():
        raise FileExistsError(f"Engagement '{slug}' already exists.")

    customer_name = customer_name.strip() if customer_name else slug.replace("-", " ").title()
    if not customer_name or any(character in customer_name for character in "\r\n\t"):
        raise ValueError("Customer display name must be non-empty and fit on one line.")
    for directory in (
        root / "customer_standards",
        root / "private_context",
        root / "chroma_db",
        root / "responses",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (root / "engagement.json").write_text(
        json.dumps({"customer_name": customer_name}, indent=2) + "\n",
        encoding="utf-8",
    )
    return set_active_engagement(slug)


def list_engagements() -> list[Engagement]:
    if not config.ENGAGEMENTS_DIR.exists():
        return []
    engagements = []
    for metadata_path in sorted(config.ENGAGEMENTS_DIR.glob("*/engagement.json")):
        engagements.append(load_engagement(metadata_path.parent.name))
    return engagements
