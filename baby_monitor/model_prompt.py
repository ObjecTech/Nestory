from __future__ import annotations

"""One wire schema, separate subject rules; never derive adult rules by replacement."""
COMMON_PROMPT = """只观察图片中直接可见的事实，不作医疗判断，不猜测看不到的内容。
仅返回完整 JSON，不用 Markdown：
{
 "baby_visible": true/false/null,
 "visibility": "visible|partial|not_visible|unknown",
 "sleep_state": "sleeping|awake|drowsy|unknown",
 "posture": "supine|side|prone|unknown",
 "expression": "calm|happy|unhappy|crying|neutral|unknown",
 "activity_level": "still|low|active|unknown",
 "confidence": 0到1, "expression_confidence": 0到1,
 "posture_confidence": 0到1, "visibility_confidence": 0到1,
 "suggested_state": "简短中文标签",
 "observed_actions": ["最多8个直接可见动作"],
 "description": "一句简短中文事实，说明关键遮挡或不确定性"
}
睡醒、姿态、表情分别判断；清醒的人也可能躺着。只有躯干主体清楚可见且明确躺卧时，才能判仰卧 supine、侧卧 side、俯卧 prone，不判断左右。
坐着、站着、行走或只见脸部时 posture=unknown、posture_confidence=0；不能把侧脸或倾斜身体当成侧卧。
灰度/红外图像同样按可见证据判断，亮度或颜色不代表睡眠。遮挡、模糊、眼睛无法辨认时不猜测睡醒；脸部不清晰时 expression=unknown、expression_confidence=0。
不同 confidence 只对应各自字段，不代表经验证的准确率。描述、标签和字段不得矛盾，不确定字段填 unknown。"""

INFANT_RULES = """
INFANT MODE：只观察能够确认的婴幼儿。baby_visible 表示宝宝是否可见。
没有宝宝或无法确认是宝宝时 baby_visible=false/null；sleep_state、posture、expression、activity_level 全为 unknown，睡醒和表情置信度为0。
只出现手脚、衣物、被子隆起或很小身体片段时 visibility=partial，睡醒、睡姿、表情必须 unknown。不要把成人、玩偶、枕头和床单褶皱当成宝宝。
明确没有宝宝时 visibility=not_visible，可以给较高 visibility_confidence。睡眠/清醒依据直接可见的眼睛和活动线索，不因静止、黑暗或遮挡就认定睡着。"""

ADULT_RULES = """
ADULT TEST MODE：成人工程测试，观察对象是画面中的测试成人。为兼容 API，baby_visible 在本模式表示测试成人是否可见。
站立、坐着、行走时不能选择任何卧姿，posture=unknown；成人明确躺下且躯干可见时可测试 supine、side、prone。
成人闭眼静止、假装睡觉或模拟入睡不能证明真实睡眠，仅有这些线索时 sleep_state=unknown 或 drowsy。不得为通过演示而强行输出 sleeping。
测试者可人工模拟入睡和醒来验证记录/UI，但人工标签不是真实睡眠证据。图像证据不足时返回 unknown。
未见或无法确认测试对象时 baby_visible=false/null，其睡醒、睡姿、表情和活动字段全为 unknown；partial 仅表示身体片段，不能据此猜测。
成人结果仅用于工程验证，不能证明婴幼儿模型准确率。"""

INFANT_PROMPT = COMMON_PROMPT + INFANT_RULES
ADULT_PROMPT = COMMON_PROMPT + ADULT_RULES


def observation_prompt(baby_prompt: str | None = None, person_test_mode: bool = False) -> str:
    # Retain the existing signature for callers providing a dedicated infant prompt.
    if person_test_mode:
        return ADULT_PROMPT
    return INFANT_PROMPT if baby_prompt is None else baby_prompt
