from __future__ import annotations

from dataclasses import dataclass
from secrets import choice
from typing import Iterable


@dataclass(frozen=True)
class RealChatTestPrompt:
    """A short, deterministic test prompt that is safe to retain in chat history."""

    id: str
    text: str


# Keep these prompts short and text-only. The goal is to exercise a few ordinary
# input shapes without invoking tools, sensitive topics, or long generations.
REAL_CHAT_TEST_PROMPTS: tuple[RealChatTestPrompt, ...] = (
    RealChatTestPrompt("basic-01", "请用一句话解释“缓存”是什么。"),
    RealChatTestPrompt("basic-02", "请用一句话说明 HTTP 200 通常代表什么。"),
    RealChatTestPrompt("basic-03", "请用三个词描述一杯咖啡。"),
    RealChatTestPrompt("basic-04", "请列出两个保持专注的小技巧，简短回答。"),
    RealChatTestPrompt("basic-05", "请把“先计划，再执行”改写得更口语化。"),
    RealChatTestPrompt("basic-06", "请用一句话解释备份的作用。"),
    RealChatTestPrompt("basic-07", "请给“耐心”写一个简短的同义表达。"),
    RealChatTestPrompt("basic-08", "请用一句话解释什么是文件夹。"),
    RealChatTestPrompt("basic-09", "请列出早晨开始工作前可以做的一件小事。"),
    RealChatTestPrompt("basic-10", "请用一句话区分计划和目标。"),
    RealChatTestPrompt("basic-11", "请为“周末散步”写一个六字以内的标题。"),
    RealChatTestPrompt("basic-12", "请用一句话说明为什么要检查输入。"),
    RealChatTestPrompt("math-01", "请计算 17 × 6，并只给出结果。"),
    RealChatTestPrompt("math-02", "请计算 48 ÷ 6，并只给出结果。"),
    RealChatTestPrompt("math-03", "一个盒子里有 4 个苹果，再放入 3 个，一共有几个？"),
    RealChatTestPrompt("math-04", "数列 2、4、8、16 的下一个数是什么？"),
    RealChatTestPrompt("math-05", "200 的 15% 是多少？请简短回答。"),
    RealChatTestPrompt("math-06", "请比较 3/4 和 2/3 哪个更大。"),
    RealChatTestPrompt("math-07", "如果一本书有 120 页，每天读 20 页，几天读完？"),
    RealChatTestPrompt("math-08", "请计算 9 + 8 - 5，并只给出结果。"),
    RealChatTestPrompt("math-09", "一个小时有多少分钟？"),
    RealChatTestPrompt("math-10", "请把数字 25 写成中文。"),
    RealChatTestPrompt("math-11", "如果今天是星期一，三天后是星期几？"),
    RealChatTestPrompt("math-12", "请计算 7 的平方，并只给出结果。"),
    RealChatTestPrompt("language-01", "请把“今天的天气很好”翻译成英文，只给出译文。"),
    RealChatTestPrompt("language-02", "请把“Good morning”翻译成中文。"),
    RealChatTestPrompt("language-03", "请把“谢谢你的帮助”翻译成英文。"),
    RealChatTestPrompt("language-04", "请为“清晰”写一个英文单词。"),
    RealChatTestPrompt("language-05", "请把“先听后说”改写成自然的英文短句。"),
    RealChatTestPrompt("language-06", "请给“快速”的一个反义词。"),
    RealChatTestPrompt("language-07", "请把“See you tomorrow”翻译成中文。"),
    RealChatTestPrompt("language-08", "请用英文写出星期一。"),
    RealChatTestPrompt("language-09", "请把“保持简单”翻译成英文。"),
    RealChatTestPrompt("language-10", "请给“important”写出中文意思。"),
    RealChatTestPrompt("language-11", "请把“我正在学习”翻译成英文。"),
    RealChatTestPrompt("language-12", "请为“安静的房间”写一个英文短语。"),
    RealChatTestPrompt("reasoning-01", "小王比小李早到，小李比小张早到，谁最晚到？"),
    RealChatTestPrompt("reasoning-02", "杯子在桌子上，桌子在房间里，杯子在哪里？"),
    RealChatTestPrompt("reasoning-03", "如果所有玫瑰都是花，红玫瑰属于什么？"),
    RealChatTestPrompt("reasoning-04", "请把“先备份再更新”概括成四个字。"),
    RealChatTestPrompt("reasoning-05", "下雨时出门，带伞和不带伞哪个更合理？请简短回答。"),
    RealChatTestPrompt("reasoning-06", "如果 A 比 B 高，B 比 C 高，谁最高？"),
    RealChatTestPrompt("reasoning-07", "请从“苹果、汽车、香蕉”中找出一个不是食物的词。"),
    RealChatTestPrompt("reasoning-08", "把“检查、记录、修复”按合理的工作顺序排列。"),
    RealChatTestPrompt("reasoning-09", "如果灯是亮的，通常说明它处于什么状态？"),
    RealChatTestPrompt("reasoning-10", "请从“春天、蓝色、跑步”中找出一个动作。"),
    RealChatTestPrompt("reasoning-11", "如果任务已经完成，下一步通常应该做什么？"),
    RealChatTestPrompt("reasoning-12", "请用一句话说明为什么要保存结果。"),
)

REAL_CHAT_TEST_PROMPT_HISTORY_LIMIT = 8


def choose_real_chat_test_prompt(recent_ids: Iterable[object] = ()) -> RealChatTestPrompt:
    """Choose a prompt while avoiding the account's recently used prompt IDs."""

    recent = {
        str(item).strip()
        for item in recent_ids
        if str(item or "").strip()
    }
    candidates = tuple(item for item in REAL_CHAT_TEST_PROMPTS if item.id not in recent)
    return choice(candidates or REAL_CHAT_TEST_PROMPTS)
