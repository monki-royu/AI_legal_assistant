"""
法律领域知识图谱抽取工具
参考 ChineseMedicalNewProjectEdit/__002__extract_information/__000__extract_graph_data_utils.py

法律领域实体类型:
  - Statute     法律法规/法典
  - Article     法条/条款
  - Concept     法律概念
  - CaseType    案件类型
  - Penalty     处罚/刑罚类型
  - Right       权利类型
  - Obligation  义务类型

法律领域关系类型:
  - CONTAINS_ARTICLE       法规包含法条
  - REFERS_TO_STATUTE      条文援引其他法
  - DEFINED_AS             概念定义于某法条
  - APPLIES_TO_CASE        法条适用案件类型
  - IMPOSES_PENALTY        法条规定某处罚
  - GRANTS_RIGHT           法条授予权利
  - IMPOSES_OBLIGATION     法条设定义务
  - RELATES_TO_CONCEPT     法条/法规涉及某概念
"""
# 📜 代码文字逻辑解析
# 本文件是法律领域知识图谱抽取的核心工具模块，定义了实体与关系的 Pydantic 数据模型、
# 实体/关系类型枚举、属性模板，并基于 LangChain 的 LCEL 语法组装了"提示词模板 → LLM →
# JSON 解析器"的抽取链(chain)。Pydantic 模型(Entity/Relation/ExtractDict)用于约束 LLM
# 输出的 JSON 结构，JsonOutputParser 自动生成格式说明注入提示词。ENTITY_TYPES 和
# RELATION_TYPES 字典以"类型名 → 中文说明"的形式定义了法律领域 7 类实体和 8 类关系，
# 作为 partial_variables 注入提示词，引导 LLM 按法律领域规范抽取。extract_graph_data
# 是对外暴露的核心函数：接收法律文本，截断超长文本后调用 chain 抽取，返回含 entities
# 和 relations 的字典，失败时返回空列表兜底。该模块被 __001__extract_law_data.py 导入
# 用于批量抽取法律文本的知识图谱三元组，供后续导入 Neo4j 图数据库。
from typing import List, Optional  # 导入 typing 类型提示：List 列表类型，Optional 可选类型（本文件实际未使用 Optional）
from langchain_core.output_parsers import JsonOutputParser  # 导入 LangChain JSON 输出解析器，自动将 LLM 输出解析为字典
from langchain_core.prompts import PromptTemplate  # 导入 LangChain 提示词模板，支持变量注入与格式化
from pydantic import BaseModel, Field  # 导入 Pydantic 数据模型基类与字段定义工具，用于约束 JSON 结构

from common.llm import my_llm  # 导入项目统一 LLM 封装实例，用于组装抽取链


# ==================== Pydantic 模型 ====================
# 定义知识图谱抽取结果的 Pydantic 数据模型，用于约束 LLM 输出的 JSON 结构
class Entity(BaseModel):
    """实体 Pydantic 模型，表示法律知识图谱中的一个实体节点"""
    name: str = Field(..., description="实体名称")  # 实体名称，必填，如"民法典""第五百八十五条""违约金"
    type: str = Field(..., description="实体类型(Statute/Article/Concept/CaseType/Penalty/Right/Obligation)")  # 实体类型，必填，限定为7种之一
    attributes: dict = Field(default_factory=dict, description="实体属性键值对")  # 实体属性字典，默认空字典，如{"article_no": "第五百八十五条", "content": "..."}


class Relation(BaseModel):
    """关系 Pydantic 模型，表示法律知识图谱中的一条三元组关系(主体-关系-客体)"""
    subject: str = Field(..., description="主体实体名称")  # 主体实体名称，必填，如"民法典"
    subject_type: str = Field(..., description="主体实体类型")  # 主体实体类型，必填，如"Statute"
    relation: str = Field(..., description="关系类型")  # 关系类型，必填，如"CONTAINS_ARTICLE"
    object: str = Field(..., description="客体实体名称")  # 客体实体名称，必填，如"第五百八十五条"
    object_type: str = Field(..., description="客体实体类型")  # 客体实体类型，必填，如"Article"


class ExtractDict(BaseModel):
    """抽取结果 Pydantic 模型，包含实体列表和关系列表，作为 JsonOutputParser 的目标结构"""
    entities: List[Entity] = Field(default_factory=list, description="实体列表")  # 实体列表，默认空列表
    relations: List[Relation] = Field(default_factory=list, description="关系列表")  # 关系列表，默认空列表


