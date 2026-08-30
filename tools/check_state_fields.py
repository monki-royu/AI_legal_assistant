# -*- coding: utf-8 -*-
"""AgentState 字段契约护栏 (AST 静态检查)

【为什么需要它】
    LangGraph 的 StateGraph 以 TypedDict 的 __annotations__ 作为 channel 集合。
    节点 return 了**未在 AgentState 声明**的键时，LangGraph **不报错、不告警、
    直接丢弃** —— 表现为"上游算了、下游读不到"，且不产生任何异常，极难排查。

    本项目已两次踩坑：
      1) 2026-08 api_sources 未声明 → 北大法宝付费门禁永不生效（已修复）
      2) 2026-08-29 新增 27 个未声明字段 → 检索三阶段断链 / 合规一票否决失效 /
         文书生成四级级联失效（本轮修复）
    本脚本是防止第三次复发的**唯一自动化手段**，建议挂 pre-commit 与 CI。

【检查逻辑】
    1. AST 扫描 __004__langgraph_more_nodes/nodes/**/*_node.py
    2. 提取节点函数的「写入键」：
         - return {...}              顶层字面量键
         - return <var>              <var> 是函数内构造的 dict 字面量 / 被下标赋过值
         - state["x"] = ...          原地写共享 state
    3. 提取「读取键」：state.get("x") / state["x"]（Load 上下文）
    4. 与 AgentState.__annotations__ 求差集 → 未声明字段
    5. 按是否有消费方分三档输出

【三档输出】
    [BLOCK]  未声明 + 有消费方        → 必须补声明（功能已断）
    [TODO]   未声明 + 已标 TODO       → 编排半成品，允许，但 TODO 超 90 天升级为 WARN
    [WARN]   未声明 + 无消费方无 TODO  → 建议删除写入（或补 TODO 说明设计意图）

    TODO 标注格式（写在 AgentState 字段声明行的尾部注释或紧邻上方注释块）：
        can_sign: str  # TODO: 待 final_delivery_node 消费 (2026-08-29)

【退出码】
    0  无 [BLOCK] 且无 [WARN]
    1  存在 [BLOCK] 或 [WARN]

【用法】
    python tools/check_state_fields.py              # 人类可读报告
    python tools/check_state_fields.py --quiet      # 只打印问题字段，无问题则静默
    python tools/check_state_fields.py --json       # 机器可读（CI 用）
"""

import ast
import io
import os
import re
import sys
import json
from datetime import date, datetime
from typing import Dict, List, Set, Tuple

# Windows 控制台默认 GBK，中文与 emoji 会触发 UnicodeEncodeError
if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扫描范围：只扫节点实现，子图/主图不直接产出 state 键
SCAN_DIRS = [os.path.join(ROOT, "__004__langgraph_more_nodes", "nodes")]

STATE_FILE = os.path.join(ROOT, "__004__langgraph_more_nodes", "agent_state.py")
STATE_CLASS = "AgentState"

# 不以 _node 结尾但确实返回 state 的节点函数（历史命名遗留）
EXTRA_NODE_FUNCS = {"qa_intent_classify", "input_source_router"}

# TODO 标注格式：# TODO: 待 <节点名> 消费 (YYYY-MM-DD)
TODO_RE = re.compile(r"#\s*TODO:\s*待\s*(?P<owner>[^\s(]+)\s*消费\s*\(\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\)")
TODO_STALE_DAYS = 90


# =============================================================================
# 1. 解析 AgentState
# =============================================================================

def parse_state_fields(path: str) -> Tuple[Dict[str, int], Dict[str, str]]:
    """返回 (字段名 -> 声明行号, 字段名 -> TODO 原文或空串)"""
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    lines = source.splitlines()

    fields: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == STATE_CLASS:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields[stmt.target.id] = stmt.lineno
            break
    else:
        raise RuntimeError(f"未在 {path} 中找到 class {STATE_CLASS}")

    # TODO 标注：优先取声明行尾注释，其次向上找紧邻的注释块（最多 3 行）
    todos: Dict[str, str] = {}
    for name, lineno in fields.items():
        found = ""
        # 声明行可能与赋值同行，也可能跨行；从声明行开始向上扫 4 行
        for i in range(lineno - 1, max(lineno - 5, -1), -1):
            line = lines[i]
            if "#" in line:
                m = TODO_RE.search(line)
                if m:
                    found = m.group(0)
                    break
            # 遇到非注释、非字段声明行就停止向上找
            if i != lineno - 1 and line.strip() and not line.strip().startswith("#"):
                break
        todos[name] = found
    return fields, todos


