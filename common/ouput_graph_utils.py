# 📜 代码文字逻辑解析
# 本文件是项目的"流程图可视化辅助工具"，核心作用是把多智能体协作图（无论是官方
# langgraph 还是在 Python 3.8 环境下使用的轻量 langgraph_compat）渲染成 PNG 图片
# 保存到本地，便于人工查看 Agent 之间的节点跳转和条件分支关系。核心逻辑非常简洁：
# 模块只导入了标准库 traceback 用于异常打印，对外暴露唯一函数 output_pic_graph，
# 该函数接收一个编译后的图对象 app 和目标文件名 filename，先通过 hasattr 判断 app
# 是否自带 get_graph 方法（兼容两种实现），拿到 graph_obj 后再判断它支持
# draw_mermaid_png 还是 draw_mermaid，分别走"二进制写入文件"或"打印 mermaid 文本"
# 两条路径；任何异常都被 except 捕获并打印 traceback，保证主流程不被绘图失败中断。
# 函数关系：本模块不依赖项目其他 common 模块，自身被多智能体流程入口（如 main 流程
# 脚本）调用，通常在 compile() 之后、invoke() 之前调用一次以生成静态流程图。

"""
流程图可视化工具
兼容 langgraph 和 langgraph_compat(轻量StateGraph)
"""
import traceback                                          # 导入标准库 traceback，用于在绘图失败时打印完整堆栈信息，便于排查问题


def output_pic_graph(app, filename: str = "graph.jpg"):
    """
    把编译后的 LangGraph 图对象渲染为 PNG 图片并保存到文件。

    作用:
        兼容官方 langgraph 与本项目的轻量 langgraph_compat 两种实现，自动探测
        app / graph_obj 上支持的绘图方法（draw_mermaid_png 优先，draw_mermaid
        次之），把流程图保存为图片或打印为 mermaid 文本。任何异常都静默捕获并
        打印，保证主流程不中断。

    参数:
        app: 编译后的图对象，通常是 StateGraph(...).compile() 的返回值。它应
            支持直接调用 draw_mermaid_png，或通过 get_graph() 返回的对象支持。
        filename (str): 输出图片文件名，默认 'graph.jpg'；实际写入的是 PNG
            格式字节流，文件名后缀只是命名约定，不影响内容格式。

    返回值:
        无返回值；成功时把图片写入 filename 指定的文件，失败时仅打印错误信息。

    可迁移性说明:
        该函数仅依赖 traceback 标准库和图对象自身的绘图方法，可独立用于任何
        兼容 langgraph 接口的图对象；若需要支持其他绘图后端（如 graphviz），
        可在函数内追加 elif 分支而不改调用方。
    """
    """生成流程图PNG, 失败时静默跳过"""
    try:
        print(f"正在生成流程图: {filename}")              # 打印进度提示，让用户知道正在生成图，避免误以为程序卡住
        # 尝试调用 get_graph().draw_mermaid_png()
        # 兼容真正的langgraph和轻量langgraph_compat
        graph_obj = app.get_graph() if hasattr(app, "get_graph") else app  # 兼容两种图对象：若 app 自带 get_graph 方法（如 CompiledGraph），则调用它拿到可视化对象；否则直接把 app 当作可视化对象使用（有些版本直接在 app 上挂绘图方法）
        if hasattr(graph_obj, "draw_mermaid_png"):        # 优先走 PNG 二进制路径（官方 langgraph 与本项目 _GraphVisualizer 都实现此方法）
            image_data = graph_obj.draw_mermaid_png()     # 调用绘图方法返回 PNG 字节流；本项目的 _GraphVisualizer 用 matplotlib 画，失败时返回 b""
        elif hasattr(graph_obj, "draw_mermaid"):          # 次选：某些版本只提供 draw_mermaid 返回 mermaid 文本而非图片
            # 某些版本只有draw_mermaid(返回文本)
            mermaid_text = graph_obj.draw_mermaid()       # 调用 draw_mermaid 拿到 mermaid 源码字符串
            print(f"Mermaid流程图:\n{mermaid_text}")      # 直接把 mermaid 文本打印到控制台，用户可复制到 mermaid live editor 渲染
            return                                        # 文本路径已处理完毕，直接返回不再写文件
        else:
            print("⚠️ 无法生成流程图(不支持draw_mermaid_png)")  # 既不支持 PNG 也不支持文本，打印警告
            return                                        # 直接返回，避免后续 image_data 未定义导致 NameError

        if image_data:                                    # 拿到了非空字节流才写文件（draw_mermaid_png 失败时可能返回 b""）
            with open(filename, 'wb') as f:               # 以二进制写模式打开目标文件；图片必须用 'wb' 而非 'w'，否则会把字节串当文本编码报错
                f.write(image_data)                       # 把 PNG 字节流一次性写入文件
            print(f"流程图已保存到: {filename}")            # 打印保存路径，便于用户定位文件
    except Exception as e:                                # 捕获所有异常（文件无写权限、绘图依赖缺失、图对象结构异常等）
        print(f"生成流程图失败: {e}")                      # 打印简短错误信息，便于快速定位
        traceback.print_exc()                             # 打印完整堆栈，便于深入排查；不打断主流程
