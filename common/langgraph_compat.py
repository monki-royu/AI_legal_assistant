# 📜 代码文字逻辑解析
# 本文件是项目为兼容 Python 3.8 环境而实现的"轻量版 LangGraph StateGraph"，对外 API
# 与官方 langgraph.graph.StateGraph 完全兼容，目的是在无法安装 langgraph 0.2+ 的旧
# Python 环境下仍能跑通多智能体协作流程。核心逻辑分三层：第一层是常量定义 START/END，
# 表示图的入口与出口；第二层是 StateGraph 类，提供建图 API（add_node/add_edge/
# add_conditional_edges/compile），把节点函数、固定边、条件边分别存入三个字典；
# 第三层是 CompiledGraph 类，compile() 返回它的实例，负责按"从 START 出发 → 取下一
# 节点 → 执行节点函数 → 合并返回值到 state → 再取下一节点"的循环方式驱动图执行，
# 同时支持同步 invoke 与异步 ainvoke，并对同步/异步节点函数做了兼容处理。辅助类
# _GraphVisualizer 用 matplotlib 简易绘制流程图，draw_mermaid_png 失败时静默返回空
# 字节串。函数关系：本模块被项目多智能体流程 import 替代官方 langgraph，下游用法与
# 官方一致；ouput_graph_utils.py 通过 CompiledGraph.get_graph().draw_mermaid_png()
# 实现可视化。未来升级到 Python 3.9+ 后只需切换 import 即可无缝迁移到官方实现。
"""
langgraph 兼容层(轻量 StateGraph)
当 Python 3.8 环境无法安装 langgraph 0.2+ 时使用本模块
API 与 langgraph.graph.StateGraph 完全兼容:
  - StateGraph(AgentState)
  - add_node(name, func)
  - add_edge(from, to)
  - add_conditional_edges(from, router_func, path_map)
  - compile() -> CompiledGraph
  - CompiledGraph.invoke(input) / ainvoke(input)
  - CompiledGraph.get_graph().draw_mermaid_png()

未来升级 Python 3.9+ 后, 只需把 import 切换回:
  from langgraph.graph import StateGraph, START, END
即可无缝迁移, 无需改动业务代码
"""
import asyncio                                            # 导入标准库 asyncio，用于检测节点函数是否为协程函数（asyncio.iscoroutinefunction）以及在同步上下文中执行异步节点（run_until_complete）
import copy                                               # 导入标准库 copy，备用（当前实现未直接使用，保留以供未来深拷贝状态时使用）
from typing import Any, Callable, Dict, Optional, Type, TypedDict  # 从 typing 导入常用类型注解，用于声明节点字典、边字典、状态类型等的类型，提升可读性与 IDE 提示


# 常量
START = "__start__"                                       # 定义图的虚拟入口节点名，与官方 langgraph 一致；invoke 时从该常量开始驱动
END = "__end__"                                           # 定义图的虚拟出口节点名，遇到该常量时停止循环；条件路由也会在无匹配时回退到 END


