# ═══════════════════════════════════════════════════════════════════════════
# 📜 文件名称: common/config.py
# 📜 文件角色: 整个 AI 法律助理项目的【配置中枢 / 总保险柜 / 通讯录】
# ═══════════════════════════════════════════════════════════════════════════
#
# ────────────────────────────────────────────────────────────────────────────
# 一、这个文件是干什么的？（写给零基础同学的大白话版）
# ────────────────────────────────────────────────────────────────────────────
# 想象你开了一家"法律咨询公司"，公司的所有"机密信息"和"资源清单"都放在一个
# 总保险柜里：
#   · 保险柜第 1 层：各种"钥匙"——大模型 API Key、Neo4j 图数据库的账号密码、
#     极梦（即梦）文生图密钥、北大法宝 Token、企查查密钥……（这些叫【环境变量】，
#     存放在项目根目录的 .env 文件里）
#   · 保险柜第 2 层：各种"地图/资源"——知识图谱的元数据 JSON 文件、FAISS 向量
#     索引文件、id2text 映射文件……（这些叫【本地资源文件】）
# 本文件（config.py）就是这座保险柜的"看门人"：它负责把钥匙和地图全部取出来，
# 整整齐齐地摆进一个叫 Config 的【类】里，让项目里其他所有模块"想用哪个就拿
# 哪个"，自己不用到处翻。
#
# 换句话说，本文件只做两件事：
#   ① 读取 .env 文件里的【环境变量】（模型 API Key、数据库连接串、第三方密钥等），
#      避免把敏感信息【硬编码】写死在业务代码里。硬编码 = 直接把密码写进代码，
#      一旦泄露就要改代码重新发布，非常危险；
#   ② 借助 common/path_utils.py 的 get_file_path 函数，把工程内的【相对路径】
#      转换成【绝对路径】，统一管理本地资源（图谱元数据 JSON、FAISS 索引、
#      id2text 映射），解决"从不同目录启动程序时找不到文件"的问题。
#
# 对使用者的体验是：
#   其他模块只需要写一行 from common.config import Config，
#   然后 Config().MODEL_API_KEY 就能拿到大模型密钥，Config().NEO4J_URI 就能拿到
#   数据库地址，Config().history_num 就能拿到记忆轮次——一行一个属性，非常方便。
#
#
# ────────────────────────────────────────────────────────────────────────────
# 二、完整代码逻辑流程（一步一步跟着走）
# ────────────────────────────────────────────────────────────────────────────
# Step 0（准备阶段）：文件最顶部导入三个"帮手"——
#     · os：标准库，负责读环境变量（os.getenv）和查文件（os.path.exists）；
#     · load_dotenv：第三方包 python-dotenv 的函数，负责解析 .env 文件；
#     · get_file_path：本项目自己写的函数，负责把相对路径转成绝对路径。
# Step 1（第一次加载 .env）：调用 load_dotenv()，让 python-dotenv 按"默认规则"
#     从【当前工作目录】开始向上逐级查找 .env 并加载。好处：无论程序在哪个目录
#     下被启动，只要那个目录（或它的上级目录）里有 .env，就能读到一部分配置。
# Step 2（第二次加载 .env）：调用 load_dotenv(get_file_path(".env"))，显式定位到
#     工程根目录下的 .env 再加载一次，形成"双保险"。注意：python-dotenv 默认
#     override=False，即【不会覆盖】已经存在的环境变量，所以第二次加载只会
#     【补齐缺失项】，不会把第一次读到的值冲掉——两个动作互相补漏、互不打架。
# Step 3（定义 Config 类）：创建全项目唯一的配置类，所有配置项都作为"实例属性"
#     存放（实例属性 = 挂在对象身上的变量，用 对象.属性名 访问）。
# Step 4（__init__ 构造方法逐项读配置）：
#     a. 大模型三件套：MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME；
#     b. Neo4j 图数据库三件套：NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD；
#     c. 极梦文生图两件套：JIMENG_AK / JIMENG_SK；
#     d. 图谱元数据【三级兜底】：优先读法律项目 legal_metadata.json，
#        不存在则读中药项目 tcm_metadata.json，再不存在就给空 JSON "{}"；
#     e. Embedding 模型路径：EMBEDDING_MODEL_PATH；
#     f. 向量索引相关：ENTITY_INDEX_PATH（FAISS 索引文件）、
#        ENTITY_ID2TEXT_PATH（id 到文本的映射文件）；
#     g. 对话记忆轮次：history_num（代码里直接写死为 5）；
#     h. 北大法宝 MCP 三件套：BEIDA_FABAO_TOKEN / BEIDA_FABAO_BASE_URL /
#        BEIDA_FABAO_TIMEOUT；
#     i. 企查查五件套：QICHACHA_AUTHORIZATION / QICHACHA_APP_KEY /
#        QICHACHA_SECRET_KEY / QICHACHA_BASE_URL / QICHACHA_TIMEOUT。
# Step 5（自测入口）：if __name__ == "__main__" —— 只有"直接运行本文件"时才
#     实例化一个 Config 做本地测试；被其他模块 import 时这段代码【不会执行】。
#
#
# ────────────────────────────────────────────────────────────────────────────
# 三、哪些文件在依赖本文件？（依赖关系图）
# ────────────────────────────────────────────────────────────────────────────
# 上游依赖（本文件依赖谁）：
#   · common/path_utils.py —— 提供 get_file_path()，把相对路径拼成绝对路径；
#     本文件用它定位 .env、legal_metadata.json、FAISS 索引等资源。
#   · 第三方库 python-dotenv —— 提供 load_dotenv()，负责解析 .env 文件。
#   · 标准库 os —— 提供 os.getenv / os.path.exists。
#
# 下游依赖（谁在使用本文件，即"调用方"）：
#   · common/llm.py —— 用 MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME 创建
#     ChatOpenAI 大模型客户端；
#   · common/neo4j_client.py —— 用 NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
#     连接 Neo4j 图数据库；
#   · common/embedding_model.py —— 用 EMBEDDING_MODEL_PATH 加载本地向量化模型；
#   · common/mcp_beidafabao.py —— 用 BEIDA_FABAO_TOKEN / BEIDA_FABAO_BASE_URL /
#     BEIDA_FABAO_TIMEOUT 调用北大法宝 MCP（付费法规数据源，质量门禁 < 0.85
#     且经 3 轮重试后才调用）；
#   · common/qichacha_client.py —— 用 QICHACHA_* 系列配置做企业资信查询；
#   · __001__clawler/llm_gen.py —— 读取大模型 API Key 做内容生成；
#   · __004__langgraph_more_nodes/nodes/image_generate_node.py —— 用
#     JIMENG_AK / JIMENG_SK 调文生图接口；
#   · __004__langgraph_more_nodes/nodes/retrieval_nodes/retrieval_entity_recall_node.py —— 检索节点
#     读取各类配置。
#   · 此外 TCM_METADATA（图谱 schema 描述）、ENTITY_INDEX_PATH / ENTITY_ID2TEXT_PATH
#     （向量召回）、history_num（对话记忆轮次）还会被图谱构建脚本与 LangGraph
#     问答节点间接使用。
#
# 一句话总结依赖关系：
#   本文件 = 下游模块的"唯一取配置入口"。下游模块【永远不要】自己读 .env 或
#   自己拼路径，全部通过 Config 实例的属性获取。这样密钥只在【一个地方】出现，
#   改配置也只需要改一个文件，这就是"集中配置管理"。
#
#
# ────────────────────────────────────────────────────────────────────────────
# 四、用到的设计模式 / 编程思想
# ────────────────────────────────────────────────────────────────────────────
# 1. 【集中配置管理】(Centralized Configuration) / 【单一配置入口】(Single Point
#    of Entry)：所有外部依赖的敏感信息与资源路径只在本文件读取一次，其他模块
#    通过 Config().属性名 获取，避免密钥散落各处，也方便统一维护。
# 2. 【单例使用风格】(Singleton-style Usage)：虽然代码允许 new 出多个 Config
#    实例，但所有实例读到的都是同一份 .env + 同一批本地文件，因此全项目共享
#    同一份配置"事实"，不会出现"两个模块配置不一致"的 bug。
# 3. 【多项目兼容 + 三级兜底】(Fallback Chain / Graceful Degradation)：图谱
#    元数据优先读法律项目（legal_metadata.json），读不到就退到中药项目
#    （tcm_metadata.json），再读不到就降级为空 JSON "{}"，保证下游 json.loads
#    永远不炸——这是"一套代码服务两个领域"的适配思想。
# 4. 【双保险加载】(Belt-and-Braces Loading)：load_dotenv 调用两次（一次默认
#    规则扫描 + 一次显式路径），解决"启动目录不确定"导致的 .env 加载失败问题。
# 5. 【默认值兜底】(Default Value Fallback)：os.getenv(key, default) 的第二个
#    参数提供默认值（如超时时间默认 15 秒），环境变量缺失时程序仍能按默认值运行。
# 6. 【自测入口模式】(Self-test Entry)：if __name__ == "__main__" 保证直接运行
#    本文件时可做本地验证，而被 import 时不产生副作用。
#
#
# ═══════════════════════════════════════════════════════════════════════════
# 【Step 0】导入工具 —— 本文件需要用到三个"帮手"
# ═══════════════════════════════════════════════════════════════════════════

