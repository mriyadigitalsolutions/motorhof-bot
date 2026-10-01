"""Упаковка: Dockerfile, docker-compose.yml, .env.example, .dockerignore, README согласованы
с core.settings. Образ бота не собирается (apt/pip за прокси) — файлы проверяются статически;
.dockerignore дополнительно проверяется настоящим docker build контекста, если демон доступен."""
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
# Семантика как у Docker (moby/patternmatcher): пути относительно корня контекста, `*` и `?` не
# переходят через `/`, `**` — любое число каталогов (в том числе ноль), правило действует и на всё
# внутри совпавшего каталога, `!` возвращает путь, при нескольких совпадениях решает последнее.

def dockerignore_rules() -> list[tuple[bool, re.Pattern]]:
    rules = []
    for raw in read(".dockerignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:].strip() if negate else line
        pattern = pattern.strip("/")
        pattern = re.sub(r"/+", "/", pattern)
        rules.append((negate, re.compile(glob_to_regex(pattern))))
    return rules


def glob_to_regex(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.index("]", i + 1)
            body = pattern[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("^") else body) + "]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "^" + "".join(out) + "$"


def dockerignored(path: str, rules) -> bool:
    parts = path.split("/")
    candidates = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    excluded = False
    for negate, rx in rules:
        if any(rx.match(c) for c in candidates):
            excluded = not negate
    return excluded


# Ожидаемое — из требований к образу, а не из самого .dockerignore
MUST_EXCLUDE = (
    ".env", ".env.prod", "bot/.env", "deploy/old/.env", "deploy/.env.local",
    "rclone.conf", "rclone.conf.bak", "rclone/rclone.conf", "rclone/rclone.conf.old123",
    "backup/rclone.conf", "backup/deep/rclone.conf.old", "rclone/any-other-file",
    "data/motorhof.sqlite", "data/tmp/MH_1022/IMG_1.DNG",
    "tests/fixtures/IMG_4561.DNG", ".autopilot/x/tickets.md", ".git/config",
)
MUST_KEEP = (
    ".env.example", "requirements.txt", "bot/main.py", "core/settings.py",
    "modules/photos/variants.yaml", "modules/photos/convert.py", "modules/__init__.py",
)


def test_dockerignore_rule_semantics_self_check():
    # проверка самого разборщика на примерах из документации Docker
    rules = [(False, re.compile(glob_to_regex("**/*.go"))), (False, re.compile(glob_to_regex("*.md"))),
             (True, re.compile(glob_to_regex("README*.md"))), (False, re.compile(glob_to_regex("README-secret.md")))]
    assert dockerignored("a/b/c.go", rules) and dockerignored("c.go", rules)
    assert dockerignored("CHANGES.md", rules) and not dockerignored("a/CHANGES.md", rules)
    assert not dockerignored("README.md", rules) and dockerignored("README-secret.md", rules)


def test_dockerignore_keeps_secrets_and_bulk_out_of_image():
    rules = dockerignore_rules()
    leaked = [p for p in MUST_EXCLUDE if not dockerignored(p, rules)]
    assert not leaked, f".dockerignore пропускает в образ: {leaked}"
    lost = [p for p in MUST_KEEP if dockerignored(p, rules)]
    assert not lost, f".dockerignore не пускает нужное: {lost}"


def docker_available() -> bool:
    import shutil
    import subprocess
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not docker_available(), reason="нет демона Docker")
def test_dockerignore_real_build_context(tmp_path):
    """Настоящий docker build: FROM scratch + COPY . /ctx, затем список файлов из docker export."""
    import shutil
    import subprocess
    import tarfile
    import uuid

    ctx = tmp_path / "ctx"
    ctx.mkdir()
    shutil.copy(ROOT / ".dockerignore", ctx / ".dockerignore")
    for rel in MUST_EXCLUDE + MUST_KEEP:
        f = ctx / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "Dockerfile").write_text("FROM scratch\nCOPY . /ctx\nCMD [\"none\"]\n", encoding="utf-8")
    tag = f"motorhof-dockerignore-test:{uuid.uuid4().hex[:12]}"
    run = lambda *a, **kw: subprocess.run(list(a), capture_output=True, timeout=300, **kw)
    r = run("docker", "build", "-q", "-t", tag, "-f", str(build_dir / "Dockerfile"), str(ctx))
    assert r.returncode == 0, r.stderr.decode(errors="replace")[-2000:]
    container = None
    try:
        r = run("docker", "create", tag)
        assert r.returncode == 0, r.stderr.decode(errors="replace")
        container = r.stdout.decode().strip()
        tar_path = tmp_path / "fs.tar"
        r = run("docker", "export", "-o", str(tar_path), container)
        assert r.returncode == 0, r.stderr.decode(errors="replace")
        with tarfile.open(tar_path) as tar:
            files = {m.name.removeprefix("ctx/") for m in tar.getmembers()
                     if m.isfile() and m.name.startswith("ctx/")}
    finally:
        if container:
            run("docker", "rm", "-f", container)
        run("docker", "rmi", "-f", tag)
    leaked = sorted(set(MUST_EXCLUDE) & files)
    assert not leaked, f"в образ попали: {leaked}"
    missing = sorted(set(MUST_KEEP) - files)
    assert not missing, f"в образ не попали: {missing}"
    # разборщик выше согласен с настоящим Docker
    rules = dockerignore_rules()
    assert {p for p in MUST_EXCLUDE + MUST_KEEP if not dockerignored(p, rules)} == files - {".dockerignore"}


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


def test_readme_rclone_setup_without_rclone_on_host():
    text = read("README.md")
    for needle in (
        # конфиг создаётся rclone из образа и сразу ложится в смонтированный ./rclone
        "docker compose run --rm --no-deps photos rclone config",
        "docker compose run --rm --no-deps photos rclone lsd motorhof:MOTORHOF_AUTO",
        "drive", "team_drive", "office@motorhof.at", "`motorhof`",
        # сервер без браузера
        "Use web browser to automatically authenticate?",
        'rclone authorize "drive"',
        "rclone.org",
    ):
        assert needle in text, f"README: нет «{needle}»"
    assert "~/.config/rclone" not in text, "на хосте rclone нет — копировать оттуда нечего"
    # .env должен существовать до первого docker compose run (env_file обязателен)
    assert text.index("cp .env.example .env") < text.index("photos rclone config")


def test_readme_warns_about_oauth_testing_mode_and_og_accounts():
    text = read("README.md")
    for needle in ("Testing", "7 дней", "Production", "client_id", "OG"):
        assert needle in text, f"README: нет «{needle}»"


def test_readme_own_oauth_client_and_rate_limit_check():
    # ADR 0007: встроенный client_id rclone упирается в общую квоту — нужен свой клиент Internal
    text = read("README.md")
    for needle in (
        "Google Drive API", "Internal", "Desktop app", "myaccount.google.com/permissions",
        "Already have a token - refresh?",
        "rclone lsjson motorhof:MOTORHOF_AUTO --dirs-only -vv 2>&1 >/dev/null | grep -ciE 'rate ?limit|403'",
    ):
        assert needle in text, f"README: нет «{needle}»"
    assert "оставить пустыми" not in text, "встроенный клиент rclone больше не советуем"


def test_readme_states_server_architecture():
    text = read("README.md")
    for needle in ("x86_64", "CX22", "linux-amd64"):
        assert needle in text, f"README: нет «{needle}»"


def test_readme_predeploy_checks():
    text = read("README.md")
    for needle in ("git log -p", "v1.0"):
        assert needle in text, f"README: нет «{needle}»"


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