class _GraphVisualizer:
    """简易图可视化(用于 draw_mermaid_png, 失败时静默)"""

    def __init__(self, nodes: Dict[str, str], edges: list):
        """
        初始化图可视化对象，保存节点和边以供绘图使用。

        作用:
            接收 CompiledGraph 收集到的节点名集合与边列表，缓存到实例属性，
            供 draw_mermaid_png 时按布局算法绘制。

        参数:
            nodes (Dict[str, str]): 节点字典，key 为节点名，value 在本实现中
                未使用（占位），主要取 keys 作为绘制节点列表。
            edges (list): 边列表，每个元素是 (src, dst, label) 三元组，label
                为条件边的条件名或固定边的空字符串。

        返回值:
            无返回值；构造完成后 self.nodes、self.edges 即可被 draw_mermaid_png 使用。

        可迁移性说明:
            该类仅依赖 matplotlib，可独立用于任何"节点 + 边"列表的简易可视化；
            若需要更复杂布局可替换布局算法而不改外部接口。
        """
        self.nodes = nodes                                # 缓存节点字典，draw_mermaid_png 时按加入顺序纵向排列
        self.edges = edges                                # 缓存边列表，draw_mermaid_png 时据此绘制箭头与条件标签

    def draw_mermaid_png(self) -> bytes:
        """
        生成流程图 PNG 图片字节流。

        作用:
            用 matplotlib 把节点纵向排布、画圆圈表示节点、画带箭头直线表示边，
            最终保存为 PNG 字节流返回。任何异常都静默返回空字节串，保证调用方
            不会因为绘图失败而中断主流程。

        参数:
            无。

        返回值:
            bytes: PNG 图片二进制数据；绘图失败时返回 b""（空字节串）。

        可迁移性说明:
            该方法用 matplotlib 简易实现，不依赖 mermaid CLI；若需更高质量图形
            可改为调用 mermaid CLI 或 graphviz，调用方接口不变。
        """
        """生成 mermaid PNG(尝试用 matplotlib 画, 失败返回空)"""
        try:
            import matplotlib.pyplot as plt               # 局部导入 matplotlib.pyplot，避免在不需要绘图的项目中强制安装该依赖
            import matplotlib.patches as mpatches         # 局部导入 patches（虽然本实现未直接使用 mpatches，保留以备扩展自定义形状）
            import io                                     # 局部导入 io，用于创建内存字节缓冲区保存 PNG

            fig, ax = plt.subplots(figsize=(14, 10))      # 创建 14×10 英寸的画布与坐标轴，尺寸足够容纳较多节点
            ax.set_xlim(0, 10)                            # 设置 x 轴范围 0~10，所有节点固定在 x=5 的纵线上
            ax.set_ylim(0, 10)                            # 设置 y 轴范围 0~10，从下到上排列节点
            ax.axis('off')                                # 关闭坐标轴刻度与边框，让图更干净
            ax.set_title("LangGraph Flow", fontsize=14, fontweight='bold')  # 设置标题"LangGraph Flow"，加粗 14 号字

            # 简单布局: 按加入顺序纵向排列
            n = len(self.nodes)                           # 节点总数，用于计算纵向间距
            positions = {}                                # 存储每个节点的 (x, y) 坐标
            for i, name in enumerate(self.nodes):         # 按节点加入顺序枚举，索引 i 决定 y 坐标
                y = 9 - i * (8 / max(n - 1, 1))           # 计算第 i 个节点的 y 坐标：从 9 线性下降到 1，max(n-1,1) 防止除零
                positions[name] = (5, y)                 # 固定 x=5，y 为计算值；所有节点排成一列

            # 画节点
            for name, (x, y) in positions.items():        # 遍历每个节点的坐标
                circle = plt.Circle((x, y), 0.6, color='#4CAF50', alpha=0.8)  # 以 (x,y) 为圆心、0.6 为半径画绿色半透明圆圈
                ax.add_patch(circle)                      # 把圆圈加到坐标轴
                ax.text(x, y, name[:18], ha='center', va='center',
                        fontsize=7, color='white', fontweight='bold', wrap=True)  # 在圆心处写节点名（截断到 18 字符），白色加粗 7 号字，居中对齐

            # 画边
            for (src, dst, label) in self.edges:          # 遍历每条边三元组
                if src in positions and dst in positions:  # 仅当两端节点都在布局中才绘制，避免虚拟节点导致 KeyError
                    x1, y1 = positions[src]               # 取起点坐标
                    x2, y2 = positions[dst]               # 取终点坐标
                    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                                arrowprops=dict(arrowstyle='->', color='#666', lw=1.2))  # 用 annotate 画一条从 (x1,y1) 到 (x2,y2) 的灰色箭头，线宽 1.2
                    if label:                             # 若边有条件标签（条件边才有）
                        mx, my = (x1 + x2) / 2, (y1 + y2) / 2  # 计算线段中点作为标签位置
                        ax.text(mx + 0.2, my, label, fontsize=6, color='#d32f2f',
                                style='italic')           # 在中点偏右处写红色斜体小字标签，标识条件分支

            buf = io.BytesIO()                            # 创建内存字节缓冲区，用于接收 savefig 输出
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')  # 把当前图保存为 PNG 写入缓冲区，dpi=100，bbox_inches='tight' 去除多余白边
            plt.close()                                  # 关闭画布释放内存，避免 matplotlib 资源泄漏
            return buf.getvalue()                        # 取出缓冲区的字节流返回给调用方写入文件
        except Exception:
            return b""                                   # 任何异常（缺依赖、字体问题等）都静默返回空字节串，保证主流程不中断


