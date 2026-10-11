def observation_prompt(baby_prompt: str, person_test_mode: bool = False) -> str:
    """Keep the production prompt unchanged; adapt only the observed subject."""
    if not person_test_mode:
        return baby_prompt
    return baby_prompt.replace("宝宝", "人") + (
        "\nTEST MODE：当前是成人工程测试。观察对象是画面中的人，包括成人。"
        "为兼容接口，baby_visible 字段在此模式表示人是否可见。"
        "posture 只表示明确看到的仰卧、侧卧、俯卧；坐着、站着、行走或仅见头脸时必须为 unknown，posture_confidence=0。"
        "不能因人在床边、背景有床，或身体倾斜，就猜测其躺卧。"
        "仍需遵守上述可见性、置信度和 unknown 规则，不要为了测试强行输出 sleeping。"
    )
