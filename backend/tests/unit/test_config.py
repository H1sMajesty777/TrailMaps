import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG = "x" * 40


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_dev_allows_defaults():
    assert make(environment="dev").docs_enabled is True


def test_docs_only_in_dev():
    for env in ("test", "staging", "production"):
        kwargs = {"jwt_secret": STRONG} if env in ("staging", "production") else {}
        assert make(environment=env, **kwargs).docs_enabled is False


def test_production_rejects_default_or_short_secret():
    with pytest.raises(ValidationError):
        make(environment="production", jwt_secret="dev-insecure-secret-change-me-0123456789abcdef")
    with pytest.raises(ValidationError):
        make(environment="production", jwt_secret="short")


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError):
        make(environment="production", jwt_secret=STRONG, cors_origins="*")


def test_production_rejects_weak_bootstrap_password():
    with pytest.raises(ValidationError):
        make(environment="production", jwt_secret=STRONG, bootstrap_admin_password="short")


def test_cors_origin_list_parsing():
    s = make(cors_origins=" https://a.example.com, https://b.example.com ,,")
    assert s.cors_origin_list == ["https://a.example.com", "https://b.example.com"]
    assert make().cors_origin_list == []


def test_production_accepts_strong_config():
    s = make(environment="production", jwt_secret=STRONG, cors_origins="https://app.example.com")
    assert s.is_production_like
