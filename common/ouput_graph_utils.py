from langgraph.graph.state import CompiledStateGraph
import traceback


def output_pic_graph(app: CompiledStateGraph, filename: str = "graph.jpg"):
    try:
        print(f"正在生成流程图: {filename}")
        mermaid_code = app.get_graph().draw_mermaid_png()
        print(f"生成的图片数据大小: {len(mermaid_code)} 字节")
        with open(filename, 'wb') as f:
            f.write(mermaid_code)
        print(f"流程图已保存到: {filename}")
    except Exception as e:
        print(f"生成流程图失败: {e}")
        traceback.print_exc()
