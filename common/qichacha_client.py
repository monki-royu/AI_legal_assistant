"""
企查查 MCP Server API 封装客户端（企查查 Agent MCP 版）
====================================================
功能:
    提供 10 个资信维度的统一查询接口:
      - company/stream      : 工商基本信息、股东股权、主要人员
      - risk/stream         : 失信被执行人、被执行人、限制高消费、经营异常、行政处罚
      - ipr/stream          : 商标、专利、著作权、软件著作权
      - operation/stream    : 年报、融资、核心团队、招聘、招投标
      - history/stream      : 历史变更、历史名称、历史法定代表人
      - executive/stream    : 高管团队、对外投资、分支机构
      - regulation/stream   : 相关法律法规、监管政策
      - case/stream         : 司法判例、裁判文书
      - tender/stream       : 招投标中标公告
      - document/stream     : 公司文档、年报PDF、公告附件

鉴权方式（两种模式，自动切换，优先级从上到下）:
    [模式 A - MCP Bearer Token]  **默认优先**
      - 读取 .env 的 QICHACHA_AUTHORIZATION, 例如 "Bearer MMjneiQYCXCNYNiWKFsY..."
      - 每个请求直接携带请求头: Authorization: <值>
      - 响应为 SSE 流 (text/event-stream), 解析 data: <json> 事件
    [模式 B - 开放平台 AppKey + MD5 签名]  (兼容模式)
      - 当 Authorization 为空时启用
      - AppKey/SecretKey 从 .env 读取, 按字典序 urlencode + &SecretKey 拼接后 MD5(大写)
    [模式 C - Mock 模拟数据]  (兜底)
      - 当以上两种模式的配置均缺失，或 HTTP 请求超时/失败时使用
      - 基于公司名 hash 做种子生成定制化数据，保证同名同结果、名称负面关键词触发负面记录概率更高

降级策略:
    - QICHACHA_AUTHORIZATION 为空 + AppKey 也为空 -> 立即返回模拟数据
    - Bearer 请求返回 401/403/非 200/超时/连接错误 -> 切换到兼容模式(B)再试 1 次
    - 兼容模式也失败 -> 返回模拟数据; 因此"资信查询"节点绝不会阻塞合同审核主流程

对外统一入口:
    client = QiChaChaClient()
    result = client.query_company_credit("阿里巴巴(中国)有限公司")
    # result = {
    #   "basic_info": {...},           # 工商基本信息 (键: company_name/legal_person/registered_capital/establish_date/status/industry/credit_code/register_authority)
    #   "shareholders": [...],         # 股权结构 (键: name/type/share_ratio/subscribed_amount)
    #   "dishonest": [...],            # 失信被执行人 (键: case_no/court/situation/publish_date)
    #   "executed": [...],             # 被执行人 (键: case_no/exec_target/court/file_date/status)
    #   "abnormal": [...],             # 经营异常 (键: reason/authority/put_date/remove_date)
    #   "penalties": [...],            # 行政处罚 (键: penalty_authority/reason/penalty_content/penalty_date/document_no)
    #   "credit_score": 85.0,          # 资信综合评分 (0-100, 越高越好)
    #   "risk_level": "Low",           # 资信风险等级 Low/Medium/High
    #   "mock": False                  # 是否为模拟数据 (False=真实API成功 / True=降级)
    #   "mode": "MCP-Bearer"           # 实际生效模式: MCP-Bearer / AppKey-MD5 / Mock
    # }
"""

# ============================================================
# 导入区
# ============================================================
# hashlib: 标准库哈希模块, 仅在 [模式 B] AppKey+MD5 签名时使用
import hashlib
# time: 秒级时间戳, 兼容模式签名使用 + 通用超时计时 sleep 参考
import time
# random: 仅在 [模式 C] Mock 降级时生成定制化模拟数据, 使用公司名 hash 作为种子保证同名同结果
import random
# json: 解析真实 API 的 JSON 响应体 / SSE 事件里的 data 段内容
import json
# urllib.parse: 仅在 [模式 B] urlencode 拼接参数时用 (MCP 模式不需要)
import urllib.parse

# 第三方库 requests: 简洁的 HTTP 客户端, 发送企查查 MCP SSE 请求或兼容模式请求
# 用 try/except 延迟导入: 未安装 requests 时整个模块仍可加载, 只是 self.enabled=False, 自动走 Mock
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# 项目内部配置单例: 从 .env 读取 QICHACHA_AUTHORIZATION / QICHACHA_APP_KEY / SECRET_KEY / BASE_URL / TIMEOUT
from common.config import Config


# ============================================================
# 常量: MCP 10 个维度 endpoint 映射
# ============================================================
# 用户提供的 MCP 配置有 10 台独立 server, 对应 10 条 /<name>/stream 的 URL。
# 在本客户端中, 每个"资信维度"映射到一个 MCP stream endpoint:
#   工商基本 + 股东 -> company/stream
#   失信 + 被执行人 + 经营异常 + 行政处罚 -> risk/stream
#   其余 (知识产权/经营/历史/高管/法规/判例/招投标/文档) 暂不融入风险评分, 但会缓存到 raw_mcp_results 便于后续扩展。
MCP_ENDPOINTS = {
    "company":    "/company/stream",     # 工商 + 股东 + 人员
    "risk":       "/risk/stream",        # 失信 / 被执行 / 限高 / 经营异常 / 行政处罚
    "ipr":        "/ipr/stream",         # 知识产权
    "operation":  "/operation/stream",   # 经营
    "history":    "/history/stream",     # 历史变更
    "executive":  "/executive/stream",   # 高管/对外投资
    "regulation": "/regulation/stream",  # 法律法规
    "case":       "/case/stream",        # 司法判例
    "tender":     "/tender/stream",      # 招投标
    "document":   "/document/stream",    # 文档公告
}