class CompiledGraph:
    """编译后的图, 支持 invoke/ainvoke"""

    def __init__(self, nodes: Dict[str, Callable], edges: Dict[str, list],
                 cond_edges: Dict[str, tuple], state_type: Type):
        """
        初始化编译后的图，保存节点、固定边、条件边与状态类型。

        作用:
            由 StateGraph.compile() 调用，把建图阶段收集到的节点与边数据传给本类，
            本类负责按图结构驱动执行。

        参数:
            nodes (Dict[str, Callable]): 节点名字到节点函数的映射，节点函数接收
                state dict 返回 update dict。
            edges (Dict[str, list]): 固定边映射，{from: [(to, label), ...]}，
                执行完 from 后无条件跳到第一条边的 to。
            cond_edges (Dict[str, tuple]): 条件边映射，{from: (router_func,
                path_map)}，执行完 from 后调用 router_func(state) 决定下一节点。
            state_type (Type): 状态类型（TypedDict 子类），本实现仅做保存不强制
                校验，保留以兼容官方 API。

        返回值:
            无返回值；构造完成后即可调用 invoke / ainvoke / get_graph。

        可迁移性说明:
            该类是轻量执行引擎，与官方 langgraph 的 CompiledGraph 接口一致；
            切换到官方实现时业务代码无需改动。
        """
        self.nodes = nodes          # {name: func}                       保存节点字典，invoke 时按 current 取出函数执行
        self.edges = edges          # {from: [(to, label)]}              保存固定边字典，_get_next_node 据此确定无条件后继
        self.cond_edges = cond_edges  # {from: (router_func, path_map)}   保存条件边字典，_get_next_node 优先据此路由
        self.state_type = state_type                                    # 保存状态类型，保留以兼容官方接口，当前未做字段校验

    def _merge_state(self, state: dict, update: dict) -> dict:
        """
        把节点函数返回的 update 合并进 state。

        作用:
            节点函数返回的 dict 表示对状态的局部更新，本方法用 dict.update 把它
            合并到全局 state 中，形成新的状态供下一节点使用。

        参数:
            state (dict): 当前完整状态。
            update (dict): 节点函数返回的更新字典；可为 None 或非 dict。

        返回值:
            dict: 合并后的 state（原地修改并返回同一对象）。

        可迁移性说明:
            该方法实现最朴素的"覆盖式合并"，未实现官方 langgraph 的 reducer 语义
            （如列表追加）；迁移到官方实现时可利用 TypedDict 的 Annotated reducer
            获得更精细的合并行为。
        """
        """合并节点返回值到 state"""
        if update is None:                                # 节点函数允许返回 None 表示无更新
            return state                                  # 直接返回原状态
        if isinstance(update, dict):                      # 仅处理 dict 类型更新，避免字符串等被误合并
            state.update(update)                          # 用 update 中的键值覆盖 state 中同名键，实现状态推进
        return state                                      # 返回合并后的 state，供下一节点读取

    def _get_next_node(self, current: str, state: dict) -> str:
        """
        根据当前节点和状态确定下一个要执行的节点名。

        作用:
            先查条件边（优先级高），调用 router_func(state) 得到路由结果，再按
            path_map / 节点名 / END 三种情况解析；若无条件边则取固定边第一条；
            都没有则返回 END 结束执行。

        参数:
            current (str): 当前刚执行完的节点名。
            state (dict): 当前状态，传给 router_func 用于路由决策。

        返回值:
            str: 下一个节点名；无后继时返回 END。

        可迁移性说明:
            与官方 langgraph 的路由语义保持一致；path_map 既支持 key 映射也支持
            直接节点名，迁移时行为一致。
        """
        """获取下一个要执行的节点"""
        # 优先检查条件边
        if current in self.cond_edges:                    # 当前节点配置了条件边，则按条件路由
            router_func, path_map = self.cond_edges[current]  # 解包出路由函数与路径映射
            try:
                result = router_func(state)               # 调用路由函数，传入当前状态，返回值决定走哪条边
            except Exception as e:                        # 路由函数抛异常时不应让整图崩溃
                print(f"⚠️ 条件路由异常({current}): {e}")  # 打印警告，便于排查路由逻辑错误
                return END                                # 异常时直接结束，避免死循环
            # result 可以是 path_map 的 key, 也可以是直接节点名
            if isinstance(result, str):                   # 路由结果必须是字符串，否则视为无效
                if result in path_map:                    # 情况1：result 是 path_map 的 key
                    return path_map[result]               # 通过 path_map 映射到真实节点名
                if result in self.nodes:                  # 情况2：result 直接是已注册节点名
                    return result                         # 直接返回该节点名
                if result == END:                         # 情况3：result 显式等于 END 常量
                    return END                            # 结束执行
                # 尝试模糊匹配
                for k, v in path_map.items():             # 情况4：result 等于 path_map 的某个 value
                    if v == result:                       # 找到 value 等于 result 的项
                        return v                          # 返回该 value 作为下一节点
                print(f"⚠️ 路由结果无匹配({current} -> {result}), 结束")  # 以上都不匹配
                return END                                # 无匹配则结束
            return END                                    # result 不是字符串，直接结束

        # 固定边: 取第一条
        if current in self.edges:                         # 当前节点配置了固定边
            edges = self.edges[current]                   # 取出该节点的所有固定边
            if edges:                                     # 边列表非空
                return edges[0][0]  # (to, label) 的 to  # 取第一条边的目标节点（本实现不分支并行，只走第一条）

        return END                                        # 既无条件边也无固定边，结束执行

    def invoke(self, input: dict, config: dict = None) -> dict:
        """
        同步执行整张图，返回最终状态。

        作用:
            从 START 开始，循环"取下一节点 → 执行节点函数 → 合并状态"，直到遇到
            END 或达到最大步数。兼容同步与异步节点函数（异步节点用事件循环驱动）。

        参数:
            input (dict): 初始状态字典，至少包含业务字段；若缺 retry_count /
                cypher_retry_count 会自动补 0。
            config (dict, optional): 预留配置参数，当前未使用，保留以兼容官方 API。

        返回值:
            dict: 执行结束后的最终状态，包含所有节点累积的更新；失败时含 output
                字段描述错误。

        可迁移性说明:
            与官方 langgraph CompiledGraph.invoke 签名兼容；切换官方实现时调用方
            无需改动。注意本实现用 run_until_complete 执行异步节点，在已有事件循环
            的环境（如 Jupyter）可能冲突，迁移到官方实现可避免。
        """
        """同步执行图"""
        state = dict(input) if isinstance(input, dict) else {"input": input}  # 复制输入为状态 dict；若 input 不是 dict（如字符串），包装成 {"input": input}
        # 确保所有字段有默认值
        if "retry_count" not in state:                    # 业务用 retry_count 控制整体重试次数
            state["retry_count"] = 0                      # 缺失则初始化为 0
        if "cypher_retry_count" not in state:             # 业务用 cypher_retry_count 控制 Cypher 重试次数
            state["cypher_retry_count"] = 0               # 缺失则初始化为 0

        current = START                                   # 当前节点指针，从 START 开始
        max_steps = 50  # 防止死循环                       # 最大步数上限，避免条件边形成环导致死循环
        steps = 0                                         # 已执行步数计数器

        while current != END and steps < max_steps:       # 主循环：未到 END 且未超步数上限
            steps += 1                                    # 步数自增
            # START -> 第一个节点
            if current == START:                          # 处于虚拟入口
                if START in self.edges and self.edges[START]:  # 若配置了从 START 出发的边
                    current = self.edges[START][0][0]     # 取该边目标作为第一个真实节点
                else:
                    # 没有从 START 出发的边, 找第一个节点
                    if self.nodes:                        # 没有显式入口边但有注册节点
                        current = list(self.nodes.keys())[0]  # 取第一个注册节点作为起点
                    else:
                        break                             # 既无入口边也无节点，直接结束
                continue                                  # 跳过本轮后续，进入下一轮执行真实节点

            if current not in self.nodes:                 # 路由到一个未注册的节点名
                print(f"⚠️ 节点不存在: {current}")         # 打印警告便于排查 add_node 拼写错误
                break                                     # 终止执行

            func = self.nodes[current]                    # 取出当前节点对应的函数
            print(f"\n▶ 执行节点: {current}")             # 打印执行进度，便于调试观察流程走向
            try:
                # 节点可能是同步或异步
                if asyncio.iscoroutinefunction(func):     # 若节点是 async def
                    update = asyncio.get_event_loop().run_until_complete(func(state))  # 在事件循环中运行协程并取结果
                else:
                    update = func(state)                  # 同步节点直接调用
                state = self._merge_state(state, update)  # 合并节点返回值到 state
            except Exception as e:                        # 节点执行抛异常
                import traceback                          # 局部导入 traceback 打印完整堆栈
                print(f"❌ 节点 {current} 执行失败: {e}")  # 打印失败节点与异常信息
                traceback.print_exc()                     # 打印完整 traceback 便于定位
                state["output"] = f"执行失败({current}): {e}"  # 把错误写入 state.output，供调用方感知
                break                                     # 终止执行

            # 下一节点
            current = self._get_next_node(current, state)  # 根据当前节点与状态计算下一节点

        print(f"\n✅ 图执行完成, 共 {steps} 步")            # 打印总步数，便于性能分析
        return state                                      # 返回最终状态

    async def ainvoke(self, input: dict, config: dict = None) -> dict:
        """
        异步执行整张图，返回最终状态。

        作用:
            与 invoke 语义一致，但在 async 上下文中运行；对异步节点用 await 执行，
            对同步节点直接调用，避免阻塞事件循环。

        参数:
            input (dict): 初始状态字典。
            config (dict, optional): 预留配置参数，当前未使用。

        返回值:
            dict: 执行结束后的最终状态。

        可迁移性说明:
            与官方 langgraph CompiledGraph.ainvoke 签名兼容；在 Streamlit 等已有
            事件循环的环境下推荐使用 ainvoke 而非 invoke。
        """
        """异步执行图"""
        state = dict(input) if isinstance(input, dict) else {"input": input}  # 复制输入为状态 dict
        if "retry_count" not in state:                    # 同步版本一致：补全重试计数默认值
            state["retry_count"] = 0
        if "cypher_retry_count" not in state:             # 补全 Cypher 重试计数默认值
            state["cypher_retry_count"] = 0

        current = START                                   # 当前节点指针
        max_steps = 50                                    # 最大步数上限
        steps = 0                                         # 步数计数器

        while current != END and steps < max_steps:       # 主循环
            steps += 1                                    # 步数自增
            if current == START:                          # 处于虚拟入口
                if START in self.edges and self.edges[START]:  # 有显式入口边
                    current = self.edges[START][0][0]     # 取入口边目标
                elif self.nodes:                          # 无入口边但有节点
                    current = list(self.nodes.keys())[0]  # 取第一个注册节点
                else:
                    break                                 # 无节点则结束
                continue                                  # 进入下一轮执行真实节点

            if current not in self.nodes:                 # 节点未注册
                break                                     # 静默结束（异步版本不打印警告，避免日志噪音）

            func = self.nodes[current]                    # 取出节点函数
            print(f"\n▶ 执行节点: {current}")             # 打印进度
            try:
                if asyncio.iscoroutinefunction(func):     # 异步节点
                    update = await func(state)            # 用 await 执行协程，不阻塞事件循环
                else:
                    update = func(state)                  # 同步节点直接调用
                state = self._merge_state(state, update)  # 合并状态
            except Exception as e:                        # 异常处理
                import traceback                          # 局部导入 traceback
                print(f"❌ 节点 {current} 执行失败: {e}")  # 打印错误
                traceback.print_exc()                     # 打印堆栈
                state["output"] = f"执行失败({current}): {e}"  # 写入错误信息
                break                                     # 终止

            current = self._get_next_node(current, state)  # 计算下一节点

        print(f"\n✅ 图执行完成, 共 {steps} 步")            # 打印总步数
        return state                                      # 返回最终状态

    def get_graph(self):
        """
        返回图可视化对象，用于调用 draw_mermaid_png。

        作用:
            把 CompiledGraph 内部的节点与边整理成 _GraphVisualizer 需要的格式，
            供 ouput_graph_utils.output_pic_graph 调用绘图。

        参数:
            无。

        返回值:
            _GraphVisualizer: 包含节点字典与边列表的可视化对象。

        可迁移性说明:
            与官方 langgraph CompiledGraph.get_graph 接口兼容；返回对象提供
            draw_mermaid_png 方法，调用方代码可无缝迁移。
        """
        """返回可视化对象"""
        all_edges = []                                    # 收集所有边（固定边 + 条件边）为三元组列表
        for src, dsts in self.edges.items():              # 遍历固定边
            for (dst, label) in dsts:                     # 每条固定边
                all_edges.append((src, dst, label))       # 加入边列表，label 为空字符串
        for src, (router, path_map) in self.cond_edges.items():  # 遍历条件边
            for key, dst in path_map.items():             # 每个条件分支
                all_edges.append((src, dst, f"条件:{key}"))  # 加入边列表，label 标注条件名
        return _GraphVisualizer(self.nodes, all_edges)    # 构造并返回可视化对象


