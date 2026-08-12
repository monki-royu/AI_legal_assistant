# 📜 代码文字逻辑解析
# 本文件是项目的"路径统一管理工具"，核心作用是把"工程相对路径"自动转换为"操作系统
# 绝对路径"，让项目里所有模块都用同一种方式访问资源文件，避免因为启动目录不同而找不到
# 文件。核心逻辑：模块加载时通过 os.path.abspath(__file__) 拿到当前 path_utils.py 的
# 绝对路径，再用两次 os.path.dirname 向上回溯两级目录，得到工程根目录的绝对路径并
# 存入模块级全局变量 root_dir。随后定义 get_file_path(relative_path) 函数，把 root_dir
# 与传入的相对路径用 os.path.join 拼接成完整绝对路径返回。函数关系：本模块是项目最
# 底层的工具模块，被 config.py 用来定位 .env、图谱元数据 JSON、FAISS 索引等资源，
# 间接服务于所有上层模块。文件底部保留了 root_path() 占位函数（返回 None）以及一段
# __main__ 自测代码，用于人工验证路径拼接是否正确。

import os
# os.path.abspath(__file__)获取当前文件的绝对路径
# os.path.dirname(os.path.abspath(__file__)))获取当前文件的上一级目录的 绝对路径
# os.path.dirname(os.path.dirname(os.path.abspath(__file__)))获取上上级 绝对路径，在本项目中 即为工程的 绝对路径，每个工程中 本文件位置不同，所以要写几个dir是不确定的
# 这里是为了 整个工程代码的可复用，绝对路径的 前半部分 各不相同，工程相关部分的 绝对路径 是一样的，书写较为简单
#当用户调取 不同的 工具类 文件时 所拼接的 后半部分 文件 路径不同，但 前半部分 绝对路径始终相同,是自己的文件路径 ，所以设为 全局变量
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 计算并保存工程根目录的绝对路径：__file__ 是本文件路径，abspath 转绝对路径，两次 dirname 上溯两级（common/ -> 工程根），作为全局变量供 get_file_path 使用


# print(root_dir)

# 一定不要画蛇添足，在 relative_path 这个变量的最开头加斜杠（即变成了 /common/llm.py），拼接后系统会自动补充
# 在 Linux 和 macOS 等类 Unix 系统中，以 / 开头的路径代表绝对路径（即从系统的根目录开始）。
# os.path.join() 有一个非常特殊的机制：当它在拼接过程中遇到一个以 / 开头的片段时，它会认为这是一个绝对路径，从而直接丢弃掉前面已经拼接好的所有内容，从这个斜杠重新开始。
def get_file_path(relative_path):
    """
    把工程内相对路径转换为绝对路径并返回。

    作用:
        以工程根目录 root_dir 为前缀，拼接传入的相对路径，得到一个与启动目录无关的
        绝对路径。这样无论从哪个工作目录运行代码，资源文件都能被稳定定位。

    参数:
        relative_path (str): 相对于工程根目录的路径片段，例如 'common/llm.py' 或
            '__003__create_neo4j_database/legal_metadata.json'。注意：开头不要加
            斜杠，否则 os.path.join 会把它当成绝对路径从而丢弃 root_dir 前缀。

    返回值:
        str: 拼接得到的绝对路径字符串，例如
            'e:\\to_github_project\\AI_legal_assistant\\common\\llm.py'。

    可迁移性说明:
        该函数只依赖标准库 os，且不依赖任何项目业务逻辑，可原样复制到任何 Python
        工程中使用。迁移时唯一需要调整的是 root_dir 的回溯层数：如果本文件位于
        工程根目录下，则只需要一次 dirname；位于子目录下则需要两次或更多次 dirname。
    """
    return os.path.join(root_dir, relative_path)  # 使用 os.path.join 拼接根目录与相对路径；join 会自动处理不同操作系统的路径分隔符（Windows 用 \，Linux/Mac 用 /），保证跨平台兼容


if __name__ == '__main__':
    print(get_file_path('common/llm.py'))  # 自测：打印 common/llm.py 的绝对路径，肉眼核对 root_dir 回溯层级是否正确


def root_path():
    """
    占位函数，当前未实现，固定返回 None。

    作用:
        预留接口，未来可用于返回工程根路径 root_dir（例如 `return root_dir`），
        当前为占位以避免外部引用报错。

    参数:
        无。

    返回值:
        None: 当前实现固定返回 None，表示功能未启用。

    可迁移性说明:
        若后续需要对外暴露工程根目录，可把返回值改为 root_dir；该函数与
        get_file_path 互不耦合，可单独修改或删除而不影响其他模块。
    """
    return None  # 占位返回值，调用方拿到 None 时应自行回退到 get_file_path('.').
