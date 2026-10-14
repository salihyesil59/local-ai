"""Images sent with a message: checked, kept with the chat, and given to the model."""

import base64

import pytest

pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from local_ai.core import images as img  # noqa: E402
from local_ai.core.text import IMAGE_CHARS, size  # noqa: E402
from local_ai.research.config import AgentSettings  # noqa: E402
from local_ai.web.app import create_app  # noqa: E402
from test_web import read_events  # noqa: E402
from test_web_chats import FINISHING  # noqa: E402
from test_web_code import ScriptedClient, own_workspace  # noqa: E402,F401

# The smallest valid PNG: one transparent pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_only_images_of_known_types_and_sizes_are_taken():
    assert img.decode(None) == [] and img.decode([URL]) == [(PNG, "png")]
    for bad in (["data:text/plain;base64,aGk="], ["https://example.com/a.png"], "not a list", [URL] * 7, [1]):
        with pytest.raises(ValueError):
            img.decode(bad)
    with pytest.raises(ValueError):
        img.decode(["data:image/png;base64,"])  # empty


def test_a_message_with_images_has_its_text_and_the_images(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(PNG)
    assert img.with_images("hi", None) == "hi"
    content = img.with_images("Fix this error", [path])
    assert content[0] == {"type": "text", "text": "Fix this error"}
    assert content[1]["image_url"]["url"] == URL
    assert img.text_of(content) == "Fix this error" and img.text_of(None) == "" and img.count(content) == 1
    assert size([{"role": "user", "content": content}]) == len("Fix this error") + IMAGE_CHARS


@pytest.mark.anyio
async def test_the_research_agent_gets_what_the_images_show(tmp_path):
    from local_ai.core.llm import LLM

    path = tmp_path / "plot.png"
    path.write_bytes(PNG)
    client = ScriptedClient([("A plot of E(z) rising from 1 at z=0.", [])])
    text = await img.describe(LLM(client, "m"), [path], "What does the plot show?")
    sent = client.requests[0]["messages"][0]["content"]
    assert "What does the plot show?" in sent[0]["text"] and sent[1]["type"] == "image_url"
    question = img.described("What does the plot show?", 1, text)
    assert question.startswith("What does the plot show?") and "attached an image" in question and "E(z)" in question


def test_a_code_chat_takes_images_with_its_messages(tmp_path):
    client = ScriptedClient(list(FINISHING))
    with TestClient(create_app([], AgentSettings(model="test"), client=client)) as ui:
        body = {"mode": "code", "question": "", "folder": str(tmp_path / "app"), "images": [URL]}
        assert ui.post("/api/runs", json={**body, "images": ["data:image/svg+xml;base64,PHN2Zz4="]}).status_code == 400
        run = ui.post("/api/runs", json=body).json()
        read_events(ui, run["id"])
        assert len(run["images"]) == 1 and run["question"].startswith("Look at the attached image")
        first = client.requests[0]["messages"][1]["content"]  # the task, with the image
        assert first[1] == {"type": "image_url", "image_url": {"url": URL}}
        name = run["images"][0]
        assert ui.get(f"/api/runs/{run['id']}/images/{name}").content == PNG
        assert ui.get(f"/api/runs/{run['id']}/images/..%2Findex.json").status_code == 404
        assert ui.get(f"/api/runs/{run['id']}/images/000000000000.png").status_code == 404

        # while the agent works, a message with an image steers it; the event shows the image
        chat = ui.app.state.runs[run["id"]]
        chat.status = "running"
        assert ui.post(f"/api/runs/{run['id']}/messages", json={"question": "", "images": [URL]}).status_code == 202
        (message, paths), = chat.agent.steering
        assert message.startswith("Look at the attached image") and paths[0].read_bytes() == PNG
        assert chat.events[-1]["kind"] == "steer" and len(chat.events[-1]["images"]) == 1
        chat.status = "done"

        folder = paths[0].parent
        assert ui.delete(f"/api/runs/{run['id']}").json() == {"ok": True} and not folder.exists()
