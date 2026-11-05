"""Word banks and sentence templates for generated real-chat test prompts.

Every slot name in a template maps to a bank of the same language, so a
template only ever receives words that fit it. Keep everything everyday,
neutral and text-only: no news or "today" (would trigger web search), no
drawing or images, no medical, legal or investment questions, short answers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from string import Formatter


@dataclass(frozen=True)
class PromptTemplate:
    key: str
    lang: str
    text: str


ZH_TEMPLATES: tuple[PromptTemplate, ...] = (
    PromptTemplate("zh-explain", "zh", "请用一句话解释{concept}是什么。"),
    PromptTemplate("zh-plain", "zh", "用大白话说说{concept}是什么意思，一两句就行。"),
    PromptTemplate("zh-example", "zh", "{concept}在日常生活里有什么常见的例子？举一个就好。"),
    PromptTemplate("zh-kid", "zh", "如果要给小朋友解释{concept}，你会怎么说？一句话。"),
    PromptTemplate("zh-words", "zh", "请用{n}个词形容{thing}。"),
    PromptTemplate("zh-metaphor", "zh", "如果用一个比喻来形容{thing}，你会怎么说？一句话。"),
    PromptTemplate("zh-fact", "zh", "说一个和{thing}有关的小知识，一句话就行。"),
    PromptTemplate("zh-tips", "zh", "{scene}有什么小技巧？说{k}条就行。"),
    PromptTemplate("zh-best-tip", "zh", "关于{scene}，给我一个最实用的建议。"),
    PromptTemplate("zh-mistake", "zh", "{scene}的时候最容易犯什么错？说一个。"),
    PromptTemplate("zh-diff", "zh", "{pair_a}和{pair_b}有什么区别？一句话说明。"),
    PromptTemplate("zh-main-diff", "zh", "{pair_a}和{pair_b}最主要的不同是什么？简单说说。"),
    PromptTemplate("zh-rewrite", "zh", "请把“{phrase}”改写得{style}一点。"),
    PromptTemplate("zh-reword", "zh", "换个说法表达“{phrase}”，意思不变。"),
    PromptTemplate("zh-title", "zh", "帮我给“{topic}”想一个{len}个字以内的标题。"),
    PromptTemplate("zh-caption", "zh", "用一句话给“{topic}”写个朋友圈文案。"),
)

EN_TEMPLATES: tuple[PromptTemplate, ...] = (
    PromptTemplate("en-explain", "en", "Explain {concept} in one sentence."),
    PromptTemplate("en-plain", "en", "In plain words, what is {concept}? One or two sentences."),
    PromptTemplate("en-example", "en", "What's an everyday example of {concept}? Just one."),
    PromptTemplate("en-kid", "en", "How would you explain {concept} to a kid? One sentence."),
    PromptTemplate("en-words", "en", "Describe {thing} in {n} words."),
    PromptTemplate("en-metaphor", "en", "What's a good metaphor for {thing}? One sentence."),
    PromptTemplate("en-fact", "en", "Tell me one fun fact about {thing}, in a sentence."),
    PromptTemplate("en-tips", "en", "Give me {k} quick tips for {scene}."),
    PromptTemplate("en-best-tip", "en", "What's the single most useful tip for {scene}?"),
    PromptTemplate("en-mistake", "en", "What's one common mistake people make when it comes to {scene}?"),
    PromptTemplate("en-diff", "en", "What's the difference between {pair_a} and {pair_b}? One sentence."),
    PromptTemplate("en-main-diff", "en", "Briefly, how do {pair_a} and {pair_b} differ?"),
    PromptTemplate("en-rewrite", "en", "Rewrite \"{phrase}\" to sound {style}."),
    PromptTemplate("en-reword", "en", "Say \"{phrase}\" another way, same meaning."),
    PromptTemplate("en-title", "en", "Suggest a title under {len} words for {topic}."),
    PromptTemplate("en-caption", "en", "Write a one-line caption for a post about {topic}."),
)

ZH_PREFIXES: tuple[str, ...] = ("", "", "", "简单问一下，", "想请教一下，", "随便问问，", "你好，", "帮我想想：")
ZH_SUFFIXES: tuple[str, ...] = ("", "", "", "简短回答就好。", "不用展开。", "越简单越好。", "谢谢！")
EN_PREFIXES: tuple[str, ...] = ("", "", "", "Quick question: ", "Hey! ", "Hi! ", "Curious: ")
EN_SUFFIXES: tuple[str, ...] = ("", "", "", " Keep it short.", " No need to elaborate.", " A brief answer is fine.", " Thanks!")

ZH_SMALL: dict[str, tuple[str, ...]] = {
    "n": ("两", "三", "四"),
    "k": ("两", "三"),
    "len": ("六", "八", "十"),
    "style": ("更口语", "更正式", "更幽默", "更简洁", "更温柔", "更有诗意"),
}
EN_SMALL: dict[str, tuple[str, ...]] = {
    "n": ("two", "three", "four"),
    "k": ("two", "three"),
    "len": ("five", "six", "eight"),
    "style": ("more casual", "more formal", "funnier", "more concise", "friendlier", "more poetic"),
}


def _words(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.split("/") if item.strip())


def _pairs(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(tuple(item.split("|", 1)) for item in _words(text))  # type: ignore[misc]


ZH_BANKS: dict[str, tuple] = {
    "concept": _words(
        "缓存/备份/复利/光合作用/时区/二维码/蓝牙/云存储/加密/密码管理器/通货膨胀/预算/版本控制/惯性/回声/彩虹/潮汐/季风/"
        "发酵/番茄工作法/断舍离/拖延/复盘/待办清单/防火墙/路由器/快捷键/文件压缩/回收站/云同步/两步验证/开源软件/操作系统/"
        "像素/屏幕分辨率/帧率/电池循环次数/静电/磁铁/浮力/摩擦力/杠杆原理/温室效应/降水概率/体感温度/时差/闰年/农历/二十四节气/"
        "汇率/保修期/会员积分/公交换乘/共享单车/卫星导航/月相/日食/极光/播客/电子表格/书签/浏览历史/夜间模式/省电模式/"
        "无线充电/条形码/邮政编码/快递单号/分期付款/折旧/口碑/品牌/广告位/排队论/木桶效应/蝴蝶效应/马太效应/边际效应/"
        "沉没成本/机会成本/从众心理/仪式感/峰终定律/冷知识/四季更替/候鸟迁徙/冬眠/年轮/化石/指南针/日晷/沙漏/温度计"
    ),
    "thing": _words(
        "一杯咖啡/下雨天/早高峰的地铁/秋天的公园/刚出炉的面包/海边的傍晚/老式收音机/雪后的街道/夏天的西瓜/安静的图书馆/"
        "深夜的便利店/周一早上/一只橘猫/山顶的日出/雨后的空气/新买的笔记本/一碗热汤面/夜市/菜市场/晚霞/机场候机厅/小区花园/"
        "家里的阳台/一盆绿植/手写信/旧书店/骑自行车/露营的夜晚/烤红薯/桂花香/夏夜的蝉鸣/雾天的清晨/晴朗的早晨/敲键盘的声音/"
        "冬天的被窝/公园长椅/春天的风/一场电影/周末的早午餐/老街/火车窗外/一封邮件/毛衣/雨伞/路灯/清晨的鸟叫/热巧克力/"
        "一本旧日记/抽屉里的零钱/刚洗好的衣服/厨房的烟火气/海浪声/星空/篝火/书桌上的台灯/一杯柠檬水/小区门口的早餐摊"
    ),
    "scene": _words(
        "早起/出差/搬家/第一次做饭/整理书桌/准备面试/坐长途车/学一门新语言/养绿植/在家办公/记账/规划周末/收拾行李/写周报/"
        "背单词/跑步入门/和新同事相处/开会记笔记/整理手机相册/清理电脑桌面/准备考试/快速做早餐/保持房间整洁/买菜/学做蛋糕/"
        "骑车通勤/下雨天出门/旅行拍照/读完一本书/管理邮箱/分配家务/准备生日惊喜/学吉他/种番茄/冬天保暖/写读书笔记/网购退货/"
        "整理衣柜/换季收纳/当众发言/做演示文稿/给文件起名/安排一天的时间/减少分心/养成运动习惯/学游泳/第一次露营/带父母旅行/"
        "收拾厨房/挑选礼物/写感谢信/学打字/规划假期/布置小房间/整理冰箱/给植物浇水/第一次坐飞机"
    ),
    "pair": _pairs(
        "复利|单利/天气|气候/计划|目标/备份|同步/文件夹|标签/速度|速率/咖啡|茶/散步|慢跑/电子书|纸质书/云存储|本地存储/"
        "习惯|爱好/台风|飓风/湖|水库/海|洋/山|丘陵/公历|农历/截图|录屏/复制|剪切/重启|关机/充电宝|充电器/蓝牙|Wi-Fi/"
        "收藏|点赞/预报|预警/批发|零售/价格|价值/效率|效果/记忆|回忆/兴趣|爱好/冷静|冷淡/坚持|固执/早餐|早午餐/地铁|轻轨/"
        "播客|广播/小说|散文/博物馆|美术馆/地图|导航/日记|周记/便签|备忘录/雾|霾/露水|霜/月亮|星星"
    ),
    "phrase": _words(
        "先计划，再执行/今日事今日毕/慢慢来，比较快/细节决定成败/多读书，多思考/熟能生巧/一步一个脚印/好记性不如烂笔头/"
        "失败是成功之母/站得高，看得远/磨刀不误砍柴工/有备无患/三思而后行/少说多做/积少成多/先完成，再完美/保持好奇心/"
        "时间就是金钱/欲速则不达/心急吃不了热豆腐/万事开头难/学无止境/知足常乐/实践出真知/温故而知新/早睡早起精神好/"
        "笑一笑，十年少/一分耕耘一分收获/行动胜过空想/小事做好也不容易"
    ),
    "topic": _words(
        "周末去爬山/第一次露营/搬进新家/学做红烧肉/雨天在家看书/和朋友逛夜市/早起跑步一个月/整理旧照片/阳台种花/一个人的旅行/"
        "公司团建/自己修好自行车/冬天的第一场雪/读完一本小说/养了一只小猫/学会游泳/周末的咖啡馆/春天去郊游/第一次做蛋糕/"
        "和家人包饺子/晚饭后散步/新学期开始/整理好的书架/海边看日落/周末大扫除/第一次坐高铁/社区跳蚤市场/秋天的银杏/"
        "早市买菜/坚持记账一个月"
    ),
}

EN_BANKS: dict[str, tuple] = {
    "concept": _words(
        "caching/a backup/compound interest/photosynthesis/time zones/a QR code/Bluetooth/cloud storage/encryption/"
        "a password manager/inflation/a budget/version control/inertia/an echo/a rainbow/tides/a monsoon/fermentation/"
        "the Pomodoro technique/procrastination/a to-do list/a firewall/a router/a keyboard shortcut/file compression/"
        "two-factor authentication/open-source software/an operating system/a pixel/screen resolution/frame rate/"
        "static electricity/a magnet/buoyancy/friction/a lever/the greenhouse effect/wind chill/jet lag/a leap year/"
        "an exchange rate/a warranty/loyalty points/satellite navigation/moon phases/a solar eclipse/the northern lights/"
        "a podcast/a spreadsheet/a bookmark/dark mode/battery saver mode/wireless charging/a barcode/a postal code/"
        "a tracking number/depreciation/word of mouth/a brand/the butterfly effect/sunk cost/opportunity cost/"
        "peer pressure/the peak-end rule/a seasonal migration/hibernation/tree rings/a fossil/a compass/a sundial/"
        "an hourglass/a thermometer/a habit loop/muscle memory/a power nap/a playlist/a group chat/autocorrect"
    ),
    "thing": _words(
        "a cup of coffee/a rainy afternoon/the morning commute/an autumn park/fresh bread/a beach at sunset/an old radio/"
        "a snowy street/summer watermelon/a quiet library/a late-night convenience store/Monday mornings/an orange cat/"
        "a mountain sunrise/the air after rain/a new notebook/a bowl of hot noodles/a night market/a farmers market/"
        "an airport lounge/a balcony garden/a houseplant/a handwritten letter/a used bookstore/a bike ride/"
        "a camping night/roasted sweet potatoes/cicadas on a summer night/a foggy morning/the sound of typing/"
        "a warm blanket in winter/a park bench/a spring breeze/a movie night/weekend brunch/an old street/"
        "the view from a train window/a wool sweater/an umbrella/a street lamp/birdsong at dawn/hot chocolate/"
        "an old diary/freshly washed laundry/ocean waves/a starry sky/a campfire/a desk lamp/a glass of lemonade"
    ),
    "scene": _words(
        "waking up early/business trips/moving house/cooking for the first time/tidying a desk/preparing for an interview/"
        "long bus rides/learning a new language/keeping houseplants alive/working from home/tracking expenses/"
        "planning a weekend/packing a suitcase/writing a weekly report/memorizing vocabulary/starting to run/"
        "taking meeting notes/organizing phone photos/cleaning up a computer desktop/studying for an exam/"
        "making a quick breakfast/keeping a room tidy/grocery shopping/baking a cake/biking to work/"
        "going out on a rainy day/taking travel photos/finishing a book/managing an inbox/sharing chores/"
        "planning a birthday surprise/learning guitar/growing tomatoes/staying warm in winter/returning online orders/"
        "organizing a closet/public speaking/making slides/naming files/planning a day/avoiding distractions/"
        "building an exercise habit/learning to swim/a first camping trip/cleaning the kitchen/choosing a gift/"
        "writing a thank-you note/learning to type faster/planning a vacation/decorating a small room"
    ),
    "pair": _pairs(
        "weather|climate/a plan|a goal/backup|sync/folders|tags/speed|velocity/coffee|tea/walking|jogging/"
        "e-books|paper books/cloud storage|local storage/a habit|a hobby/a typhoon|a hurricane/a lake|a pond/"
        "a sea|an ocean/a hill|a mountain/a screenshot|a screen recording/copy|cut/restart|shut down/"
        "a power bank|a charger/Bluetooth|Wi-Fi/a forecast|a warning/wholesale|retail/price|value/"
        "efficiency|effectiveness/memory|nostalgia/brunch|lunch/a podcast|a radio show/a novel|a short story/"
        "a museum|a gallery/a map|a GPS app/a diary|a journal/a sticky note|a to-do app/fog|mist/dew|frost/"
        "a crocodile|an alligator/a rabbit|a hare/a latte|a cappuccino/baking|roasting/a sprint|a marathon"
    ),
    "phrase": _words(
        "Plan first, then act/Slow and steady wins the race/Practice makes perfect/Better safe than sorry/"
        "Look before you leap/Done is better than perfect/Stay curious/Time is money/Haste makes waste/"
        "Every journey starts with a single step/Less talk, more action/Little by little, a lot is done/"
        "Early to bed, early to rise/Keep it simple/One thing at a time/Learn something new every day/"
        "Small steps add up/Rome wasn't built in a day/Actions speak louder than words/Measure twice, cut once/"
        "Good things take time/Make it work, then make it better/Rest is part of the work/Write it down so you remember"
    ),
    "topic": _words(
        "a weekend hike/a first camping trip/moving into a new place/learning to make soup/reading on a rainy day/"
        "a night market with friends/a month of morning runs/sorting old photos/a balcony flower garden/a solo trip/"
        "fixing my own bike/the first snow of winter/finishing a novel/adopting a kitten/learning to swim/"
        "a cozy coffee shop/a spring picnic/baking my first cake/making dumplings with family/an evening walk/"
        "a new semester/a tidy bookshelf/sunset at the beach/a weekend deep clean/a first train trip/"
        "a neighborhood flea market/autumn leaves/an early grocery run/a month of tracking expenses"
    ),
}

BANKS: dict[str, dict[str, tuple]] = {"zh": ZH_BANKS, "en": EN_BANKS}
SMALL: dict[str, dict[str, tuple[str, ...]]] = {"zh": ZH_SMALL, "en": EN_SMALL}
TEMPLATES: dict[str, tuple[PromptTemplate, ...]] = {"zh": ZH_TEMPLATES, "en": EN_TEMPLATES}
PREFIXES: dict[str, tuple[str, ...]] = {"zh": ZH_PREFIXES, "en": EN_PREFIXES}
SUFFIXES: dict[str, tuple[str, ...]] = {"zh": ZH_SUFFIXES, "en": EN_SUFFIXES}


def template_slots(template: PromptTemplate) -> tuple[str, ...]:
    return tuple(name for _, name, _, _ in Formatter().parse(template.text) if name)


def fill_template(template: PromptTemplate, rng: random.Random) -> str:
    """Fill every slot of a template from its language's banks, plus a random prefix/suffix."""

    lang = template.lang
    banks, small = BANKS[lang], SMALL[lang]
    values: dict[str, str] = {}
    for slot in template_slots(template):
        if slot in values:
            continue
        if slot in ("pair_a", "pair_b"):
            values["pair_a"], values["pair_b"] = rng.choice(banks["pair"])
        elif slot in small:
            values[slot] = rng.choice(small[slot])
        else:
            values[slot] = rng.choice(banks[slot])
    body = template.text.format(**values)
    return f"{rng.choice(PREFIXES[lang])}{body}{rng.choice(SUFFIXES[lang])}"
