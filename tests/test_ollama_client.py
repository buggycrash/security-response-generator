from types import SimpleNamespace

import pytest

from security_response_generator.llm import ollama_client


@pytest.fixture(autouse=True)
def local_ollama(monkeypatch):
    fake_client = SimpleNamespace()
    monkeypatch.setattr(ollama_client.ollama, "Client", lambda *, host, trust_env: fake_client)
    return fake_client


def test_local_client_is_pinned_to_loopback(monkeypatch):
    captured = {}

    def fake_client(*, host, trust_env):
        captured["host"] = host
        captured["trust_env"] = trust_env
        return object()

    monkeypatch.setattr(ollama_client.ollama, "Client", fake_client)

    ollama_client._local_client()

    assert captured["host"] == "http://127.0.0.1:11434"
    assert captured["trust_env"] is False


def test_embed_texts_calls_ollama_with_configured_model(monkeypatch):
    captured = {}

    def fake_embed(model, input):
        captured["model"] = model
        captured["input"] = input
        return {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}

    monkeypatch.setattr(ollama_client._local_client(), "embed", fake_embed, raising=False)

    result = ollama_client.embed_texts(["a", "b"])

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["model"] == ollama_client.EMBEDDING_MODEL
    assert captured["input"] == ["a", "b"]


def test_embed_texts_splits_large_input_into_batches(monkeypatch):
    monkeypatch.setattr(ollama_client, "EMBED_BATCH_SIZE", 2)
    calls = []

    def fake_embed(model, input):
        calls.append(list(input))
        return {"embeddings": [[float(len(text))] for text in input]}

    monkeypatch.setattr(ollama_client._local_client(), "embed", fake_embed, raising=False)

    result = ollama_client.embed_texts(["a", "bb", "ccc", "dddd", "e"])

    assert calls == [["a", "bb"], ["ccc", "dddd"], ["e"]]
    assert result == [[1.0], [2.0], [3.0], [4.0], [1.0]]


def test_embed_texts_single_batch_when_under_batch_size(monkeypatch):
    monkeypatch.setattr(ollama_client, "EMBED_BATCH_SIZE", 10)
    calls = []

    def fake_embed(model, input):
        calls.append(list(input))
        return {"embeddings": [[0.0] for _ in input]}

    monkeypatch.setattr(ollama_client._local_client(), "embed", fake_embed, raising=False)

    ollama_client.embed_texts(["a", "b", "c"])

    assert len(calls) == 1


def test_embed_texts_calls_on_batch_after_each_batch(monkeypatch):
    monkeypatch.setattr(ollama_client, "EMBED_BATCH_SIZE", 2)

    def fake_embed(model, input):
        return {"embeddings": [[0.0] for _ in input]}

    monkeypatch.setattr(ollama_client._local_client(), "embed", fake_embed, raising=False)

    batch_sizes = []
    ollama_client.embed_texts(["a", "bb", "ccc", "dddd", "e"], on_batch=batch_sizes.append)

    assert batch_sizes == [2, 2, 1]


def test_embed_texts_on_batch_not_required(monkeypatch):
    monkeypatch.setattr(
        ollama_client._local_client(),
        "embed",
        lambda model, input: {"embeddings": [[0.0]]},
        raising=False,
    )

    # Should not raise when on_batch is omitted.
    assert ollama_client.embed_texts(["a"]) == [[0.0]]


def test_embed_texts_empty_input_short_circuits(monkeypatch):
    def fake_embed(*args, **kwargs):
        raise AssertionError("should not be called for empty input")

    monkeypatch.setattr(ollama_client._local_client(), "embed", fake_embed, raising=False)

    assert ollama_client.embed_texts([]) == []


def test_embed_query_returns_single_vector(monkeypatch):
    monkeypatch.setattr(
        ollama_client._local_client(),
        "embed",
        lambda model, input: {"embeddings": [[1.0, 2.0]]},
        raising=False,
    )

    assert ollama_client.embed_query("hello") == [1.0, 2.0]


def test_chat_messages_calls_ollama_with_configured_model_and_raw_messages(monkeypatch):
    captured = {}

    def fake_chat(model, messages, options, format):
        captured["model"] = model
        captured["messages"] = messages
        captured["options"] = options
        captured["format"] = format
        return {"message": {"content": "response text"}}

    monkeypatch.setattr(ollama_client._local_client(), "chat", fake_chat, raising=False)

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
        {"role": "assistant", "content": '{"needs_info": true, "question": "what tool?"}'},
        {"role": "user", "content": "Acme Sentinel"},
    ]
    result = ollama_client.chat_messages(messages)

    assert result == "response text"
    assert captured["model"] == ollama_client.GENERATION_MODEL
    assert captured["messages"] == messages
    assert captured["options"] == {"num_ctx": ollama_client.NUM_CTX}
    assert captured["format"] is None


def test_chat_messages_passes_response_format_through(monkeypatch):
    captured = {}
    schema = {"type": "object", "properties": {"needs_info": {"type": "boolean"}}}

    def fake_chat(model, messages, options, format):
        captured["format"] = format
        return {"message": {"content": "response text"}}

    monkeypatch.setattr(ollama_client._local_client(), "chat", fake_chat, raising=False)

    ollama_client.chat_messages([{"role": "user", "content": "hi"}], response_format=schema)

    assert captured["format"] == schema


def test_chat_messages_num_ctx_respects_override(monkeypatch):
    monkeypatch.setattr(ollama_client, "NUM_CTX", 32768)
    captured = {}

    def fake_chat(model, messages, options, format):
        captured["options"] = options
        return {"message": {"content": "response text"}}

    monkeypatch.setattr(ollama_client._local_client(), "chat", fake_chat, raising=False)

    ollama_client.chat_messages([{"role": "user", "content": "hi"}])

    assert captured["options"] == {"num_ctx": 32768}


@pytest.mark.parametrize("model", ["gpt-oss:cloud", "gpt-oss:120b-cloud"])
def test_chat_messages_rejects_cloud_models_before_creating_client(monkeypatch, model):
    monkeypatch.setattr(ollama_client, "GENERATION_MODEL", model)
    monkeypatch.setattr(
        ollama_client,
        "_local_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be created")),
    )

    with pytest.raises(ValueError, match="refuses Ollama cloud model"):
        ollama_client.chat_messages([{"role": "user", "content": "private"}])


def test_embed_texts_rejects_cloud_model_before_creating_client(monkeypatch):
    monkeypatch.setattr(ollama_client, "EMBEDDING_MODEL", "embedding-model:cloud")
    monkeypatch.setattr(
        ollama_client,
        "_local_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be created")),
    )

    with pytest.raises(ValueError, match="refuses Ollama cloud model"):
        ollama_client.embed_texts(["private document"])
