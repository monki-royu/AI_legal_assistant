# -*- coding: utf-8 -*-
"""
数据准备工作流（一站式）
========================

用途：
  在运行 kb_builder 构建知识库之前，确保所有数据源的原始 txt 文件准备就绪。

执行顺序：
  1. industry_docs.json 已存在 → 导出为 txt 文件到 data/industry_sources/
  2. 若 interpretation txt 缺失 → 运行 interpretation_crawler 生成
  3. 一键验证各数据源完整性
"""
import os
import sys
import json
import glob

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def export_industry_from_kb():
    """
    从已有的 industry_docs.json 导出原始 txt 文件到 data/industry_sources/，
    使 kb_builder 能够从 txt 重建知识库（确保可复现性）。
    """
    kb_json = os.path.join(_PROJECT_ROOT, "data", "knowledge_base", "industry_docs.json")
    txt_dir = os.path.join(_PROJECT_ROOT, "data", "industry_sources")

    if not os.path.exists(kb_json):
        print("[data_prep] industry_docs.json 不存在，跳过导出")
        return 0

    with open(kb_json, "r", encoding="utf-8") as f:
        docs = json.load(f)

    if not docs:
        print("[data_prep] industry_docs.json 为空，跳过导出")
        return 0

    os.makedirs(txt_dir, exist_ok=True)

    # 按 standard_name 分组
    by_name = {}
    for d in docs:
        name = d.get("standard_name", "未知标准")
        if name not in by_name:
            by_name[name] = []
        by_name[name].append(d)

    count = 0
    for std_name, articles in by_name.items():
        # 文件名：用标准名，去掉非法字符
        safe_name = std_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        fpath = os.path.join(txt_dir, f"{safe_name}.txt")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# {std_name}\n")
            f.write("# 来源: 行业标准知识库\n\n")
            for a in articles:
                section = a.get("section", "")
                standard_no = a.get("standard_no", "")
                content = a.get("content", "")
                if section:
                    f.write(f"[{section}]\n")
                f.write(f"{standard_no} {content}\n\n")
        count += len(articles)
        print(f"  [export] {std_name}: {len(articles)} 条 -> {os.path.basename(fpath)}")

    print(f"[data_prep] 行业标准导出完成：{count} 条 -> {txt_dir}")
    return count


def verify_data_sources():
    """验证所有数据源的文件完整性"""
    print("\n" + "=" * 60)
    print("📊 数据源完整性检查")
    print("=" * 60)

    checks = [
        ("法律法规", os.path.join(_PROJECT_ROOT, "data", "laws"),
         lambda d: len([f for f in os.listdir(d) if f.endswith(".txt")]) if os.path.isdir(d) else 0),
        ("行政法规/部门规章(原始txt)", os.path.join(_PROJECT_ROOT, "data", "regulations"),
         lambda d: len([f for f in os.listdir(d) if f.endswith(".txt")]) if os.path.isdir(d) else 0),
        ("裁判案例", os.path.join(_PROJECT_ROOT, "data", "cases"),
         lambda d: sum(len([f for f in files if f.endswith(".txt")]) for root, _, files in os.walk(d)) if os.path.isdir(d) else 0),
        ("行业标准(原始txt)", os.path.join(_PROJECT_ROOT, "data", "industry_sources"),
         lambda d: len([f for f in os.listdir(d) if f.endswith(".txt")]) if os.path.isdir(d) else 0),
        ("司法解释(原始txt)", os.path.join(_PROJECT_ROOT, "data", "interpretations"),
         lambda d: len([f for f in os.listdir(d) if f.endswith(".txt")]) if os.path.isdir(d) else 0),
    ]

    all_ok = True
    for name, path, counter in checks:
        n = counter(path)
        status = "✅" if n > 0 else ("⚠️ 目录存在但为空" if os.path.isdir(path) else "❌ 目录不存在")
        print(f"  {status} {name}: {n} 个文件 ({path})")
        if n == 0:
            all_ok = False

    # KB JSON 检查
    kb_dir = os.path.join(_PROJECT_ROOT, "data", "knowledge_base")
    kb_checks = [
        ("laws_docs.json", os.path.join(kb_dir, "laws_docs.json")),
        ("regulations_docs.json", os.path.join(kb_dir, "regulations_docs.json")),
        ("cases_docs.json", os.path.join(kb_dir, "cases_docs.json")),
        ("industry_docs.json", os.path.join(kb_dir, "industry_docs.json")),
        ("interpretations_docs.json", os.path.join(kb_dir, "interpretations_docs.json")),
    ]
    for name, path in kb_checks:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                docs = json.load(f)
            print(f"  {'✅' if len(docs) > 0 else '⚠️ 空'} 知识库 {name}: {len(docs)} 条")
            if len(docs) == 0:
                all_ok = False
        else:
            print(f"  ❌ 知识库 {name}: 文件不存在")
            all_ok = False

    # 索引检查
    idx_dir = os.path.join(kb_dir, "index")
    idx_checks = [
        "laws_faiss.index", "laws_bm25.pkl",
        "regulations_faiss.index", "regulations_bm25.pkl",
        "cases_faiss.index", "cases_bm25.pkl",
        # 键/文件命名统一为 "industry_sources"（与 retrieval_entity_recall_node._SOURCE_INDEX_MAP
        # 和 retrieval_intent_decompose_node.KNOWN_DOMAIN_SOURCES 完全一致），
        # 历史简名 "industry_*" 已废弃。
        "industry_sources_faiss.index", "industry_sources_bm25.pkl",
        "interpretations_faiss.index", "interpretations_bm25.pkl",
    ]
    for name in idx_checks:
        path = os.path.join(idx_dir, name)
        if os.path.exists(path):
            print(f"  ✅ 索引 {name}")
        else:
            print(f"  ⚠️ 索引 {name}: 不存在（kb_builder 运行后会自动生成）")

    print("=" * 60)
    return all_ok


