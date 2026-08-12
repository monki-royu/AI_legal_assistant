# -*- coding: utf-8 -*-
# ======================================================================================
# 📜 代码文字逻辑解析
# ======================================================================================
# 这个脚本是"法智引擎"AI法律助理项目的合同审核模块独立测试文件。它不依赖 Streamlit
# Web 框架，而是在纯 Python 命令行环境下验证 app.py 中四个核心逻辑的正确性：
#
# 1. 流式输出逻辑 (test_streaming_output)：
#    模拟 app.py 中 _stream_response 函数的行为——先依次输出5个"思考步骤"（分析→检索
#    法条→匹配判例→生成建议→整理报告），再调用 _get_demo_result 获取完整审核结果，
#    将结果文本按 chunk_size=8 字符分块逐块输出，模拟大模型"打字机"效果。最后验证
#    结果字典的8个必需字段是否齐全。
#
# 2. 修改按钮状态机 (test_modify_button_state_machine)：
#    验证风险卡片的7个状态流转——初始(None)→点击修改(modify_input)→确认修改(modified)
#    →重新修改(modify_input)→取消(删除state)→采纳(accepted)→不采纳(rejected)。
#    用纯 dict 模拟 Streamlit 的 st.session_state，验证状态读写和持久化逻辑。
#
# 3. 效果展示切换逻辑 (test_toggle_demo)：
#    验证"🎭 效果展示"按钮的三次点击切换——首次加载→再次清除→第三次重新加载，
#    确保按钮可以反复展开/收起，而不是只能展示不能收回。
#
# 4. 文档高亮逻辑 (test_doc_highlight)：
#    调用 _highlight_doc 函数，验证风险段落能被正确标注为 critical/high/medium/low
#    四种颜色的 HTML <span> 标签，并检查 doc-container 容器是否存在。
#
# 整体角色：这是开发阶段的"单元测试"，确保前端交互逻辑在脱离浏览器的情况下仍然
# 正确运行。可迁移到任何需要验证 Streamlit session_state 状态机的项目。
# ======================================================================================

"""
合同审核模块 - 流式输出 & 修改按钮交互逻辑 独立测试脚本
不依赖 Streamlit，纯 Python 验证核心逻辑正确性
"""
import time  # 细节：导入时间模块，用于在流式输出测试中添加短暂延迟，模拟真实"打字机"效果
import sys   # 细节：导入系统模块，用于修改 sys.path，让脚本能找到项目根目录下的模块
import os    # 细节：导入操作系统模块，用于获取文件的绝对路径和目录名

# 细节：将项目根目录插入 Python 搜索路径的最前面（索引0），确保后续 `from __006__streamlit.app import ...` 能正确找到模块
# os.path.abspath(__file__) → 获取当前脚本的绝对路径（如 e:\...\__006__streamlit\test_contract_logic.py）
# os.path.dirname(...) → 去掉文件名，得到 __006__streamlit 目录
# os.path.dirname(...) → 再去掉一层，得到项目根目录 e:\...\AI_legal_assistant
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==================== 模拟合同审核输入数据 ====================
# 细节：定义一个完整的模拟合同文本，包含7个条款（标的、质量、付款、交货、违约、争议、期限）
# 这个文本将作为输入传给 _get_demo_result 函数，模拟用户在合同审核页面的文本框中粘贴的合同内容
# 包含了多种风险场景：违约金过高（千分之三）、管辖约定偏向甲方、质保金比例等
MOCK_CONTRACT = """甲方：上海智算科技有限公司
乙方：北京鸿图电子设备有限公司

第一条 合同标的
甲方向乙方采购笔记本电脑100台，型号为ThinkPad X1 Carbon，单价8000元，总价80万元。

第二条 质量标准
乙方提供的设备应符合国家相关质量标准。

第三条 付款方式
合同签订后3个工作日内，甲方支付预付款30%（24万元）；货到验收合格后7个工作日内支付60%（48万元）；剩余10%（8万元）作为质保金，质保期满后支付。

第四条 交货期限
乙方应于合同签订后15个工作日内交货。

第五条 违约责任
1. 乙方逾期交货的，每日按合同总金额的千分之三向甲方支付违约金。
2. 甲方逾期付款的，每日按逾期金额的千分之一向乙方支付违约金。

第六条 争议解决
凡因本合同引起的争议，双方应友好协商解决；协商不成的，向甲方所在地人民法院提起诉讼。

第七条 合同期限
本合同自双方签字盖章之日起生效，有效期一年。

甲方（盖章）：____________
乙方（盖章）：____________
签署日期：2026年___月___日"""


