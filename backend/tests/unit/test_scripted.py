"""ScriptedChatModel 单元测试：队列驱动、调用记录、结构化输出、responder。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from app.llm.scripted import ScriptedChatModel, ScriptExhausted


@tool
def _fake_add(a: int, b: int) -> int:
    """测试工具：加法。"""
    return a + b


@tool
def _fake_echo(text: str) -> str:
    """测试工具：回显。"""
    return text


class Plan(BaseModel):
    steps: list[str]


async def test_returns_scripted_messages_in_order() -> None:
    llm = ScriptedChatModel(responses=[AIMessage(content="第一轮"), AIMessage(content="第二轮")])
    r1 = await llm.ainvoke([HumanMessage(content="hi")])
    r2 = await llm.ainvoke([HumanMessage(content="hi")])
    assert r1.content == "第一轮"
    assert r2.content == "第二轮"
    assert len(llm.calls) == 2
    assert llm.calls[0].kind == "generate"


async def test_records_messages_and_tool_names() -> None:
    llm = ScriptedChatModel(responses=[AIMessage(content="ok")])
    llm_with_tools = llm.bind_tools([_fake_add, _fake_echo])
    await llm_with_tools.ainvoke([SystemMessage(content="sys"), HumanMessage(content="q")])
    call = llm.calls[0]
    assert call.tool_names == ["_fake_add", "_fake_echo"]
    assert len(call.messages) == 2


async def test_tool_call_message_passthrough() -> None:
    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "calculator", "args": {"expression": "1+2"}, "id": "c1", "type": "tool_call"}
        ],
    )
    llm = ScriptedChatModel(responses=[msg])
    out = await llm.ainvoke([HumanMessage(content="算一下")])
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0]["name"] == "calculator"


async def test_exception_script_is_raised() -> None:
    llm = ScriptedChatModel(responses=[RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        await llm.ainvoke([HumanMessage(content="hi")])


async def test_exhausted_raises() -> None:
    llm = ScriptedChatModel(responses=[])
    with pytest.raises(ScriptExhausted):
        await llm.ainvoke([HumanMessage(content="hi")])


async def test_structured_output_pops_basemodel() -> None:
    llm = ScriptedChatModel(responses=[Plan(steps=["a", "b"])])
    runnable = llm.with_structured_output(Plan)
    out = await runnable.ainvoke([HumanMessage(content="plan")])
    assert isinstance(out, Plan)
    assert out.steps == ["a", "b"]
    call = llm.calls[0]
    assert call.kind == "structured"
    assert call.schema_name == "Plan"


async def test_structured_output_accepts_dict() -> None:
    llm = ScriptedChatModel(responses=[{"steps": ["x"]}])
    out = await llm.with_structured_output(Plan).ainvoke([HumanMessage(content="plan")])
    assert out == Plan(steps=["x"])


async def test_responder_fallback_generates_responses() -> None:
    seen: list[str] = []

    def responder(messages):
        seen.append(messages[0].content)
        return [AIMessage(content="generated")]

    llm = ScriptedChatModel(responder=responder)
    out = await llm.ainvoke([HumanMessage(content="trigger")])
    assert out.content == "generated"
    assert seen == ["trigger"]
