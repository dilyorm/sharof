import asyncio
import base64

import httpx
import pytest

from sharof import tools
from sharof.config import Settings


class FakeNotifier:
    def __init__(self):
        self.texts = []
        self.photos = []

    async def text(self, body):
        self.texts.append(body)

    async def photo(self, png, caption):
        self.photos.append((png, caption))


def _settings(**kw):
    base = dict(
        telegram_token="t", openrouter_api_key="k", openrouter_model="m:free",
        database_url="d", context_msgs=20, interject_cooldown_sec=300,
        trigger_words=[], bot_username="sharof_bot",
        owner_ids=frozenset({7}), pc_url="http://pc", pc_secret="s3cret",
    )
    base.update(kw)
    return Settings(**base)


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "sudo mkfs.ext4 /dev/sda1",
    "shutdown now",
    "dd if=/dev/zero of=/dev/sda",
    "Stop-Computer -Force",
    "Format-Volume -DriveLetter C",
    "Remove-Item C:\\ -Recurse",
])
def test_deny_blocks_catastrophic(cmd):
    assert tools.denied(cmd)


@pytest.mark.parametrize("cmd", [
    "ls -la /opt/sharof",
    "Get-Process | Select-Object -First 5",
    "systemctl status sharof",
    "rm -rf ./build",
])
def test_deny_allows_normal(cmd):
    assert not tools.denied(cmd)


@pytest.mark.asyncio
async def test_pc_run_refuses_destructive_without_calling_pc():
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("PC must not be contacted")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute(
            "pc_run", {"command": "Stop-Computer"}, client, _settings()
        )
    assert "refused" in out


@pytest.mark.asyncio
async def test_pc_offline_maps_to_message():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_intent", {"text": "open youtube"}, client, _settings())
    assert out == "PC offline (tunnel down)"


@pytest.mark.asyncio
async def test_pc_intent_sends_secret_and_returns_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["secret"] = request.headers.get("x-miki-secret")
        return httpx.Response(200, json={"reply": "Opening YouTube"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_intent", {"text": "open youtube"}, client, _settings())
    assert out == "Opening YouTube"
    assert seen["url"] == "http://pc/intent"
    assert seen["secret"] == "s3cret"


@pytest.mark.asyncio
async def test_pc_bad_secret_is_reported():
    def handler(request):
        return httpx.Response(401, text="nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_run", {"command": "echo hi"}, client, _settings())
    assert "secret mismatch" in out


@pytest.mark.asyncio
async def test_shell_tools_hidden_and_refused_when_disabled():
    settings = _settings(allow_shell=False)
    names = [t["function"]["name"] for t in tools.schemas(settings)]
    assert "pc_run" not in names and "server_run" not in names
    assert "pc_intent" in names

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        out = await tools.execute("server_run", {"command": "ls"}, c, settings)
    assert "shell is disabled" in out


@pytest.mark.asyncio
async def test_claude_code_starts_a_job_and_reports_the_result_later(monkeypatch):
    monkeypatch.setattr(tools, "_JOB_POLL_SEC", 0)
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/claude":
            return httpx.Response(200, json={"job_id": "j1"})
        polls["n"] += 1
        if polls["n"] < 2:
            return httpx.Response(200, json={"done": False})
        return httpx.Response(200, json={
            "done": True, "ok": True, "output": "fixed the bug",
            "session_id": "sess-9", "project": "C:/kaggle/rogii",
        })

    notifier = FakeNotifier()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute(
            "claude_code",
            {"project": "C:/kaggle/rogii", "prompt": "fix the bug"},
            client, _settings(), notifier,
        )
        # The tool returns at once; the answer lands in the chat when Claude is done.
        assert "j1" in out
        assert not notifier.texts
        for _ in range(50):
            await asyncio.sleep(0)
            if notifier.texts:
                break

    assert len(notifier.texts) == 1
    assert "fixed the bug" in notifier.texts[0]
    assert "sess-9" in notifier.texts[0]  # so the owner can resume the thread


@pytest.mark.asyncio
async def test_screenshot_is_sent_as_a_photo_not_pasted_as_text():
    png = b"\x89PNG\r\n\x1a\n fake"

    def handler(request):
        return httpx.Response(200, json={"png_b64": base64.b64encode(png).decode()})

    notifier = FakeNotifier()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_screenshot", {}, client, _settings(), notifier)

    assert notifier.photos == [(png, "screen")]
    assert "sent" in out


@pytest.mark.asyncio
async def test_pc_look_sends_the_screen_to_the_vision_model():
    png = base64.b64encode(b"fakepng").decode()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/screenshot":
            return httpx.Response(200, json={"png_b64": png})
        body = request.read().decode()
        seen["model"] = request.url.host
        seen["has_image"] = "image_url" in body and png in body
        seen["vision_model"] = "gemini" in body
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "VS Code is focused, tests are red."}}]
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute(
            "pc_look", {"question": "what is focused?"}, client,
            _settings(openrouter_vision_model="google/gemini-2.5-flash"),
        )

    assert out == "VS Code is focused, tests are red."
    assert seen["has_image"], "the screenshot must actually reach the model"
    assert seen["vision_model"], "must use the vision model, not the text-only tool model"


@pytest.mark.asyncio
async def test_pc_look_reports_an_offline_pc_instead_of_calling_the_model():
    def handler(request):
        if request.url.path == "/screenshot":
            raise httpx.ConnectError("refused", request=request)
        raise AssertionError("must not call the model with no screen")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_look", {"question": "?"}, client, _settings())
    assert out == "PC offline (tunnel down)"


@pytest.mark.asyncio
async def test_pc_keys_posts_the_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"sent": "sent"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await tools.execute("pc_keys", {"keys": "hi{ENTER}"}, client, _settings())

    assert seen["path"] == "/keys"
    assert b"hi{ENTER}" in seen["body"]
    assert out == "keys sent"


@pytest.mark.asyncio
async def test_read_write_file_roundtrip(tmp_path):
    target = tmp_path / "note.txt"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        wrote = await tools.execute(
            "write_file", {"path": str(target), "content": "hello"}, c, _settings()
        )
        read = await tools.execute("read_file", {"path": str(target)}, c, _settings())
        missing = await tools.execute("read_file", {"path": str(tmp_path / "nope")}, c, _settings())
    assert "wrote" in wrote
    assert read == "hello"
    assert missing.startswith("error:")