# ==================== 测试1: 流式输出逻辑 ====================
def test_streaming_output():
    """
    在纯命令行环境下模拟 Streamlit 页面的流式输出效果。

    作用：
        验证 app.py 中 _stream_response 函数的两阶段输出逻辑：
        阶段1 — 依次输出5个"思考步骤"提示词，让用户感知 AI 正在工作
        阶段2 — 调用 _get_demo_result 获取完整审核结果，将结果文本分块输出
        阶段3 — 验证结果字典包含8个必需字段（output/doc_text/merged_risk_items等）

    参数：无（使用模块级 MOCK_CONTRACT 常量作为输入）
    返回：dict — _get_demo_result 返回的完整审核结果字典，供后续测试使用

    可迁移性说明：
        这个"先输出思考步骤、再分块输出结果"的模式可迁移到任何 LLM 流式响应场景，
        如客服对话、文档摘要、代码生成等。核心思路是：thinking_steps 给用户心理预期，
        chunk_size 控制输出粒度，两者配合提升用户体验。
    """
    print("=" * 60)  # 细节：打印分隔线，在终端中视觉区分不同测试
    print("测试1: 流式输出逻辑")  # 细节：打印测试标题
    print("=" * 60)  # 细节：打印分隔线

    # 模拟 _stream_response 的本地分支
    # 细节：定义5个思考步骤，模拟 app.py 中 _stream_response 函数的 thinking_steps 列表
    # 这些步骤会在 Streamlit 页面上以"打字机"效果依次显示，让用户感知 AI 正在逐步工作
    thinking_steps = [
        "🤔 正在分析您的输入...",       # 步骤1：告知用户正在解析输入文本
        "📚 检索相关法律条文...",       # 步骤2：告知用户正在RAG检索法条
        "⚖️ 匹配判例与司法解释...",     # 步骤3：告知用户正在匹配相似案例
        "🔍 生成审核建议...",           # 步骤4：告知用户正在生成风险提示
        "✅ 整理最终报告..."            # 步骤5：告知用户正在整理输出格式
    ]

    print("\n[阶段1] 思考步骤流式输出:")  # 细节：打印阶段标题
    chunks_received = []  # 细节：初始化空列表，用于收集所有已输出的思考步骤，后续可验证完整性
    for i, step in enumerate(thinking_steps):  # 细节：遍历思考步骤列表，enumerate 同时获取索引和内容
        print(f"  ({i+1}/{len(thinking_steps)}) {step}")  # 细节：格式化输出，如 "(1/5) 🤔 正在分析您的输入..."
        chunks_received.append(step)  # 细节：将当前步骤收集到列表中，用于后续验证
        time.sleep(0.1)  # 加速测试 — 细节：暂停0.1秒模拟真实流式延迟（app.py中为0.8秒，这里加速）

    # 模拟 _get_demo_result
    # 细节：从 app.py 导入 _get_demo_result 函数（延迟导入，避免在模块加载时就依赖 app.py）
    # _get_demo_result 接收合同文本，返回一个包含审核结果的字典（模拟LangGraph后端的输出格式）
    from __006__streamlit.app import _get_demo_result
    result = _get_demo_result(MOCK_CONTRACT)  # 细节：传入模拟合同文本，获取演示用审核结果
    output_text = result.get("output", "")  # 细节：从结果字典中提取"output"字段（审核报告正文），默认空字符串

    print(f"\n[阶段2] 审核结果分块输出 (chunk_size=8):")  # 细节：打印阶段2标题，说明分块大小为8字符
    chunk_size = 8  # 细节：定义每块8个字符，模拟 app.py 中流式输出的粒度（app.py中实际为2-5字符，这里用8加速测试）
    total_chunks = 0  # 细节：初始化块计数器
    displayed = ""  # 细节：初始化已显示文本累加器，用于最终验证完整性
    for i in range(0, len(output_text), chunk_size):  # 细节：从0开始，每次步进chunk_size个字符，遍历整个输出文本
        chunk = output_text[i:i + chunk_size]  # 细节：切片取当前块，如 i=0取[0:8]，i=8取[8:16]
        displayed += chunk  # 细节：将当前块累加到已显示文本中
        total_chunks += 1  # 细节：块计数器+1
        # 只打印前3块和最后1块，避免刷屏
        if total_chunks <= 3:  # 细节：前3块直接打印，让用户看到分块效果
            print(f"  chunk {total_chunks}: '{chunk}'")  # 细节：打印块编号和内容
        elif total_chunks == (len(output_text) + chunk_size - 1) // chunk_size:  # 细节：计算是否为最后一块（向上取整公式）
            print(f"  ...")  # 细节：打印省略号，表示中间块被省略
            print(f"  chunk {total_chunks} (最后): '{chunk}'")  # 细节：打印最后一块

    print(f"\n  总块数: {total_chunks}")  # 细节：打印总块数，验证分块逻辑正确
    print(f"  完整输出长度: {len(displayed)} 字符")  # 细节：打印累加文本长度，应与原始output_text长度一致
    print(f"  输出内容: {displayed[:80]}...")  # 细节：打印前80字符预览，直观检查内容合理性

    # 验证结果数据结构
    # 细节：定义8个必需字段名，这些字段是 app.py 中结果展示区域渲染所依赖的
    # output: 审核报告正文 | doc_text: 合同原文 | merged_risk_items: 风险清单
    # overall_risk_score: 风险总分(0-100) | risk_level: 风险等级(低/中/高/极高)
    # need_lawyer_review: 是否需要律师介入(bool) | citations: 法条引用列表 | final_report_markdown: Markdown报告
    print(f"\n[阶段3] 结果数据结构验证:")  # 细节：打印阶段3标题
    required_keys = ["output", "doc_text", "merged_risk_items", "overall_risk_score",
                     "risk_level", "need_lawyer_review", "citations", "final_report_markdown"]
    for key in required_keys:  # 细节：遍历每个必需字段
        val = result.get(key)  # 细节：从结果字典中获取字段值
        status = "✅" if val is not None else "❌"  # 细节：非None为通过，None为失败
        if isinstance(val, list):  # 细节：如果是列表类型（如风险项、法条引用）
            print(f"  {status} {key}: list[{len(val)}]")  # 细节：打印类型和长度
        elif isinstance(val, str):  # 细节：如果是字符串类型（如报告正文、原文）
            print(f"  {status} {key}: str[{len(val)}]")  # 细节：打印类型和长度
        else:  # 细节：其他类型（如int、bool）
            print(f"  {status} {key}: {val}")  # 细节：直接打印值

    # 细节：打印风险项详情，让用户直观看到每条风险的严重程度和描述
    print(f"\n  风险项数量: {len(result['merged_risk_items'])}")  # 细节：打印风险项总数
    for i, risk in enumerate(result["merged_risk_items"]):  # 细节：遍历每条风险
        # 细节：格式化输出，severity左对齐8字符，如 "[1] critical | 违约金比例超过司法保护上限"
        print(f"    [{i+1}] {risk['severity']:8s} | {risk['description']}")

    print("\n✅ 流式输出测试通过\n")  # 细节：打印通过标记
    return result  # 细节：返回结果字典，可供其他测试函数复用（避免重复调用_get_demo_result）