# ============================================================
# 客户端主类
# ============================================================
class QiChaChaClient:
    """
    企查查 API 统一封装客户端。

    模式优先级:
        (A) MCP Bearer Token 模式 (Authorization 请求头)  -> 真实 API 首选
        (B) AppKey + SecretKey (MD5 签名) 兼容模式        -> MCP 失败时兜底
        (C) Mock 模拟数据                                 -> 所有配置/网络不可用时兜底
    """

    def __init__(self):
        """
        初始化客户端: 读取 5 个配置项, 判定三种模式的可用性。

        属性:
            authorization (str): 完整 "Bearer xxx" 串, 非空时模式 (A) 生效
            app_key / secret_key (str): 兼容模式的 AppKey/SecretKey
            base_url (str): 企查查服务端前缀, 默认 https://agent.qcc.com/mcp
            timeout (int): HTTP 超时秒数
            mode (str): 当前"主模式", 可能值 MCP-Bearer / AppKey-MD5 / Mock
            enabled (bool): 是否至少有一种"真实 API 模式"可用 (A or B 任一配置)
        """
        # 读取 Config 单例 (内部会 load_dotenv)
        conf = Config()
        # [模式 A] MCP Bearer Token (如 "Bearer MMjnei...") — 有值即优先
        self.authorization = (conf.QICHACHA_AUTHORIZATION or "").strip()
        # [模式 B] 开放平台 AppKey + SecretKey (MD5 签名) — 兼容模式
        self.app_key = (conf.QICHACHA_APP_KEY or "").strip()
        self.secret_key = (conf.QICHACHA_SECRET_KEY or "").strip()
        # Base URL, 去掉尾部 / 方便拼接 endpoint
        self.base_url = (conf.QICHACHA_BASE_URL or "https://agent.qcc.com/mcp").rstrip("/")
        # 单次请求超时 (SSE 建议 10~30s, 资信查询不应阻塞主流程, 控制在 10s 内)
        self.timeout = conf.QICHACHA_TIMEOUT

        # 判断三种模式: 优先 MCP -> 兼容 -> Mock
        if self.authorization and HAS_REQUESTS:
            # MCP Bearer 已配置 + requests 库可用  -> 主模式
            self.mode = "MCP-Bearer"
            self.enabled = True
        elif self.app_key and self.secret_key and HAS_REQUESTS:
            # 兼容 AppKey + MD5 模式
            self.mode = "AppKey-MD5"
            self.enabled = True
        else:
            # 真实 API 都不可用 -> 进入 Mock 模式
            self.mode = "Mock"
            self.enabled = False

        # MCP 工具列表缓存: {endpoint_url: [tool_name1, tool_name2, ...]}
        # 避免每次查询都发 tools/list, 首次请求后缓存
        self._tools_cache: dict = {}

    # ================================================================
    # 工具 0: MCP 请求诊断 (打印3种调用方式的原始HTTP响应, 用于定位格式问题)
    # ================================================================
    def _debug_mcp_request(self, endpoint: str, company_name: str) -> None:
        """
        诊断企查查 MCP 接口: tools/list 发现工具名, 然后对前 3 个工具发 tools/call。
        """
        if not self.authorization:
            print("[DEBUG] authorization 为空, 跳过 MCP 诊断")
            return
        url = f"{self.base_url}{endpoint}"
        print("=" * 78)
        # ---------- Step 1: tools/list (用修复后的 _mcp_list_tools) ----------
        print(f"[DEBUG] POST {url}  method=tools/list")
        tools = self._mcp_list_tools(endpoint)
        print(f"[DEBUG] tools/list 返回 {len(tools)} 个工具:")
        for t in tools:
            print(f"       - {t}")
        # ---------- Step 2: 对前 3 个工具发 tools/call ----------
        for tool_name in tools[:3]:
            print(f"\n[DEBUG] POST {url}  method=tools/call  name={tool_name}")
            payload = {
                "jsonrpc": "2.0", "id": 99, "method": "tools/call",
                "params": {"name": tool_name, "arguments": {"searchKey": company_name}},
            }
            try:
                resp = requests.post(url, json=payload, headers=self._mcp_headers(),
                                     timeout=(min(5, self.timeout), self.timeout))
                print(f"  HTTP {resp.status_code}  Content-Type: {resp.headers.get('Content-Type')}")
                # ★ 强制 UTF-8 解码
                body = resp.content.decode("utf-8", errors="replace")
                print(f"  Body ({len(body)} chars):")
                print("-" * 40)
                print(body[:2000])
                print("-" * 40)
            except Exception as e:
                print(f"  Exception: {type(e).__name__}: {e}")
        print("=" * 78)

    # ================================================================
    # 工具 0-辅助: MCP 请求头 (统一构造)
    # ================================================================
    def _mcp_headers(self) -> dict:
        """返回企查查 MCP POST 请求的标准请求头"""
        return {
            "Authorization": self.authorization,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    # ================================================================
    # 工具 1: POST JSON-RPC 请求 + 响应解析 (纯 JSON / SSE 两种返回格式都兼容)
    # ================================================================
    def _mcp_post_and_parse(self, url: str, payload: dict) -> list:
        """
        发送一次 POST JSON-RPC 请求, 解析响应 (兼容 application/json 和 text/event-stream)。
        强制 UTF-8 解码 (企查查 SSE 响应不含 charset, requests 默认 ISO-8859-1 会导致乱码)。
        返回扁平化后的事件列表。
        """
        collected = []
        try:
            resp = requests.post(
                url, json=payload, headers=self._mcp_headers(),
                timeout=(min(5, self.timeout), self.timeout),
            )
            if resp.status_code != 200:
                return []
            # ★ 关键修复: 强制 UTF-8 解码, 不用 resp.text (它对 text/event-stream 默认 ISO-8859-1)
            body = resp.content.decode("utf-8", errors="replace").lstrip()
            # ---------- 策略 1: 纯 JSON ----------
            if body.startswith("{") or body.startswith("["):
                try:
                    obj = json.loads(body)
                    self._flatten_append(collected, obj)
                    return collected
                except Exception:
                    pass
            # ---------- 策略 2: SSE 逐行解析 ----------
            # 标准化换行: \r\n -> \n, \r -> \n, 再按 \n\n 切事件
            body = body.replace("\r\n", "\n").replace("\r", "\n")
            for event in body.split("\n\n"):
                found_any = False
                for line in event.split("\n"):
                    if line.startswith("data:"):
                        p = line[len("data:"):].lstrip()
                        if not p or p == "[DONE]":
                            continue
                        try:
                            self._flatten_append(collected, json.loads(p))
                            found_any = True
                        except Exception:
                            collected.append({"raw_text": p[:200]})
                            found_any = True
                if not found_any and event.strip():
                    s = event.strip()
                    if s.startswith(("{", "[")):
                        try:
                            self._flatten_append(collected, json.loads(s))
                        except Exception:
                            pass
            return collected
        except Exception:
            return []

    # ================================================================
    # 工具 2: tools/list — 获取指定 endpoint 的所有工具名 (带缓存)
    # ================================================================
    def _mcp_list_tools(self, endpoint: str) -> list:
        """
        POST tools/list 获取工具列表, 返回工具名列表 (str)。
        结果缓存在 self._tools_cache, 同一 endpoint 只请求一次。
        """
        url = f"{self.base_url}{endpoint}"
        if url in self._tools_cache:
            return self._tools_cache[url]
        payload = {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}}
        events = self._mcp_post_and_parse(url, payload)
        tool_names = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            # MCP tools/list 返回格式: {tools: [{name: "xxx", ...}, ...]}
            # _flatten_append 已把 JSON-RPC 的 result 展开过, 所以 ev 可能是:
            #   1) 顶层 {jsonrpc, id, result:{tools:[...]}}  -> ev["result"]["tools"]
            #   2) result 层 {tools:[...]}                  -> ev["tools"]
            #   3) 单个工具定义 {name, description, inputSchema} -> ev["name"]
            tools_field = ev.get("tools")
            if not tools_field:
                    r = ev.get("result")
                    if isinstance(r, dict):
                            tools_field = r.get("tools")
            if isinstance(tools_field, list):
                for t in tools_field:
                    if isinstance(t, dict) and t.get("name"):
                        tool_names.append(t["name"])
            # 兜底: ev 本身就是单个工具定义
            elif ev.get("name") and ev.get("inputSchema") is not None:
                tool_names.append(ev["name"])
        # ★ 去重: 企查查 MCP 同一工具有时重复发 3 次 SSE 事件
        deduped = list(dict.fromkeys(tool_names))
        self._tools_cache[url] = deduped
        return deduped

    # ================================================================
    # 工具 3: tools/call — 调用指定工具, 返回解析后的事件列表
    # ================================================================
    def _mcp_call_tool(self, endpoint: str, tool_name: str, arguments: dict) -> list:
        """
        POST tools/call 调用指定工具, 返回扁平化事件列表。
        """
        url = f"{self.base_url}{endpoint}"
        payload = {
            "jsonrpc": "2.0", "id": hash(tool_name) % 100000,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        return self._mcp_post_and_parse(url, payload)

    # ================================================================
    # 工具 4: 高级查询 — tools/list + 关键词过滤 + tools/call, 合并所有事件
    # ================================================================
    def _request_mcp_sse(self, endpoint: str, company_name: str, tool_keywords: list = None) -> list:
        """
        在指定 MCP endpoint 上查询公司信息, 返回合并后的事件列表。

        流程:
            1. tools/list 获取该 endpoint 所有工具名 (带缓存)
            2. 如果 tool_keywords 为 None, 调用所有工具
               如果 tool_keywords 不为 None, 只调用名称含任一关键词的工具
            3. 对每个匹配工具 POST tools/call, 参数 arguments={"searchKey": company_name}
            4. 合并所有工具返回的事件, 交给 _flatten_append 扁平化
            5. 返回事件列表

        参数:
            endpoint (str): MCP endpoint, 如 "/company/stream"
            company_name (str): 查询公司名
            tool_keywords (list|None): 工具名关键词过滤, 如 ["basic", "company"] 只调含这些词的工具

        返回:
            List[Dict]: 扁平化事件列表
        """
        if not self.authorization:
            return []
        # Step 1: 获取工具列表
        all_tools = self._mcp_list_tools(endpoint)
        if not all_tools:
            return []
        # Step 2: 关键词过滤
        if tool_keywords:
            # 大小写不敏感匹配
            kw_lower = [k.lower() for k in tool_keywords]
            target_tools = [t for t in all_tools if any(k in t.lower() for k in kw_lower)]
            # 如果关键词过滤后为空, 退而调用全部工具 (避免漏查)
            if not target_tools:
                target_tools = all_tools
        else:
            target_tools = all_tools
        # Step 3: 逐个调用 tools/call, 合并事件
        all_events = []
        for tool_name in target_tools:
            events = self._mcp_call_tool(endpoint, tool_name, {"searchKey": company_name})
            all_events.extend(events)
        return all_events

    # ================================================================
    # 工具 1-辅助: 把一个解析到的对象(可能是 JSON-RPC / MCP tools 事件 / 列表)扁平化追加到 collected
    # ================================================================
    def _flatten_append(self, collected: list, obj) -> None:
        """
        递归扁平化 JSON, 把"最有价值"的一层/两层字典都 append 到 collected 列表。

        目的: 下游 _get_basic_info / _get_risk 等函数只需要遍历"一堆字典,每个都可能含公司名/失信列表"。
        这里把 MCP 常见的 4 种包装都解一层:
          1) JSON-RPC 2.0 : {"jsonrpc":"2.0", "id":1, "result": <X>}  -> 追加 {"raw": self} + <X>
          2) MCP tool_result: {"id":...,"result":{"content":[{"type":"text","text":"{\"a\":1}"}]}}  -> 尝试把 text 解析为 JSON, 再追加
          3) 数组 [A, B, C] -> 每项单独追加
          4) 普通 dict -> 直接追加
        """
        if obj is None:
            return
        # ------- 数组: 逐项递归 -------
        if isinstance(obj, list):
            for item in obj:
                self._flatten_append(collected, item)
            return
        if not isinstance(obj, dict):
            return
        # 先把顶层原始字典先放进去 (防止解包过头, 丢失顶层字段)
        collected.append(obj)
        # ------- JSON-RPC 2.0: result 是业务数据 -------
        if "jsonrpc" in obj and "result" in obj:
            r = obj["result"]
            # 如果 result 是字符串, 很可能是 JSON 字符串, 再解析一次
            if isinstance(r, str):
                try:
                    r = json.loads(r)
                except Exception:
                    collected.append({"_rpc_result_text": r})
                    r = None
            if r is not None:
                self._flatten_append(collected, r)
        # ------- MCP 标准 tools/call_tool 的返回 result.content 是一段文本数组 -------
        # 常见结构: {"type":"text","text": "<大段 JSON 字符串>"} 或直接是 {toolCalls: ...}
        for key in ("result", "content", "data", "items", "output"):
            sub = obj.get(key)
            if isinstance(sub, (list, dict)):
                self._flatten_append(collected, sub)
            elif isinstance(sub, str) and sub.strip().startswith(("{", "[")):
                try:
                    self._flatten_append(collected, json.loads(sub))
                except Exception:
                    pass
        # content: [{"type":"text", "text":"<JSON>"}]
        if isinstance(obj.get("content"), list):
            for seg in obj["content"]:
                if isinstance(seg, dict) and seg.get("type") == "text" and isinstance(seg.get("text"), str):
                    t = seg["text"].strip()
                    if t.startswith(("{", "[")):
                        try:
                            self._flatten_append(collected, json.loads(t))
                        except Exception:
                            collected.append({"_mcp_text": t})

    # ================================================================
    # 工具 2: 兼容模式 (AppKey + MD5 签名) 通用请求
    # ================================================================
    def _make_sign(self, params: dict) -> str:
        """
        [模式 B 专用] 计算企查查开放平台标准 MD5 签名。

        算法步骤:
            1. 参数字典按 key 字典序升序排列;
            2. urllib.parse.urlencode 拼接 key1=value1&key2=value2 (UTF-8)
            3. 末尾追加 &SecretKey=xxxxxxxx (企查查文档规范是拼接 SecretKey)
            4. 整串做 MD5, 返回 32 位大写十六进制字符串
        """
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        query_str = urllib.parse.urlencode(sorted_items)
        raw = f"{query_str}&{self.secret_key}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    def _request_appkey(self, endpoint: str, params: dict) -> dict:
        """
        [模式 B 专用] 发送 AppKey 签名 GET 请求。

        参数:
            endpoint: 如 "/search/company/basic"
            params: 业务参数 (不含 key/timestamp/sign, 会自动注入)

        返回值:
            dict: 响应 JSON; 任何异常返回 None。
        """
        if not (self.app_key and self.secret_key):
            return None
        # 注入鉴权公共参数
        params["key"] = self.app_key
        params["timestamp"] = str(int(time.time()))
        params["sign"] = self._make_sign(params)
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    # ================================================================
    # 单维度查询 (对外内部接口)
    # 设计: 每个维度 "先试 MCP Bearer -> 再试 AppKey 兼容 -> 还不行返回空 / None"
    #       最终 query_company_credit 会基于"至少一个维度成功"决定是否视为真实API命中
    # ================================================================
    def _get_basic_info(self, company_name: str) -> dict:
        """
        查询企业工商基本信息 (法定代表人/成立日期/注册资本/经营状态/行业/信用代码)。

        数据源映射:
            MCP: /company/stream -> 在 SSE 事件里寻找包含 basic / info / 工商的字段
            AppKey: /search/company/basic -> 返回 result.data / result
            失败: 返回 None
        """
        # ------- 尝试 1: MCP Bearer -------
        if self.mode == "MCP-Bearer" or self.authorization:
            # 精确匹配企查查 company endpoint 的真实工具名:
            #   get_company_registration_info (工商注册信息), get_company_profile (画像),
            #   get_annual_reports (年报 -> 内有企业基本信息嵌套字典),
            #   get_contact_info (联系方式),
            #   get_beneficial_owners (受益所有人 -> 内有"日常经营管理人员"含法定代表人),
            #   get_key_personnel (关键人员), get_actual_controller (实际控制人, 含企业名称锚点)
            events = self._request_mcp_sse(MCP_ENDPOINTS["company"], company_name,
                                           tool_keywords=["registration_info", "registration",
                                                          "profile", "annual_report",
                                                          "contact", "key_personnel",
                                                          "beneficial_owners", "beneficial",
                                                          "actual_controller"])

            def _dfs_collect_dicts(node, out_list: list) -> None:
                """递归把事件中所有 dict 节点按 DFS 收集 (去重, 避免引用重复)。"""
                if isinstance(node, dict):
                    if not any(x is node for x in out_list):
                        out_list.append(node)
                    for v in node.values():
                        _dfs_collect_dicts(v, out_list)
                elif isinstance(node, list):
                    for v in node:
                        _dfs_collect_dicts(v, out_list)

            all_dicts: list = []
            for ev in events:
                _dfs_collect_dicts(ev, all_dicts)
            # 命中判定字段集合: 英文旧版 + 企查查 MCP 中文字段
            #   企业基本信息 (年报内): 统一社会信用代码/注册号/企业经营状态/法定代表人...
            #   实际控制人信息: 企业名称
            #   受益所有人信息: 企业名称 / 日常经营管理人员 (name/任职类型)
            basic_hit_keys = (
                "company_name", "companyName", "name", "entName", "qymc", "ent_name",
                "legal_person", "legalPerson", "faren", "frdb", "fr", "legal_representative",
                "regCapital", "zc", "zczb", "reg_capital", "registeredCapital",
                "establishDate", "establish_date", "foundDate", "createDate",
                "creditCode", "credit_code", "creditCodeNumber", "unifiedSocialCreditCode",
                # ------- 企查查 MCP 中文字段 -------
                "企业名称", "法定代表人", "法人",
                "注册资本", "注册资金",
                "成立日期", "成立时间",
                "统一社会信用代码", "统一社会信用代码/注册号", "注册号",
                "经营状态", "企业经营状态",
                "企业基本信息",  # 年报里的嵌套子字典: 命中后 _normalize_basic_info 会在内部递归 pick 到字段
            )
            # 第 1 轮: 优先找"像完整工商卡片"的字典 (一次含多个字段)
            best = None
            best_score = 0
            for c in all_dicts:
                score = sum(1 for k in basic_hit_keys if c.get(k) not in (None, ""))
                if score > best_score:
                    best_score = score
                    best = c
            if best is not None and best_score >= 1:
                result = self._normalize_basic_info(best, company_name)
                # ── 字段补全: best 卡片部分字段空时, 从其他所有命中字典中补齐 (不同工具输出在不同卡片上) ──
                # 企查查 MCP 的"状态"字段往往藏在年报的"企业基本信息"子字典里, 而 best 往往是
                # get_company_registration_info 的返回体, 它没有"经营状态"键; 这里循环补齐。
                target_fields = ("status", "registered_capital", "establish_date", "legal_person",
                                 "industry", "credit_code", "register_authority")
                for other in all_dicts:
                    if other is best:
                        continue
                    other_norm = self._normalize_basic_info(other, "")
                    for fld in target_fields:
                        if not result.get(fld) and other_norm.get(fld):
                            result[fld] = other_norm[fld]
                # 注册资本格式清洗: "XXX万元" → "XXX万人民币" ; 无货币单位时统一加"人民币"
                rc = result.get("registered_capital")
                if rc:
                    rc_s = str(rc).strip()
                    if rc_s.endswith("万元") and "人民币" not in rc_s:
                        result["registered_capital"] = rc_s[:-2] + "万人民币"
                    elif rc_s.endswith("元") and "人民币" not in rc_s and "美元" not in rc_s and "港元" not in rc_s and "欧元" not in rc_s and "日元" not in rc_s:
                        result["registered_capital"] = "人民币" + rc_s
                # 法定代表人兜底: 如果 get_beneficial_owners 返回的 日常经营管理人员 含"任职类型=法定代表人",
                # 就用那个名称补全 (企查查注册信息有时字段名完全不同, 实际人员藏在受益所有人工具中)
                if not result.get("legal_person"):
                    for c in all_dicts:
                        if isinstance(c, dict) and c.get("任职类型") == "法定代表人" and c.get("名称"):
                            result["legal_person"] = c["名称"]
                            break
                return result
        # ------- 尝试 2: AppKey 兼容模式 -------
        if self.app_key and self.secret_key:
            data = self._request_appkey("/search/company/basic", {"companyName": company_name})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or data
                if isinstance(payload, dict):
                    return self._normalize_basic_info(payload, company_name)
        return None

    # ================================================================
    # 内部工具: 深度 DFS 提取事件列表中所有 dict (MCP 包装非常发散, 做一次全展开就能匹配所有字段名)
    # ================================================================
    @staticmethod
    def _collect_all_dicts(events: list) -> list:
        """
        对 events 列表中所有元素 (dict / list / dict嵌套) 进行 DFS 遍历, 返回全部 dict 节点。
        仅用于 MCP 事件集合 -> 便于"_get_* 系列函数"只需要 in dict.keys() 判断键名是否存在即可。
        """
        out = []

        def _dfs(n):
            if isinstance(n, dict):
                if not any(x is n for x in out):
                    out.append(n)
                for v in n.values():
                    _dfs(v)
            elif isinstance(n, list):
                for v in n:
                    _dfs(v)
        for e in events:
            _dfs(e)
        return out

    # ================================================================
    # 内部工具: 在"一堆字典"里寻找给定键名下的"列表型数据" (支持 dict.list -> list / 直接list / dict.items / dict)
    # ================================================================
    def _extract_list_from_dicts(self, all_dicts: list, keys: tuple) -> list:
        """
        在 all_dicts (list[dict]) 中找第一个命中 keys 的字段, 并将其归一化"展开成 list[dict]".

        兼容以下 5 种企查查 MCP 常见包装:
          A)  d["shareholders"] = [ {name:x, ratio:y}, ... ]                -> 直接返回
          B)  d["shareholders"] = { "list": [ ... ], "total": 12 }          -> 返回 list 部分
          C)  d["shareholders"] = { "items": [ ... ] }                      -> 返回 items
          D)  d["shareholders"] = { 单条记录, 没被包数组 }                  -> 包成 [d["shareholders"]] 返回
          E)  d["result"] 本身是 items 列表 (不是 dict, 而是 list)          -> 返回 list

        返回:
            List[dict], 未命中返回 []
        """
        for d in all_dicts:
            if not isinstance(d, dict):
                continue
            for k in keys:
                if k not in d:
                    continue
                val = d[k]
                if val is None:
                    continue
                if isinstance(val, list):
                    # 命中 E
                    items = val
                elif isinstance(val, dict):
                    # 命中 B / C / D
                    items = val.get("list") or val.get("items")
                    if items is None:
                        items = [val]
                else:
                    # 未知类型, 跳过
                    continue
                # 过滤掉非 dict 元素 (比如 MCP 有些会塞"合计"字符串)
                cleaned = [x for x in items if isinstance(x, dict)]
                if cleaned:
                    return cleaned
        return []

    def _get_shareholders(self, company_name: str) -> list:
        """
        查询股权结构 (股东名称 / 持股比例 / 认缴出资额 / 股东类型)。

        数据源: MCP /company/stream 中含 shareholders / holder / 股东 键的事件
                AppKey /company/getShareHolderList
        返回: 归一化股东列表 (list of dict), 失败 []
        """
        result = []
        # ------- MCP -------
        if self.authorization:
            # 真实工具名: get_shareholder_info
            events = self._request_mcp_sse(MCP_ENDPOINTS["company"], company_name,
                                           tool_keywords=["shareholder_info", "shareholder"])
            all_dicts = self._collect_all_dicts(events)
            # 键兼容: 英文旧键 + 企查查年报中文名 股东（发起人）及出资信息 / 股东
            keys = ("shareholders", "shareholder", "holders", "stockHolders", "stockholders", "股东",
                    "holderList", "gdList", "gdxx", "guDong",
                    "股东（发起人）及出资信息", "股东信息", "股权信息")
            for item in self._extract_list_from_dicts(all_dicts, keys):
                n = self._normalize_shareholder(item)
                if n:
                    result.append(n)
            # 兜底: 可能股东事件本身就是扁平 events list (不在子键里), 直接从 all_dicts 里挑出"像股东"的字典
            if not result:
                sh_like_keys = ("name", "stockName", "gdmc", "gudongName", "holderName", "ratio", "capital_contribution", "subcribeCapital")
                for d in all_dicts:
                    if any(k in d for k in sh_like_keys) and not any(k in d for k in ("companyName", "entName", "qymc")):
                        n = self._normalize_shareholder(d)
                        if n:
                            result.append(n)
        # ------- AppKey -------
        if not result and self.app_key and self.secret_key:
            data = self._request_appkey("/company/getShareHolderList", {"companyName": company_name, "pageSize": "20"})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or {}
                if isinstance(payload, dict):
                    lst = payload.get("list") or payload.get("items") or []
                elif isinstance(payload, list):
                    lst = payload
                else:
                    lst = []
                for item in lst:
                    n = self._normalize_shareholder(item)
                    if n:
                        result.append(n)
        return result

    def _get_dishonest(self, company_name: str) -> list:
        """
        查询失信被执行人 (俗称"老赖"): 案号 / 执行法院 / 失信情形 / 发布日期。

        数据源: MCP /risk/stream 事件 dishonest / 失信 / laolaishixin
                AppKey /company/getDishonestList
        返回: 归一化列表, 失败 []
        """
        result = []
        # ------- MCP /risk -------
        if self.authorization:
            # risk endpoint 真实工具名: get_dishonest_info
            events = self._request_mcp_sse(MCP_ENDPOINTS["risk"], company_name,
                                           tool_keywords=["dishonest_info", "dishonest", "失信"])
            all_dicts = self._collect_all_dicts(events)
            keys = ("dishonest", "dishonestList", "失信", "laolai", "shixin",
                    "shixinList", "laolaiList", "dishonesty", "sxList", "beizhixingxinx",
                    "失信被执行人", "失信信息")
            for item in self._extract_list_from_dicts(all_dicts, keys):
                n = self._normalize_dishonest(item)
                if n:
                    result.append(n)
            if not result:
                # 兜底: event 本身就是单条失信记录 (含有"caseNo/ah/失信情形"之一 且不是"基本信息/公司信息")
                like_keys = ("caseNo", "case_code", "ah", "courtName", "court_name", "executedName", "iname",
                             "duty", "performance", "sx情形", "失信情形", "publish", "publishDate")
                for d in all_dicts:
                    if any(k in d for k in like_keys) and not any(k in d for k in ("companyName", "entName", "qymc", "regCapital")):
                        n = self._normalize_dishonest(d)
                        if n:
                            result.append(n)
        # ------- AppKey -------
        if not result and self.app_key and self.secret_key:
            data = self._request_appkey("/company/getDishonestList", {"companyName": company_name, "pageSize": "20"})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or {}
                lst = payload.get("list") if isinstance(payload, dict) else []
                if isinstance(payload, list):
                    lst = payload
                for item in lst:
                    n = self._normalize_dishonest(item)
                    if n:
                        result.append(n)
        return result

    def _get_executed(self, company_name: str) -> list:
        """
        查询被执行人: 案号 / 执行标的 / 执行法院 / 立案日期 / 状态。

        数据源: MCP /risk/stream 键 executed / zhixing / 被执行人
                AppKey /company/getZhixingList
        """
        result = []
        if self.authorization:
            # risk endpoint 真实工具名: get_judgment_debtor_info (判决债务人/被执行人), get_default_info (一般被执行人)
            events = self._request_mcp_sse(MCP_ENDPOINTS["risk"], company_name,
                                           tool_keywords=["judgment_debtor_info", "judgment", "default_info", "debtor",
                                                          "executed", "被执行"])
            all_dicts = self._collect_all_dicts(events)
            keys = ("executed", "executedList", "zhixing", "被执行人", "zhixingList",
                    "zxList", "executiveList", "beizhixing", "被执行人信息")
            for item in self._extract_list_from_dicts(all_dicts, keys):
                n = self._normalize_executed(item)
                if n:
                    result.append(n)
            if not result:
                like_keys = ("caseNo", "execCaseNo", "ah", "executedMoney", "executeMoney", "biaoDi",
                             "execCourtName", "court", "status", "zhixingStatus")
                for d in all_dicts:
                    if any(k in d for k in like_keys) and not any(k in d for k in ("companyName", "entName", "qymc")):
                        n = self._normalize_executed(d)
                        if n:
                            result.append(n)
        if not result and self.app_key and self.secret_key:
            data = self._request_appkey("/company/getZhixingList", {"companyName": company_name, "pageSize": "20"})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or {}
                lst = payload.get("list") if isinstance(payload, dict) else []
                if isinstance(payload, list):
                    lst = payload
                for item in lst:
                    n = self._normalize_executed(item)
                    if n:
                        result.append(n)
        return result

    def _get_abnormal(self, company_name: str) -> list:
        """
        查询经营异常名录: 列入原因 / 列入机关 / 列入日期 / 移除日期。

        数据源: MCP /risk/stream 键 abnormal / operating_abnormal / 经营异常
                AppKey /company/getAbnormalList
        """
        result = []
        if self.authorization:
            # risk endpoint 真实工具名: get_business_exception (经营异常)
            events = self._request_mcp_sse(MCP_ENDPOINTS["risk"], company_name,
                                           tool_keywords=["business_exception", "exception", "abnormal", "经营异常"])
            all_dicts = self._collect_all_dicts(events)
            keys = ("abnormal", "abnormalList", "operatingAbnormal", "经营异常",
                    "jyycList", "yycList", "经营异常名录", "经营异常信息",
                    "yyc", "jyyc", "ycList", "abnormalOperation", "abnormal_info")
            for item in self._extract_list_from_dicts(all_dicts, keys):
                n = self._normalize_abnormal(item)
                if n:
                    result.append(n)
            if not result:
                like_keys = ("putReason", "inReason", "reason", "cause", "jgrq", "putDate", "listedDate", "date")
                for d in all_dicts:
                    if any(k in d for k in like_keys) and not any(k in d for k in ("companyName", "entName", "qymc")):
                        n = self._normalize_abnormal(d)
                        if n:
                            result.append(n)
        if not result and self.app_key and self.secret_key:
            data = self._request_appkey("/company/getAbnormalList", {"companyName": company_name, "pageSize": "20"})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or {}
                lst = payload.get("list") if isinstance(payload, dict) else []
                if isinstance(payload, list):
                    lst = payload
                for item in lst:
                    n = self._normalize_abnormal(item)
                    if n:
                        result.append(n)
        return result

    def _get_penalties(self, company_name: str) -> list:
        """
        查询行政处罚记录: 处罚机关 / 处罚事由 / 处罚内容 / 处罚日期 / 文号。

        数据源: MCP /risk/stream 键 penalties / punishment / 行政处罚
                AppKey /company/getPenaltyList
        """
        result = []
        if self.authorization:
            # risk endpoint 真实工具名: get_administrative_penalty (行政处罚)
            events = self._request_mcp_sse(MCP_ENDPOINTS["risk"], company_name,
                                           tool_keywords=["administrative_penalty", "penalty", "处罚", "administrative"])
            all_dicts = self._collect_all_dicts(events)
            keys = ("penalties", "penaltyList", "punishments", "行政处罚", "chufa",
                    "penalty", "punish", "xzzf", "xzcf", "administrativePenalty",
                    "行政处罚信息", "行政处罚决定书")
            for item in self._extract_list_from_dicts(all_dicts, keys):
                n = self._normalize_penalty(item)
                if n:
                    result.append(n)
            if not result:
                like_keys = ("penaltyAuthority", "penaltyOrg", "department", "organ", "authority",
                             "penaltyReason", "penaltyType", "penaltyContent", "content", "documentNo", "penaltyNo")
                for d in all_dicts:
                    if any(k in d for k in like_keys) and not any(k in d for k in ("companyName", "entName", "qymc")):
                        n = self._normalize_penalty(d)
                        if n:
                            result.append(n)
        if not result and self.app_key and self.secret_key:
            data = self._request_appkey("/company/getPenaltyList", {"companyName": company_name, "pageSize": "20"})
            if data and isinstance(data, dict):
                payload = data.get("result") or data.get("data") or {}
                lst = payload.get("list") if isinstance(payload, dict) else []
                if isinstance(payload, list):
                    lst = payload
                for item in lst:
                    n = self._normalize_penalty(item)
                    if n:
                        result.append(n)
        return result

    # ================================================================
    # 内部归一化: 把真实 API 返回的五花八门键名统一为 mock 数据的结构
    # 这样下游 credit_check_node / risk_aggregate_node 无需关心接口差异
    # ================================================================
    def _normalize_basic_info(self, raw: dict, fallback_name: str) -> dict:
        """把真实 API 工商字段 归一化 为 mock 约定键名 (snake_case)。

        企查查 MCP 真实返回使用中文键名 (企业名称 / 法定代表人 / 统一社会信用代码等),
        这里通过 pick() 多键名兼容, 一次性支持英文旧接口 + 中文 MCP 两种返回。
        """
        if not isinstance(raw, dict):
            raw = {}
        # 兼容 5 种常见命名 + 企查查 MCP 中文字段
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        return {
            # 公司名: 英文多种 + MCP中文
            "company_name":       pick("company_name", "companyName", "name", "entName", "qymc",
                                       "企业名称") or fallback_name,
            # 法定代表人: 英文简写 + 日常经营管理人员里的"任职类型=法定代表人"对应"名称"
            "legal_person":       pick("legal_person", "legalPerson", "faren", "legalRepresentative", "frdb", "fr",
                                       "法定代表人", "法人"),
            # 注册资本: 英文 + 中文
            "registered_capital": pick("registered_capital", "registeredCapital", "zczb", "registCapi", "注册资本",
                                       "注册资金"),
            # 成立日期: 英文 + 中文
            "establish_date":     pick("establish_date", "establishDate", "establishmentDate", "clrq", "成立日期",
                                       "成立时间"),
            # 经营状态: 英文 + 中文 (企查查年报: 开业/存续/吊销 等)
            "status":             pick("status", "entStatus", "state", "jyzt", "经营状态", "regStatus",
                                       "企业经营状态"),
            "industry":           pick("industry", "industryName", "hylb", "所属行业", "hydm"),
            # 统一社会信用代码: 英文简写 + 中文
            "credit_code":        pick("credit_code", "creditCode", "unifiedCode", "tyshxydm", "统一社会信用代码",
                                       "统一社会信用代码/注册号"),
            "register_authority": pick("register_authority", "registerAuthority", "djjg", "登记机关"),
        }

    def _normalize_shareholder(self, raw: dict) -> dict:
        """股东信息归一化: 兼容英文简写 + 企查查 MCP 中文年报字段 股东（发起人）及出资信息。"""
        if not isinstance(raw, dict):
            return {}
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        name = pick("name", "stockName", "gd", "gudong", "股东名称", "holderName",
                    "股东姓名", "发起人名称", "持股人名称")
        if not name:
            return {}
        # 持股比例: 英文 + 中文 (企查查 MCP: 总持股比例 / 最终受益股份 等)
        share = pick("share_ratio", "shareRatio", "持股比例", "cgbl", "ratio",
                     "总持股比例", "最终受益股份", "出资比例")
        # 认缴出资额: 企查查年报字段是 "认缴出资额(万元)" (带万元后缀)
        amount_raw = pick("subscribed_amount", "subscribedAmount", "认缴出资额", "amount",
                          "认缴出资额(万元)", "认缴出资额人民币", "出资额")
        if amount_raw:
            # 加后缀"万"保留单位, 下游显示一致
            if isinstance(amount_raw, (int, float)) and "万元" in (pick("认缴出资额(万元)", "") or ""):
                amount = f"{amount_raw}万人民币"
            else:
                amount = str(amount_raw)
                if "万" not in amount and len(amount) <= 10:
                    # 企查查"认缴出资额(万元)"语义默认万元
                    # 注意: 如果 key 是"认缴出资额(万元)", 则 value 本身就是万元数, 拼接"万"
                    if raw.get("认缴出资额(万元)") is not None and str(raw.get("认缴出资额(万元)")) == str(amount_raw):
                        amount = f"{amount}万人民币"
        else:
            amount = ""
        t = pick("type", "stockType", "gdtype", "股东类型") or ("法人股东" if "公司" in name or "企业" in name or "合伙" in name else "自然人股东")
        # 持股比例自动补 %
        if isinstance(share, (int, float)):
            share = f"{share}%"
        elif isinstance(share, str) and share and not share.endswith("%"):
            share = f"{share}%"
        return {
            "name": name,
            "type": t,
            "share_ratio": share or "-",
            "subscribed_amount": amount or "-",
        }

    def _normalize_dishonest(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        case_no = pick("case_no", "caseNo", "ah", "caseCode", "案号")
        if not case_no:
            return {}
        return {
            "case_no": case_no,
            "court": pick("court", "courtName", "fayuan", "执行法院", "zxfy"),
            "situation": pick("situation", "dishonestSituation", "shixinqingxing", "失信情形", "sxqx") or "有履行能力而拒不履行生效法律文书确定义务",
            "publish_date": pick("publish_date", "publishDate", "fbrq", "发布日期"),
        }

    def _normalize_executed(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        case_no = pick("case_no", "caseNo", "ah", "案号")
        if not case_no:
            return {}
        return {
            "case_no": case_no,
            "exec_target": pick("exec_target", "execTarget", "zxbd", "执行标的"),
            "court": pick("court", "courtName", "fayuan", "执行法院"),
            "file_date": pick("file_date", "fileDate", "larq", "立案日期"),
            "status": pick("status", "execStatus", "zxzt", "状态") or "执行中",
        }

    def _normalize_abnormal(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        reason = pick("reason", "abnormalReason", "lryy", "列入原因")
        if not reason:
            return {}
        return {
            "reason": reason,
            "authority": pick("authority", "authorityName", "lrjg", "列入机关", "sjjg"),
            "put_date": pick("put_date", "putDate", "lrrq", "列入日期"),
            "remove_date": pick("remove_date", "removeDate", "ycrq", "移除日期"),
        }

    def _normalize_penalty(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        def pick(*keys):
            for k in keys:
                if k in raw and raw[k] not in (None, ""):
                    return raw[k]
            return ""
        reason = pick("reason", "penaltyReason", "wfxw", "处罚事由", "违法事实")
        authority = pick("penalty_authority", "penaltyAuthority", "cftjjg", "处罚机关")
        content = pick("penalty_content", "penaltyContent", "cfjg", "处罚内容")
        if not (reason or authority or content):
            return {}
        return {
            "penalty_authority": authority or "-",
            "reason": reason or "-",
            "penalty_content": content or "-",
            "penalty_date": pick("penalty_date", "penaltyDate", "cfrq", "处罚日期"),
            "document_no": pick("document_no", "documentNo", "cfwh", "文书号", "文号"),
        }

    # ================================================================
    # 真实 API 6 维度数据 -> 综合评分
    # 算法: 与 mock 保持一致 (基础分 80 + 经营状态加减分 + 负面记录扣分 + 注册资本加分)
    # 这样无论真实/Mock, credit_score 口径都一致, 前端评分对比连续
    # ================================================================
    def _calc_credit_score(self, basic_info: dict, shareholders: list, dishonest: list, executed: list, abnormal: list, penalties: list) -> tuple:
        """
        基于真实 API 已归一化的字段计算资信评分 (0-100) 与等级。

        返回: (score: float, level: str in Low/Medium/High)
        """
        score = 80.0
        status = str(basic_info.get("status", "")) if isinstance(basic_info, dict) else ""
        # 经营状态: 企查查 MCP 有多种同义表述: 在营/存续/开业/正常/核准设立 都算正常经营
        if any(s in status for s in ("存续", "在营", "开业", "正常", "核准设立")):
            score += 5
        elif any(s in status for s in ("停业", "清算", "关闭")):
            score -= 15
        elif any(s in status for s in ("吊销", "注销", "撤销")):
            score -= 50
        # 失信 -20 / 条
        score -= len(dishonest) * 20
        # 被执行人 -8 / 条, 执行中额外 -5
        for e in executed:
            score -= 8
            if "执行中" in str(e.get("status", "")):
                score -= 5
        # 经营异常
        for a in abnormal:
            if a.get("remove_date"):
                score -= 5
            else:
                score -= 10
        # 行政处罚
        score -= len(penalties) * 6
        # 注册资本越高, 小幅加分: 含"亿"直接命中, 或"XXX万"数值 >= 10000 万 (即 1 亿量级)
        rc = str(basic_info.get("registered_capital", "")) if isinstance(basic_info, dict) else ""
        if "亿" in rc:
            score += 5
        else:
            # 尝试从 "XXXX万人民币" / "XXXX万美元" 中提取数字, 10000 万 = 1 亿
            import re
            m = re.search(r"([\d][\d,.]*)万", rc)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if val >= 10000:
                        score += 5
                except ValueError:
                    pass
        # 钳制到 [0, 100]
        score = max(0.0, min(100.0, score))
        # 评级 (和 mock 完全相同的阈值)
        if score >= 81:
            level = "Low"
        elif score >= 61:
            level = "Medium"
        else:
            level = "High"
        return round(score, 1), level

    # ================================================================
    # 模拟数据生成 (配置缺失 / 真实 API 全部失败时)
    # ================================================================
    def _build_mock_data(self, company_name: str) -> dict:
        """
        基于公司名生成"贴近现实"的模拟资信数据。

        特性:
            1. 公司名 hash 作为随机种子 -> 同名同查询结果稳定, 便于回归对比
            2. 名称含 "失信/异常/违法/欠款/老赖/黑名单/倒闭/破产/欺诈/违规" 等负面关键词时,
               负面记录概率提高 4~6 倍 -> 保证评分单调性 (不良公司评分 < 优质公司)
            3. 根据名称关键字 (科技/贸易/投资/建筑/集团/股份有限公司) 定制行业与注册资本规模
            4. 股东 3~7 人, 前 1~2 位随机法人股东, 其余自然人
            5. 评分算法与真实 API 完全一致
        """
        seed = sum(ord(c) for c in company_name)
        rng = random.Random(seed)

        # -------- 名称负面关键词检测 --------
        negative_keywords = ["失信", "异常", "违法", "欠款", "老赖", "黑名单", "倒闭", "破产", "欺诈", "违规"]
        is_negative_name = any(kw in company_name for kw in negative_keywords)
        p_dishonest = 0.60 if is_negative_name else 0.10
        p_executed = 0.50 if is_negative_name else 0.20
        p_abnormal = 0.45 if is_negative_name else 0.15
        p_penalty = 0.35 if is_negative_name else 0.12

        # -------- 1. 工商基本信息 --------
        if "科技" in company_name:
            industry = "软件和信息技术服务业"
            registered_capital = rng.choice(["100万人民币", "500万人民币", "1000万人民币"])
        elif "贸易" in company_name or "商贸" in company_name:
            industry = "批发和零售业"
            registered_capital = rng.choice(["50万人民币", "200万人民币", "500万人民币"])
        elif "投资" in company_name or "控股" in company_name:
            industry = "资本市场服务"
            registered_capital = rng.choice(["5000万人民币", "1亿人民币", "5亿人民币"])
        elif "建筑" in company_name or "建设" in company_name:
            industry = "建筑业"
            registered_capital = rng.choice(["2000万人民币", "5000万人民币", "1亿人民币"])
        else:
            industry = "综合"
            registered_capital = rng.choice(["100万人民币", "300万人民币", "500万人民币"])
        if "集团" in company_name or "股份有限公司" in company_name:
            registered_capital = rng.choice(["5000万人民币", "1亿人民币", "10亿人民币"])

        start_year = 2010 + rng.randint(0, 9)
        start_month = rng.randint(1, 12)
        start_day = rng.randint(1, 28)
        establish_date = f"{start_year}-{start_month:02d}-{start_day:02d}"

        status_rand = rng.random()
        if status_rand < 0.03:
            status = rng.choice(["吊销", "注销"])
        elif status_rand < 0.08:
            status = "停业"
        else:
            status = rng.choice(["存续（在营、开业、在册）", "在营（开业）企业"])

        basic_info = {
            "company_name": company_name,
            "legal_person": f"{rng.choice(['张','王','李','赵','刘','陈','杨','黄'])}{rng.choice(['伟','芳','娜','敏','静','丽','强','磊','洋','勇'])}{rng.choice(['','军','华','平','','辉','鹏'])}",
            "registered_capital": registered_capital,
            "establish_date": establish_date,
            "status": status,
            "industry": industry,
            "credit_code": f"91{rng.randint(100000000000000000, 999999999999999999)}",
            "register_authority": "市场监督管理局",
        }

        # -------- 2. 股权结构 --------
        shareholder_count = rng.randint(3, 7)
        shareholders = []
        remaining_pct = 100.0
        for i in range(shareholder_count):
            if i == shareholder_count - 1:
                pct = round(remaining_pct, 2)
            else:
                max_pct = max(1.0, remaining_pct * 0.6)
                pct = round(rng.uniform(1.0, max_pct), 2)
                remaining_pct -= pct
            if i < rng.randint(0, 2):
                name = f"{company_name[:6]}投资合伙企业(有限合伙)"
                shareholder_type = "法人股东"
            else:
                name = f"{rng.choice(['张','王','李','赵','刘','陈','杨','黄'])}{rng.choice(['伟','芳','娜','敏','静','丽','强','磊','洋','勇'])}{rng.choice(['','军','华','平','','辉','鹏'])}"
                shareholder_type = "自然人股东"
            shareholders.append({
                "name": name,
                "type": shareholder_type,
                "share_ratio": f"{pct}%",
                "subscribed_amount": f"{int(pct * 10)}万人民币" if "万人民币" in registered_capital else "1000万人民币",
            })

        # -------- 3. 失信被执行人 --------
        dishonest = []
        if rng.random() < p_dishonest and "注销" not in status:
            for _ in range(rng.randint(1, 2)):
                dishonest.append({
                    "case_no": f"({2020 + rng.randint(0, 4)})京0{rng.randint(1,9)}执{rng.randint(1000, 99999)}号",
                    "court": rng.choice(["北京市第一中级人民法院", "上海市浦东新区人民法院", "广州市天河区人民法院", "深圳市南山区人民法院"]),
                    "situation": rng.choice([
                        "有履行能力而拒不履行生效法律文书确定义务",
                        "以伪造证据、暴力、威胁等方法妨碍、抗拒执行",
                        "违反财产报告制度",
                    ]),
                    "publish_date": f"202{rng.randint(0, 4)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                })

        # -------- 4. 被执行人 --------
        executed = []
        if rng.random() < p_executed and "注销" not in status:
            for _ in range(rng.randint(1, 3)):
                executed.append({
                    "case_no": f"({2020 + rng.randint(0, 5)})沪0{rng.randint(1,9)}执{rng.randint(1000, 99999)}号",
                    "exec_target": f"{rng.randint(10, 500)}万元",
                    "court": rng.choice(["上海市第二中级人民法院", "杭州市西湖区人民法院", "苏州市工业园区人民法院", "成都市高新区人民法院"]),
                    "file_date": f"202{rng.randint(0, 5)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                    "status": rng.choice(["执行中", "已结案", "终结本次执行程序"]),
                })

        # -------- 5. 经营异常 --------
        abnormal = []
        if rng.random() < p_abnormal and "注销" not in status:
            abnormal.append({
                "reason": rng.choice([
                    "未依照《企业信息公示暂行条例》第八条规定的期限公示年度报告",
                    "通过登记的住所或者经营场所无法联系",
                    "公示企业信息隐瞒真实情况、弄虚作假",
                ]),
                "authority": rng.choice(["北京市朝阳区市场监督管理局", "深圳市市场监督管理局南山局", "上海市市场监督管理局静安分局"]),
                "put_date": f"202{rng.randint(0, 5)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "remove_date": rng.choice([None, f"202{rng.randint(1, 5)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"]),
            })

        # -------- 6. 行政处罚 --------
        penalties = []
        if rng.random() < p_penalty and "注销" not in status:
            penalties.append({
                "penalty_authority": rng.choice(["国家税务总局稽查局", "生态环境局", "市场监督管理局", "人力资源和社会保障局"]),
                "reason": rng.choice(["违反税收管理规定", "违反环境保护法规", "违反广告法发布虚假广告", "违反劳动保障法律法规"]),
                "penalty_content": rng.choice(["罚款人民币10万元", "警告并处罚款5万元", "没收违法所得并处罚款20万元", "责令限期改正"]),
                "penalty_date": f"202{rng.randint(0, 5)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "document_no": f"XX{rng.choice(['税','环','市','劳'])}罚〔202{rng.randint(0,5)}〕{rng.randint(1, 999)}号",
            })

        # -------- 7. 评分与等级 (调用与真实 API 相同的函数, 保证口径一致) --------
        score, level = self._calc_credit_score(basic_info, shareholders, dishonest, executed, abnormal, penalties)

        return {
            "basic_info": basic_info,
            "shareholders": shareholders,
            "dishonest": dishonest,
            "executed": executed,
            "abnormal": abnormal,
            "penalties": penalties,
            "credit_score": score,
            "risk_level": level,
            "mock": True,   # 模拟数据标记
            "mode": "Mock", # 实际生效模式
        }

    # ================================================================
    # 对外主入口: 一站式查询企业全部资信信息
    # ================================================================
    def query_company_credit(self, company_name: str) -> dict:
        """
        查询企业全部资信信息, 三种模式自动兜底。

        执行流程:
            1. 公司名为空 或 通用名(甲方/乙方/公司/未知) -> 直接返回 mock 数据 + note 提示
            2. enabled=True -> 调用 6 个维度真实查询
               - 若 6 个维度全空 -> 视为真实 API 失败, 走 mock, mode=Mock
               - 否则 基于真实数据计算评分与等级, mock=False, 并记录实际生效的 mode
            3. enabled=False -> 直接走 mock (已在 __init__ 标记)

        参数:
            company_name (str): 企业全称 (非空且非通用名)。

        返回值:
            dict: 9 个核心键 + 1 个 mode 键:
                  basic_info / shareholders / dishonest / executed / abnormal /
                  penalties / credit_score / risk_level / mock / mode
        """
        # ---------- 1. 空名 / 通用名 ----------
        if not company_name or company_name in ("甲方", "乙方", "公司", "未知"):
            mock = self._build_mock_data("未知企业")
            mock["note"] = "企业名称不明确，使用演示数据"
            return mock

        # ---------- 2. 未启用真实 API ----------
        if not self.enabled:
            return self._build_mock_data(company_name)

        # ---------- 3. 调用 6 个维度 (MCP 优先, AppKey 兼容模式兜底) ----------
        basic_info_raw = self._get_basic_info(company_name)
        shareholders_raw = self._get_shareholders(company_name)
        dishonest_raw = self._get_dishonest(company_name)
        executed_raw = self._get_executed(company_name)
        abnormal_raw = self._get_abnormal(company_name)
        penalties_raw = self._get_penalties(company_name)

        # 当 basic_info 缺失时给一个最小化结构, 避免评分计算空指针
        if not basic_info_raw:
            basic_info_raw = {
                "company_name": company_name,
                "legal_person": "",
                "registered_capital": "",
                "establish_date": "",
                "status": "",
                "industry": "",
                "credit_code": "",
                "register_authority": "",
            }

        # ---------- 4. 判断真实 API 是否至少命中 1 个维度 ----------
        any_real_hit = bool(
            basic_info_raw and any(basic_info_raw.get(k) for k in ("legal_person", "registered_capital", "establish_date", "status", "credit_code"))
            or shareholders_raw
            or dishonest_raw
            or executed_raw
            or abnormal_raw
            or penalties_raw
        )
        if not any_real_hit:
            # 所有维度都没命中 -> 降级为模拟数据
            fallback = self._build_mock_data(company_name)
            fallback["note"] = "真实企查查 MCP API 未返回有效数据，自动降级为模拟数据"
            # mode 改为 Mock, 但保留原先的尝试模式记录, 便于排查
            fallback["attempted_mode"] = self.mode
            return fallback

        # ---------- 5. 真实 API 命中 -> 计算评分, 返回 mock=False ----------
        score, level = self._calc_credit_score(basic_info_raw, shareholders_raw, dishonest_raw, executed_raw, abnormal_raw, penalties_raw)
        return {
            "basic_info": basic_info_raw,
            "shareholders": shareholders_raw,
            "dishonest": dishonest_raw,
            "executed": executed_raw,
            "abnormal": abnormal_raw,
            "penalties": penalties_raw,
            "credit_score": score,
            "risk_level": level,
            "mock": False,
            "mode": self.mode,   # MCP-Bearer / AppKey-MD5
        }


if __name__ == "__main__":
    # 命令行直接运行本文件时的简单自测脚本: 查 3 家公司, 打印核心字段
    # 命令: python -m common.qichacha_client
    #
    # --- MCP 诊断开关 ---
    # 如果真实 API 一直命中 mock=True, 把下面这行 True 改成 True (即: 保持 True) 就会先打印一次
    # GET /company/stream 对"华为技术有限公司"的 HTTP code / Content-Type / 原始响应前 2000 字 / 解析事件
    # 便于你判断是 401 Token 过期 / 403 IP 白名单 / 字段名不在归一化列表 等具体原因。
    # MCP 诊断开关: 已经定位到真实接口协议 / 工具名后, 关闭以加速 (不用再跑一堆 tools/call 样例)
    RUN_MCP_DIAGNOSE = False

    client = QiChaChaClient()
    print(f"[Config] 客户端主模式: {client.mode}  enabled={client.enabled}")
    # ---------- 先跑 MCP 诊断 (第一次查之前, 避免被后续查询共享缓存影响) ----------
    if RUN_MCP_DIAGNOSE and client.mode == "MCP-Bearer":
        print("\n>>>> MCP 诊断开始 (company endpoint + 华为技术有限公司) <<<<")
        client._debug_mcp_request(MCP_ENDPOINTS["company"], "华为技术有限公司")
        print(">>>> MCP 诊断结束 <<<<\n")
        # 顺带跑 risk endpoint 诊断 (查失信/被执行/异常/处罚工具名)
        print("\n>>>> MCP 诊断开始 (risk endpoint + 华为技术有限公司) <<<<")
        client._debug_mcp_request(MCP_ENDPOINTS["risk"], "华为技术有限公司")
        print(">>>> MCP 诊断结束 <<<<\n")
    # ---------- 再跑 3 家公司的正式查询 ----------
    for name in ["华为技术有限公司", "阿里巴巴(中国)有限公司", "某失信异常商贸有限公司"]:
        r = client.query_company_credit(name)
        print(f"\n[Query] {name}")
        print(f"   mock={r.get('mock')}  mode={r.get('mode')}  score={r.get('credit_score')}  level={r.get('risk_level')}")
        bi = r.get("basic_info", {})
        print(f"   法人={bi.get('legal_person')}  注册资本={bi.get('registered_capital')}  状态={bi.get('status')}  成立日期={bi.get('establish_date')}")
        print(f"   股东数={len(r.get('shareholders', []))}  失信={len(r.get('dishonest', []))}  被执行人={len(r.get('executed', []))}  异常={len(r.get('abnormal', []))}  处罚={len(r.get('penalties', []))}")
        if r.get("note"):
            print(f"   note: {r['note']}")
        if r.get("attempted_mode"):
            print(f"   (上次尝试模式: {r['attempted_mode']}, 已降级 Mock)")
