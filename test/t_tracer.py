# -*- coding: utf-8 -*-
"""LangGraph 状态流转追踪器

【这个文件是整个测试体系的"眼睛"】

    后端是一条由 6 个子图组合、约 40 个节点构成的 LangGraph 工作流。
    只测"最终输出对不对"远远不够 —— 输出错了, 你不知道是哪一个节点、
    哪一次状态流转出的错。本追踪器解决三件事:

    1. **节点级执行轨迹**: 用 stream(subgraphs=True) 拿到 (子图命名空间, 节点名)
       的精确执行序列, 压平后与主图期望路径比对 → "状态是否正常流转"的直接证据。

    2. **节点级耗时**: 用 chunk 到达时间差近似节点耗时 (串行执行下精确),
       直接定位链路瓶颈, 而不是笼统地说"系统有点慢"。

    3. **节点级日志**: 把每个节点 print 的诊断信息 (挂载了哪些知识源、召回多少条、
       质量分多少) 按节点窗口切片保存 —— 这是定位问题的第一手材料。

【SAFE MODE 打桩】
    测试必须可重复且无副作用。SAFE MODE 下替换掉三个会产生真实外部行为的调用:
      - 小红书自动发布 (Playwright 开浏览器发帖) → 桩
      - 即梦图片生成 API (按次计费)             → 返回 None, 走本地占位图降级
      - 企查查资信查询 (付费 MCP)                → 返回模拟数据
    打桩点是**函数体内运行时查找的符号**, 因此 patch 生效 (图编译期已绑定的
    节点函数不需要改动)。
"""
import os
import io
import sys
import time
import uuid
import threading
import traceback
from contextlib import redirect_stdout

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_TEST_DIR), _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from t_config import apply_env, SAFE_MODE, CASE_TIMEOUT_SEC  # noqa: E402

apply_env()

_GRAPH = None
_GRAPH_LOCK = threading.Lock()


# ============================================================================
# 一、线程安全的 stdout 分流器 (采集节点日志)
# ============================================================================
class _Tee(io.TextIOBase):
    """把 print 同时写进内存缓冲和真实 stdout, 支持按字节偏移切片"""

    def __init__(self, real):
        self._real = real
        self._buf = io.StringIO()
        self._lock = threading.Lock()

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        with self._lock:
            self._buf.write(s)
            n = len(s)
        try:
            self._real.write(s)
        except Exception:
            pass
        return n

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    def mark(self):
        with self._lock:
            return self._buf.tell()

    def slice_since(self, pos):
        with self._lock:
            cur = self._buf.tell()
            self._buf.seek(pos)
            s = self._buf.read(cur - pos)
            self._buf.seek(cur)
            return s

    def all(self):
        with self._lock:
            cur = self._buf.tell()
            self._buf.seek(0)
            s = self._buf.read()
            self._buf.seek(cur)
            return s


# ============================================================================
# 二、LLM token 统计
#
# 实现方式: 在 LLM 客户端实例上包一层 (object.__setattr__ 绕过 pydantic 校验)。
# 为什么不用 ContextVar 版的 usage callback: 图在 worker 线程里跑, ContextVar
# 的注册/读取跨线程传播不稳定; 而实例方法包装是"跟随对象"的, 任何持有
# my_llm 引用的节点 (全部节点都是 from common.llm import my_llm) 都会经过包装器。
# ============================================================================
class TokenCollector:
    """线程安全的 LLM token 累加器, 支持按用例启停"""

    def __init__(self):
        self._lock = threading.Lock()
        self._prompt = 0
        self._completion = 0
        self._calls = 0
        self._installed = False
        self._orig = {}

    def install(self):
        if self._installed:
            return
        try:
            from common.llm import my_llm
        except Exception:
            return
        for meth in ("invoke", "ainvoke", "batch", "abatch"):
            orig = getattr(type(my_llm), meth, None)
            if orig is None:
                continue
            self._orig[meth] = orig
            setattr(self, f"_bound_{meth}", getattr(my_llm, meth))
        self._llm = my_llm
        # 逐个方法包装 (用闭包捕获 orig)
        self._wrap("invoke")
        self._wrap("ainvoke")
        self._wrap("batch")
        self._wrap("abatch")
        self._installed = True

    def _wrap(self, meth):
        """包装单个方法: 同步方法直接包, 异步方法包一层协程"""
        llm = getattr(self, "_llm", None)
        if llm is None:
            return
        bound = getattr(llm, meth, None)
        if bound is None:
            return
        import inspect
        coll = self

        if meth in ("invoke", "batch"):
            def wrapper(*a, __o=bound, __m=meth, **kw):
                r = __o(*a, **kw)
                coll._harvest(r, __m)
                return r
        else:
            async def wrapper(*a, __o=bound, __m=meth, **kw):
                r = await __o(*a, **kw)
                coll._harvest(r, __m)
                return r
        try:
            object.__setattr__(llm, meth, wrapper)
        except Exception:
            pass

    def _harvest(self, resp, meth):
        """从响应对象里抠 token 用量 (兼容 AIMessage / 批量返回 list)"""
        try:
            items = resp if isinstance(resp, list) else [resp]
            with self._lock:
                self._calls += 1
                for it in items:
                    um = getattr(it, "usage_metadata", None) or {}
                    if um:
                        self._prompt += int(um.get("input_tokens", 0) or 0)
                        self._completion += int(um.get("output_tokens", 0) or 0)
                        continue
                    rm = getattr(it, "response_metadata", None) or {}
                    tu = rm.get("token_usage") or rm.get("usage") or {}
                    if tu:
                        self._prompt += int(tu.get("prompt_tokens", 0) or 0)
                        self._completion += int(tu.get("completion_tokens", 0) or 0)
        except Exception:
            pass

    def reset(self):
        with self._lock:
            self._prompt = 0
            self._completion = 0
            self._calls = 0

    def snapshot(self):
        with self._lock:
            return {"prompt": self._prompt, "completion": self._completion,
                    "calls": self._calls}


