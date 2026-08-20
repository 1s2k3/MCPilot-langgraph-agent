"""ScriptedChatModel：脚本化假 LLM（测试 / 离线演示 / LIVE_LLM=0 评估）。

- 队列驱动：依次弹出预设输出（AIMessage | BaseModel | Exception）
- 全量记录每次调用（messages / 工具名 / 结构化 schema），供精确断言
- responder 回调：队列耗尽时按输入动态生成脚本（demo / eval 用）
- 与 ChatAnthropic 同为 BaseChatModel，图上可无缝替换
"""

from collections import deque
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel, Field, PrivateAttr


class ScriptExhausted(RuntimeError):
    """脚本队列耗尽且无 responder：图上调用 LLM 次数多于脚本。"""


class RecordedCall(BaseModel):
    kind: str  # generate | structured
    messages: list[dict]
    tool_names: list[str] = []
    schema_name: str | None = None
    output: Any = None


def _dump(messages: list[BaseMessage]) -> list[dict]:
    out = []
    for m in messages:
        try:
            out.append(m.to_json())
        except Exception:  # noqa: BLE001
            out.append({"type": m.type, "content": str(m.content)})
    return out


class ScriptedChatModel(BaseChatModel):
    """按队列返回预设输出；空队列时尝试 responder(messages) 动态生成。"""

    model_name: str = "scripted"
    calls: list[RecordedCall] = Field(default_factory=list)

    # 运行期状态（pydantic v2 不允许给模型实例直接设置未声明字段，用 PrivateAttr）
    _responses: Any = PrivateAttr(default=None)
    _responder: Any = PrivateAttr(default=None)
    _bound_tools: Any = PrivateAttr(default=None)

    def __init__(
        self,
        responses: list[Any] | None = None,
        *,
        responder: Callable[[list[BaseMessage]], list[Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._responses = deque(responses or [])
        self._responder = responder
        self.calls = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def _identifying_params(self) -> dict:
        return {"model": self.model_name}

    def _pop(self, messages: list[BaseMessage]) -> Any:
        if not self._responses and self._responder is not None:
            self._responses.extend(self._responder(messages))
        if not self._responses:
            raise ScriptExhausted("ScriptedChatModel 队列已耗尽")
        return self._responses.popleft()

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """记录绑定工具并返回自身（langchain-core 1.x 基类默认 NotImplementedError）。"""
        self._bound_tools = list(tools)
        return self

    def _tool_names(self, kwargs: dict) -> list[str]:
        tools = kwargs.get("tools") or self._bound_tools or []
        return [t.get("name") if isinstance(t, dict) else getattr(t, "name", "?") for t in tools]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._dispatch(messages, kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._dispatch(messages, kwargs)

    def _dispatch(self, messages: list[BaseMessage], kwargs: dict) -> ChatResult:
        out = self._pop(messages)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, AIMessage):
            msg = out
        elif isinstance(out, str):
            msg = AIMessage(content=out)
        elif isinstance(out, BaseModel):
            # 注意：AIMessage 也是 BaseModel 子类，此分支必须在上面之后
            raise TypeError("结构化输出请在 with_structured_output 路径使用 BaseModel 脚本")
        else:
            raise TypeError(f"不支持的脚本元素: {type(out).__name__}")
        self.calls.append(
            RecordedCall(
                kind="generate",
                messages=_dump(messages),
                tool_names=self._tool_names(kwargs),
                output=msg.content,
            )
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(self, schema, **kwargs):
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return _ScriptedStructured(self, schema)
        return super().with_structured_output(schema, **kwargs)


class _ScriptedStructured(Runnable):
    """with_structured_output 的脚本实现：从同一队列弹出 BaseModel / dict。"""

    def __init__(self, model: ScriptedChatModel, schema: type[BaseModel]) -> None:
        self.model = model
        self.schema = schema

    def invoke(self, input, config: RunnableConfig | None = None, **kwargs) -> BaseModel:
        return self._run(input)

    async def ainvoke(self, input, config: RunnableConfig | None = None, **kwargs) -> BaseModel:
        return self._run(input)

    def _run(self, input) -> BaseModel:
        messages = input if isinstance(input, list) else [input]
        out = self.model._pop(messages)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, self.schema):
            value = out
        elif isinstance(out, dict):
            value = self.schema(**out)
        else:
            raise TypeError(
                f"结构化输出脚本元素不匹配 {self.schema.__name__}: {type(out).__name__}"
            )
        self.model.calls.append(
            RecordedCall(
                kind="structured",
                messages=_dump(messages),
                schema_name=self.schema.__name__,
                output=value,
            )
        )
        return value


def demo_responder(messages: list[BaseMessage]) -> list[Any]:
    """离线演示：计算类问题调用 calculator 工具，其余直接回答（无 API Key 时可用）。"""
    user_text = "".join(
        m.content if isinstance(m.content, str) else "" for m in messages if m.type == "user"
    )
    if any(k in user_text for k in ("计算", "1+2", "2*3")):
        return [
            AIMessage(
                content="我来算一下。",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": "1+2"},
                        "id": "demo-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="计算结果是 3。"),
        ]
    return [
        AIMessage(content="（离线演示模式）这是脚本化回复。配置 ANTHROPIC_API_KEY 后启用真实模型。")
    ]


def demo_scripted() -> ScriptedChatModel:
    return ScriptedChatModel(responder=demo_responder)