# 【变量定义】import os
# 【功能】导入 Python 标准库 os（Operating System，操作系统接口），它提供与系统
#         交互的函数；本文件主要用到 os.getenv（读环境变量）和 os.path.exists
#         （判断文件是否存在）。
# 【参数】无 —— import 语句不接收"调用参数"，它只是把 os 模块绑定到当前命名空间。
# 【返回值】无 —— import 的"结果"是当前文件里多了一个可直接使用的名字 os。
# 【逻辑】① Python 解释器在 sys.path（模块搜索路径列表）中找到 os 模块；
#         ② 执行该模块的顶层代码；
#         ③ 把模块对象绑定到名字 os 上。从此以后，本文件里写 os.getenv 就等价于
#            "调用操作系统环境变量读取函数"。
import os  # 行尾：这一行执行完后，os.getenv("MODEL_API_KEY")、os.path.exists(...) 等调用才可用；如果删掉这一行，下面所有 os.xxx 都会报 NameError（名字未定义）

# 【变量定义】from dotenv import load_dotenv
# 【功能】从第三方包 python-dotenv 导入 load_dotenv 函数。它的作用是把 .env 文件
#         里的"键=值"逐行解析出来，写入 os.environ（系统环境变量字典）。
# 【参数】无 —— 这里只是"导入"；真正调用 load_dotenv() 发生在下方 Step 1 / Step 2。
# 【返回值】无 —— 导入完成后，本文件里可以直接调用 load_dotenv(...) 这个名字。
# 【逻辑】① 找到名为 dotenv 的第三方包；② 从包内取出 load_dotenv 函数；
#         ③ 绑定到当前文件。注意：dotenv 不是标准库，如果 pip 没安装
#            python-dotenv，这一行会直接报 ModuleNotFoundError，程序启动即失败。
from dotenv import load_dotenv  # 行尾：这是"拿工具"的动作；真正"读 .env 文件"的动作在下方 load_dotenv() 调用处（Step 1 和 Step 2）

# 【变量定义】from common.path_utils import get_file_path
# 【功能】导入本项目自己写的路径工具函数 get_file_path(relative_path)。它负责把
#         "工程内的相对路径"（如 ".env"）拼接成"绝对路径"（如
#         E:\...\AI_legal_assistant\.env），与当前启动目录无关。
# 【参数】无 —— 导入阶段不传参；真正传参发生在 Step 2 的
#         load_dotenv(get_file_path(".env"))，以及 __init__ 里拼接元数据/索引路径时。
# 【返回值】无 —— 导入后，本文件可直接调用 get_file_path(...)。
# 【逻辑】common 是一个"包"（包 = 带 __init__.py 的目录），path_utils 是包内的
#         模块，get_file_path 是该模块里的函数。导入路径用"点"（.）分隔，意思是
#         "从 common 包里取出 path_utils 模块，再取出 get_file_path 函数"。
from common.path_utils import get_file_path, root_dir  # 行尾：get_file_path 内部用 os.path.join(root_dir, relative_path) 拼接路径；它拼出来的绝对路径字符串会被 load_dotenv() 和 open() 直接使用；root_dir 用于直接拼接 FAISS 索引路径等场景


# ═══════════════════════════════════════════════════════════════════════════
# 【Step 1】第一次加载 .env —— 按"默认规则"扫描
# ═══════════════════════════════════════════════════════════════════════════
# 【函数调用】load_dotenv()
# 【功能】扫描并加载 .env 文件里的键值对到系统环境变量 os.environ。
# 【参数】无 —— 使用默认行为：从【当前工作目录】开始，逐级向上查找 .env 文件，
#         找到就加载；默认 override=False（不覆盖已有环境变量）。
# 【返回值】bool —— 找到并成功加载返回 True，没找到返回 False（本文件不关心返回值）。
# 【逻辑】① python-dotenv 内部用 find_dotenv() 从当前目录向上搜索 .env；
#         ② 找到后逐行解析 "KEY=VALUE" 格式；
#         ③ 写入 os.environ。之所以保留这次调用，是为了照顾"程序从任意目录启动"
#            的场景：只要启动目录（或其父目录）下有 .env，就能先读到一部分配置。
load_dotenv()  # 行尾：第一次加载，按默认规则从当前工作目录向上找 .env；边界情况：如果 .env 在工程根目录而程序却在别的目录启动，这次可能找不到，所以还要有 Step 2 兜底