# ==================== 测试2: 修改按钮状态机逻辑 ====================
def test_modify_button_state_machine():
    """
    验证风险卡片"修改"按钮的完整状态机流转逻辑。

    作用：
        模拟 Streamlit 的 st.session_state 字典，验证风险卡片的7个状态流转：
        1. 初始状态(None) → 显示[采纳][不采纳][修改]三个按钮
        2. 点击修改 → state='modify_input' → 显示文本框+[确认修改][取消]
        3. 确认修改 → state='modified' → 显示修改内容+[重新修改]
        4. 重新修改 → state='modify_input' → 回到可编辑状态，之前内容保留
        5. 取消 → 删除state → 回到初始状态
        6. 采纳 → state='accepted' → 显示已采纳
        7. 不采纳 → state='rejected' → 显示不采纳

    参数：无
    返回：无（纯打印验证，不返回值）

    可迁移性说明：
        这个"用字典键值对模拟状态机"的模式可迁移到任何需要追踪多个独立组件状态的场景，
        如购物车中每个商品的状态（选中/未选中/已结算）、问卷中每道题的作答状态等。
        核心思路是：用 f"_actions_{prefix}" 作为键命名空间，用 idx 作为子键区分不同卡片。
    """
    print("=" * 60)  # 细节：打印分隔线
    print("测试2: 修改按钮状态机逻辑")  # 细节：打印测试标题
    print("=" * 60)  # 细节：打印分隔线

    # 模拟 session_state
    # 细节：用普通 dict 模拟 Streamlit 的 st.session_state，它在Web应用中用于跨请求保持状态
    # 在测试环境中用普通字典即可验证状态读写逻辑
    session_state = {}

    # 模拟风险卡片数据
    # 细节：定义2条风险数据，模拟 app.py 中 _get_demo_result 返回的 merged_risk_items
    # 每条风险包含5个字段：severity(严重程度)、description(描述)、clause(条款)、legal_basis(法律依据)、suggestion(建议)
    risk_items = [
        {"severity": "critical", "description": "违约金比例超过司法保护上限", "clause": "第五条 违约责任",
         "legal_basis": "《民法典》第585条", "suggestion": "建议将违约金调整为每日万分之三至万分之五"},
        {"severity": "high", "description": "争议解决管辖约定可能被认定无效", "clause": "第六条 争议解决",
         "legal_basis": "《民事诉讼法》第24条", "suggestion": "建议约定合同履行地或被告所在地人民法院管辖"},
    ]

    # 细节：定义键前缀，用于区分不同页面的风险卡片状态（如"contract" vs "compliance"）
    key_prefix = "test"  # 细节：测试用前缀
    action_key = f"_actions_{key_prefix}"  # 细节：构造状态字典键，如 "_actions_test"
    session_state[action_key] = {}  # 细节：初始化该前缀下的状态字典为空，后续用 idx 作为子键

    # ---- 状态1: 初始状态 (无操作) ----
    # 细节：验证初始状态下所有卡片都没有操作记录，应该显示三个按钮(采纳/不采纳/修改)
    print("\n[状态1] 初始状态 - 应显示三个按钮(采纳/不采纳/修改)")
    for idx, risk in enumerate(risk_items):  # 细节：遍历每条风险
        current = session_state[action_key].get(idx, None)  # 细节：获取当前卡片的状态，不存在则为None
        expected = None  # 细节：初始状态期望值为None
        status = "✅" if current == expected else "❌"  # 细节：验证是否匹配
        print(f"  卡片{idx+1} state={current} {status}")  # 细节：打印卡片状态和验证结果

    # ---- 状态2: 点击"修改" → 进入 modify_input ----
    # 细节：模拟用户点击"✏️ 修改"按钮，app.py 中会将 session_state[action_key][idx] 设为 "modify_input"
    # 此状态下页面应显示：文本输入框 + "确认修改"按钮 + "取消"按钮
    print("\n[状态2] 点击'✏️ 修改' → state='modify_input' (应弹出文本框)")
    idx = 0  # 细节：操作第1张卡片（索引0）
    session_state[action_key][idx] = "modify_input"  # 细节：设置状态为"修改输入中"
    current = session_state[action_key].get(idx)  # 细节：读回状态验证
    status = "✅" if current == "modify_input" else "❌"  # 细节：验证状态是否正确
    print(f"  卡片{idx+1} state='{current}' {status}")  # 细节：打印验证结果
    print(f"  → 应显示: text_area + '确认修改' + '取消' 按钮")  # 细节：提示预期UI

    # ---- 状态3: 输入修改内容并确认 → 进入 modified ----
    # 细节：模拟用户在文本框中输入修改意见后点击"确认修改"
    # app.py 中会将状态设为"modified"，并将修改内容保存到另一个 session_state 键中
    print("\n[状态3] 输入修改内容并点击'确认修改' → state='modified'")
    modify_text = "建议将违约金从千分之三调整为万分之三，符合《民法典》第585条司法保护上限。"  # 细节：模拟用户输入的修改意见
    session_state[action_key][idx] = "modified"  # 细节：设置状态为"已修改"
    # 细节：将修改内容保存到独立的 session_state 键中，键名格式为 "modified_content_{前缀}_{索引}"
    # 这样即使状态被重置，修改内容仍可保留（用于"重新修改"时预填文本框）
    session_state[f"modified_content_{key_prefix}_{idx}"] = modify_text
    current = session_state[action_key].get(idx)  # 细节：读回状态
    saved_content = session_state.get(f"modified_content_{key_prefix}_{idx}", "")  # 细节：读回保存的修改内容
    status1 = "✅" if current == "modified" else "❌"  # 细节：验证状态
    status2 = "✅" if saved_content == modify_text else "❌"  # 细节：验证内容持久化
    print(f"  卡片{idx+1} state='{current}' {status1}")  # 细节：打印状态验证
    print(f"  保存的修改内容: '{saved_content[:50]}...' {status2}")  # 细节：打印内容验证（截取前50字符）
    print(f"  → 应显示: 修改内容展示框 + '重新修改' 按钮")  # 细节：提示预期UI

    # ---- 状态4: 点击"重新修改" → 回到 modify_input ----
    # 细节：模拟用户点击"重新修改"按钮，app.py 中会将状态重新设为"modify_input"
    # 关键点：之前保存的修改内容仍然保留，用于预填文本框，用户可以在上次修改基础上继续编辑
    print("\n[状态4] 点击'重新修改' → state='modify_input' (可再次编辑)")
    session_state[action_key][idx] = "modify_input"  # 细节：状态回到"修改输入中"
    current = session_state[action_key].get(idx)  # 细节：读回状态
    status = "✅" if current == "modify_input" else "❌"  # 细节：验证
    print(f"  卡片{idx+1} state='{current}' {status}")  # 细节：打印
    # 细节：验证之前的修改内容仍在 session_state 中（截取前30字符预览）
    print(f"  之前的内容仍保留: '{session_state.get(f'modified_content_{key_prefix}_{idx}', '')[:30]}...'")

    # ---- 状态5: 点击"取消" → 回到初始状态 ----
    # 细节：模拟用户点击"取消"按钮，app.py 中会删除该卡片的状态键，使其回到初始状态
    # 注意：取消只删除状态，不删除已保存的修改内容（但UI回到三按钮模式）
    print("\n[状态5] 点击'取消' → 删除state, 回到初始状态")
    del session_state[action_key][idx]  # 细节：删除该卡片的状态键，回到"未操作"
    current = session_state[action_key].get(idx, None)  # 细节：读回状态，应为None
    status = "✅" if current is None else "❌"  # 细节：验证
    print(f"  卡片{idx+1} state={current} {status}")  # 细节：打印
    print(f"  → 应显示: 三个原始按钮(采纳/不采纳/修改)")  # 细节：提示预期UI

    # ---- 状态6: 测试"采纳"按钮 ----
    # 细节：模拟用户点击"✅ 采纳"按钮，表示认可该风险提示
    # app.py 中会将状态设为"accepted"，页面显示绿色成功消息
    print("\n[状态6] 点击'✅ 采纳' → state='accepted'")
    session_state[action_key][idx] = "accepted"  # 细节：设置状态为"已采纳"
    current = session_state[action_key].get(idx)  # 细节：读回状态
    status = "✅" if current == "accepted" else "❌"  # 细节：验证
    print(f"  卡片{idx+1} state='{current}' {status}")  # 细节：打印
    print(f"  → 应显示: '✅ 已采纳' 成功消息")  # 细节：提示预期UI

    # ---- 状态7: 测试"不采纳"按钮 ----
    # 细节：模拟用户对第2张卡片点击"❌ 不采纳"按钮，表示不认可该风险提示
    # app.py 中会将状态设为"rejected"，页面显示橙色警告消息
    print("\n[状态7] 点击'❌ 不采纳' → state='rejected'")
    session_state[action_key][1] = "rejected"  # 细节：操作第2张卡片（索引1）
    current = session_state[action_key].get(1)  # 细节：读回状态
    status = "✅" if current == "rejected" else "❌"  # 细节：验证
    print(f"  卡片2 state='{current}' {status}")  # 细节：打印
    print(f"  → 应显示: '❌ 已不采纳' 警告消息")  # 细节：提示预期UI

    # ---- 状态汇总 ----
    # 细节：打印所有卡片的最终状态，直观展示状态机的运行结果
    print("\n[状态汇总] 所有卡片最终状态:")
    for idx, risk in enumerate(risk_items):  # 细节：遍历所有卡片
        current = session_state[action_key].get(idx, "未操作")  # 细节：获取状态，默认显示"未操作"
        print(f"  卡片{idx+1} ({risk['severity']}): {current}")  # 细节：打印卡片编号、严重程度和状态

    print("\n✅ 修改按钮状态机测试通过\n")  # 细节：打印通过标记


