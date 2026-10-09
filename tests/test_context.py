from local_ai.agent.context import (
    extract_json,
    parse_inline_tool_calls,
    strip_think,
    trim,
    unknown_citations,
    unverified_numbers,
)


def test_strip_think():
    assert strip_think("<think>hmm</think>\nAnswer") == "Answer"
    assert strip_think("reasoning</think>Answer") == "Answer"
    assert strip_think(None) == ""


def test_trim_keeps_head_and_tail():
    out = trim("a" * 100 + "b" * 100, 60)
    assert out.startswith("a" * 40) and out.endswith("b" * 20) and "omitted" in out


def test_extract_json_from_fenced_reply():
    assert extract_json('Plan:\n```json\n{"a": [1]}\n```') == {"a": [1]}


def test_inline_tool_calls():
    calls = parse_inline_tool_calls(
        '<tool_call>{"name": "library__search_arxiv", "arguments": {"query": "x"}}</tool_call>'
    )
    assert calls[0]["function"]["name"] == "library__search_arxiv"
    assert calls[0]["function"]["arguments"] == '{"query": "x"}'


def test_unverified_numbers():
    evidence = "c = 299792458 m/s\n1.0 light-second is 299792.458 km"
    draft = "Light travels 299,792 km in a second [1], about 3.7 times X, in 3 steps.\n\n## References\n[1] 12345"
    assert unverified_numbers(draft, evidence) == ["3.7"]
    # Turkish formatting: 299.792 (thousands) and 299792,458 (decimal comma)
    assert unverified_numbers("Işık 299.792 km yol alır, yani 299792,458 km.", evidence) == []


def test_unknown_citations():
    assert unknown_citations("A [1], B [2, 5], C [3-4]", 4) == [5]