# ═══════════════════════════════════════════════════════════════════════════
# 【Step 2】第二次加载 .env —— 显式定位工程根目录下的 .env（双保险）
# ═══════════════════════════════════════════════════════════════════════════
# 【函数调用】load_dotenv(get_file_path(".env"))
# 【功能】显式指定 .env 的完整路径后再加载一次，确保无论启动目录在哪，工程根目录
#         下的 .env 都一定能被读到。
# 【参数】dotenv_path（位置参数）：.env 文件的绝对路径，由内层函数调用
#         get_file_path(".env") 计算得出（字符串，如 E:\...\AI_legal_assistant\.env）。
# 【返回值】bool —— 加载成功返回 True（本文件不关心返回值）。
# 【逻辑】① 先执行内层 get_file_path(".env")：它只负责"找路标"，返回一个绝对路径
#            字符串，本身不去读文件内容；
#         ② 把该字符串作为参数传给外层 load_dotenv，后者直接打开这个指定文件解析；
#         ③ 因为默认 override=False，第二次加载【不会覆盖】第一次已读到的变量，
#            只会补齐缺失项——这正是"双保险"的精髓：互相补漏，互不覆盖。
load_dotenv(get_file_path(".env"))  # 行尾：调用顺序是"先内后外"——get_file_path 先执行完，把结果字符串传给 load_dotenv；再强调一遍：get_file_path 只是"查地图"，load_dotenv 才是"搬东西"（真正把配置加载进系统）
# 补充说明：get_file_path(".env") 只是一个"找路标"的动作，它只返回一个字符串
# （比如 /project/.env），它本身不会去读取文件内容。
# load_dotenv() 才是真正把文件里的配置加载到系统中的动作。
# 这两个动作一个是"查地图"，一个是"搬东西"，分工完全不同。


# ═══════════════════════════════════════════════════════════════════════════
# 【Step 3】定义 Config 类 —— 所有配置的"总容器"
# ═══════════════════════════════════════════════════════════════════════════

