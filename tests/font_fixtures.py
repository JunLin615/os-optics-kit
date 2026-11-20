"""The Chinese font that tests drawing Chinese text need.

Such a test skips when no installed font draws Chinese, like the other optional presentation dependencies. With
``OS_OPTICS_KIT_REQUIRE_PRESENTATION=1`` or ``OS_OPTICS_KIT_REQUIRE_CJK_FONT=1`` (the scheduled alignment checks set
it: every runner there has a Chinese font) a missing font fails the test instead.
"""

import os

REQUIRE_CJK_FONT = "1" in (os.environ.get("OS_OPTICS_KIT_REQUIRE_PRESENTATION"),
                           os.environ.get("OS_OPTICS_KIT_REQUIRE_CJK_FONT"))
CJK_FONT_MISSING = ("no installed font draws Chinese: install Noto Sans CJK SC (fonts-noto-cjk on Linux); Windows "
                    "(Microsoft YaHei) and macOS (PingFang SC, Hiragino Sans GB) include one")


def has_chinese_font():
    """Whether the kit finds an installed font that draws Simplified Chinese (False without matplotlib)."""
    try:
        from os_optics_kit import presentation

        return presentation.has_cjk_font()
    except ImportError:
        return False


def require_chinese_font(test):
    """Skip ``test`` without a Chinese font, or fail it when the environment requires one."""
    if has_chinese_font():
        return
    if REQUIRE_CJK_FONT:
        test.fail(CJK_FONT_MISSING + " (required: OS_OPTICS_KIT_REQUIRE_PRESENTATION or OS_OPTICS_KIT_REQUIRE_CJK_FONT)")
    test.skipTest(CJK_FONT_MISSING)