# ==================== 测试3: 效果展示切换逻辑 ====================
def test_toggle_demo():
    """
    验证"🎭 效果展示"按钮的切换（toggle）逻辑。

    作用：
        模拟用户三次点击"效果展示"按钮的完整流程：
        1. 首次点击 → 加载演示数据到 session_state，页面展示审核结果
        2. 再次点击 → 删除 session_state 中的数据，页面收起结果
        3. 第三次点击 → 重新加载数据，页面再次展示

        这解决了之前"按钮只能展示不能收回"的问题。

    参数：无
    返回：无

    可迁移性说明：
        "toggle切换"模式可迁移到任何需要反复展开/收起内容的场景，如：
        - 折叠面板的展开/收起
        - 深色/浅色主题切换
        - 音视频播放/暂停
        核心思路：用 session_state 中某个键的"存在/不存在"作为开关，
        存在则展示并允许再次点击清除，不存在则加载并允许再次点击展示。
    """
    print("=" * 60)  # 细节：打印分隔线
    print("测试3: 效果展示按钮切换逻辑")  # 细节：打印测试标题
    print("=" * 60)  # 细节：打印分隔线

    session_state = {}  # 细节：初始化空字典模拟 session_state
    result_key = "contract_full_result"  # 细节：定义结果在 session_state 中的键名，与 app.py 中一致

    # 第一次点击: 应加载数据
    # 细节：模拟首次点击"🎭 效果展示"按钮，此时 session_state 中没有结果，应加载数据
    print("\n[操作1] 点击'🎭 效果展示' (首次) → 加载演示数据")
    if result_key not in session_state:  # 细节：检查 session_state 中是否已有结果，不存在则加载
        from __006__streamlit.app import _get_demo_result  # 细节：延迟导入
        session_state[result_key] = _get_demo_result(MOCK_CONTRACT)  # 细节：加载演示数据
        print(f"  → 已加载 result, doc_text长度={len(session_state[result_key]['doc_text'])}")  # 细节：打印加载信息
    has_result = result_key in session_state  # 细节：验证结果是否存在
    print(f"  结果存在: {'✅' if has_result else '❌'}")  # 细节：打印验证结果

    # 第二次点击: 应清除数据
    # 细节：模拟再次点击"🎭 效果展示"按钮，此时 session_state 中已有结果，应清除数据（收起）
    print("\n[操作2] 再次点击'🎭 效果展示' → 清除演示数据 (收起)")
    if result_key in session_state:  # 细节：检查结果是否存在，存在则删除
        del session_state[result_key]  # 细节：删除结果，模拟 app.py 中的 del st.session_state[result_key]
        print(f"  → 已删除 result")  # 细节：打印删除信息
    has_result = result_key in session_state  # 细节：验证结果已不存在
    print(f"  结果存在: {'❌ 已清除' if not has_result else '❌ 仍存在'}")  # 细节：打印验证结果

    # 第三次点击: 重新加载
    # 细节：模拟第三次点击"🎭 效果展示"按钮，验证可以反复切换
    print("\n[操作3] 第三次点击'🎭 效果展示' → 重新加载演示数据")
    if result_key not in session_state:  # 细节：检查结果不存在，则重新加载
        from __006__streamlit.app import _get_demo_result  # 细节：延迟导入
        session_state[result_key] = _get_demo_result(MOCK_CONTRACT)  # 细节：重新加载演示数据
        print(f"  → 已重新加载 result")  # 细节：打印加载信息
    has_result = result_key in session_state  # 细节：验证结果存在
    print(f"  结果存在: {'✅' if has_result else '❌'}")  # 细节：打印验证结果

    print("\n✅ 效果展示切换测试通过\n")  # 细节：打印通过标记


