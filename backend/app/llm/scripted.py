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
            if isinstance(m, tuple):
                out.append({"type": m[0], "content": str(m[1])})
            else:
                out.append(m.to_json())
        except Exception:  # noqa: BLE001
            out.append({"type": getattr(m, "type", "?"), "content": str(getattr(m, "content", ""))})
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


def _step_goal_and_user_text(messages: list[BaseMessage]) -> str:
    """executor 分支键：步骤目标（系统消息中的「当前步骤目标」）+ 用户文本。"""
    goal = ""
    user_text = ""
    for m in messages:
        if m.type == "system" and "当前步骤目标" in (
            m.content if isinstance(m.content, str) else ""
        ):
            goal = m.content if isinstance(m.content, str) else ""
        elif m.type == "human" and isinstance(m.content, str):
            user_text += m.content
    return goal + " " + user_text


def demo_responder(messages: list[BaseMessage]) -> list[Any]:
    """离线演示 executor：计算→calculator；记住→remember_memory；其余直接完成步骤。

    工具被拒绝（ToolMessage status=error，§5.9 deny）时改道：不再重复请求被拒工具，
    直接结束本步骤（与真实 LLM 收到拒绝反馈后的行为一致）。
    """
    if any(
        getattr(m, "type", None) == "tool" and getattr(m, "status", None) == "error"
        for m in messages
    ):
        return [AIMessage(content="工具调用被拒绝，本步骤无法完成，直接结束本步骤。")]
    key = _step_goal_and_user_text(messages)
    if "确认" in key:
        return [AIMessage(content="已确认，结果正确。")]
    if "时间" in key or "几点" in key:
        return [
            AIMessage(
                content="我来查一下当前时间。",
                tool_calls=[
                    {
                        "name": "get_current_time",
                        "args": {},
                        "id": "demo-time-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已查到当前时间。"),
        ]
    if "记住" in key:
        user_text = "".join(
            m.content if isinstance(m.content, str) else "" for m in messages if m.type == "human"
        )
        content = user_text.split("记住", 1)[1].strip(" ：:，,。") or "用户要求记住一些信息"
        return [
            AIMessage(
                content="好的，我来记下。",
                tool_calls=[
                    {
                        "name": "remember_memory",
                        "args": {"content": content, "type": "fact"},
                        "id": "demo-mem-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已记住。"),
        ]
    if "计算" in key or "算" in key or "多少" in key or "1+2" in key or "2*3" in key:
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
            AIMessage(content="已算出结果。"),
        ]
    return [
        AIMessage(content="（离线演示模式）本步骤已完成。配置 ANTHROPIC_API_KEY 后启用真实模型。")
    ]


def demo_plan_responder(messages: list[BaseMessage]) -> list[Any]:
    """离线演示 planner：计算任务给两步计划，其余单步。"""
    from app.agent.schemas import Plan, PlanStep

    text = "".join(
        m.content if isinstance(m.content, str) else "" for m in messages if m.type == "human"
    )
    if any(k in text for k in ("计算", "算", "多少", "1+2", "2*3")):
        return [
            Plan(
                steps=[
                    PlanStep(id="s1", goal="用 calculator 工具计算 1+2", tools_hint=["calculator"]),
                    PlanStep(id="s2", goal="确认计算结果正确"),
                ],
                rationale="演示：两步计划（计算 → 确认）",
            )
        ]
    return [Plan(steps=[PlanStep(id="s1", goal="直接回答用户问题")], rationale="简单任务单步即可")]


def demo_reflect_responder(messages: list[BaseMessage]) -> list[Any]:
    """离线演示 reflector：一律评审通过。"""
    from app.agent.schemas import ReflectionVerdict

    return [ReflectionVerdict(verdict="pass", reason="演示评审：通过")]


def demo_finalizer_responder(messages: list[BaseMessage]) -> list[Any]:
    """离线演示 finalizer：按任务类型给最终回答。"""
    text = "".join(
        m.content if isinstance(m.content, str) else "" for m in messages if m.type == "human"
    )
    if "计算" in text:
        return [AIMessage(content="计算结果是 3。")]
    return [
        AIMessage(content="（离线演示模式）任务已完成。配置 ANTHROPIC_API_KEY 后启用真实模型。")
    ]


def demo_scripted() -> ScriptedChatModel:
    return ScriptedChatModel(responder=demo_responder)
