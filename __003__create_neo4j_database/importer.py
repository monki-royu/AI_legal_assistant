# -*- coding: utf-8 -*-
"""
知识图谱导入流水线 (Importer Pipeline)
======================================

【职责】
将法律法规 TXT 文件 → 实体抽取 → Cypher 生成 → Neo4j 导入 的完整流水线。
支持断点续跑和增量导入。

【流程】
  ① 扫描 __001__clawler/法律法规/*.txt (或 data/knowledge_base/txt/*.txt)
  ② 对每个 TXT 文件调用 entity_extractor.extract_entities() → 得到实体/关系
  ③ 调用 cypher_generator.generate_create_cypher() → 得到 Cypher 语句
  ④ 对 Cypher 调用 neo4j_client.explain() 语法校验
  ⑤ 校验通过 → 写入 Neo4j → 记录进度(跳过已处理文件)

【用法】
  python -m __003__create_neo4j_database.importer          # 全量导入
  python -m __003__create_neo4j_database.importer --resume # 断点续跑
"""
import os, sys, json, glob
from typing import List, Set

if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from common.path_utils import root_dir

# ============================================================
# 路径配置
# ============================================================
_LAW_TXT_DIRS = [
    os.path.join(root_dir, "__001__clawler", "法律法规"),
    os.path.join(root_dir, "data", "interpretations"),
]
_PROGRESS_PATH = os.path.join(root_dir, "data", "neo4j_import_progress.json")
_FINETUNE_PATH = os.path.join(root_dir, "data", "extract_finetune_data.jsonl")


def find_txt_files() -> List[str]:
    """扫描所有法律法规 TXT 文件。"""
    files = []
    for d in _LAW_TXT_DIRS:
        if os.path.isdir(d):
            files.extend(glob.glob(os.path.join(d, "*.txt")))
    return sorted(files)


def load_progress() -> Set[str]:
    """加载已处理的文件名列表。"""
    if os.path.exists(_PROGRESS_PATH):
        with open(_PROGRESS_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()


def save_progress(done: Set[str], total: int):
    """保存进度。"""
    os.makedirs(os.path.dirname(_PROGRESS_PATH), exist_ok=True)
    with open(_PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done), "total": total}, f, ensure_ascii=False)


def run_pipeline(resume: bool = False) -> int:
    """
    运行完整导入流水线。

    Parameters
    ----------
    resume : bool
        是否从断点续跑。

    Returns
    -------
    int : 成功导入的文件数。
    """
    from __002__extract_information.entity_extractor import extract_entities, merge_results
    from __003__create_neo4j_database.cypher_generator import generate_create_cypher
    from __003__create_neo4j_database.neo4j_client import neo4j_client

    # 1. 检查 Neo4j 是否可用
    if not neo4j_client.health_check():
        print("[Importer] ❌ Neo4j 不可用，请先启动 Neo4j 服务")
        print("[Importer] 降级: 仅执行实体抽取 + Cypher 生成(不写入)，可后续手动导入")
        write_only = True
    else:
        write_only = False

    # 2. 扫描 TXT 文件
    all_files = find_txt_files()
    done = load_progress() if resume else set()
    pending = [f for f in all_files if os.path.basename(f) not in done]

    if not pending:
        print(f"[Importer] 全部 {len(all_files)} 个文件已处理，无需导入")
        return 0

    print(f"[Importer] 共 {len(all_files)} 个文件，待处理 {len(pending)} 个")

    success = 0
    for fpath in pending:
        fname = os.path.basename(fpath)
        print(f"  [Importer] 处理: {fname}")

        try:
            # 3. 读取 TXT
            with open(fpath, encoding="utf-8") as f:
                text = f.read()

            # 4. 实体抽取
            result = extract_entities(text, use_llm=True)
            if not result.get("entities"):
                print(f"    ⚠️ 未抽取到实体(文件可能为空或格式不符)，跳过")
                continue

            # 5. Cypher 生成
            cypher = generate_create_cypher(
                result["entities"], result.get("relations", []), use_llm=True
            )

            if not cypher:
                print(f"    ⚠️ Cypher 生成为空，跳过")
                continue

            # 6. Cypher 语法校验
            if not write_only:
                plan = neo4j_client.explain(cypher)
                if not plan.get("ok", False):
                    print(f"    ⚠️ Cypher 语法校验失败: {plan.get('error', '未知错误')}")
                    print(f"       降级: 保存 Cypher 文件(不写入)")
                    # 保存供人工审核
                    _save_cypher_file(fname, cypher)
                    continue

                # 7. 写入 Neo4j
                count = neo4j_client.run_in_tx(
                    [(stmt.strip() + ";", {}) for stmt in cypher.split(";") if stmt.strip()]
                )
                print(f"    ✅ 写入 Neo4j: {count} 条语句")
            else:
                # Neo4j 不可用, 保存 Cypher 文件
                _save_cypher_file(fname, cypher)
                print(f"    💾 Cypher 已保存(Neo4j 不可用)")

            # 8. 记录进度
            done.add(fname)
            save_progress(done, len(all_files))
            success += 1

        except Exception as e:
            print(f"    ❌ 失败: {e}")
            import traceback
            traceback.print_exc()

    print(f"[Importer] ✅ 完成: {success}/{len(pending)} 个文件成功")
    return success


def _save_cypher_file(fname: str, cypher: str):
    """保存 Cypher 文件到 data/cypher_output/ 目录。"""
    out_dir = os.path.join(root_dir, "data", "cypher_output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fname.replace(".txt", ".cypher"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(cypher)
    print(f"    💾 保存 Cypher: {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="知识图谱导入流水线")
    parser.add_argument("--resume", action="store_true", help="断点续跑")
    args = parser.parse_args()
    run_pipeline(resume=args.resume)