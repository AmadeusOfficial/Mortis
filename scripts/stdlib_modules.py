"""打包用：要收进后端的标准库顶层模块名，一行一个。

改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）的 scripts/stdlib_modules.py。

PyInstaller 只收后端静态 import 到的标准库。插件是运行时才从插件目录加载的，
它们用到而后端没用到的模块（如 winreg、html.parser）打包后会缺，所以整个标准库都收进去。
"""

import importlib.util
import sys

EXCLUDE = frozenset(
    {"idlelib", "tkinter", "turtle", "turtledemo", "ensurepip", "antigravity", "this"}
)
"""图形界面和安装器用的原生依赖收不进来；antigravity 和 this 一 import 就会开浏览器或打印。"""


def stdlib_module_names() -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in sys.stdlib_module_names
            if not name.startswith("_")
            and "." not in name
            and name not in EXCLUDE
            and importlib.util.find_spec(name) is not None
        )
    )


if __name__ == "__main__":
    print("\n".join(stdlib_module_names()))
