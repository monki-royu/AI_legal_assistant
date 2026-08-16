# 📜 ============================================================
# 文件名称: common/path_utils.py
# 文件作用: 项目的"路径统一管理工具"
# ============================================================
#
# 【这个文件是干什么的？】
# 这个文件是整个法智引擎项目中的"路径导航员"。它的核心作用就是：
#   把"工程内的相对路径"（比如 'common/llm.py'）自动转成"操作系统能理解的绝对路径"
#   （比如 'E:\\to_github_project\\AI_legal_assistant\\common\\llm.py'）。
#
# 【为什么需要这个文件？】
# 想象一下：你的 Python 代码里有这样一行：
#     with open('data/some_file.json') as f:
# 如果你的终端当前在 E:\project 下运行，那文件能打开；
# 但如果你的终端在 E:\ 下运行，Python 就会报错"文件找不到"。
# path_utils.py 就是为了解决这个"启动目录不确定"的问题而生的。
#
# 【代码逻辑主线】
# 1. 模块加载时（import 时），os.path.abspath(__file__) 拿到本文件自己的绝对路径。
# 2. os.path.dirname() 向上退两级（common/ → 上一级 → 工程根），存入 root_dir。
# 3. get_file_path(relative_path) 用 os.path.join 把 root_dir 和传入的相对路径拼起来。
# 4. 其他模块引用本文件时，调用 get_file_path('data/xxx.json') 就能拿到稳定路径。
#
# 【谁在用它？】
#   config.py     — 用来定位 .env、legal_metadata.json、FAISS 索引等资源
#   entity_extractor.py — 用来定位微调数据文件路径
#   （间接服务于所有上层模块）

import os
# 【import os】：引入 Python 标准库 os（Operation System），它提供了与操作系统交互
#   的函数。我们主要使用 os.path.abspath()（获取绝对路径）、os.path.dirname()（获取
#   上一级目录）、os.path.join()（拼接路径）这三个函数。

# ============================================================
# 全局变量: root_dir — 工程根目录的绝对路径
# ============================================================
# 【root_dir 的计算逻辑】：
#   Step 1: __file__ 是 Python 自动提供的变量，存着当前文件（path_utils.py）的路径。
#   Step 2: os.path.abspath(__file__) 把这个路径转成绝对路径。
#           结果类似：E:\project\AI_legal_assistant\common\path_utils.py
#   Step 3: os.path.dirname(...) 第一次调用，退到上一级目录。
#           结果类似：E:\project\AI_legal_assistant\common\
#   Step 4: os.path.dirname(...) 第二次调用，再退一级。
#           结果类似：E:\project\AI_legal_assistant\  ← 这就是工程根目录！
#
# 【重要提醒】：
#   如果将来移动了 path_utils.py 的位置（比如放到 project/utils/ 下），
#   就需要增加 dirname 的调用次数（从工程根目录到本文件有几层，就调用几次 dirname）。
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将计算得到的工程根目录绝对路径存入 root_dir 全局变量。
# 这个变量一旦在模块加载时计算完毕，之后所有调用 get_file_path 的人都会用它作为路径前缀。
# 用 root_dir 作为变量名，取自英文"项目根目录"（Root Directory of the Project）。

# ============================================================
# 函数: get_file_path(relative_path)
# ============================================================
def get_file_path(relative_path):
    """
    把工程内相对路径转换为绝对路径并返回。

    【功能】
        以工程根目录 root_dir 为前缀，拼接传入的相对路径，得到一个与启动目录无关的
        绝对路径。这样无论从哪个工作目录运行代码，资源文件都能被稳定定位。

    【参数】
        relative_path (str): 相对于工程根目录的路径片段。
            正确例子: 'common/llm.py'、'__003__create_neo4j_database/legal_metadata.json'
            错误例子: '/common/llm.py'（开头加了斜杠）
                为什么不能加斜杠？—— 因为 os.path.join() 有一个"特殊机制"：
                当遇到以 / 开头的路径片段时，Python 会认为这是一个"绝对路径"，
                于是丢弃掉前面已经拼好的 root_dir，直接从斜杠开始重新拼。
                结果你得到的是 '/common/llm.py'，而不是完整的工程绝对路径。

    【返回值】
        str: 拼接得到的绝对路径字符串。
        例如: 'e:\\to_github_project\\AI_legal_assistant\\common\\llm.py'

    【可迁移性】
        这个函数只依赖标准库 os，不依赖任何项目业务逻辑。
        你可以把它原样复制到任何 Python 工程中使用。
        唯一需要调整的是 root_dir 的回溯层数。
    """
    # os.path.join(root_dir, relative_path)：
    #   把 root_dir（E:\...\AI_legal_assistant\）和 relative_path（common/llm.py）
    #   拼接起来。os.path.join 会自动处理 Windows 和 Linux 的路径分隔符差异
    #   （Windows 用 \， Linux 用 /），保证跨平台兼容。
    return os.path.join(root_dir, relative_path)

# ============================================================
# 模块自测入口
# ============================================================
if __name__ == '__main__':
    # 【自测逻辑】：
    #   当直接运行 python path_utils.py 时，会执行下面的代码，
    #   打印 common/llm.py 的绝对路径，你可以"肉眼核对" root_dir 回溯层级是否正确。
    #   如果打印出来的路径前半部分找不到你的工程根目录，说明 dirname 的层数不对。
    print(get_file_path('common/llm.py'))

# ============================================================
# 函数: root_path() — 占位/预留
# ============================================================
def root_path():
    """
    占位函数，当前未实现，固定返回 None。

    【功能】
        这是一个预留接口，未来可能会返回 root_dir 的值。
        当前只返回 None，作用是"占个位置"，避免其他模块引用这个函数时报错。

    【参数】
        无。

    【返回值】
        None: 当前实现固定返回 None，表示功能未启用。

    【为什么需要这个占位函数？】
        有时候在开发过程中，你"计划"要写一个功能，但还没开始写。
        其他模块可能已经调用了 root_path()，如果这个函数不存在，Python 会抛
        AttributeError 错误。所以先写一个返回 None 的"空壳"，等以后需要时再补全。
    """
    return None  # 占位返回值。调用方拿到 None 后，应该自行回退到 get_file_path('.')