# =============================================================================
# 2. 扫描节点函数的写入键
# =============================================================================

class NodeVisitor(ast.NodeVisitor):
    """提取单个函数体内：return 的 dict 键 + state[...] 下标赋值键"""

    def __init__(self, func: ast.FunctionDef, state_var: str, rel_path: str):
        self.func = func
        self.state_var = state_var
        self.rel_path = rel_path
        self.writes: Dict[str, int] = {}          # key -> lineno
        self.dynamic_return = False               # return 了 **解包 或 无法静态解析的对象

    def record(self, key: str, lineno: int):
        self.writes.setdefault(key, lineno)

    # ---- 收集函数内 dict 字面量赋值：result = {...} ----
    def _collect_local_dicts(self) -> Dict[str, Set[str]]:
        local: Dict[str, Set[str]] = {}
        for node in ast.walk(self.func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
                if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Dict):
                    keys = {
                        k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
                    local.setdefault(tgt.id, set()).update(keys)
        return local

    # ---- 收集对已跟踪 dict 变量的下标赋值：result["x"] = ... ----
    def _collect_subscript_writes(self, tracked: Set[str]) -> Dict[str, int]:
        found: Dict[str, int] = {}
        for node in ast.walk(self.func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt = node.targets[0]
                if (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id in tracked
                    and isinstance(tgt.slice, ast.Constant)
                    and isinstance(tgt.slice.value, str)
                ):
                    found.setdefault(tgt.slice.value, node.lineno)
        return found

    def run(self):
        local = self._collect_local_dicts()
        # 也把 state 本身当作可跟踪对象（原地写 state["x"] = ...）
        tracked = set(local.keys()) | {self.state_var}

        for node in ast.walk(self.func):
            if not isinstance(node, ast.Return) or node.value is None:
                continue

            val = node.value
            # return {...}
            if isinstance(val, ast.Dict):
                for k in val.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        self.record(k.value, node.lineno)
                    else:
                        self.dynamic_return = True
            # return <var>  → 解析函数内构造的 dict
            elif isinstance(val, ast.Name) and val.id in local:
                for k in local[val.id]:
                    self.record(k, node.lineno)
            # return state（整包回写）：键由下标赋值决定，下面统一收集
            elif isinstance(val, ast.Name) and val.id == self.state_var:
                pass
            else:
                self.dynamic_return = True

        for key, lineno in self._collect_subscript_writes(tracked).items():
            self.record(key, lineno)


def _is_node_function(fn: ast.FunctionDef) -> bool:
    return fn.name.endswith("_node") or fn.name in EXTRA_NODE_FUNCS


def _first_arg_name(fn: ast.FunctionDef) -> str:
    """节点函数第一个参数即 state 对象（命名统一为 state，但这里不硬编码）"""
    if fn.args.args:
        return fn.args.args[0].arg
    return "state"


def scan_node_writes() -> Tuple[Dict[str, List[Tuple[str, int]]], List[str]]:
    """返回 (写入键 -> [(相对路径, 行号)], 动态返回告警列表)"""
    writes: Dict[str, List[Tuple[str, int]]] = {}
    dynamic: List[str] = []

    for scan_dir in SCAN_DIRS:
        for dirpath, dirnames, filenames in os.walk(scan_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                if not filename.endswith("_node.py"):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=full)
                except SyntaxError as e:
                    dynamic.append(f"{rel}: 语法错误无法解析 ({e})")
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, ast.FunctionDef):
                        continue
                    if not _is_node_function(node):
                        continue
                    visitor = NodeVisitor(node, _first_arg_name(node), rel)
                    visitor.run()
                    for key, lineno in visitor.writes.items():
                        writes.setdefault(key, []).append((rel, lineno))
                    if visitor.dynamic_return:
                        dynamic.append(f"{rel}:{node.lineno} {node.name}() 含无法静态解析的 return")

    return writes, dynamic


# =============================================================================
# 3. 扫描读取键
# =============================================================================

def _unwrap_state_names(node: ast.AST) -> Set[str]:
    """从表达式中解出可能的"状态变量"名。

    需要处理的写法:
        state.get("x")            → Name
        (result or {}).get("x")   → BoolOp(values=[Name, Dict])
        (res or {}).get("x")      → 同上
    FastAPI 层大量使用 `(result or {}).get(...)` 的空值兜底写法,
    不解包会把这些读取全部漏掉, 进而把只被接口层消费的字段误报成僵尸字段。
    """
    names: Set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.Name):
            names.add(cur.id)
        elif isinstance(cur, ast.BoolOp):
            stack.extend(cur.values)
        elif isinstance(cur, ast.IfExp):
            stack.extend([cur.body, cur.orelse])
    return names


