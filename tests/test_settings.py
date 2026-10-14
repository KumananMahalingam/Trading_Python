import pytest

from ml_service.settings import PROJECT_ROOT, SettingsError, load_settings


def test_defaults_without_env():
    s = load_settings(environ={}, env_file=None)
    assert s.lookback_days == 730
    assert s.news_sleep_time == 12.0
    assert s.max_related_companies == 5
    assert s.ensemble_size == 3
    assert s.window_size == 30
    assert s.model_dir == PROJECT_ROOT / "models"
    assert s.jobs_db_path == PROJECT_ROOT / "models" / "jobs.db"
    assert not s.has_polygon and not s.has_groq


def test_env_overrides():
    s = load_settings(
        environ={
            "POLYGON_API_KEY": "poly-secret",
            "GROQ_API_KEY": "groq-secret",
            "ML_INTERNAL_TOKEN": "tok",
            "LOOKBACK_DAYS": "365",
            "NEWS_SLEEP_TIME": "0.5",
            "MODEL_DIR": "custom_models",
        },
        env_file=None,
    )
    assert s.has_polygon and s.has_groq
    assert s.internal_token == "tok"
    assert s.lookback_days == 365
    assert s.news_sleep_time == 0.5
    assert s.model_dir == PROJECT_ROOT / "custom_models"
    assert s.jobs_db_path == PROJECT_ROOT / "custom_models" / "jobs.db"


def test_secrets_not_in_repr():
    s = load_settings(
        environ={"POLYGON_API_KEY": "poly-secret", "GROQ_API_KEY": "groq-secret", "ML_INTERNAL_TOKEN": "tok-secret"},
        env_file=None,
    )
    text = repr(s)
    assert "poly-secret" not in text
    assert "groq-secret" not in text
    assert "tok-secret" not in text


def test_env_file_loaded_and_environ_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LOOKBACK_DAYS=400\nNEWS_SLEEP_TIME=3\n# comment\n", encoding="utf-8")
    s = load_settings(environ={"NEWS_SLEEP_TIME": "1"}, env_file=env_file)
    assert s.lookback_days == 400
    assert s.news_sleep_time == 1.0


def test_missing_env_file_is_ignored(tmp_path):
    s = load_settings(environ={}, env_file=tmp_path / "nope.env")
    assert s.lookback_days == 730


@pytest.mark.parametrize(
    "key,value",
    [("LOOKBACK_DAYS", "abc"), ("LOOKBACK_DAYS", "30"), ("NEWS_SLEEP_TIME", "-1"), ("ENSEMBLE_SIZE", "0")],
)
def test_invalid_values_raise(key, value):
    with pytest.raises(SettingsError):
        load_settings(environ={key: value}, env_file=None)