_COLLECTOR = TokenCollector()


# ============================================================================
# 三、SAFE MODE 打桩
# ============================================================================
def apply_safe_mode(verbose=True):
    """替换会产生真实外部行为 / 真实计费的调用"""
    patched = []

    # ① 小红书真实发布 (Playwright 开浏览器)
    try:
        from __004__langgraph_more_nodes.nodes.xhs_publish_nodes import (
            xhs_auto_publish_node as _m,
        )

        async def _stub_publish(images, title, content):
            print(f"  [SAFE MODE] 已跳过真实发布, 收到 {len(images or [])} 张图 / "
                  f"标题《{title}》")
            return False, "SAFE_MODE_SKIPPED"

        _m.auto_publish_xiaohongshu = _stub_publish
        patched.append("xhs.auto_publish_xiaohongshu")
    except Exception as e:
        if verbose:
            print(f"  ⚠️ 打桩失败 xhs_publish: {e}")

    # ② 即梦图片生成 API (按次计费) → 降级为本地占位图
    try:
        from __004__langgraph_more_nodes.nodes.xhs_publish_nodes import (
            image_generate_node as _im,
        )

        def _stub_generate_image(prompt, output_path):
            print("  [SAFE MODE] 跳过即梦付费生图, 走本地占位图降级")
            return None
        _im.generate_image = _stub_generate_image
        patched.append("xhs.generate_image")
    except Exception as e:
        if verbose:
            print(f"  ⚠️ 打桩失败 image_generate: {e}")

    # ③ 企查查资信查询 (付费 MCP / 网络)
    try:
        from common.qichacha_client import QiChaChaClient

        def _stub_credit(self, company):
            print(f"  [SAFE MODE] 跳过企查查真实查询: {company}")
            return {"basic": {"name": company, "status": "存续"},
                    "risk": {"abnormal": [], "dishonest": [], "executed": []},
                    "_mock": True}
        QiChaChaClient.query_company_credit = _stub_credit
        patched.append("qichacha.query_company_credit")
    except Exception as e:
        if verbose:
            print(f"  ⚠️ 打桩失败 qichacha: {e}")

    if verbose and patched:
        print(f"  ✅ SAFE MODE 已打桩: {', '.join(patched)}")
    return patched


# ============================================================================
# 四、图加载 (单例)
# ============================================================================
def get_graph():
    global _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            from __004__langgraph_more_nodes.langgraph_main import graph
            _GRAPH = graph
        return _GRAPH


def _clean_ns(ns):
    """剥离命名空间里的 uuid 后缀: 'r_retrieval:486eaa95-...' → 'r_retrieval'"""
    if not ns:
        return ()
    out = []
    for part in ns:
        out.append(str(part).split(":")[0] if ":" in str(part) else str(part))
    return tuple(out)


