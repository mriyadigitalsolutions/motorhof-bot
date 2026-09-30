"""Упаковка: Dockerfile, docker-compose.yml, .env.example, .dockerignore, README согласованы
с core.settings. Образ здесь не собирается (нет демона Docker) — проверяем файлы статически."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from core.settings import Settings, load_settings

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ("TELEGRAM_BOT_TOKEN", "ALLOWED_TELEGRAM_IDS", "ADMIN_TELEGRAM_IDS")
RCLONE_CONF = "/config/rclone/rclone.conf"
DATA_MOUNT = "/app/data"


def read(name: str) -> str:
    path = ROOT / name
    if not path.exists():
        pytest.fail(f"нет файла {name}")
    return path.read_text(encoding="utf-8")


def env_example() -> dict[str, str]:
    """.env.example как его читает docker compose: строки ИМЯ=значение, комментарии — отдельной строкой."""
    result = {}
    for line in read(".env.example").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep, f"строка без '=': {line!r}"
        result[name.strip()] = value
    return result


def setting_names() -> set[str]:
    return {f.name.upper() for f in dataclasses.fields(Settings)}


def compose() -> dict:
    return yaml.safe_load(read("docker-compose.yml"))


def service() -> dict:
    return compose()["services"]["photos"]


# --- .env.example ---

def test_env_example_lists_every_setting():
    assert set(env_example()) == setting_names()


def test_env_example_secrets_are_empty():
    env = env_example()
    for name in SECRETS:
        assert env[name] == "", f"{name} в .env.example должен быть пустым"


def test_env_example_values_have_no_inline_comments():
    # docker compose и core.settings по-разному режут «значение  # комментарий» — не рискуем
    for name, value in env_example().items():
        assert "#" not in value, f"{name}: комментарий должен стоять отдельной строкой"


def test_env_example_values_match_settings_defaults():
    assert load_settings(env_example()) == load_settings({})


# --- docker-compose.yml ---

def test_compose_has_single_photos_service():
    assert list(compose()["services"]) == ["photos"]


def test_compose_survives_reboot_and_reads_env_file():
    svc = service()
    assert svc["restart"] == "unless-stopped"
    env_file = svc["env_file"]
    assert env_file in (".env", [".env"]) or env_file == [{"path": ".env"}]
    assert svc["build"] in (".", {"context": "."})


def volumes() -> dict[str, str]:
    """container_path -> 'host[:mode]' для коротких записей volumes."""
    result = {}
    for item in service()["volumes"]:
        host, container, *mode = item.split(":")
        result[container] = ":".join([host, *mode])
    return result


def test_compose_mounts_data_and_writable_rclone_conf():
    vols = volumes()
    assert vols[DATA_MOUNT] == "./data"
    # rclone сохраняет токен переименованием файла — монтируем каталог, на запись (без :ro)
    assert vols[str(Path(RCLONE_CONF).parent)] == "./rclone"
    assert RCLONE_CONF not in vols


def compose_environment() -> dict[str, str]:
    env = service().get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


def test_compose_has_no_secrets():
    env = compose_environment()
    assert not set(env) & set(SECRETS), "секреты только в .env"
    text = read("docker-compose.yml")
    assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", text), "похоже на токен бота"


def test_rclone_config_defined_once_and_points_to_mounted_dir():
    # одно место правды: иначе при правке одного из двух они молча разъедутся
    places = {
        "Dockerfile": dockerfile_env().get("RCLONE_CONFIG"),
        "docker-compose.yml": compose_environment().get("RCLONE_CONFIG"),
    }
    defined = {k: v for k, v in places.items() if v is not None}
    assert len(defined) == 1, f"RCLONE_CONFIG задан в {sorted(defined) or 'нигде'}, нужно ровно одно место"
    value = next(iter(defined.values()))
    assert value == RCLONE_CONF
    assert volumes()[str(Path(value).parent)] == "./rclone"


def test_data_paths_live_on_data_volume():
    # .env.example попадает в .env как есть — его пути и должны лежать на volume ./data
    assert volumes()[DATA_MOUNT] == "./data"
    env = env_example()
    for name in ("TMP_DIR", "DB_PATH"):
        assert Path(env[name]).is_relative_to(DATA_MOUNT), f"{name} должен лежать в ./data"


# --- Dockerfile ---

def dockerfile_instructions() -> list[tuple[str, str]]:
    """(ИНСТРУКЦИЯ, аргументы) с учётом переносов строк через обратный слэш."""
    text = re.sub(r"\\\n", " ", read("Dockerfile"))
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            op, _, args = line.partition(" ")
            result.append((op.upper(), args.strip()))
    return result


def dockerfile_env() -> dict[str, str]:
    env = {}
    for op, args in dockerfile_instructions():
        if op == "ENV":
            for pair in args.split():
                name, _, value = pair.partition("=")
                env[name] = value.strip('"')
    return env


def test_dockerfile_base_and_locale():
    ops = dockerfile_instructions()
    assert ops[0] == ("FROM", "python:3.12-slim")
    env = dockerfile_env()
    assert env["LANG"] == "C.UTF-8"  # кириллица в путях Drive
    assert env["TZ"] == "Europe/Vienna"


def test_dockerfile_installs_pinned_rclone_from_github_and_checks_it():
    ops = dockerfile_instructions()
    args_defaults = dict(a.split("=", 1) for op, a in ops if op == "ARG" and "=" in a)
    runs = " ".join(args for op, args in ops if op == "RUN")
    runs = re.sub(r"\$\{(\w+)\}", lambda m: args_defaults.get(m.group(1), m.group(0)), runs)
    assert "https://github.com/rclone/rclone/releases/download/v1.71.1/rclone-v1.71.1-linux-amd64.zip" in runs
    assert "sha256sum -c" in runs
    assert "downloads.rclone.org" not in runs
    assert "rclone version" in runs
    # версия из Debian слишком старая для SHA256 у Drive — только официальный релиз
    assert not re.search(r"apt-get install[^&]*\brclone\b", runs)


def test_dockerfile_installs_requirements_and_starts_bot():
    ops = dockerfile_instructions()
    runs = " ".join(args for op, args in ops if op == "RUN")
    assert "pip install" in runs and "-r requirements.txt" in runs
    workdir = [args for op, args in ops if op == "WORKDIR"][-1]
    assert Path(DATA_MOUNT).parent == Path(workdir)
    cmd = [args for op, args in ops if op == "CMD"][-1]
    assert cmd.replace(" ", "") == '["python","-m","bot.main"]'


# --- .dockerignore ---

def test_dockerignore_keeps_secrets_and_bulk_out_of_image():
    lines = {l.strip().rstrip("/") for l in read(".dockerignore").splitlines()
             if l.strip() and not l.startswith("#")}
    for pattern in (".env", "rclone.conf", "rclone", "data", ".autopilot", ".git"):
        assert pattern in lines, f".dockerignore: нет {pattern}"
    assert "tests/fixtures" in lines or "tests" in lines


# --- README.md ---

def test_readme_covers_deploy_update_and_checks():
    text = read("README.md")
    for needle in (
        "docker compose up -d --build",
        "git pull && docker compose up -d --build",
        "docker compose logs -f photos",
        "systemctl enable docker",
        "cp .env.example .env",
        "rclone lsd motorhof:MOTORHOF_AUTO",
        "python -m modules.photos MH_1022",
        "/status",
        "variants.yaml",
        "modules/_template",
        "modules/__init__.py",
    ):
        assert needle in text, f"README: нет «{needle}»"
    for name in setting_names():
        assert name in text, f"README: не описана переменная {name}"


def test_git_ignores_rclone_dir_config():
    import shutil
    import subprocess
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("нет git-репозитория")
    for path in ("rclone/rclone.conf", "rclone/rclone.conf.old123", "rclone.conf", ".env", "data/x"):
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
        assert r.returncode == 0, f"git не игнорирует {path}"


def test_dockerfile_vars_do_not_look_like_rclone_flags():
    # rclone читает любые RCLONE_* как свои флаги, а ARG видны в RUN как переменные окружения
    names = []
    for op, args in dockerfile_instructions():
        if op == "ARG":
            names.append(args.split("=", 1)[0])
        elif op == "ENV":
            names += [pair.split("=", 1)[0] for pair in args.split()]
    bad = [n for n in names if n.startswith("RCLONE_") and n != "RCLONE_CONFIG"]
    assert not bad, f"переименуй: {bad}"