def main():
    print("📦 法智引擎 · 数据准备工作流")
    print("=" * 60)

    # Step 1: 从已有 KB 导出行业标准原始 txt
    print("\n[Step 1/3] 导出行业标准原始 txt...")
    n = export_industry_from_kb()

    # Step 2: 检查司法解释数据
    print("\n[Step 2/3] 检查司法解释数据...")
    int_dir = os.path.join(_PROJECT_ROOT, "data", "interpretations")
    if os.path.isdir(int_dir):
        txt_files = [f for f in os.listdir(int_dir) if f.endswith(".txt")]
        if txt_files:
            print(f"  ✅ 司法解释已有 {len(txt_files)} 个 txt 文件")
        else:
            print(f"  ⚠️ 司法解释目录存在但为空")
            print(f"  💡 运行: python -m __001__clawler.runner interpretations")
    else:
        os.makedirs(int_dir, exist_ok=True)
        print(f"  ⚠️ 司法解释目录不存在，已创建")
        print(f"  💡 运行: python -m __001__clawler.runner interpretations")

    # Step 3: 验证完整性
    print("\n[Step 3/3] 验证数据源完整性...")
    verify_data_sources()

    print("\n" + "=" * 60)
    print("📋 建议执行顺序（按需选择）:")
    print("=" * 60)
    print("""
  1️⃣  生成司法解释数据（如无原始 txt）:
       python -m __001__clawler.runner interpretations

  2️⃣  构建/重建知识库（解析 txt → 结构化 JSON + 预建索引）:
       python -m __001__clawler.runner kb
       或: python -m __001__clawler.kb_builder

  3️⃣  导入 Neo4j 知识图谱（主路径, 覆盖 laws/regulations/interpretations/cases 四源）:
       python __003__create_neo4j_database/generate_neo4j_cypher.py --write
       # 仅生成 Cypher 文件(不写库): python __003__create_neo4j_database/generate_neo4j_cypher.py

  4️⃣  导出图谱元数据（供 Cypher 生成 / 向量索引参考）:
       python -m __003__create_neo4j_database.__002__export_metadata

  5️⃣  构建 FAISS 向量索引（图谱三元组）:
       python -m __003__create_neo4j_database.__003__vector_index

  💡 如果只想更新检索用的知识库（不涉及 Neo4j）:
       只需执行 1️⃣ → 2️⃣ 两步即可
    """)


if __name__ == "__main__":
    main()