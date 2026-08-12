# -*- coding: utf-8 -*-
"""
Streamlit 前端启动器（升级增强版）— 法智引擎 AI 法律助理
========================================================
功能：
  1) 前置诊断：依赖 / 端口 / .env / app.py 语法  四重检查（--diagnose 模式）
  2) 启动模式：后台常驻拉起 Streamlit，stdout+stderr 全量落盘
  3) 双重验证：端口监听成功 + 10 秒后二次确认，避免"启动后立刻崩溃"假阳性
  4) 崩溃溯源：若失败，自动扫描日志文件最后 60 行，定位真实异常栈

用法：
  正常启动：  python _start_streamlit_server.py
  先做诊断：  python _start_streamlit_server.py --diagnose
  指定端口：  python _start_streamlit_server.py --port 8502
"""
import os
import sys
import time
import socket
import argparse
import traceback
from datetime import datetime

# ===== 基础路径 =====
ROOT = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(ROOT, "__006__streamlit", "app.py")
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
RUNTIME_LOG = os.path.join(LOG_DIR, f"streamlit_runtime_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
LATEST_LOG = os.path.join(LOG_DIR, "streamlit_runtime_latest.log")


def log(msg):
    """同时打印控制台 + 写入最新日志文件。"""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LATEST_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===== 诊断工具函数 =====
CRITICAL_IMPORTS = [
    ("streamlit", "streamlit 前端框架 —— 运行必需"),
    ("dotenv", "python-dotenv —— 读取 .env 配置"),
    ("yaml", "pyyaml —— 数值校验规则解析"),
    ("langchain_core", "langchain-core —— LLM 消息接口"),
    ("langchain_openai", "langchain-openai —— 大模型客户端"),
    ("neo4j", "neo4j —— 图数据库驱动（未使用也可以不装，但建议装上）"),
    ("sentence_transformers", "sentence-transformers —— 向量模型（未使用也可以不装）"),
    ("faiss", "faiss —— 向量检索（未使用也可以不装）"),
]


def check_dependencies():
    """逐个 import 关键依赖，返回 (ok_count, missing_list)。"""
    ok = 0
    missing = []
    log("=" * 50)
    log("【诊断 1/4】关键依赖导入检查")
    for pkg, desc in CRITICAL_IMPORTS:
        try:
            __import__(pkg)
            ok += 1
            log(f"  ✅ {pkg:<22s}  已安装 ({desc})")
        except ImportError as e:
            missing.append((pkg, desc, str(e)))
            log(f"  ❌ {pkg:<22s}  缺失! 原因: {e}")
    log(f"  结果: {ok}/{len(CRITICAL_IMPORTS)} 通过，缺失 {len(missing)} 项")
    return ok, missing


def check_port(port):
    """检查端口是否被占用。返回 (occupied, pid_or_None)。"""
    log("=" * 50)
    log(f"【诊断 2/4】端口 {port} 占用检查")
    occupied = False
    pid = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.8)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                occupied = True
    except Exception:
        pass

    # 尝试找占用 PID（通过 netstat）
    if occupied:
        try:
            import subprocess as _sp
            out = _sp.check_output(
                ["netstat", "-ano"], encoding="gbk", errors="ignore"
            )
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    break
        except Exception:
            pass
        log(f"  ⚠️  端口 {port} 已被占用 (PID={pid or '未知'})，启动大概率失败")
    else:
        log(f"  ✅ 端口 {port} 空闲，可正常监听")
    return occupied, pid


def check_env_file():
    """检查 .env 是否存在及关键字段。"""
    log("=" * 50)
    log("【诊断 3/4】.env 配置文件检查")
    env_path = os.path.join(ROOT, ".env")
    if not os.path.isfile(env_path):
        log("  ⚠️  未找到 .env 文件，大模型/Neo4j 连接可能失败（但前端仍能渲染界面）")
        return False
    with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    required_keys = ["MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"]
    found = sum(1 for k in required_keys if k in content)
    log(f"  ✅ .env 已找到，关键 LLM 配置 {found}/{len(required_keys)} 项存在")
    if found < len(required_keys):
        log("  💡 提示：MODEL_API_KEY / MODEL_BASE_URL / MODEL_NAME 任一缺失，大模型调用接口会失败，但 UI 仍可展示 demo 结果")
    return True


def check_app_syntax():
    """py_compile 编译 app.py 和公共模块，捕获语法错误。"""
    import py_compile
    log("=" * 50)
    log("【诊断 4/4】核心 Python 语法编译检查")
    files_to_check = [
        APP_PATH,
        os.path.join(ROOT, "common", "path_utils.py"),
        os.path.join(ROOT, "common", "config.py"),
        os.path.join(ROOT, "common", "llm.py"),
        os.path.join(ROOT, "__004__langgraph_more_nodes", "langgraph_main.py"),
    ]
    ok = 0
    bad = []
    for fp in files_to_check:
        if not os.path.isfile(fp):
            log(f"  ⚪ {os.path.basename(fp):<30s} 不存在，跳过")
            continue
        try:
            py_compile.compile(fp, doraise=True)
            ok += 1
            log(f"  ✅ {os.path.basename(fp):<30s} 语法正常")
        except py_compile.PyCompileError as e:
            bad.append((fp, str(e)))
            log(f"  ❌ {os.path.basename(fp):<30s} 语法错误: {e}")
    log(f"  结果: {ok} 个文件语法正常，{len(bad)} 个文件存在语法错误")
    return bad