# ============================================================================
# 五、核心: 追踪一次图执行
# ============================================================================
def trace_run(init_state, timeout_sec=None, verbose=False):
    """执行图并采集完整可观测数据

    Returns:
        dict: {
            node_trace: [{seq, name, full_name, subgraph, namespace, start_sec,
                          duration_sec, log, error}],
            final_state: dict,
            latency_sec, tokens_prompt, tokens_completion,
            error, error_type, timed_out, interrupted
        }
    """
    timeout_sec = timeout_sec or CASE_TIMEOUT_SEC
    graph = get_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    node_trace = []
    result = {
        "node_trace": node_trace, "final_state": {}, "thread_id": thread_id,
        "latency_sec": None, "tokens_prompt": 0, "tokens_completion": 0,
        "error": None, "error_type": None, "timed_out": False,
        "interrupted": False, "interrupt_payloads": [],
    }

    tee = _Tee(sys.stdout)
    real_stdout = sys.stdout
    t0 = time.time()
    err_holder = {}
    finished = threading.Event()

    def _worker():
        sys.stdout = tee
        _COLLECTOR.reset()
        try:
            prev = time.time()
            mark = tee.mark()
            for ns, chunk in graph.stream(init_state, config=config,
                                          subgraphs=True):
                now = time.time()
                ns_clean = _clean_ns(ns)
                for node_name, payload in (chunk or {}).items():
                    log = tee.slice_since(mark)
                    mark = tee.mark()
                    node_trace.append({
                        "seq": len(node_trace) + 1,
                        "name": node_name,
                        "full_name": "::".join(list(ns_clean) + [node_name])
                                      if ns_clean else node_name,
                        "subgraph": ns_clean[-1] if ns_clean else "(main)",
                        "namespace": list(ns_clean),
                        "start_sec": round(prev - t0, 3),
                        "duration_sec": round(now - prev, 3),
                        "log": log[-4000:],
                        "error": None,
                        "state_delta_keys": (sorted(payload.keys())
                                             if isinstance(payload, dict) else []),
                    })
                    if isinstance(payload, dict) and "__interrupt__" in payload:
                        result["interrupted"] = True
                prev = now
        except Exception as e:
            err_holder["e"] = e
            err_holder["tb"] = traceback.format_exc()
        finally:
            snap_tk = _COLLECTOR.snapshot()
            result["tokens_prompt"] = snap_tk["prompt"]
            result["tokens_completion"] = snap_tk["completion"]
            result["llm_calls"] = snap_tk["calls"]
            sys.stdout = real_stdout
            finished.set()

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    finished.wait(timeout=timeout_sec)

    if not finished.is_set():
        # 超时: 记录已完成的节点, 标记超时 (线程为 daemon, 进程退出即回收)
        result["timed_out"] = True
        result["error_type"] = "TIMEOUT"
        result["error"] = f"执行超过 {timeout_sec}s 未完成"
        if node_trace:
            node_trace[-1]["error"] = "TIMEOUT (该节点及后续未执行完)"
        result["latency_sec"] = round(time.time() - t0, 2)
        result["stdout"] = tee.all()[-6000:]
        return result

    if "e" in err_holder:
        e = err_holder["e"]
        result["error"] = f"{type(e).__name__}: {e}"
        result["error_type"] = "EXCEPTION"
        result["traceback"] = err_holder.get("tb", "")[-4000:]
        if node_trace:
            node_trace[-1]["error"] = result["error"]

    result["latency_sec"] = round(time.time() - t0, 2)

    # 取最终完整状态 (checkpointer 已落盘, 零额外 LLM 成本)
    try:
        snap = graph.get_state(config)
        vals = dict(snap.values or {})
        # 剥掉不可序列化的对象
        safe = {}
        for k, v in vals.items():
            try:
                import json
                json.dumps(v, ensure_ascii=False, default=str)
                safe[k] = v
            except Exception:
                safe[k] = str(v)[:2000]
        result["final_state"] = safe
        if getattr(snap, "tasks", None):
            for t in snap.tasks:
                if getattr(t, "interrupts", None):
                    result["interrupted"] = True
    except Exception as e:
        result["state_error"] = f"{type(e).__name__}: {e}"

    result["stdout"] = tee.all()[-8000:]
    return result


