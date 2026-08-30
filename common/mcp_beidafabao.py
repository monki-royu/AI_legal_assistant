# -*- coding: utf-8 -*-
# 📜 ============================================================
# 文件名称: common/mcp_beidafabao.py
# 文件作用: 北大法宝 MCP 付费外挂客户端 —— 仅作为「质量门 3 次重试失败后」的付费补充检索层
# ============================================================
#
# 【这个文件是干什么的？】
#   本文件只做一件事：封装北大法宝 MCP 付费 HTTP 接口的调用。
#   它是「纯客户端工具层」，**不自己决定什么时候调用**；
#   唯一的调用入口是 nodes/retrieval_nodes/beida_fabao_gate_node.py，
#   触发前置条件在该节点顶部写死（V5 定稿）：
#
#      只有 nodes/retrieval_nodes/quality_gate_retry_node.py 在
#      quality_retry_count >= MAX_QUALITY_RETRIES 且质量分仍低于
#      QUALITY_GATE_THRESHOLD 时，写入 state.fabao_retry_eligible=True，
#      beida_fabao_gate_node 才会 interrupt() 问用户，用户确认后才
#      get_beida_mcp_client().search_all(query, top_k)。
#
#   因此：关键词命中（涉外/银行保险/反垄断）、低分第 1~2 次、前端手动指定
#   api_sources=[...] 都**不会**触发本文件的任何方法。
#
# 【代码逻辑主线】
#   1. BeidaFabaoMCPClient：封装所有 HTTP / Token / 超时 / 降级逻辑。
#   2. search_laws()    → /law/search
#   3. search_cases()   → /case/search
#   4. search_all()     → 同时搜法规 + 案例，合并取前 top_k*2
#   5. get_beida_mcp_client() → 从 common.config.Config 读 Token/Base URL，
#      暴露全局单例给 beida_fabao_gate_node 调用。
#   6. 降级策略：没 Token / requests 未安装 / 非 200 / 任何异常 → 空列表，
#      永远不抛错，不打断主流程输出免费结果。
#
# 【谁在调用它？】
#   __004__langgraph_more_nodes/nodes/retrieval_nodes/beida_fabao_gate_node.py
#   (LangGraph interrupt 询问用户确认后，在「⑥ 用户确认」分支 import)
#
# 【和 retrieval_nodes 目录的关系】
#   retrieval_nodes 目录下已经**没有任何 beida_fabao 关键词规则 / API_SOURCES 白名单**；
#   从 keyword → 挂载这个通道在 V5 被彻底删干净。本客户端是「付费执行层」，
#   和挂载/检索矩阵的设计完全解耦。
#
# 【调用前的逐级升级（免费 → 付费，严格单向）】
#   retrieval_intent_decompose → entity_recall(3通道) × domain_sources
#     → fusion_ranking / single_source_sort → quality_score
#     → quality_gate_retry (MAX_QUALITY_RETRIES 次关键词扩展 + 源切换)
#     → 仍低于阈值 → fabao_retry_eligible=True
#     → beida_fabao_gate_node.interrupt() 真中断问用户
#     → 用户确认 → 本文件的 search_all() 实际发起付费调用
#
# 本模块依赖: common.config.Config（读 Token/Base URL/Timeout）
#             requests（HTTP 请求，在函数内部懒加载以避免 ImportError）

import json          # 【import  json】：用于把 Python 字典转成 JSON 字符串（序列化）和把 JSON 字符串转回 Python 字典（反序列化）。这里主要在 __main__ 自测时用来打印格式化结果。
import logging       # 【import  logging】：导入 Python 的日志系统，用来输出警告和调试信息（不会打断主流程）。
import time          # 【import  time】：时间模块，预留用于可能的请求耗时统计（当前未使用）。
from typing import Optional, Dict, List  # 【类型提示】: Optional[str]=str或None, Dict=字典类型, List=列表类型。Python 3.8 需要从 typing 导入

# 【logger = logging.getLogger(__name__)】：
# 创建一个日志记录器。__name__ 是当前模块的完整名称（'common.mcp_beidafabao'）。
# 这样打印日志时能知道是哪个模块输出的，便于调试。
logger = logging.getLogger(__name__)