def run_full_diagnose(port):
    log("\n" + "╔" + "═" * 58 + "╗")
    log("║          法智引擎启动诊断报告                          ║")
    log("╚" + "═" * 58 + "╝")
    log(f"Python 解释器: {sys.executable}")
    log(f"Python 版本:   {sys.version}")
    log(f"项目根目录:   {ROOT}")
    ok_dep, miss_dep = check_dependencies()
    port_occ, port_pid = check_port(port)
    check_env_file()
    bad_syn = check_app_syntax()
    log("=" * 50)
    log("【诊断总结】")
    if not miss_dep and not port_occ and not bad_syn:
        log("  ✅ 全部检查通过，可以正常启动！")
        return 0
    if miss_dep:
        log(f"  ❌ 缺失 {len(miss_dep)} 个关键依赖，建议执行：")
        pkgs_str = " ".join(p for p, _, _ in miss_dep)
        log(f"     pip install {pkgs_str}")
    if port_occ:
        log(f"  ❌ 端口 {port} 被占用(PID={port_pid})，请先释放或改用 --port 其他端口")
    if bad_syn:
        log(f"  ❌ {len(bad_syn)} 个文件有语法错误，请修复后再启动")
    return 1


def is_port_listening(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def tail_log(path, lines=80):
    """读取日志文件最后 N 行。"""
    if not os.path.isfile(path):
        return "(日志文件未生成)"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception as e:
        return f"(读取日志失败: {e})"


def start_server(port):
    import subprocess as _sp

    log("\n" + "╔" + "═" * 58 + "╗")
    log("║          启动 Streamlit 前端服务                        ║")
    log("╚" + "═" * 58 + "╝")
    log(f"前端脚本:   {APP_PATH}")
    log(f"运行日志:   {RUNTIME_LOG}")
    log(f"最新日志:   {LATEST_LOG}")

    # 清空 latest log
    try:
        open(LATEST_LOG, "w", encoding="utf-8").close()
    except Exception:
        pass

    # ===== 环境变量 =====
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env["STREAMLIT_SERVER_PORT"] = str(port)
    env["STREAMLIT_SERVER_ADDRESS"] = "0.0.0.0"
    env["STREAMLIT_LOGGER_LEVEL"] = "info"
    env["STREAMLIT_SERVER_ENABLE_CORS"] = "false"
    env["STREAMLIT_SERVER_ENABLE_XSRF"] = "false"

    cmd = [
        sys.executable, "-u", "-m", "streamlit", "run", APP_PATH,
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.address", "0.0.0.0",
        "--browser.gatherUsageStats", "false",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
        "--global.developmentMode", "false",
    ]
    log(f"执行命令: {' '.join(cmd)}")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    # 打开日志文件句柄，让子进程把 stdout/stderr 直接写进去（不会被缓冲截断）
    log_fh = open(RUNTIME_LOG, "ab", buffering=0)
    log_fh.write(
        f"\n===== 启动时间: {datetime.now().isoformat()} =====\n".encode("utf-8")
    )
    log_fh.flush()

    try:
        proc = _sp.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=log_fh,
            stderr=_sp.STDOUT,
            stdin=_sp.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except Exception as e:
        log(f"[致命] 创建子进程失败: {e}\n{traceback.format_exc()}")
        try:
            log_fh.close()
        except Exception:
            pass
        return 1

    log(f"✅ 子进程已创建 PID = {proc.pid}，等待端口 {port} 监听……（最多40秒）")

    # ===== 第一次验证 =====
    first_ok = is_port_listening(port, timeout=40)
    if not first_ok:
        log("❌ 40 秒内仍未检测到端口监听！启动失败。")
        log("=" * 50)
        log("【扫描运行日志最后 80 行】")
        log(tail_log(RUNTIME_LOG, lines=80))
        log("=" * 50)
        log(f"💡 请将上方错误或完整日志文件内容发给开发者排查：\n{RUNTIME_LOG}")
        try:
            log_fh.close()
        except Exception:
            pass
        return 2

    log(f"✅ 第一次确认: 端口 {port} 已监听")
    log("     再等 10 秒做二次确认（防止启动后立刻崩溃）……")

    # ===== 第二次验证：10 秒后再查 =====
    time.sleep(10)
    second_ok = is_port_listening(port, timeout=3)
    if not second_ok:
        log("❌ 二次确认失败: 服务监听后立刻崩溃了！")
        log("=" * 50)
        log("【崩溃日志最后 80 行】")
        log(tail_log(RUNTIME_LOG, lines=80))
        log("=" * 50)
        log(f"💡 请将以上日志发给开发者定位问题；日志完整路径：\n{RUNTIME_LOG}")
        try:
            log_fh.close()
        except Exception:
            pass
        return 3

    log("\n" + "╔" + "═" * 58 + "╗")
    log("║  ✅ 服务稳定运行中，双重监听验证通过！                  ║")
    log(f"║  👉 请浏览器打开：http://localhost:{port}/               ")
    log(f"║  📜 实时日志：{LATEST_LOG}")
    log("╚" + "═" * 58 + "╝")
    print(f"\n再次提示👉 访问 http://localhost:{port}/ （如仍打不开，请把 {RUNTIME_LOG} 发给开发者）")
    try:
        log_fh.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="法智引擎 Streamlit 前端启动器")
    parser.add_argument("--port", type=int, default=8501, help="监听端口（默认 8501）")
    parser.add_argument("--diagnose", action="store_true", help="只做启动前诊断，不真正启动服务")
    args = parser.parse_args()

    print("=" * 60, flush=True)
    print("  法智引擎 AI 法律助理 · Streamlit 前端启动器（增强版）   ", flush=True)
    print("=" * 60, flush=True)

    if args.diagnose:
        code = run_full_diagnose(args.port)
    else:
        code = start_server(args.port)
    sys.exit(code)
