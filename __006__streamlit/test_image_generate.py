"""
小红书图片生成节点 — Mock 本地测试脚本

测试目标:
    1. 验证 sanitize_title_for_filename() 文件名生成逻辑
    2. 验证 generate_legal_image_prompt() 提示词构造
    3. 验证 generate_placeholder_image() 占位图生成与保存
    4. 验证 image_generator_node() 完整节点流程（含降级路径）
    5. 模拟即梦AI可用场景（mock generate_image 返回假路径）

运行方式:
    cd e:\\to_github_project\\AI_legal_assistant
    python -m __006__streamlit.test_image_generate

前置条件:
    - 安装 matplotlib（占位图生成依赖）
    - 无需即梦AI密钥（测试脚本会自动mock）
"""
import os
import sys
import time
from unittest.mock import patch, MagicMock

# 确保项目根目录在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入被测模块
from __004__langgraph_more_nodes.nodes.xhs_publish_nodes.image_generate_node import (
    sanitize_title_for_filename,
    generate_legal_image_prompt,
    generate_placeholder_image,
    image_generator_node,
)
from common.path_utils import get_file_path


def print_separator(title):
    """打印分隔线，便于区分不同测试段落"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_sanitize_title():
    """测试1: 文件名清洗函数"""
    print_separator("测试1: sanitize_title_for_filename()")
    test_cases = [
        ("劳动合同维权指南", "应截取前5字符"),
        ("abc", "短标题正常处理"),
        ("", "空标题应返回时间戳+.png"),
        ("法智引擎2026年合同审核报告", "长标题截取"),
    ]
    all_pass = True
    for title, desc in test_cases:
        result = sanitize_title_for_filename(title)
        # 验证: 以.png结尾
        has_png = result.endswith(".png")
        # 验证: 时间戳部分为14位数字
        timestamp_part = result[:14] if len(result) >= 14 else ""
        is_timestamp = timestamp_part.isdigit() and len(timestamp_part) == 14
        # 验证: 标题部分正确截取
        expected_title_part = title[:5] if title else ""
        title_part = result[14:-4] if len(result) > 18 else ""
        title_match = title_part == expected_title_part

        status = "✅" if (has_png and is_timestamp and title_match) else "❌"
        if status == "❌":
            all_pass = False
        print(f"  {status} 输入='{title}' → 文件名='{result}'  ({desc})")
        print(f"     .png后缀={has_png}, 时间戳合法={is_timestamp}, 标题匹配={title_match}")

    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def test_prompt_generation():
    """测试2: 提示词构造函数"""
    print_separator("测试2: generate_legal_image_prompt()")
    title = "劳动合同维权指南"
    content = "劳动者在签订合同时应注意..."
    prompt = generate_legal_image_prompt(title, content)

    checks = [
        ("包含标题", title in prompt),
        ("包含'法律'", "法律" in prompt),
        ("包含'天平'", "天平" in prompt),
        ("包含'不能有任何文字'", "不能有任何文字" in prompt),
        ("包含'扁平插画'", "扁平插画" in prompt),
    ]
    all_pass = True
    for desc, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {desc}")

    print(f"\n  完整提示词预览:\n  {prompt[:120]}...")
    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def test_placeholder_image():
    """测试3: 占位图生成与文件保存"""
    print_separator("测试3: generate_placeholder_image()")
    title = "劳动合同维权指南"
    save_dir = get_file_path("assets/images")
    os.makedirs(save_dir, exist_ok=True)
    output_path = os.path.join(save_dir, f"test_placeholder_{int(time.time())}.png")

    print(f"  保存路径: {output_path}")
    result = generate_placeholder_image(title, output_path)

    checks = [
        ("返回值不为None", result is not None),
        ("返回值等于输出路径", result == output_path),
        ("文件实际存在", os.path.exists(output_path)),
        ("文件大小>0", os.path.getsize(output_path) > 0 if os.path.exists(output_path) else False),
    ]
    all_pass = True
    for desc, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {desc}")

    if os.path.exists(output_path):
        size_kb = os.path.getsize(output_path) / 1024
        print(f"\n  文件大小: {size_kb:.1f} KB")
        print(f"  文件路径: {output_path}")

    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def test_node_full_flow_placeholder():
    """测试4: 完整节点流程（无即梦AI密钥 → 降级占位图）"""
    print_separator("测试4: image_generator_node() 降级路径")
    mock_state = {
        "xiaohongshu_title": "二手房买卖合同注意事项",
        "xiaohongshu_content": "买方在签订二手房买卖合同时，应注意产权清晰...",
    }
    print(f"  输入 state: title='{mock_state['xiaohongshu_title']}'")
    print(f"  预期: 即梦AI未配置 → 降级生成占位图\n")

    result_state = image_generator_node(state=mock_state)

    checks = [
        ("state含xiaohongshu_image_path_list", "xiaohongshu_image_path_list" in result_state),
        ("图片路径列表非空", len(result_state.get("xiaohongshu_image_path_list", [])) > 0),
        ("tip为'图片生成成功'", result_state.get("xiaohongshu_tip") == "图片生成成功"),
        ("图片文件实际存在",
         os.path.exists(result_state["xiaohongshu_image_path_list"][0])
         if result_state.get("xiaohongshu_image_path_list") else False),
    ]
    all_pass = True
    for desc, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {desc}")

    if result_state.get("xiaohongshu_image_path_list"):
        path = result_state["xiaohongshu_image_path_list"][0]
        size_kb = os.path.getsize(path) / 1024
        print(f"\n  生成图片路径: {path}")
        print(f"  文件大小: {size_kb:.1f} KB")

    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def test_node_mock_jimeng_ai():
    """测试5: 模拟即梦AI可用场景"""
    print_separator("测试5: image_generator_node() 模拟即梦AI成功")
    mock_state = {
        "xiaohongshu_title": "民间借贷利息计算",
        "xiaohongshu_content": "民间借贷利率上限为LPR的4倍...",
    }

    # 创建一个假的图片文件作为mock返回值
    save_dir = get_file_path("assets/images")
    os.makedirs(save_dir, exist_ok=True)
    mock_image_path = os.path.join(save_dir, f"test_mock_jimeng_{int(time.time())}.png")

    # 用matplotlib先生成一张真实图片作为mock
    generate_placeholder_image("民间借贷利息计算", mock_image_path)

    print(f"  输入 state: title='{mock_state['xiaohongshu_title']}'")
    print(f"  Mock: generate_image() → 返回假路径")
    print(f"  Mock图片: {mock_image_path}\n")

    # patch generate_image 使其返回mock路径，同时patch Config使其假装有AK/SK
    with patch("__004__langgraph_more_nodes.nodes.xhs_publish_nodes.image_generate_node.generate_image",
               return_value=mock_image_path) as mock_gen, \
         patch("__004__langgraph_more_nodes.nodes.xhs_publish_nodes.image_generate_node.conf") as mock_conf:
        mock_conf.JIMENG_AK = "fake_ak"
        mock_conf.JIMENG_SK = "fake_sk"

        result_state = image_generator_node(state=mock_state)

    checks = [
        ("generate_image被调用1次", mock_gen.call_count == 1),
        ("state含图片路径", len(result_state.get("xiaohongshu_image_path_list", [])) > 0),
        ("tip为'图片生成成功'", result_state.get("xiaohongshu_tip") == "图片生成成功"),
        ("图片路径等于mock路径",
         result_state.get("xiaohongshu_image_path_list", [None])[0] == mock_image_path),
        ("图片文件实际存在", os.path.exists(mock_image_path)),
    ]
    all_pass = True
    for desc, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {desc}")

    # 验证传入generate_image的参数
    if mock_gen.call_count > 0:
        call_args = mock_gen.call_args
        prompt_arg = call_args[0][0]  # 第一个位置参数
        path_arg = call_args[0][1]    # 第二个位置参数
        print(f"\n  generate_image 调用参数:")
        print(f"    prompt长度: {len(prompt_arg)} 字符")
        print(f"    output_path: {path_arg}")
        print(f"    prompt含标题: {'民间借贷利息计算' in prompt_arg}")

    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def test_node_empty_title():
    """测试6: 边界情况 — 空标题"""
    print_separator("测试6: image_generator_node() 空标题边界")
    mock_state = {
        "xiaohongshu_title": "",
        "xiaohongshu_content": "",
    }
    print(f"  输入: 空标题 + 空内容")
    print(f"  预期: 使用默认标题'法律科普'，降级生成占位图\n")

    result_state = image_generator_node(state=mock_state)

    checks = [
        ("节点正常返回不报错", result_state is not None),
        ("图片路径列表非空", len(result_state.get("xiaohongshu_image_path_list", [])) > 0),
        ("图片文件实际存在",
         os.path.exists(result_state["xiaohongshu_image_path_list"][0])
         if result_state.get("xiaohongshu_image_path_list") else False),
    ]
    all_pass = True
    for desc, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {desc}")

    print(f"\n  结果: {'全部通过' if all_pass else '存在失败'}")
    return all_pass


def main():
    """主测试入口"""
    print("\n" + "🔴" * 30)
    print("  小红书图片生成节点 — Mock 本地测试")
    print("🔴" * 30)

    results = []
    results.append(("文件名清洗", test_sanitize_title()))
    results.append(("提示词构造", test_prompt_generation()))
    results.append(("占位图生成", test_placeholder_image()))
    results.append(("节点降级流程", test_node_full_flow_placeholder()))
    results.append(("模拟即梦AI", test_node_mock_jimeng_ai()))
    results.append(("空标题边界", test_node_empty_title()))

    print_separator("测试汇总")
    passed = sum(1 for _, r in results if r)
    total = len(results)
    for name, r in results:
        print(f"  {'✅' if r else '❌'} {name}")
    print(f"\n  总计: {passed}/{total} 通过")

    if passed == total:
        print("\n  🎉 所有测试通过！图片生成与保存逻辑正常。")
    else:
        print(f"\n  ⚠️ {total - passed} 个测试未通过，请检查上方日志。")


if __name__ == "__main__":
    main()