# ==================== 实体类型枚举(供提示词引用) ====================
# 定义法律领域 7 类实体类型及其中文说明，作为 partial_variables 注入提示词
# LLM 在抽取时会参考此字典判断实体应归类为哪种类型
ENTITY_TYPES = {
    "Statute": "法律法规/法典名, 如《民法典》《刑法》《劳动法》",  # 法规/法典层级实体
    "Article": "法条/条款, 如'第五百八十五条'、'第一百零三条'",  # 具体条文层级实体
    "Concept": "法律概念, 如'违约金'、'不可抗力'、'竞业限制'、'定金'",  # 抽象法律概念实体
    "CaseType": "案件类型, 如'合同纠纷'、'劳动争议'、'侵权纠纷'",  # 案件分类实体
    "Penalty": "处罚/刑罚类型, 如'罚金'、'有期徒刑'、'行政处罚'、'民事赔偿'",  # 处罚/刑罚实体
    "Right": "权利类型, 如'物权'、'债权'、'人格权'、'知情权'",  # 权利实体
    "Obligation": "义务类型, 如'保密义务'、'告知义务'、'给付义务'",  # 义务实体
}

# 定义法律领域 8 类关系类型及其中文说明，作为 partial_variables 注入提示词
# 每类关系附带示例，帮助 LLM 理解关系的主体-客体方向
RELATION_TYPES = {
    "CONTAINS_ARTICLE": "法规包含法条, 如《民法典》CONTAINS_ARTICLE 第五百八十五条",  # 法规→法条
    "REFERS_TO_STATUTE": "条文援引其他法规, 如本条REFERS_TO_STATUTE《XX法》",  # 法条→法规
    "DEFINED_AS": "概念定义于某法条, 如'违约金'DEFINED_AS 第五百八十五条",  # 概念→法条
    "APPLIES_TO_CASE": "法条适用案件类型, 如本条APPLIES_TO_CASE'合同纠纷'",  # 法条→案件类型
    "IMPOSES_PENALTY": "法条规定某处罚, 如本条IMPOSES_PENALTY'罚金'",  # 法条→处罚
    "GRANTS_RIGHT": "法条授予权利, 如本条GRANTS_RIGHT'知情权'",  # 法条→权利
    "IMPOSES_OBLIGATION": "法条设定义务, 如本条IMPOSES_OBLIGATION'保密义务'",  # 法条→义务
    "RELATES_TO_CONCEPT": "法条/法规涉及某概念, 如本条RELATES_TO_CONCEPT'定金'",  # 法条→概念
}

# 实体属性模板(供提示词参考, 实际抽取时按需填写)
# 定义每类实体建议包含的属性字段及中文说明，引导 LLM 抽取更丰富的属性信息
ENTITY_ATTR_TEMPLATES = {
    "Statute": {"code_no": "法律编号/文号", "version_date": "版本日期",  # 法规属性：编号、版本日期
                "scope": "适用范围", "authority": "颁布机关", "effective_status": "现行有效状态"},  # 法规属性：范围、机关、状态
    "Article": {"article_no": "条文编号", "chapter": "所在章节", "content": "条文内容",  # 法条属性：编号、章节、内容
                 "penalty_range": "处罚区间", "fine_range": "罚款区间"},  # 法条属性：处罚/罚款区间
    "Concept": {"definition": "定义", "category": "所属分类"},  # 概念属性：定义、分类
    "CaseType": {"cause_type": "案由", "statute_basis": "法律依据"},  # 案件类型属性：案由、法律依据
    "Penalty": {"type": "处罚类型", "range": "幅度"},  # 处罚属性：类型、幅度
    "Right": {"scope": "权利范围", "holder": "权利主体"},  # 权利属性：范围、主体
    "Obligation": {"scope": "义务范围", "bearer": "义务主体"},  # 义务属性：范围、主体
}


# ==================== JsonOutputParser + PromptTemplate ====================
# 创建 JSON 输出解析器，绑定 ExtractDict Pydantic 模型
# 解析器会自动生成格式说明(format_instructions)注入提示词，并将 LLM 输出解析为字典
parser = JsonOutputParser(pydantic_object=ExtractDict)  # 绑定 ExtractDict 模型，约束输出含 entities 和 relations

