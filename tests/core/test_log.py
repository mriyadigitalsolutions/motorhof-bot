import io
import logging

from core.log import redact, setup_logging

BOT_TOKEN = "7123456789:AAHfakeFAKEfake_fake-fakefakeFAKE12"  # 10 цифр : 35 символов


def test_redacts_given_secret_values():
    out = redact("token is s3cr3t-value here", ["s3cr3t-value"])
    assert "s3cr3t-value" not in out
    assert "token is" in out and "here" in out


def test_redacts_bot_token_shape_without_being_told():
    out = redact(f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", [])
    assert BOT_TOKEN not in out
    assert "7123456789" not in out


def test_redacts_rclone_oauth_material():
    stderr = (
        'Failed to refresh: access_token=ya29.a0AfB_byFAKE refresh_token: "1//0gFAKErefresh" '
        "client_secret=GOCSPX-FAKEsecret\n"
        'token = {"access_token":"ya29.other","token_type":"Bearer","refresh_token":"1//09zz","expiry":"2026"}\n'
        "loose ya29.LOOSEtoken and 1//LOOSErefresh\n"
    )
    out = redact(stderr, [])
    for leaked in ("ya29.a0AfB_byFAKE", "1//0gFAKErefresh", "GOCSPX-FAKEsecret", "ya29.other",
                   "1//09zz", "ya29.LOOSEtoken", "1//LOOSErefresh"):
        assert leaked not in out, leaked
    assert "Failed to refresh" in out


def test_redacts_json_token_block():
    text = 'config: "token": {"access_token": "abc", "expiry": "2026-01-01"} end'
    out = redact(text, [])
    assert '"access_token": "abc"' not in out
    assert out.endswith("end")


def test_keeps_ordinary_text():
    assert redact("MH_1022: 24 JPEG, 1//2 done", []) == "MH_1022: 24 JPEG, 1//2 done"


def test_setup_logging_filters_root_logger_output():
    stream = io.StringIO()
    setup_logging("INFO", ["my-secret-xyz"], stream=stream)
    try:
        logging.getLogger("some.module").info("value %s and %s", "my-secret-xyz", BOT_TOKEN)
        try:
            raise RuntimeError(f"boom {BOT_TOKEN}")
        except RuntimeError:
            logging.getLogger("x").exception("failed")
        text = stream.getvalue()
        assert "value" in text and "failed" in text
        assert "my-secret-xyz" not in text
        assert BOT_TOKEN not in text
    finally:
        setup_logging("WARNING", [])
