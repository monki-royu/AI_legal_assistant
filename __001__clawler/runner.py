# -*- coding: utf-8 -*-
"""
爬虫任务执行器 (统一调度入口)
==============================

参考 legal-documents/backend/app/crawlers/runner.py 实现.

职责:
   按 task_type 分发到对应采集器, 支持: laws(法律法规)/cases(裁判案例)/
   industry(行业标准)/interpretations(司法解释)/kb(知识库构建).

可被命令行直接调用, 也可被外部脚本 import 复用.

用法:
  # 爬取所有默认法律
  python -m __001__clawler.runner laws

  # 爬取指定法律
  python -m __001__clawler.runner laws --keywords "中华人民共和国民法典"

  # 生成所有默认案由的案例
  python -m __001__clawler.runner cases

  # 爬取行业标准
  python -m __001__clawler.runner industry --keywords "建设工程"

  # 爬取司法解释
  python -m __001__clawler.runner interpretations

  # 构建知识库(解析所有原始数据为结构化 JSON + 预建索引)
  python -m __001__clawler.runner kb
"""
import os
import sys
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_task(task_type: str, keywords: str = "") -> int:
    """
    执行一个爬虫任务.

    Parameters
    ----------
    task_type : str
        任务类型: "laws"=法律法规, "cases"=裁判案例, "industry"=行业标准,
                 "interpretations"=司法解释, "kb"=知识库构建.
    keywords : str
        关键词. 非空时只爬指定法律/案由/标准; 为空时爬默认列表.

    Returns
    -------
    int
        本次采集数量.
    """
    print(f"[Runner] 开始执行任务 type={task_type} keywords='{keywords}'")
    try:
        if task_type == "laws":
            from __001__clawler.__002__crawl_law_database import crawl_single_law, TARGET_LAWS, export_law_index
            from common.path_utils import root_dir
            output_dir = os.path.join(root_dir, "data", "laws_txt")
            os.makedirs(output_dir, exist_ok=True)
            if keywords.strip():
                targets = [{"name": keywords.strip(), "code": "", "keywords": ""}]
            else:
                targets = TARGET_LAWS
            results = []
            success = 0
            import time, random
            for law in targets:
                text, source = crawl_single_law(law, output_dir)
                ok = bool(text) and len(text) > 100
                results.append({**law, "ok": ok, "source": source, "path": os.path.join(output_dir, law["name"] + ".txt") if ok else ""})
                if ok:
                    success += 1
                time.sleep(random.uniform(0.3, 0.8))
            if not keywords.strip():
                index_path = os.path.join(root_dir, "__001__clawler", "法律列表.xlsx")
                export_law_index(index_path, results)
            print(f"[Runner] 法律法规任务完成, 成功 {success}/{len(targets)}")
            return success

        elif task_type == "cases":
            from __001__clawler.cases_collector import generate_cases
            count = generate_cases(keywords=keywords)
            print(f"[Runner] 裁判案例任务完成, 新增 {count} 个")
            return count

        elif task_type == "interpretations":
            from __001__clawler.interpretation_crawler import crawl_interpretations
            count = crawl_interpretations(keywords=keywords)
            print(f"[Runner] 司法解释任务完成, 新增 {count} 条")
            return count

        elif task_type == "kb":
            from __001__clawler.kb_builder import build_all
            build_all()
            print("[Runner] 知识库构建完成")
            return 1

        else:
            raise ValueError(f"未知任务类型: {task_type} (支持: laws/cases/industry/interpretations/kb)")

    except Exception as e:
        print(f"[Runner] 任务失败: {e}")
        import traceback
        traceback.print_exc()
        return 0


def main():
    """命令行入口: 解析参数并调用 run_task."""
    parser = argparse.ArgumentParser(description="法智引擎 · 爬虫任务执行器")
    parser.add_argument("task_type", choices=["laws", "cases", "industry", "interpretations", "kb"],
                        help="任务类型: laws=法律法规, cases=裁判案例, industry=行业标准, interpretations=司法解释, kb=知识库构建")
    parser.add_argument("--keywords", "-k", default="",
                        help="关键词(非空时只爬指定法律/案由/标准)")
    args = parser.parse_args()
    run_task(args.task_type, args.keywords)


if __name__ == "__main__":
    main()