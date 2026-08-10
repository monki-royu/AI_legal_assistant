import os
# os.path.abspath(__file__)获取当前文件的绝对路径
# os.path.dirname(os.path.abspath(__file__)))获取当前文件的上一级目录的 绝对路径
# os.path.dirname(os.path.dirname(os.path.abspath(__file__)))获取上上级 绝对路径，在本项目中 即为工程的 绝对路径，每个工程中 本文件位置不同，所以要写几个dir是不确定的
# 这里是为了 整个工程代码的可复用，绝对路径的 前半部分 各不相同，工程相关部分的 绝对路径 是一样的，书写较为简单
#当用户调取 不同的 工具类 文件时 所拼接的 后半部分 文件 路径不同，但 前半部分 绝对路径始终相同,是自己的文件路径 ，所以设为 全局变量
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# print(root_dir)

# 一定不要画蛇添足，在 relative_path 这个变量的最开头加斜杠（即变成了 /common/llm.py），拼接后系统会自动补充
# 在 Linux 和 macOS 等类 Unix 系统中，以 / 开头的路径代表绝对路径（即从系统的根目录开始）。
# os.path.join() 有一个非常特殊的机制：当它在拼接过程中遇到一个以 / 开头的片段时，它会认为这是一个绝对路径，从而直接丢弃掉前面已经拼接好的所有内容，从这个斜杠重新开始。
def get_file_path(relative_path):
    return os.path.join(root_dir, relative_path)


if __name__ == '__main__':
    print(get_file_path('common/llm.py'))


def root_path():
    return None