# 【类定义】class Config:
# 【功能】定义一个"配置类"，它像一个带标签的抽屉柜：每个抽屉（实例属性）存一项
#         配置，其他模块实例化后通过 实例.属性名 取用。
# 【参数】无 —— 类定义本身不接收参数；实例化时写 Config() 也不需要任何参数。
# 【返回值】无 —— 类本身是一个"模板"；真正产出的是实例化后的 Config 对象。
# 【逻辑】① Python 读到 class 关键字后创建类对象并绑定名字 Config；
#         ② 类内部的 __init__ 是"构造方法"，在 Config() 实例化时自动执行；
#         ③ 因为本项目里配置来源只有 .env + 本地文件（不随实例变化），所以
#            new 出多少个实例读到的值都一样，全项目共享同一份配置。
class Config:
    """
    【功能】
        全项目唯一的"配置总容器"：在实例化时一次性把 .env 环境变量和本地资源路径
        全部读入内存，保存为实例属性。其他模块 import 本类后，通过
        Config().属性名 即可取到任意配置，无需重复读取文件。
    【参数】
        无 —— 实例化时 Config() 不需要传任何参数，所有配置来源均为环境变量
        （由 Step 1 / Step 2 的 load_dotenv 加载进 os.environ）与本地文件。
    【返回值】
        无 —— 类是"模板"，本身不返回值；实例化得到的是一个 Config 对象，
        该对象身上挂着 MODEL_API_KEY、MODEL_BASE_URL、MODEL_NAME、NEO4J_URI、
        NEO4J_USER、NEO4J_PASSWORD、JIMENG_AK、JIMENG_SK、TCM_METADATA、
        EMBEDDING_MODEL_PATH、ENTITY_INDEX_PATH、ENTITY_ID2TEXT_PATH、history_num、
        BEIDA_FABAO_TOKEN、BEIDA_FABAO_BASE_URL、BEIDA_FABAO_TIMEOUT、
        QICHACHA_AUTHORIZATION、QICHACHA_APP_KEY、QICHACHA_SECRET_KEY、
        QICHACHA_BASE_URL、QICHACHA_TIMEOUT 等约 21 个属性。
    【逻辑】
        Step A：定义类名 Config，创建类对象；
        Step B：类体内定义构造方法 __init__，实例化时自动逐项读取配置；
        Step C：类体末尾没有其他方法，所有取值逻辑都在 __init__ 中完成。
    """
    # 类体说明：这个类负责存放本项目相关的所有配置信息，负责对具体配置文件的读取、
    # 存放变量名；后期其他模块只需要调用 Config() 拿到实例，然后 .属性名 即可取到
    # 具体取值。因为所有配置都集中在这一个类里，所以无论创建多少个不同的对象，
    # 只需要调用这一个类，然后 .属性 就能取到对应配置——这就是"单一配置入口"。

    # ═══════════════════════════════════════════════════════════════════════
    # 【Step 4】构造方法 __init__ —— 实例化时自动执行，逐项读取所有配置
    # ═══════════════════════════════════════════════════════════════════════

    # 【方法定义】def __init__(self):
    # 【功能】初始化配置对象：在 Config() 被调用时，把所有外部配置一次性读入内存，
    #         存成实例属性，后续业务模块无需重复读取。
    # 【参数】self：Python 自动传入的"实例自己"的引用，用于往实例上挂属性
    #         （self.xxx = ... 表示"给这个实例装一个名为 xxx 的属性"）。
    #         除 self 外不接受任何参数，所有配置来源均为环境变量与本地文件。
    # 【返回值】无 —— 构造方法不允许 return 值；它通过 self.属性 = 值 的方式把
    #         配置"写"到实例上。
    # 【逻辑】
    #   Step A：读大模型相关（MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME）；
    #   Step B：读 Neo4j 相关（NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD）；
    #   Step C：读极梦文生图（JIMENG_AK / JIMENG_SK）；
    #   Step D：读图谱元数据（三级兜底：legal → tcm → "{}"）；
    #   Step E：读 Embedding 模型路径（EMBEDDING_MODEL_PATH）；
    #   Step F：读向量索引路径（ENTITY_INDEX_PATH / ENTITY_ID2TEXT_PATH）；
    #   Step G：写死对话记忆轮次 history_num = 5；
    #   Step H：读北大法宝 MCP 三件套（TOKEN / BASE_URL / TIMEOUT）；
    #   Step I：读企查查五件套（AUTHORIZATION / APP_KEY / SECRET_KEY /
    #           BASE_URL / TIMEOUT）。
    def __init__(self):
        """
        【功能】
            初始化配置对象，集中读取并保存项目所有运行期需要的配置项。
            在实例化 Config 时一次性把所有外部配置读入内存，后续业务模块只需持有
            一个 Config 实例（或直接 Config().属性名 临时取用）即可访问全部配置，
            避免重复读取 .env 和本地文件。
        【参数】
            无（self 之外不接受参数，所有配置来源均为环境变量与本地文件）。
        【返回值】
            无返回值；构造完成后实例自身拥有 MODEL_API_KEY、MODEL_BASE_URL、
            MODEL_NAME、NEO4J_URI、NEO4J_USER、NEO4J_PASSWORD、JIMENG_AK、
            JIMENG_SK、TCM_METADATA、EMBEDDING_MODEL_PATH、ENTITY_INDEX_PATH、
            ENTITY_ID2TEXT_PATH、history_num、BEIDA_FABAO_TOKEN、
            BEIDA_FABAO_BASE_URL、BEIDA_FABAO_TIMEOUT、QICHACHA_AUTHORIZATION、
            QICHACHA_APP_KEY、QICHACHA_SECRET_KEY、QICHACHA_BASE_URL、
            QICHACHA_TIMEOUT 等属性。
        【逻辑】
            按功能分组逐项读取，顺序如下：
            1. 大模型：os.getenv("MODEL_API_KEY" / "MODEL_BASE_URL" / "MODEL_NAME")；
            2. Neo4j：os.getenv("NEO4J_URI" / "NEO4J_USER" / "NEO4J_PASSWORD")；
            3. 极梦：os.getenv("JIMENG_AK" / "JIMENG_SK")；
            4. 图谱元数据：get_file_path 拼路径 → os.path.exists 判断存在性 →
               三级兜底（法律 → 中药 → 空 JSON）；
            5. Embedding 路径：os.getenv("EMBEDDING_MODEL_PATH")；
            6. 索引路径：get_file_path 拼接 FAISS 索引与 id2text 映射；
            7. 记忆轮次：直接写死 history_num = 5；
            8. 北大法宝：os.getenv + 默认值（超时默认 "15" 再 int 转换）；
            9. 企查查：os.getenv + 默认值（Base URL / 超时 10 秒）。

        可迁移性说明：
            该类只依赖标准库 os、第三方 python-dotenv 和项目内 path_utils，
            不耦合任何业务逻辑，可直接复制到其他需要 .env + 工程相对路径管理的
            Python 项目中；若新增配置项，只需在 __init__ 中追加一行 os.getenv
            或 get_file_path 即可，向后兼容性良好。
        """

        # ═══════════════════════════════════════════════════════════════════
        # 【A. 大模型相关】—— 供 common/llm.py 创建 ChatOpenAI 客户端使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.MODEL_API_KEY
        # 【功能】保存大模型服务商（如 DeepSeek / 通义千问 / 智谱）的 API Key，
        #         用于调用大模型接口时的身份鉴权（证明"我是付费用户"）。
        # 【参数】取值来源：环境变量 MODEL_API_KEY，在 .env 文件里配置，
        #         例如 MODEL_API_KEY=sk-xxxxxxxx。
        # 【返回值】属性值类型：字符串（str）；若未配置则为 None（Python 的空值）。
        # 【逻辑】① os.getenv("MODEL_API_KEY") 按名字查系统环境变量字典 os.environ；
        #         ② 找到就返回对应字符串，找不到返回 None；
        #         ③ 把结果赋给 self.MODEL_API_KEY。
        self.MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # 行尾：getenv 找到就把值读进变量，找不到返回 None，后续 ChatOpenAI 鉴权会失败——所以 .env 里这一项必须配置；下游 common/llm.py 通过 Config().MODEL_API_KEY 取用

        # 【属性定义】self.MODEL_BASE_URL
        # 【功能】保存大模型服务的 API 基础地址，决定请求打到哪个推理服务商。
        # 【参数】取值来源：环境变量 MODEL_BASE_URL，例如
        #         MODEL_BASE_URL=https://api.deepseek.com/v1。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】同 MODEL_API_KEY：os.getenv 按名查找 → 赋值给 self.MODEL_BASE_URL。
        self.MODEL_BASE_URL = os.getenv("MODEL_BASE_URL")  # 行尾：base_url（基础地址）是"域名 + 版本路径"，请求会在它后面拼上 /chat/completions 之类的具体接口；配错地址会连不上服务

        # 【属性定义】self.MODEL_NAME
        # 【功能】保存具体要调用的模型名（如 deepseek-chat），后续传给
        #         ChatOpenAI(model=...) 决定用哪个模型回答。
        # 【参数】取值来源：环境变量 MODEL_NAME。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("MODEL_NAME") 按名查找 → 赋值给 self.MODEL_NAME。
        self.MODEL_NAME = os.getenv("MODEL_NAME")  # 行尾：模型名必须和服务商实际支持的模型名完全一致（大小写、连字符都要对），否则 API 会报 model not found 错误

        # 【属性定义】self.MODEL_TIMEOUT
        # 【功能】LLM 单次请求超时(秒)。重负载任务(如案例全文抽取, 长输入+长 JSON
        #         输出)生成耗时远超闲聊, 300s 在大案例上会 "Request timed out"。
        # 【取值来源】环境变量 MODEL_TIMEOUT, 未配置默认 600(兼顾长任务与故障暴露速度)。
        self.MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "600"))

        # ═══════════════════════════════════════════════════════════════════
        # 【B. Neo4j 图数据库相关】—— 供 common/neo4j_client.py 连接图数据库使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.NEO4J_URI
        # 【功能】保存 Neo4j 图数据库的连接地址（URI），用于建立数据库连接。
        # 【参数】取值来源：环境变量 NEO4J_URI，形如 bolt://localhost:7687
        #         （本地）或 neo4j+s://xxx.databases.neo4j.io（云服务）。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("NEO4J_URI") 按名查找 → 赋值给 self.NEO4J_URI。
        self.NEO4J_URI = os.getenv("NEO4J_URI")  # 行尾：URI（统一资源标识符）里的协议前缀很重要——bolt:// 是本地直连，neo4j+s:// 是云数据库加密连接；写错协议连不上

        # 【属性定义】self.NEO4J_USER
        # 【功能】保存 Neo4j 登录用户名（默认常为 neo4j）。
        # 【参数】取值来源：环境变量 NEO4J_USER。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("NEO4J_USER") 按名查找 → 赋值给 self.NEO4J_USER。
        self.NEO4J_USER = os.getenv("NEO4J_USER")  # 行尾：用户名与密码、URI 三者会一起传给 GraphDatabase.driver(uri, auth=(user, password)) 完成鉴权连接

        # 【属性定义】self.NEO4J_PASSWORD
        # 【功能】保存 Neo4j 登录密码，与 URI、USER 一起完成数据库鉴权。
        # 【参数】取值来源：环境变量 NEO4J_PASSWORD。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("NEO4J_PASSWORD") 按名查找 → 赋值给 self.NEO4J_PASSWORD。
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")  # 行尾：密码是敏感信息，绝不允许硬编码进代码；如果 Neo4j 改过密码，只需改 .env 一处，全项目生效

        # ═══════════════════════════════════════════════════════════════════
        # 【C. 极梦（即梦）文生图相关】—— 供 image_generate_node.py 调图像生成使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.JIMENG_AK
        # 【功能】保存火山引擎"即梦/极梦"文生图服务的 AccessKey，用于图像生成
        #         接口的请求签名（证明调用者身份）。
        # 【参数】取值来源：环境变量 JIMENG_AK。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("JIMENG_AK") 按名查找 → 赋值给 self.JIMENG_AK。
        self.JIMENG_AK = os.getenv("JIMENG_AK")  # 行尾：AK（AccessKey，访问密钥）是公开标识，相当于"用户名"；它通常要和 SK 配对使用，单独一个无法完成鉴权

        # 【属性定义】self.JIMENG_SK
        # 【功能】保存对应的 SecretKey，与 AK 配对用于 HMAC 签名鉴权（证明
        #         "用户名 + 密码"都正确）。
        # 【参数】取值来源：环境变量 JIMENG_SK。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("JIMENG_SK") 按名查找 → 赋值给 self.JIMENG_SK。
        self.JIMENG_SK = os.getenv("JIMENG_SK")  # 行尾：SK（SecretKey，私密密钥）必须严格保密，绝不能提交到 Git 仓库；泄露等同账号被盗

        # ═══════════════════════════════════════════════════════════════════
        # 【D. 图谱模式层元数据】—— 供 Cypher 生成时作为 schema（模式）参考
        # 【设计思想】三级兜底：优先法律项目，兼容中药项目，缺失降级为空 JSON
        # ═══════════════════════════════════════════════════════════════════

        # 【变量定义】legal_meta_path（注意：这是 __init__ 里的"局部变量"，
        # 不是 self.xxx 实例属性，只在这一层代码里使用）
        # 【功能】保存法律项目图谱元数据 JSON 文件的绝对路径（首选方案）。
        # 【参数】取值来源：get_file_path("__003__create_neo4j_database/legal_metadata.json")，
        #         即"工程根目录 / __003__create_neo4j_database / legal_metadata.json"。
        # 【返回值】变量值类型：字符串（str），是一个绝对路径。
        # 【逻辑】get_file_path 内部用 os.path.join(root_dir, relative_path) 把
        #         工程根目录和传入的相对路径拼起来，得到与启动目录无关的绝对路径。
        legal_meta_path = get_file_path("__003__create_neo4j_database/legal_metadata.json")  # 行尾：legal_meta_path 的变量名含义 = legal（法律的）+ meta（元数据）+ path（路径）；这个文件描述图谱有哪些节点标签、关系类型、三元组结构，供后续 Cypher 生成时参考

        # 【变量定义】tcm_meta_path
        # 【功能】保存中药项目图谱元数据 JSON 文件的绝对路径（兼容方案，
        #         让同一套代码可以服务其他领域项目）。
        # 【参数】取值来源：get_file_path("__003__create_neo4j_database/tcm_metadata.json")。
        # 【返回值】变量值类型：字符串（str），是一个绝对路径。
        # 【逻辑】与 legal_meta_path 完全相同，只是文件名换成 tcm_metadata.json
        #         （tcm = Traditional Chinese Medicine，中医药）。
        tcm_meta_path = get_file_path("__003__create_neo4j_database/tcm_metadata.json")  # 行尾：tcm 是"中医药"的英文缩写；这个变量是"备胎"，只有当法律元数据不存在时才会被用到

        # 【逻辑判断】if os.path.exists(legal_meta_path):
        # 【功能】判断法律项目元数据文件是否真实存在于磁盘上。
        # 【参数】legal_meta_path：上一步拼好的绝对路径字符串。
        # 【返回值】布尔值（True / False）：True = 文件存在，False = 文件不存在。
        # 【逻辑】os.path.exists(路径) 去操作系统查一下这个路径指向的文件是否存在，
        #         返回 True 则进入 if 分支（首选方案），否则跳过看 elif。
        if os.path.exists(legal_meta_path):                # 行尾：先判断"法律元数据在不在"——在的话就用它（第一优先级）；注意 os.path.exists 对"路径是目录"也会返回 True，但这里指向的是明确的 .json 文件

            # 【属性定义】self.TCM_METADATA
            # 【功能】保存图谱元数据的【文本内容】。注意：虽然属性名带 TCM（中药），
            #         但这里存的是法律项目元数据——这是历史命名遗留，读代码时别被
            #         名字误导，它其实"谁的数据都存"。
            # 【参数】取值来源：直接 open() 打开 legal_meta_path 文件并 read()。
            # 【返回值】属性值类型：字符串（str），是 JSON 文件的完整文本。
            # 【逻辑】open(路径, "r", encoding="utf-8") 以只读模式打开文件 →
            #         .read() 把整个文件内容一次性读成字符串 → 赋给属性。
            #         注意存的是字符串而非 dict，后续由调用方按需 json.loads 转换。
            self.TCM_METADATA = open(legal_meta_path, "r", encoding="utf-8").read()  # 行尾：encoding="utf-8" 保证中文不乱码（元数据里全是中文标签名）；open(...).read() 没有用 with 语句关闭文件句柄，属于小瑕疵——CPython 靠引用计数会自动回收，但严谨写法应该是 with open(...) as f: f.read()

        # 【逻辑判断】elif os.path.exists(tcm_meta_path):
        # 【功能】法律元数据不存在时，判断中药元数据是否存在（第二优先级）。
        # 【参数】tcm_meta_path：中药元数据绝对路径。
        # 【返回值】布尔值（True / False）。
        # 【逻辑】只有 if 的条件为 False（法律文件不存在）时才会执行到这里；
        #         若中药文件存在，进入本分支读取中药数据——这就是"兼容模式"。
        elif os.path.exists(tcm_meta_path):                # 行尾：elif = "else if"的缩写，表示"否则，再判断另一个条件"；到这里说明 legal 文件不存在，试试 tcm 文件在不在

            # 【属性定义】self.TCM_METADATA（elif 分支赋值）
            # 【功能】保存中药项目图谱元数据的文本内容。
            # 【参数】取值来源：open() 打开 tcm_meta_path 并 read()。
            # 【返回值】属性值类型：字符串（str）。
            # 【逻辑】与 if 分支完全对称：打开 tcm 文件 → 读成字符串 → 赋给同一个
            #         属性名 self.TCM_METADATA。同一个属性名在不同分支被赋不同值，
            #         最终"谁存在就用谁的数据"。
            self.TCM_METADATA = open(tcm_meta_path, "r", encoding="utf-8").read()    # 行尾：注意这里读取的同样是字符串而非 dict，调用方需要 json.loads 后再使用；这样一套代码就能同时服务法律和中药两个领域的项目

        # 【逻辑分支】else:
        # 【功能】法律、中药两个文件都不存在时执行的兜底分支（第三优先级）。
        # 【参数】无。
        # 【返回值】无 —— 分支内直接给属性赋值。
        # 【逻辑】if 和 elif 的条件都不满足（两个文件都不存在）时进入 else，
        #         直接给 self.TCM_METADATA 一个合法的空 JSON 字符串。
        else:
            # 【属性定义】self.TCM_METADATA = "{}"（兜底赋值）
            # 【功能】给元数据一个"空 JSON"默认值，保证程序不中断。
            # 【参数】取值来源：直接写死的字符串常量 "{}"。
            # 【返回值】属性值类型：字符串（str），内容是空 JSON 对象 "{}"。
            # 【逻辑】没有任何判断，直接赋值。字符串 "{}" 可以被 json.loads
            #         成功解析为一个空字典 {}，所以下游代码不会因解析失败而崩溃。
            self.TCM_METADATA = "{}"  # 文件不存在时降级为空JSON  # 行尾：这是"降级保护"——宁可给空数据，也不让程序崩；如果这里给 None，下游 json.loads(None) 会直接抛 TypeError 异常

        # ═══════════════════════════════════════════════════════════════════
        # 【E. Embedding 模型相关】—— 供 common/embedding_model.py 加载向量模型使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.EMBEDDING_MODEL_PATH
        # 【功能】保存本地 Embedding（嵌入/向量化）模型的路径（如 bge-small-zh），
        #         供 SentenceTransformer 加载后把文本转成向量。
        # 【参数】取值来源：环境变量 EMBEDDING_MODEL_PATH。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("EMBEDDING_MODEL_PATH") 按名查找 → 赋值给属性。
        #         路径可以是 HuggingFace 模型名（自动下载）或本地模型目录。
        self.EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH")  # 行尾：embedding（嵌入）模型的作用是把"文字"变成"一串数字（向量）"，让机器能算相似度；路径配错会导致 SentenceTransformer 加载失败

        # ═══════════════════════════════════════════════════════════════════
        # 【F. FAISS 向量索引相关】—— 供实体召回 / RAG 向量检索使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.ENTITY_INDEX_PATH
        # 【功能】保存实体向量 FAISS 索引文件的绝对路径，用于实体召回时的近邻搜索。
        # 【参数】取值来源：get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss.index")。
        # 【返回值】属性值类型：字符串（str），绝对路径。
        # 【逻辑】get_file_path 把工程根目录与相对路径拼接 → 得到索引文件绝对路径
        #         → 赋给属性。FAISS（Facebook AI Similarity Search）是向量检索库，
        #         这个 .index 文件里存的是所有实体的向量，用于"给一个向量，找最像的
        #         实体"。
        self.ENTITY_INDEX_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss.index")  # 行尾：注意文件名里 nero4j 疑似是 neo4j 的拼写笔误——是历史遗留，不要"好心"改文件名，否则和磁盘上实际生成的文件对不上会找不到文件

        # 【属性定义】self.ENTITY_ID2TEXT_PATH
        # 【功能】保存实体 ID 到文本的映射文件（pickle 格式）路径，配合 FAISS 索引
        #         把召回的 id 还原成可读的实体名。
        # 【参数】取值来源：get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss_id2text.pkl")。
        # 【返回值】属性值类型：字符串（str），绝对路径。
        # 【逻辑】get_file_path 拼接 → 赋给属性。原理：FAISS 检索返回的是"编号"
        #         （id），而人类要看的是"文字"，所以需要这个 .pkl 映射文件做
        #         id → 文本 的翻译；id2text = id to text（从 id 到文本）。
        self.ENTITY_ID2TEXT_PATH = get_file_path("__003__create_neo4j_database/nero4j_embedding_faiss_id2text.pkl")  # 行尾：pkl 是 Python pickle（序列化）文件格式，需要用 pickle.load 读取成字典 {id: 文本}；这个文件和上面的 .index 文件必须配套生成，否则 id 对不上

        # ═══════════════════════════════════════════════════════════════════
        # 【F2. 领域知识库 FAISS 索引】—— 各领域独立索引
        # ═══════════════════════════════════════════════════════════════════
        # 注意：本处是全局配置的只读副本，实际被 FAISS 召回链路读取的「唯一真相」是
        #   retrieval_entity_recall_node._SOURCE_INDEX_MAP，
        #   两边必须 100% 对齐（键名、index 文件名、id2text 文件名）。
        #   本 FAISS_INDEX_PATHS 用于 006_streamlit 展示 / __001__clawler 生成前校验等外围场景，
        #   若改知识源文件，优先改 entity_recall_node._SOURCE_INDEX_MAP，再同步此处。
        # 所有源 key 语义平等，不携带 priority / authority 分层（融合层有独立 authority_weights）。
        _KB_INDEX_DIR = os.path.join(root_dir, "data", "knowledge_base", "index")
        self.FAISS_INDEX_PATHS = {
            "laws": {
                "index": os.path.join(_KB_INDEX_DIR, "laws_faiss.index"),
                "id2text": os.path.join(_KB_INDEX_DIR, "laws_id2text.pkl"),
            },
            "regulations": {
                "index": os.path.join(_KB_INDEX_DIR, "regulations_faiss.index"),
                "id2text": os.path.join(_KB_INDEX_DIR, "regulations_id2text.pkl"),
            },
            "cases": {
                "index": os.path.join(_KB_INDEX_DIR, "cases_faiss.index"),
                "id2text": os.path.join(_KB_INDEX_DIR, "cases_id2text.pkl"),
            },
            "industry_sources": {
                # 注意: 键名必须是 "industry_sources"（和实际文件 industry_sources_faiss.index 前缀一致），
                # 历史上的简写 "industry" 已废弃（因为 KNOWN_DOMAIN_SOURCES / FAISS 映射键统一用全称）。
                "index": os.path.join(_KB_INDEX_DIR, "industry_sources_faiss.index"),
                "id2text": os.path.join(_KB_INDEX_DIR, "industry_sources_id2text.pkl"),
            },
            "interpretations": {
                "index": os.path.join(_KB_INDEX_DIR, "interpretations_faiss.index"),
                "id2text": os.path.join(_KB_INDEX_DIR, "interpretations_id2text.pkl"),
            },
        }
        # 任务 → 挂载矩阵 (V4 用户定稿方案 · 与 retrieval_intent_decompose_node.TASK_SOURCE_DEFAULTS 完全对齐)
        # 说明：本 DEFAULT_TASK_MOUNTS 是外围展示/前端任务卡片用的「镜像配置」，
        #       LangGraph 实际运行时只读 intent_decompose_node 里那份 TASK_SOURCE_DEFAULTS；
        #       两者修改时必须同步。字段仅保留 domain，graph/api 两类不存在于新挂载层 (V4 起删除)：
        #         - graph 三通道是每个 domain 源内部默认包含的能力，不单独挂；
        #         - api(beida_fabao) 只在 quality_gate 3 次重试失败后置 fabao_retry_eligible=True 生效，
        #           与任务类型/关键词完全解耦。
        self.DEFAULT_TASK_MOUNTS = {
            "contract_review":    {"domain": ["laws", "regulations", "interpretations", "cases"]},  # 4 源, industry 关键词触发追加
            "compliance_review":  {"domain": ["laws", "regulations", "interpretations", "cases"]},  # 4 源
            "legal_research":     {"domain": ["laws", "regulations", "interpretations"]},            # 3 源(纯法规, 不含 cases/industry)
            "case_search":        {"domain": ["cases"]},                                             # 1 源, skip_fusion 透传
            "legal_qa":           {"domain": ["laws", "regulations", "interpretations", "cases"]},  # 4 源
            "legal_document_gen": {"domain": ["laws", "regulations", "interpretations"]},            # 3 源 (文书法条检索, cases 独立子图)
        }

        # ═══════════════════════════════════════════════════════════════════
        # 【G. 对话记忆轮次】—— 供多轮对话的记忆管理使用
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.history_num
        # 【功能】保存多轮对话保留的历史轮数上限。
        # 【参数】取值来源：直接写死的常量 5（不是从环境变量读取的）。
        # 【返回值】属性值类型：整数（int），固定为 5。
        # 【逻辑】无计算逻辑，直接赋值。它的作用是控制送入大模型的 Context（上下文）
        #         长度：只保留最近 5 轮对话，超过的旧消息会被截断，防止超出模型
        #         token（令牌）上限导致请求失败。
        self.history_num = 5                                # 行尾：history_num = history（历史）+ num（数量），即"保留几轮历史"；写死 5 是一个经验值——太小丢失上下文，太大会撑爆 token 限制，想改可以直接改这个数字

        # ═══════════════════════════════════════════════════════════════════
        # 【H. 北大法宝 MCP 相关】—— 付费外挂法规数据源
        # 【背景】V5 最终定稿: 只有 quality_gate_retry_node 达到 MAX_QUALITY_RETRIES 仍低于阈值
        #       时（即免费链路的「三通道召回 + 融合/单源排序 + 关键词扩展 + 源切换 + fallback」
        #       连续 3 次重试全用尽仍不达标），由它置 fabao_retry_eligible=True，
        #       beida_fabao_gate_node 仅对该标记 True 生效时才 interrupt 询问用户，
        #       用户确认后才真正走到 mcp_beidafabao.search_all() 这里。
        #       不再按质量分阈值 < X 或关键词触发。
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.BEIDA_FABAO_TOKEN
        # 【功能】保存北大法宝 MCP 的 Bearer Token（鉴权令牌），请求时放在
        #         Authorization 请求头里，例如 "Bearer e260a91c-9a46-3bf9-b417-76d2d2b9e9d0"。
        # 【参数】取值来源：环境变量 BEIDA_FABAO_TOKEN。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv("BEIDA_FABAO_TOKEN") 按名查找 → 赋值给属性。
        self.BEIDA_FABAO_TOKEN = os.getenv("BEIDA_FABAO_TOKEN")  # 行尾：BEIDA_FABAO = 北大法宝（付费法律数据服务商）；Token 是付费凭证，通常带 "Bearer " 前缀，供 common/mcp_beidafabao.py 使用

        # 【属性定义】self.BEIDA_FABAO_BASE_URL
        # 【功能】保存北大法宝 MCP API 的基础地址（默认 https://mcp.beidafabao.com/api/v1）。
        # 【参数】取值来源：os.getenv("BEIDA_FABAO_BASE_URL", "https://mcp.beidafabao.com/api/v1")
        #         ——第二个参数是【默认值】：环境变量没配时就用默认地址。
        # 【返回值】属性值类型：字符串（str），必然有值（要么环境变量，要么默认值）。
        # 【逻辑】os.getenv 先查环境变量 → 查到用查到的值，查不到用默认值 → 赋给属性。
        self.BEIDA_FABAO_BASE_URL = os.getenv("BEIDA_FABAO_BASE_URL", "https://mcp.beidafabao.com/api/v1")  # 行尾：这里是 os.getenv 的"默认值兜底"用法（设计模式 5）——即使 .env 里漏配，程序也能按官方默认地址运行

        # 【属性定义】self.BEIDA_FABAO_TIMEOUT
        # 【功能】保存北大法宝 API 请求的超时秒数（默认 15 秒）。
        # 【参数】取值来源：os.getenv("BEIDA_FABAO_TIMEOUT", "15") 读出来是字符串
        #         "15"，再用 int() 转成整数 15。
        # 【返回值】属性值类型：整数（int），默认 15。
        # 【逻辑】① os.getenv 取出字符串（或默认 "15"）；② int() 把字符串转成整数；
        #         ③ 赋给属性。超时（timeout）的意思是：请求超过 N 秒没响应就放弃，
        #         防止外部服务卡死拖垮整个程序。
        self.BEIDA_FABAO_TIMEOUT = int(os.getenv("BEIDA_FABAO_TIMEOUT", "15"))  # 行尾：int() 是字符串转整数的强制转换；【边角情况】如果 .env 里把这一项配成 "abc" 这种非数字，int() 会抛 ValueError，程序在实例化时就崩溃——所以配置值必须保证是数字

        # ═══════════════════════════════════════════════════════════════════
        # 【I. 企查查 API 相关】—— 相对方资信查询
        # 【背景】优先使用 MCP Bearer Token 模式（Authorization 请求头）；
        #        若 QICHACHA_AUTHORIZATION 为空，则降级为旧版开放平台
        #        AppKey + SecretKey（MD5 签名）模式
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.QICHACHA_AUTHORIZATION
        # 【功能】保存企查查 MCP 的完整鉴权串（如 "Bearer xxxxx"），优先模式。
        # 【参数】取值来源：环境变量 QICHACHA_AUTHORIZATION。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv 按名查找 → 赋给属性。若为空（None 或空串），下游
        #         qichacha_client.py 会降级走 AppKey 兼容模式。
        self.QICHACHA_AUTHORIZATION = os.getenv("QICHACHA_AUTHORIZATION")  # 行尾：QICHACHA = 企查查（企业信息查询服务商）；AUTHORIZATION（授权）串通常带 "Bearer " 前缀，直接作为请求头 Authorization 的值使用

        # 【属性定义】self.QICHACHA_APP_KEY
        # 【功能】保存企查查开放平台的 AppKey（兼容模式），未配置时资信节点会降级
        #         为模拟数据。
        # 【参数】取值来源：环境变量 QICHACHA_APP_KEY。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv 按名查找 → 赋给属性。AppKey 是开放平台模式的"账号标识"，
        #         需要和 SecretKey 配合签名。
        self.QICHACHA_APP_KEY = os.getenv("QICHACHA_APP_KEY")    # 行尾：AppKey（应用密钥）属于"旧版开放平台"接入方式；如果这一项和 SECRET_KEY 都没配，企查查查询会降级成模拟数据，不会报错中断

        # 【属性定义】self.QICHACHA_SECRET_KEY
        # 【功能】保存企查查开放平台的 SecretKey（签名用，兼容模式）。
        # 【参数】取值来源：环境变量 QICHACHA_SECRET_KEY。
        # 【返回值】属性值类型：字符串（str）；未配置则为 None。
        # 【逻辑】os.getenv 按名查找 → 赋给属性。签名（Signature）机制：
        #         用 SecretKey 对请求参数做 MD5 哈希，服务端验签确认请求真实。
        self.QICHACHA_SECRET_KEY = os.getenv("QICHACHA_SECRET_KEY")  # 行尾：SecretKey 是签名用的私密密钥，和 AppKey 配对；注意旧版签名模式走的是 https://api.qcc.com/api 这类开放平台地址

        # 【属性定义】self.QICHACHA_BASE_URL
        # 【功能】保存企查查 MCP 的默认 Base URL（默认 https://agent.qcc.com/mcp）。
        # 【参数】取值来源：os.getenv("QICHACHA_BASE_URL", "https://agent.qcc.com/mcp")，
        #         带默认值兜底。
        # 【返回值】属性值类型：字符串（str），必然有值。
        # 【逻辑】os.getenv 先查环境变量 → 查不到用默认值 → 赋给属性。
        self.QICHACHA_BASE_URL = os.getenv("QICHACHA_BASE_URL", "https://agent.qcc.com/mcp")  # 行尾：注释里写明了兼容模式默认 https://api.qcc.com/api——两套模式对应两个不同的服务地址，下游会按模式选地址

        # 【属性定义】self.QICHACHA_TIMEOUT
        # 【功能】保存企查查 API 单次请求的超时秒数（默认 10 秒）。
        # 【参数】取值来源：os.getenv("QICHACHA_TIMEOUT", "10") 读字符串，int() 转整数。
        # 【返回值】属性值类型：整数（int），默认 10。
        # 【逻辑】① os.getenv 取出字符串（或默认 "10"）；② int() 转整数；③ 赋给属性。
        #         设计意图：资信查询是"附加信息"，不应阻塞主流程过久，所以超时设得
        #         比较短（10 秒），查不到就跳过。
        self.QICHACHA_TIMEOUT = int(os.getenv("QICHACHA_TIMEOUT", "10"))  # 行尾：与 BEIDA_FABAO_TIMEOUT 同理，这里也是"字符串 → int"转换 + 默认值兜底；如果环境变量配了非数字值同样会抛 ValueError，配置时务必写数字

        # ═══════════════════════════════════════════════════════════════════
        # 【J. MinerU 文档多模态解析 (magic-pdf)】—— 用户上传 PDF/DOCX 合同解析
        # 【背景】首次解析时会下载布局/OCR/公式模型; AK/SK 未配置时自动降级纯文本
        # ═══════════════════════════════════════════════════════════════════

        # 【属性定义】self.MINERU_ACCESS_KEY
        # 【功能】保存 MinerU (magic-pdf) 火山 TOS 模型仓库 Access Key,
        #         首次解析会用它拉取布局/公式/OCR 预训练权重。
        # 【参数】取值来源: 环境变量 MINERU_ACCESS_KEY 或 MINERU_AK (双别名兼容)。
        # 【返回值】属性值类型: 字符串(str); 未配置则为 None。
        # 【逻辑】优先取长名 MINERU_ACCESS_KEY, 为空再取短名 MINERU_AK。
        self.MINERU_ACCESS_KEY = (
            os.getenv("MINERU_ACCESS_KEY") or os.getenv("MINERU_AK")
        )  # 行尾: MinerU 不同版本使用 MINERU_ACCESS_KEY 或 MINERU_AK 两种命名, 这里双别名兜底；两者都没配时 MinerU 模型下载会失败，但解析链路会退化为纯文本

        # 【属性定义】self.MINERU_SECRET_KEY
        # 【功能】保存 MinerU 对应的 Secret Key, 与 Access Key 配对做 TOS 鉴权。
        # 【参数】取值来源: 环境变量 MINERU_SECRET_KEY 或 MINERU_SK (双别名兼容)。
        # 【返回值】属性值类型: 字符串(str); 未配置则为 None。
        self.MINERU_SECRET_KEY = (
            os.getenv("MINERU_SECRET_KEY") or os.getenv("MINERU_SK")
        )  # 行尾: Secret Key 是敏感信息, 泄露等同 TOS 仓库账号被盗, 绝不能提交到 Git

        # 【属性定义】self.MINERU_AK / self.MINERU_SK
        # 【功能】MINERU_ACCESS_KEY / SECRET_KEY 的简短别名, 方便下游代码书写。
        # 【参数】取值来源: 直接复用上面两个属性 (保持同源, 单一配置事实)。
        self.MINERU_AK = self.MINERU_ACCESS_KEY  # 行尾: 别名直接指向同一个"事实值", 避免再调一次 os.getenv 或产生两套"漂移值"
        self.MINERU_SK = self.MINERU_SECRET_KEY


