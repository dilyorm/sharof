import httpx
import pytest

from sharof import tools
from sharof.config import Settings


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