class BeidaFabaoMCPClient:
    """
    【北大法宝 MCP 客户端】—— 通过 Bearer Token 鉴权调用北大法宝 API。

    【功能】
        封装了所有与北大法宝 MCP API 通信的细节，包括：
        - HTTP 请求的构建（URL、Header、Body）
        - Token 鉴权（Bearer Token）
        - 超时控制
        - 结果格式化（把 API 返回的原始数据结构转成项目统一格式）
        - 降级处理（API 不可用时返回空列表，不抛异常）

    【使用方式】
        client = BeidaFabaoMCPClient(token="xxx", base_url="https://...", timeout=15)
        results = client.search_laws("违约金上限", top_k=5)

    【降级策略】
        - token 为空 → 直接返回空列表（不发起 HTTP 请求）
        - requests 库未安装 → 捕获 ImportError，返回空列表
        - API 返回非 200 → 打印警告，返回空列表
        - 任何其他异常 → 打印警告，返回空列表
        总之：MCP 调用失败不会影响主流程，保证系统健壮性。

    Parameters
    ----------
    token : str, optional
        【Bearer Token】：北大法宝 API 的访问令牌。
        如果为 None 或空字符串，则跳过所有真实 API 调用（静默降级）。
    base_url : str
        【API 基础地址】：北大法宝 MCP API 的入口 URL。
        默认值: https://mcp.beidafabao.com/api/v1
    timeout : int
        【请求超时】：HTTP 请求的最大等待时间（秒）。
        默认 15 秒。超过这个时间还没收到响应就放弃。
    """

    def __init__(self, token: Optional[str] = None,
                 base_url: str = "https://mcp.beidafabao.com/api/v1",
                 timeout: int = 15):
        # 【self.token = token】：保存传入的 Bearer Token，后续每次请求都要用它做鉴权。
        # Bearer Token 是一种 HTTP 鉴权方式，在请求头里加 Authorization: Bearer xxx。
        self.token = token
        # 【self.base_url = base_url.rstrip("/")】：
        # 保存 API 基础地址，并去掉末尾的斜杠（如果有的话）。
        # rstrip("/") 的作用是防止 URL 拼接时出现双斜杠（如 https://xxx/api//law/search）。
        self.base_url = base_url.rstrip("/")
        # 【self.timeout = timeout】：保存超时秒数，传给 requests.post 的 timeout 参数。
        self.timeout = timeout
        # 【self._available = bool(token and token.strip())】：
        # 判断客户端是否可用。如果 token 不为 None 且不是空字符串，则为 True。
        # bool(token and token.strip()) 的逻辑：
        #   - token and token.strip()：如果 token 是 None 或 ""，则表达式短路为 None/""（假值）。
        #   - bool(...)：把结果转成 True/False。
        # 这个值会被 available 属性（@property）暴露出去，供外部检查。
        self._available = bool(token and token.strip())

    @property
    def available(self) -> bool:
        """【可用性检查】：API 是否可用（即 Token 是否已配置）。"""
        return self._available

    def search_laws(self, query: str, top_k: int = 5) -> List[dict]:
        """
        【检索法律法规】—— 调用北大法宝的 /law/search 接口。

        【功能】
            把用户输入的查询字符串发给北大法宝，检索相关的法律法规条文。
            如果 Token 没配置或者请求失败，返回空列表。

        【参数】
            query (str): 检索关键词或自然语言查询，
                         比如 "违约金不得超过合同总价的20%"。
            top_k (int): 返回条数上限，默认 5 条。

        【返回值】
            list[dict]: 格式化后的法规条目列表，每条的格式：
                {
                    "title": "中华人民共和国民法典",
                    "article_no": "第五百八十五条",
                    "content": "当事人可以约定一方违约时...",
                    "source": "北大法宝·MCP·laws",
                    "score": 0.95,
                    "status": "现行有效",
                    "date": "2021-01-01",
                    "level": "法律",
                    "court": ""
                }
            如果 API 不可用或请求失败，返回 []。

        【逻辑步骤】
            1. 检查 _available，False 则直接返回 []。
            2. 发起 HTTP POST 请求到 {base_url}/law/search。
            3. 请求头加 Authorization: Bearer {token} 和 Content-Type: application/json。
            4. 请求体传 {"query": query, "top_k": top_k}。
            5. 如果状态码 200，解析返回 JSON。
            6. 调用 _format_results() 统一格式化。
            7. 任何异常（ImportError/ConnectionError/Timeout）都返回 []。
        """
        # 如果 Token 未配置，直接返回空列表（静默降级，不报错）。
        if not self._available:
            # 【logger.info(...)】：打印一条 INFO 级别的日志，说明 Token 未配置。
            # 这比 print() 更规范，因为 logging 可以控制输出等级和格式。
            logger.info("[BeidaFabaoMCP] Token 未配置，跳过真实 API")
            return []

        try:
            # 【import requests】：在函数内部导入 requests 库。
            # 为什么不在文件顶部 import？—— 因为 requests 不是 Python 标准库，
            # 如果用户没安装 requests，顶部 import 会导致模块加载失败。
            # 在函数内部 import，可以捕获 ImportError，优雅降级。
            import requests
            # 【requests.post(...)】：发起 HTTP POST 请求。
            # POST 比 GET 更适合传复杂查询参数，因为请求体可以传 JSON。
            resp = requests.post(
                f"{self.base_url}/law/search",        # 拼接完整的 API URL
                headers={
                    "Authorization": f"Bearer {self.token}",  # Bearer Token 鉴权
                    "Content-Type": "application/json",       # 告诉服务器传的是 JSON
                },
                json={"query": query, "top_k": top_k},  # 请求体自动转 JSON
                timeout=self.timeout,                    # 超时控制
            )
            # 【resp.status_code == 200】：HTTP 状态码 200 表示请求成功。
            if resp.status_code == 200:
                # 【resp.json()】：把响应体从 JSON 字符串解析成 Python 字典。
                data = resp.json()
                # 【data.get("results", data.get("data", []))】：
                # 不同版本的 API 返回的字段名可能不同，有的用 "results"，
                # 有的用 "data"。这里两个都试试，哪个有值用哪个。
                results = data.get("results", data.get("data", []))
                # 调用 _format_results() 统一格式，source_type="laws" 标记为法规。
                return self._format_results(results, "laws")
            else:
                # 状态码不是 200，打印警告日志，截取响应体的前 200 个字符。
                logger.warning(f"[BeidaFabaoMCP] API 返回 {resp.status_code}: {resp.text[:200]}")
                return []
        except ImportError:
            # 【ImportError】：requests 库未安装。
            # 打印警告，返回空列表。
            logger.warning("[BeidaFabaoMCP] requests 未安装，跳过")
            return []
        except Exception as e:
            # 【Exception】：捕获所有其他异常（网络错误、超时、JSON 解析错误等）。
            # 打印警告，返回空列表。绝不抛异常影响主流程。
            logger.warning(f"[BeidaFabaoMCP] 请求异常: {e}")
            return []

    def search_cases(self, query: str, top_k: int = 5) -> List[dict]:
        """
        【检索裁判案例】—— 调用北大法宝的 /case/search 接口。

        【功能】
            与 search_laws 完全对称，只是换了一个 API 路径（/case/search），
            并且 source_type 标记为 "cases"。

        【参数】
            query (str): 检索关键词，比如 "买卖合同纠纷 违约金过高"。
            top_k (int): 返回条数上限，默认 5 条。

        【返回值】
            list[dict]: 格式化后的案例条目列表，多了一个 court（法院）字段。
                格式：{"title": "张三诉李四买卖合同纠纷案",
                       "content": "...",
                       "source": "北大法宝·MCP·cases",
                       "court": "北京市朝阳区人民法院",
                       ...}
            如果 API 不可用或请求失败，返回 []。

        【逻辑】
            与 search_laws 完全一致，只是 URL 和 source_type 不同。
        """
        if not self._available:
            return []

        try:
            import requests
            resp = requests.post(
                f"{self.base_url}/case/search",
                headers={"Authorization": f"Bearer {self.token}",
                         "Content-Type": "application/json"},
                json={"query": query, "top_k": top_k},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", data.get("data", []))
                return self._format_results(results, "cases")
            else:
                logger.warning(f"[BeidaFabaoMCP] API 返回 {resp.status_code}: {resp.text[:200]}")
                return []
        except ImportError:
            return []
        except Exception as e:
            logger.warning(f"[BeidaFabaoMCP] 请求异常: {e}")
            return []

    def search_all(self, query: str, top_k: int = 5) -> List[dict]:
        """
        【统一检索】—— 同时搜索法律法规和裁判案例，合并结果。

        【功能】
            同时调用 search_laws() 和 search_cases()，然后把两边的结果合并在一起，
            按分数从高到低排序，取前 top_k * 2 条返回。

        【为什么需要这个？】
            有些用户查询既可能涉及法规条文，也可能涉及相关判例。
            比如用户问"违约金最高多少"，你同时搜到民法典第 585 条（法规）
            和"最高院关于违约金调整的指导案例"（案例），两个都很有用。

        【参数】
            query (str): 检索关键词。
            top_k (int): 每类搜索返回条数上限，默认 5 条。

        【返回值】
            list[dict]: 合并排序后的结果列表，最多 top_k * 2 条。
        """
        # 分别搜法规和案例。
        laws = self.search_laws(query, top_k)
        cases = self.search_cases(query, top_k)
        # 把两个列表拼在一起。
        merged = laws + cases

        # 按分数从高到低排序。
        # 【lambda x: -x.get("score", 0)】：取每个条目的 score 字段，取负值（负号实现降序）。
        # 如果 score 字段不存在或不是数字，用 0 代替（兜底）。
        try:
            merged.sort(key=lambda x: -x.get("score", 0) if isinstance(x.get("score", 0), (int, float)) else 0)
        except Exception:
            pass  # 排序失败不报错，用原始顺序。

        # 返回前 top_k * 2 条（截断，防止数据量过大）。
        return merged[:top_k * 2]

    def _format_results(self, raw_results: list, source_type: str) -> List[dict]:
        """
        【结果格式化】—— 把北大法宝 API 的原始数据结构转成项目统一格式。

        【为什么需要这个？】
            不同数据源（法规、案例、司法解释）的 API 返回的数据结构可能不同。
            这个函数把所有的差异统一掉，让项目里的其他模块不需要关心数据来源，
            都用同样的字段名访问数据。

        【参数】
            raw_results (list): 北大法宝 API 返回的原始结果列表，每个元素是 dict。
            source_type (str): 数据来源类型，"laws" 或 "cases"，用于给 source 字段赋值。

        【返回值】
            list[dict]: 统一格式后的结果列表。每个 dict 包含：
                {
                    "title": "标题/法规名",
                    "article_no": "条文编号",
                    "content": "内容（截取前 500 字符）",
                    "source": "北大法宝·MCP·{source_type}",
                    "score": 分数（浮点数）,
                    "status": "现行有效/已废止",
                    "date": "公布/生效日期",
                    "level": "法律/行政法规/司法解释",
                    "court": "法院名称（仅案例有）"
                }
        """
        formatted = []  # 初始化空列表，用来装格式化后的结果。
        for r in raw_results:  # 遍历每条原始数据。
            if isinstance(r, dict):  # 确保是字典类型（如果不是就跳过，防止报错）。
                formatted.append({
                    # 【title】：标题/法规名。不同 API 返回的字段名不同：
                    #   "title"、"law_name"、"statute" 都可能是标题字段。
                    #   用 or 链逐个尝试，取第一个有值的。
                    "title": r.get("title") or r.get("law_name") or r.get("statute", ""),
                    # 【article_no】：条文编号，如 "第五百八十五条"。
                    "article_no": r.get("article_no") or r.get("article", ""),
                    # 【content】：内容文本，截取前 500 字符防止数据过大。
                    "content": r.get("content") or r.get("text", "")[:500],
                    # 【source】：数据来源标记，便于追溯和显示。
                    "source": f"北大法宝·MCP·{source_type}",
                    # 【score】：相关度分数，0~1 之间的浮点数，越大越相关。
                    "score": float(r.get("score", r.get("relevance", 0.8))),
                    # 【status】：时效性状态，"现行有效"或"已废止"。
                    "status": r.get("status", "现行有效"),
                    # 【date】：公布日期或生效日期。
                    "date": r.get("date", r.get("effective_date", "")),
                    # 【level】：法规层级，如"法律"、"行政法规"、"司法解释"。
                    "level": r.get("level", ""),
                    # 【court】：审理法院（仅案例数据有，法规数据为空）。
                    "court": r.get("court", "") if source_type == "cases" else "",
                })
        return formatted

    def health_check(self) -> bool:
        """
        【健康检查】—— 检查北大法宝 MCP API 是否可用。

        【功能】
            发送一个 GET 请求到 /health 端点，检查 API 是否存活。
            这通常用于启动时的可用性检查。

        【返回值】
            bool: True 表示 API 可用，False 表示不可用。
        """
        if not self._available:
            return False  # Token 没配置，直接返回不可用。
        try:
            import requests
            resp = requests.get(
                f"{self.base_url}/health",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,  # 健康检查用较短的超时（5 秒）。
            )
            return resp.status_code == 200  # 200 表示正常。
        except Exception:
            return False  # 任何异常都视为不可用。


# ============================================================
# 【全局单例工厂】
# ============================================================
# 为什么需要单例？
#   如果每次需要用北大法宝都 new 一个 BeidaFabaoMCPClient，就会反复从 Config 读取
#   Token、创建 HTTP 连接。做成单例可以省掉这些重复开销。
#
# 延迟初始化（Lazy Initialization）：
#   第一次调用 get_beida_mcp_client() 时才真正创建客户端实例，
#   如果整个流程中从未触发质量门禁降级，就一次都不会调用这个函数，
#   避免了不必要的 Token 读取和内存占用。

# 【_beida_client_instance = None】：
# 模块级私有变量，用来保存客户端单例。
# 初始值为 None，表示尚未创建。
_beida_client_instance = None


def get_beida_mcp_client() -> BeidaFabaoMCPClient:
    """
    【获取北大法宝 MCP 客户端单例】—— 工厂函数。

    【功能】
        返回一个全局共享的 BeidaFabaoMCPClient 实例。
        第一次调用时自动从 Config 读取 Token、Base URL、Timeout 等配置，
        后续调用直接返回已创建的实例。

    【为什么需要用 Config？】
        因为 Token 是敏感信息，不适合写死在代码里。
        Config 从 .env 环境变量文件读取 Token，这样：
        - 开发环境和生产环境可以用不同的 Token。
        - Token 不会提交到 Git 仓库（.env 在 .gitignore 中）。
        - 修改 Token 不需要改代码，只需要改 .env 文件。

    【返回值】
        BeidaFabaoMCPClient: 全局单例客户端实例。
    """
    # 【global _beida_client_instance】：
    # 声明要使用模块级的全局变量。
    # 不加 global 关键字的话，Python 会认为 _beida_client_instance 是函数内的局部变量。
    global _beida_client_instance

    # 如果还没创建过实例，就创建一个。
    if _beida_client_instance is None:
        # 【from common.config import Config】：
        # 在函数内部导入 Config，避免在文件顶部导入造成的循环依赖。
        from common.config import Config
        # 实例化 Config（读取 .env 文件）。
        cfg = Config()
        # 用 Config 中的配置创建客户端实例。
        _beida_client_instance = BeidaFabaoMCPClient(
            token=cfg.BEIDA_FABAO_TOKEN,        # 从 .env 读取的 Token
            base_url=cfg.BEIDA_FABAO_BASE_URL,   # API 基础地址
            timeout=cfg.BEIDA_FABAO_TIMEOUT,      # 超时秒数
        )
    # 返回单例实例。
    return _beida_client_instance


# ============================================================
# 【模块自测】
# ============================================================
if __name__ == "__main__":
    # 当直接运行 python -m common.mcp_beidafabao 时执行。
    client = get_beida_mcp_client()
    print(f"  MCP 可用: {client.available}")
    if client.available:
        results = client.search_laws("违约金不得超过合同总价的20%", top_k=3)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("  Token 未配置, 跳过测试")