# ═══════════════════════════════════════════════════════════════════════════
# 【Step 5】自测入口 —— 只有"直接运行本文件"时才执行
# ═══════════════════════════════════════════════════════════════════════════

# 【逻辑判断】if __name__ == "__main__":
# 【功能】判断当前文件是被"直接运行"还是被"import 导入"。
# 【参数】无 —— __name__ 是 Python 自动提供的特殊变量。
# 【返回值】布尔值（True / False）。
# 【逻辑】① Python 执行文件时自动设置 __name__ 变量；
#         ② 直接运行本文件时 __name__ 的值是字符串 "__main__"；
#         ③ 被其他模块 import 时 __name__ 的值是模块名 "common.config"；
#         ④ 所以这个判断只在"直接运行"时为 True，进入分支做自测；
#            被 import 时整个分支跳过，不产生任何副作用。
if __name__ == "__main__":                    # 行尾：这是 Python 的"标准入口写法"，几乎所有可执行脚本都会用它；作用就是区分"我是主角（直接运行）"还是"我是配角（被导入）"

    # 【变量定义】conf（局部变量）
    # 【功能】在直接运行本文件时实例化一个 Config 对象，用于本地自测。
    # 【参数】无 —— Config() 不需要任何参数。
    # 【返回值】变量值类型：Config 实例对象，身上挂满所有配置属性。
    # 【逻辑】调用 Config() 会触发 __init__，把 .env 和本地文件全部读一遍，
    #         生成一个完整的配置实例。如果这里能顺利执行完不报错，说明
    #         配置文件基本没问题（比如 .env 找到了、文件路径都对了）。
    conf = Config()                           # 行尾：仅在直接运行本文件时实例化 Config，用于本地自测；被 import 时不会执行，避免每次导入都重复构造对象浪费资源
    # print(conf.TCM_METADATA)                # 调试用：打印图谱元数据字符串以人工核对内容（已注释，默认不输出）。想验证元数据读取是否正确时，把这一行开头的 # 去掉再运行 python common/config.py 即可看到输出


# ═══════════════════════════════════════════════════════════════════════════
# 📌 使用总结（给调用方的速查手册）
# ═══════════════════════════════════════════════════════════════════════════
# 在任意业务模块里：
#   from common.config import Config        # 导入配置类
#   conf = Config()                          # 实例化（可只做一次，之后复用）
#   conf.MODEL_API_KEY                       # 大模型密钥
#   conf.NEO4J_URI / conf.NEO4J_USER / conf.NEO4J_PASSWORD   # 图数据库连接
#   conf.TCM_METADATA                        # 图谱元数据文本（需自行 json.loads）
#   conf.ENTITY_INDEX_PATH / conf.ENTITY_ID2TEXT_PATH        # 向量召回资源
#   conf.history_num                         # 记忆轮次
#   conf.BEIDA_FABAO_* / conf.QICHACHA_*     # 付费数据源 / 资信查询
# 新增配置项时：在 .env 里加一行，再在 __init__ 里加一行 os.getenv 即可。
