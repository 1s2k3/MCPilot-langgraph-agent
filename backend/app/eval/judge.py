"""LLM Judge（§11.3）：对照参考回答 + rubric 给最终回答打 1–5 分（结构化输出 + 理由）。"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

_JUDGE_PROMPT = """你是评估裁判。对照参考回答与评分标准，给 Agent 的最终回答打分。

评分标准（1-5 分）：
- 5: 完全满足标准，信息准确完整
- 4: 基本满足，存在小瑕疵
- 3: 部分满足，有明显遗漏或偏差
- 2: 大部分不满足
- 1: 错误、无关或未完成任务

只输出分数与理由，不评判过程只评判最终回答。"""


class JudgeVerdict(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str = ""


async def judge_answer(
    llm,
    *,
    question: str,
    reference: str | None,
    rubric: str | None,
    actual: str,
) -> JudgeVerdict | None:
    """打分；LLM 不可用/无脚本时返回 None（metrics 标记 judge_skipped）。"""
    try:
        chain = llm.with_structured_output(JudgeVerdict)
        out = await chain.ainvoke(
            [
                SystemMessage(content=_JUDGE_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "用户问题": question,
                            "参考回答": reference or "（无）",
                            "评分标准": rubric or "（无）",
                            "Agent 最终回答": actual[:4000],
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            ]
        )
        return out
    except Exception:  # noqa: BLE001
        logger.warning("judge_skipped")
        return None