def scan_state_reads() -> Dict[str, List[Tuple[str, int]]]:
    """返回 (读取键 -> [(相对路径, 行号)])

    扫描范围: nodes/ + subgraphs/ + __005__fastapi/。
    之所以要带上 FastAPI: 它是 LangGraph 的下游消费方, 很多字段(如 search_page、
    case_type_filter)是给接口层用的, 只扫 __004__ 会把它们误报成"僵尸字段"。
    """
    reads: Dict[str, List[Tuple[str, int]]] = {}
    # (扫描根, 该层里代表"图状态/图结果"的变量名集合)
    #   __004__ 内部节点签名统一用 state;
    #   __005__fastapi 里图的返回值通常绑定到 result / res, 也要计入读取方,
    #   否则会把"只被接口层消费"的字段误报成僵尸字段。
    fastapi_dir = os.path.join(ROOT, "__005__fastapi")
    scan_roots = [
        (SCAN_DIRS[0], {"state"}),
        (os.path.join(ROOT, "__004__langgraph_more_nodes", "subgraphs"), {"state"}),
        (fastapi_dir, {"state", "result", "res", "r"}),
    ]

    for scan_dir, state_vars in scan_roots:
        if not os.path.isdir(scan_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(scan_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        source = f.read()
                    tree = ast.parse(source)
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    line = getattr(node, "lineno", 0)
                    # <state_var>.get("x")  或  (<state_var> or {}).get("x")
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        and _unwrap_state_names(node.func.value) & state_vars
                    ):
                        reads.setdefault(node.args[0].value, []).append((rel, line))
                    # <state_var>["x"]（Load 上下文）
                    elif (
                        isinstance(node, ast.Subscript)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in state_vars
                        and isinstance(node.slice, ast.Constant)
                        and isinstance(node.slice.value, str)
                        and isinstance(node.ctx, ast.Load)
                    ):
                        reads.setdefault(node.slice.value, []).append((rel, line))
    return reads


# =============================================================================
# 4. 报告
# =============================================================================

def _fmt_locs(locs: List[Tuple[str, int]], limit: int = 4) -> List[str]:
    out = []
    for rel, line in locs[:limit]:
        short = rel.split("/")[-1]
        out.append(f"{short}:{line}")
    if len(locs) > limit:
        out.append(f"... 另 {len(locs) - limit} 处")
    return out


def _todo_age_days(todo: str) -> int:
    m = TODO_RE.search(todo)
    if not m:
        return 0
    try:
        d = datetime.strptime(m.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return 0
    return (date.today() - d).days


def build_report() -> dict:
    declared, todos = parse_state_fields(STATE_FILE)
    writes, dynamic = scan_node_writes()
    reads = scan_state_reads()

    undeclared = sorted(k for k in writes if k not in declared)

    blocks, todo_ok, todo_stale, warns = [], [], [], []
    for key in undeclared:
        w_locs = _fmt_locs(writes.get(key, []))
        r_locs = _fmt_locs(reads.get(key, []), limit=3)
        entry = {"field": key, "writes": w_locs, "reads": r_locs}

        if r_locs:
            entry["level"] = "BLOCK"
            blocks.append(entry)
        elif key in todos and todos[key]:
            age = _todo_age_days(todos[key])
            entry["level"] = "WARN" if age > TODO_STALE_DAYS else "TODO"
            entry["todo"] = todos[key]
            entry["age_days"] = age
            (todo_stale if age > TODO_STALE_DAYS else todo_ok).append(entry)
        else:
            entry["level"] = "WARN"
            warns.append(entry)

    # 反向检查：声明了但全项目无人写入也无人读取的僵尸字段（仅提示，不阻断）
    zombies = sorted(
        k for k in declared
        if k not in writes and k not in reads and not k.startswith("_")
    )

    return {
        "declared_count": len(declared),
        "undeclared": blocks + todo_stale + todo_ok + warns,
        "counts": {
            "BLOCK": len(blocks),
            "TODO": len(todo_ok),
            "TODO_STALE": len(todo_stale),
            "WARN": len(warns),
        },
        "zombies": zombies,
        "dynamic_returns": dynamic,
    }


def print_report(report: dict, quiet: bool = False):
    counts = report["counts"]
    print("=" * 72)
    print("AgentState 字段契约护栏")
    print(f"  已声明字段: {report['declared_count']}")
    print(f"  BLOCK={counts['BLOCK']}  TODO={counts['TODO']}  "
          f"TODO过期={counts['TODO_STALE']}  WARN={counts['WARN']}")
    print("=" * 72)

    def _block(title, items, hint):
        if not items:
            return
        print(f"\n{title}  ({len(items)} 项) — {hint}")
        for e in items:
            print(f"  [{e['level']}] {e['field']}")
            print(f"          写入: {', '.join(e['writes']) or '-'}")
            print(f"          读取: {', '.join(e['reads']) or '无（全项目零消费方）'}")
            if e.get("todo"):
                print(f"          标注: {e['todo']}  (已 {e.get('age_days')} 天)")

    _block("[BLOCK] 未声明且有消费方", report["undeclared"][: counts["BLOCK"]],
           "功能已断，必须补声明")
    start = counts["BLOCK"]
    _block("[WARN] TODO 已过期", report["undeclared"][start:start + counts["TODO_STALE"]],
           f"标注超过 {TODO_STALE_DAYS} 天仍未接线，请补接线或删除写入")
    start += counts["TODO_STALE"]
    _block("[TODO] 编排半成品", report["undeclared"][start:start + counts["TODO"]],
           "设计有消费方但尚未接线，允许保留")
    start += counts["TODO"]
    _block("[WARN] 无消费方且无 TODO", report["undeclared"][start:],
           "建议删除写入，或补 TODO 说明设计意图")

    if report["dynamic_returns"] and not quiet:
        print("\n[INFO] 无法静态解析的 return（人工确认）:")
        for d in report["dynamic_returns"]:
            print(f"  - {d}")

    if report["zombies"] and not quiet:
        print(f"\n[INFO] 声明了但全项目无写入也无读取的字段 ({len(report['zombies'])} 个，"
              f"可由阶段5 命名治理统一清理):")
        for i in range(0, len(report["zombies"]), 4):
            print("  " + ", ".join(report["zombies"][i:i + 4]))

    print()
    failing = counts["BLOCK"] + counts["TODO_STALE"] + counts["WARN"]
    if failing == 0:
        print("PASS — 未发现未声明字段。")
    else:
        print(f"FAIL — {failing} 项需要处理。")
    return failing


def main() -> int:
    quiet = "--quiet" in sys.argv
    as_json = "--json" in sys.argv
    report = build_report()

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, quiet=quiet)

    c = report["counts"]
    return 1 if (c["BLOCK"] + c["TODO_STALE"] + c["WARN"]) else 0


if __name__ == "__main__":
    sys.exit(main())