class StateGraph:
    """轻量 StateGraph, API 兼容 langgraph"""

    def __init__(self, state_type: Type = None):
        """
        初始化一个空的 StateGraph 建图对象。

        作用:
            提供建图容器，允许通过 add_node / add_edge / add_conditional_edges
            注册节点与边，最后通过 compile 生成可执行的 CompiledGraph。

        参数:
            state_type (Type, optional): 状态类型（TypedDict 子类），用于声明状态
                字段；本实现仅做保存，不做强校验，保留以兼容官方 API。

        返回值:
            无返回值；构造完成后即可调用 add_node 等方法建图。

        可迁移性说明:
            与官方 langgraph.graph.StateGraph 接口一致；切换官方实现时建图代码
            完全不用改。
        """
        self.state_type = state_type                      # 保存状态类型
        self.nodes: Dict[str, Callable] = {}              # 节点字典，初始为空，add_node 时填充
        self.edges: Dict[str, list] = {}       # {from: [(to, label)]}   固定边字典，初始为空
        self.cond_edges: Dict[str, tuple] = {}  # {from: (router_func, path_map)}  条件边字典，初始为空

    def add_node(self, name: str, func: Callable):
        """
        注册一个节点及其处理函数。

        作用:
            把节点名与节点函数存入 self.nodes，供 compile 后的 CompiledGraph
            在执行到该节点时调用 func(state)。

        参数:
            name (str): 节点名，唯一标识；不能与 START/END 保留字冲突。
            func (Callable): 节点处理函数，签名为 func(state: dict) -> dict |
                None，可为同步或 async def。

        返回值:
            无返回值；节点被记录到 self.nodes。

        可迁移性说明:
            与官方 langgraph StateGraph.add_node 完全兼容；同名节点会覆盖，
            行为与官方一致。
        """
        """注册节点"""
        if name in (START, END):                          # 节点名不能与保留字冲突
            raise ValueError(f"节点名不能用 {START} 或 {END}")  # 抛错避免后续路由混淆
        self.nodes[name] = func                           # 注册节点函数

    def add_edge(self, from_node: str, to_node: str):
        """
        添加一条固定边（无条件跳转）。

        作用:
            告知图执行器：执行完 from_node 后无条件跳到 to_node。

        参数:
            from_node (str): 起点节点名，可以是 START 或已注册节点。
            to_node (str): 终点节点名，可以是 END 或已注册节点。

        返回值:
            无返回值；边被追加到 self.edges[from_node] 列表。

        可迁移性说明:
            与官方 langgraph StateGraph.add_edge 兼容；本实现允许多条固定边但
            执行时只走第一条，迁移到官方实现后可用并行分支语义。
        """
        """添加固定边"""
        if from_node not in self.edges:                   # 该起点还没有任何边
            self.edges[from_node] = []                    # 初始化边列表
        self.edges[from_node].append((to_node, ""))       # 追加一条边，label 为空字符串

    def add_conditional_edges(self, from_node: str, router_func: Callable,
                               path_map: Dict[str, str] = None):
        """
        添加条件边（按路由函数结果跳转）。

        作用:
            告知图执行器：执行完 from_node 后调用 router_func(state)，根据返回
            值在 path_map 中查找下一节点。

        参数:
            from_node (str): 起点节点名。
            router_func (Callable): 路由函数，签名为 router_func(state: dict)
                -> str，返回值用于在 path_map 中查找或直接作为节点名。
            path_map (Dict[str, str], optional): 路由结果到节点名的映射，例如
                {"need_search": "search_node", "end": "__end__"}；为 None 时用空 dict。

        返回值:
            无返回值；条件边被存入 self.cond_edges[from_node]。

        可迁移性说明:
            与官方 langgraph StateGraph.add_conditional_edges 兼容；path_map 的
            value 也可以直接是 END 常量。
        """
        """添加条件边"""
        self.cond_edges[from_node] = (router_func, path_map or {})  # 存储路由函数与路径映射，path_map 为 None 时用空 dict

    def compile(self) -> CompiledGraph:
        """
        编译图为可执行的 CompiledGraph。

        作用:
            把建图阶段收集的节点、固定边、条件边与状态类型打包成 CompiledGraph
            实例返回，调用方可对其 invoke / ainvoke。

        参数:
            无。

        返回值:
            CompiledGraph: 可执行的图对象。

        可迁移性说明:
            与官方 langgraph StateGraph.compile 兼容；切换官方实现后调用方代码
            无需改动。
        """
        """编译图"""
        return CompiledGraph(self.nodes, self.edges, self.cond_edges, self.state_type)  # 把建图数据传入 CompiledGraph 构造可执行图