# ============================================================================
# 六、非图任务 (历史记录) 的执行入口
# ============================================================================
def run_history_op(op, payload, verbose=False):
    """执行 HistoryStore 的单个行为契约

    返回 (passed: bool, detail: str, extra: dict)
    """
    from common.history_store import store
    extra = {}

    try:
        if op == "store_get_roundtrip":
            rec = store.store(**payload)
            got = store.get(rec["id"])
            ok = (got is not None
                  and got["task_type"] == payload["task_type"]
                  and got["title"] == payload["title"]
                  and got["user_input"] == payload["user_input"]
                  and got["result"] == payload["result"])
            extra["record_id"] = rec["id"]
            store.delete(rec["id"])
            return ok, ("字段往返一致" if ok else
                        f"回读不一致: {str(got)[:200]}"), extra

        if op == "list_pagination":
            tag = payload["task_type"]
            n, page, size = payload["n"], payload["page"], payload["page_size"]
            ids = [store.store(task_type=tag, title=f"p{i}") ["id"] for i in range(n)]
            rows, total = store.list(task_type=tag, page=page, page_size=size)
            ok = (total == n and len(rows) == size)
            for i in ids:
                store.delete(i)
            return ok, (f"total={total}(期望{n}), 第{page}页返回{len(rows)}条(期望{size})"), extra

        if op == "filter_by_task_type":
            tag = payload["task_type"]
            created = {}
            for t, cnt in payload["types"]:
                created[t] = [store.store(task_type=f"{tag}_{t}", title=f"{t}{i}")["id"]
                              for i in range(cnt)]
            rows, total = store.list(task_type=f"{tag}_contract")
            ok = (total == dict(payload["types"])["contract"]
                  and all(r["task_type"] == f"{tag}_contract" for r in rows))
            for lst in created.values():
                for i in lst:
                    store.delete(i)
            return ok, f"筛选 contract 得 {total} 条, 无类型串扰={ok}", extra

        if op == "toggle_star":
            tag = payload["task_type"]
            rid = store.store(task_type=tag, title="star")["id"]
            s1 = store.toggle_star(rid)
            s2 = store.toggle_star(rid)
            s_none = store.toggle_star(99999999)
            store.delete(rid)
            ok = (s1 is True and s2 is False and s_none is None)
            return ok, f"0→{s1}→{s2}, 不存在id返回 {s_none}", extra

        if op == "delete":
            tag = payload["task_type"]
            rid = store.store(task_type=tag, title="del")["id"]
            d1 = store.delete(rid)
            d2 = store.delete(rid)
            ok = (d1 is True and d2 is False)
            return ok, f"首次删除={d1}, 重复删除={d2}", extra

        if op == "boundary_payload":
            tag = payload["task_type"]
            rec = store.store(task_type=tag, title="", user_input={}, result={},
                              summary=payload["summary"])
            got = store.get(rec["id"])
            ok = (got is not None and len(got["summary"]) <= 200
                  and got["user_input"] == {})
            extra["summary_len"] = len(got["summary"]) if got else -1
            store.delete(rec["id"])
            return ok, f"空 payload 不报错, summary 截断为 {extra['summary_len']} 字", extra

        if op == "e2e_graph_persist":
            from t_tracer import trace_run
            r = trace_run({"input": payload["query"],
                           "task_type": payload["task_type"]})
            out = (r.get("final_state") or {}).get("output", "")
            cits = (r.get("final_state") or {}).get("citations", []) or []
            rec = store.store(task_type=payload["task_type"],
                              title=payload["query"][:40],
                              user_input={"query": payload["query"]},
                              result={"output": out, "citations": cits},
                              summary=(out or "")[:200])
            got = store.get(rec["id"])
            ok = (bool(out) and got is not None
                  and got["result"].get("output") == out
                  and len(got["result"].get("citations", [])) == len(cits))
            extra.update({"graph_latency_sec": r.get("latency_sec"),
                          "citation_count": len(cits),
                          "tokens": (r.get("tokens_prompt", 0)
                                     + r.get("tokens_completion", 0)),
                          "record_id": rec["id"]})
            store.delete(rec["id"])
            return ok, (f"graph 输出 {len(out)} 字 / {len(cits)} 条引用, "
                        f"落库回读一致={ok}"), extra

        return False, f"未知 op: {op}", extra
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", extra


if __name__ == "__main__":
    apply_safe_mode()
    _COLLECTOR.install()
    r = trace_run({"input": "设立个人独资企业应当具备哪些条件？",
                   "task_type": "legal_research"}, verbose=True)
    print("\n" + "=" * 60)
    print("节点轨迹:")
    for nd in r["node_trace"]:
        print(f"  {nd['seq']:>2}. {nd['full_name']:<55} {nd['duration_sec']:>7}s")
    print(f"\n总耗时 {r['latency_sec']}s | LLM 调用 {r.get('llm_calls')} 次 | "
          f"tokens {r['tokens_prompt']}/{r['tokens_completion']}")
    print(f"citations: {len(r['final_state'].get('citations', []) or [])}")
