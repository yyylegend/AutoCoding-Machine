from unittest.mock import Mock, patch

from src.common.llm_client import chat


def build_response():
    response = Mock()
    response.ok = True
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [],
                }
            }
        ]
    }
    return response


@patch("src.common.llm_client.requests.post")
def test_single_required_tool_uses_named_choice(post_mock):
    post_mock.return_value = build_response()
    tool = {
        "type": "function",
        "function": {
            "name": "agent_action",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    chat("next action", tools=[tool], tool_choice="required")

    payload = post_mock.call_args.kwargs["json"]
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "agent_action"},
    }
    assert payload["parallel_tool_calls"] is False


@patch("src.common.llm_client.requests.post")
def test_auto_tool_choice_is_preserved(post_mock):
    post_mock.return_value = build_response()
    tool = {
        "type": "function",
        "function": {
            "name": "agent_action",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    chat("next action", tools=[tool], tool_choice="auto")

    payload = post_mock.call_args.kwargs["json"]
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False