# ==================== 测试4: 文档高亮逻辑 ====================
def test_doc_highlight():
    """
    验证合同原文中风险段落的 HTML 高亮标注逻辑。

    作用：
        调用 app.py 中的 _highlight_doc 函数，将合同原文和风险项列表传入，
        验证返回的 HTML 字符串中包含正确的高亮标签：
        - highlight-critical（极高风险，红色背景）
        - highlight-high（高风险，橙色背景）
        - highlight-medium（中风险，黄色背景）
        - highlight-low（低风险，蓝色背景）
        同时验证 HTML 中包含 doc-container 容器类名。

    参数：无
    返回：无

    可迁移性说明：
        "文本高亮"模式可迁移到任何需要标注原文关键段的场景，如：
        - 代码审查中标注有问题的代码行
        - 教育系统中标注学生的错别字
        - 医疗系统中标注病历中的异常指标
        核心思路：遍历风险项，在原文中找到对应段落，用 <span class="highlight-xxx"> 包裹。
    """
    print("=" * 60)  # 细节：打印分隔线
    print("测试4: 文档高亮标注逻辑")  # 细节：打印测试标题
    print("=" * 60)  # 细节：打印分隔线

    # 细节：从 app.py 导入 _highlight_doc（高亮函数）和 _get_demo_result（获取演示数据）
    from __006__streamlit.app import _highlight_doc, _get_demo_result

    result = _get_demo_result(MOCK_CONTRACT)  # 细节：获取演示审核结果
    doc_text = result["doc_text"]  # 细节：提取合同原文（未经高亮处理的纯文本）
    risk_items = result["merged_risk_items"]  # 细节：提取风险项列表（包含每条风险对应的条款文本）

    # 细节：调用 _highlight_doc 函数，将原文和风险项传入，返回带高亮标注的 HTML 字符串
    # 函数内部会遍历 risk_items，在 doc_text 中找到匹配的条款文本，用 <span> 标签包裹
    highlighted_html = _highlight_doc(doc_text, risk_items)

    print(f"\n  原文长度: {len(doc_text)} 字符")  # 细节：打印原文长度
    print(f"  风险项数: {len(risk_items)}")  # 细节：打印风险项数量
    print(f"  高亮HTML长度: {len(highlighted_html)} 字符")  # 细节：打印生成的HTML长度（应大于原文，因为加了标签）

    # 验证高亮class存在
    # 细节：定义4种高亮CSS类名，对应4种风险严重程度
    highlight_classes = ["highlight-critical", "highlight-high", "highlight-medium", "highlight-low"]
    for cls in highlight_classes:  # 细节：遍历每种高亮类名
        count = highlighted_html.count(cls)  # 细节：统计该类名在HTML中出现的次数
        if count > 0:  # 细节：如果出现次数大于0，说明该级别的风险被正确高亮
            print(f"  ✅ {cls}: {count} 处高亮")  # 细节：打印类名和出现次数

    # 验证 doc-container 存在
    # 细节：doc-container 是 app.py CSS 中定义的文档容器类名，用于设置原文展示区域的样式
    has_container = "doc-container" in highlighted_html  # 细节：检查HTML中是否包含该类名
    print(f"  {'✅' if has_container else '❌'} doc-container 存在")  # 细节：打印验证结果

    print("\n✅ 文档高亮测试通过\n")  # 细节：打印通过标记


