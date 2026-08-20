"""Memory 纯函数单元测试（不依赖数据库）。"""

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import _summarize_prompt
from app.agent.runner import sanitize_state
from app.memory.extractor import normalize_content
from app.memory.retriever import memory_context_text


def test_normalize_content() -> None:
    assert normalize_content("  用户  喜欢  蓝色  ") == "用户 喜欢 蓝色"
    assert normalize_content("Hello World") == "hello world"


def test_memory_context_text_formatting() -> None:
    rows = [
        {"type": "preference", "content": "喜欢蓝色", "similarity": 0.9},
        {"type": "fact", "content": "名字是小明", "similarity": 0.8},
    ]
    text = memory_context_text(rows)
    assert "喜欢蓝色" in text and "名字是小明" in text
    assert "0.9" in text
    assert memory_context_text([]) == ""


def test_sanitize_state_messages_and_scalars() -> None:
    values = {
        "messages": [
            HumanMessage(content="你好"),
            AIMessage(
                content="回复",
                tool_calls=[{"name": "calculator", "args": {"expression": "1+2"}, "id": "c1"}],
            ),
        ],
        "iteration_count": 3,
        "usage_total": {"input_tokens": 10},
        "summary": "摘要",
        "memory_context": "记忆上下文",
    }
    out = sanitize_state(values)
    assert out["iteration_count"] == 3
    assert out["usage_total"] == {"input_tokens": 10}
    msgs = out["messages"]
    assert msgs[0] == {"role": "human", "content": "你好"}
    assert msgs[1]["role"] == "ai"
    assert msgs[1]["tool_calls"] == [{"name": "calculator", "args": {"expression": "1+2"}}]
    assert out["summary"] == "摘要"


def test_summarize_prompt_contains_history() -> None:
    prompt = _summarize_prompt(
        "旧摘要",
        [HumanMessage(content="用户说喜欢蓝色"), AIMessage(content="好的")],
    )
    assert "旧摘要" in prompt
    assert "用户说喜欢蓝色" in prompt
