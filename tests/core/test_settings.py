from pathlib import Path

from core.settings import load_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _parse_env_example() -> dict[str, str]:
    env = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        env[name.strip()] = value.split("#", 1)[0].strip()
    return env


def test_env_example_lists_new_variables():
    env = _parse_env_example()
    for name in ("ADMIN_TELEGRAM_IDS", "DNG_REMINDER_DAYS", "DAILY_CHECK_TIME", "TZ", "QUEUE_LIMIT"):
        assert name in env


def test_defaults_from_env_example():
    s = load_settings(_parse_env_example())
    assert s.dng_reminder_days == 60
    assert s.daily_check_time == "03:00"
    assert s.tz == "Europe/Vienna"
    assert s.queue_limit == 10
    assert s.rclone_remote == "motorhof"
    assert s.drive_root == "MOTORHOF_AUTO"
    assert s.source_subdir == "Фотографии"
    assert s.output_subdir == "На выгрузку"
    assert s.tmp_dir == Path("/app/data/tmp")
    assert s.db_path == Path("/app/data/motorhof.sqlite")
    assert s.log_level == "INFO"
    assert s.allowed_telegram_ids == frozenset()
    assert s.admin_telegram_ids == frozenset()


def test_every_env_example_variable_is_read():
    env = {name: "" for name in _parse_env_example()}
    env.update(
        TELEGRAM_BOT_TOKEN="123:abc",
        ALLOWED_TELEGRAM_IDS="11, 22,33",
        ADMIN_TELEGRAM_IDS="22",
        RCLONE_REMOTE="r",
        DRIVE_ROOT="root",
        SOURCE_SUBDIR="src",
        OUTPUT_SUBDIR="out",
        TMP_DIR="/tmp/x",
        DB_PATH="/tmp/x.sqlite",
        LOG_LEVEL="debug",
        DNG_REMINDER_DAYS="30",
        DAILY_CHECK_TIME="04:15",
        TZ="UTC",
        QUEUE_LIMIT="3",
    )
    assert set(env) == set(_parse_env_example())
    s = load_settings(env)
    assert s.telegram_bot_token == "123:abc"
    assert s.allowed_telegram_ids == frozenset({11, 22, 33})
    assert s.admin_telegram_ids == frozenset({22})
    assert (s.rclone_remote, s.drive_root, s.source_subdir, s.output_subdir) == ("r", "root", "src", "out")
    assert s.tmp_dir == Path("/tmp/x") and s.db_path == Path("/tmp/x.sqlite")
    assert s.log_level == "DEBUG"
    assert (s.dng_reminder_days, s.daily_check_time, s.tz, s.queue_limit) == (30, "04:15", "UTC", 3)


def test_broken_id_list_gives_empty_set():
    assert load_settings({"ALLOWED_TELEGRAM_IDS": "abc,;;"}).allowed_telegram_ids == frozenset()
    assert load_settings({"ALLOWED_TELEGRAM_IDS": ""}).allowed_telegram_ids == frozenset()
    assert load_settings({}).allowed_telegram_ids == frozenset()


def test_hash_inside_value_is_kept_and_spaced_comment_is_cut():
    s = load_settings({
        "QUEUE_LIMIT": "10   # комментарий",
        "TMP_DIR": "/data/a#b",
        "OUTPUT_SUBDIR": "На выгрузку #2",
        "SOURCE_SUBDIR": '"Фото #1"',
        "DRIVE_ROOT": "'root # x'",
    })
    assert s.queue_limit == 10
    assert s.tmp_dir == Path("/data/a#b")
    assert s.output_subdir == "На выгрузку"
    assert s.source_subdir == "Фото #1"
    assert s.drive_root == "root # x"


def test_max_upload_mb_default_and_override():
    assert load_settings(_parse_env_example()).max_upload_mb == 20
    assert load_settings({}).max_upload_mb == 20
    assert load_settings({"MAX_UPLOAD_MB": "15"}).max_upload_mb == 15
    assert load_settings({"MAX_UPLOAD_MB": "много"}).max_upload_mb == 20


def test_max_upload_mb_above_bot_api_limit_is_capped(caplog):
    with caplog.at_level("WARNING"):
        assert load_settings({"MAX_UPLOAD_MB": "50"}).max_upload_mb == 20
    assert "MAX_UPLOAD_MB=50" in caplog.text