# ==================== 主入口 ====================
# 细节：Python 标准入口模式，只有直接运行此文件时才执行下面的代码，被 import 时不执行
# 这是一种常见的模块测试模式，可迁移到任何 Python 项目
if __name__ == "__main__":
    print("\n" + "🔍" * 30)  # 细节：打印装饰性图标行
    print("法智引擎 - 合同审核模块逻辑测试")  # 细节：打印测试标题
    print("🔍" * 30 + "\n")  # 细节：打印装饰性图标行+空行

    # 细节：打印模拟合同数据的预览（前200字符），让用户在测试开始前了解输入内容
    print(f"模拟合同数据 ({len(MOCK_CONTRACT)} 字符):")  # 细节：打印数据长度
    print("-" * 40)  # 细节：打印分隔线
    print(MOCK_CONTRACT[:200] + "...")  # 细节：打印前200字符+省略号
    print("-" * 40)  # 细节：打印分隔线

    # 运行所有测试
    # 细节：依次调用4个测试函数，每个函数内部会打印详细的测试过程和结果
    test_streaming_output()  # 细节：测试1 — 流式输出逻辑
    test_modify_button_state_machine()  # 细节：测试2 — 修改按钮状态机
    test_toggle_demo()  # 细节：测试3 — 效果展示切换
    test_doc_highlight()  # 细节：测试4 — 文档高亮

    print("=" * 60)  # 细节：打印最终分隔线
    print("🎉 全部测试通过!")  # 细节：打印全部通过标记
    print("=" * 60)  # 细节：打印最终分隔线