# 创建提示词模板，定义 LLM 的角色、抽取要求、输入变量和输出格式
prompt = PromptTemplate(
    template="""你是一个法律领域知识图谱抽取专家。请从给定的法律文本中抽取实体和关系, 用于构建法律知识图谱。  # 设定 LLM 角色为法律图谱抽取专家

## 实体类型(共7类)
{entity_types}  # 注入实体类型说明（来自 ENTITY_TYPES 字典）

## 关系类型(共8类)
{relation_types}  # 注入关系类型说明（来自 RELATION_TYPES 字典）

## 抽取要求
1. 只抽取文本中明确出现的实体和关系, 不要臆造
2. 每个实体必须包含 name、type、attributes 三个字段
3. 法条(Article)的 attributes 必须包含 article_no(条文编号) 和 content(条文内容)
4. 关系必须包含 subject、subject_type、relation、object、object_type 五个字段
5. 同一概念在不同法条中出现, 应合并为同一个实体
6. 重点抽取: 违约金、定金、不可抗力、竞业限制、格式条款、连带责任、合同解除等核心概念

## 法律文本
{law_text}  # 注入待抽取的法律文本（运行时传入）

## 输出格式
{format_instructions}  # 注入 JSON 输出格式说明（由 parser 自动生成）

请输出 JSON:""",  # 要求 LLM 输出 JSON
    input_variables=["law_text"],  # 运行时需要传入的变量：法律文本
    partial_variables={  # 预填充的变量（构造时即固定）
        "format_instructions": parser.get_format_instructions(),  # 从 parser 获取 JSON 格式说明
        "entity_types": "\n".join([f"- {k}: {v}" for k, v in ENTITY_TYPES.items()]),  # 将实体类型字典格式化为列表文本
        "relation_types": "\n".join([f"- {k}: {v}" for k, v in RELATION_TYPES.items()]),  # 将关系类型字典格式化为列表文本
    },
)

# 组装链
# 使用 LangChain LCEL 语法将提示词模板、LLM、解析器串联为一条可调用的链
# 调用 chain.invoke({"law_text": ...}) 时：prompt 格式化 → my_llm 生成 → parser 解析为 dict
chain = prompt | my_llm | parser  # 提示词 | LLM | JSON解析器


def extract_graph_data(law_text: str, filename: str = "") -> dict:
    """
    从法律文本中抽取实体和关系

    【作用】
        接收一段法律文本，截断超长部分后调用预组装的 chain(提示词→LLM→JSON解析器)抽取
        实体和关系。返回包含 entities 和 relations 两个列表的字典。任何异常均打印日志
        并返回空列表兜底，保证调用方不会因 LLM 失败而崩溃。

    【参数】
        law_text (str): 法律文本，可以是一部法律或一个章节的分块
        filename (str): 文件名，仅用于日志标识，默认空字符串

    【返回值】
        dict: {"entities": [...], "relations": [...]}，失败时两个列表均为空

    【可迁移性说明】
        该函数是知识图谱抽取的统一入口，依赖模块级 chain(需 LLM 配置就绪)。
        max_chars 截断阈值(8000)可根据 LLM 上下文窗口大小调整。异常兜底策略
        保证批量抽取任务中单个分块失败不会中断整体流程。可迁移到任何基于
        LangChain LCEL 的信息抽取场景，只需调整 prompt 和 Pydantic 模型。
    """
    # 文本过长截断(避免超出上下文)
    max_chars = 8000  # 定义最大字符数阈值，避免超出 LLM 上下文窗口
    if len(law_text) > max_chars:  # 若文本超过阈值
        law_text = law_text[:max_chars] + "\n...(文本过长, 已截断)"  # 截断并追加提示

    try:  # 捕获 LLM 调用与解析全过程的异常
        result = chain.invoke({"law_text": law_text})  # 调用链抽取，传入法律文本
        # JsonOutputParser 返回 dict
        if isinstance(result, dict):  # 若返回结果为字典（解析成功）
            return {  # 返回规范化的结果字典
                "entities": result.get("entities", []),  # 提取实体列表，缺失则空列表
                "relations": result.get("relations", []),  # 提取关系列表，缺失则空列表
            }
        return {"entities": [], "relations": []}  # 返回结果非字典时返回空列表兜底
    except Exception as e:  # 捕获任何异常（LLM 超时、解析失败等）
        print(f"❌ 抽取失败 {filename}: {e}")  # 打印失败日志，含文件名和异常信息
        return {"entities": [], "relations": []}  # 返回空列表兜底


if __name__ == "__main__":
    # 测试
    # 定义测试用的法律文本样本（民法典第五百八十五条 违约金条款）
    sample = """
    第五百八十五条 当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金；
    也可以约定因违约产生的损失赔偿额的计算方法。
    约定的违约金低于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以增加；
    约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。
    """
    result = extract_graph_data(sample, "测试")  # 调用抽取函数，传入测试文本
    import json  # 导入 json 模块用于格式化输出
    print(json.dumps(result, ensure_ascii=False, indent=2))  # 以 JSON 格式打印结果，ensure_ascii=False 保留中文，indent=2 缩进2空格
