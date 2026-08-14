@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   移动无用文件到 _待审阅_可删除 文件夹
echo ========================================
echo.

set DEST=_待审阅_可删除

echo [1/9] agent_state.py (旧版中医状态定义)
if exist "agent_state.py" move "agent_state.py" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [2/9] langgraph_more_nodes.py (旧版中医主入口)
if exist "langgraph_more_nodes.py" move "langgraph_more_nodes.py" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [3/9] auto_publish_xiaohongshu_node.py (旧版副本)
if exist "auto_publish_xiaohongshu_node.py" move "auto_publish_xiaohongshu_node.py" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [4/9] check_text_image_node.py (旧版副本)
if exist "check_text_image_node.py" move "check_text_image_node.py" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [5/9] image_generate_node.py (旧版副本)
if exist "image_generate_node.py" move "image_generate_node.py" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [6/9] test_qichacha_report.json (临时测试报告)
if exist "test_qichacha_report.json" move "test_qichacha_report.json" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [7/9] run_mcp_debug.bat (临时调试脚本)
if exist "run_mcp_debug.bat" move "run_mcp_debug.bat" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [8/9] docs\flowcharts\根据架构初步流程图.html (旧版流程图)
if exist "docs\flowcharts\根据架构初步流程图.html" move "docs\flowcharts\根据架构初步流程图.html" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo [9/9] docs\flowcharts\节点式流程图.html (旧版流程图)
if exist "docs\flowcharts\节点式流程图.html" move "docs\flowcharts\节点式流程图.html" "%DEST%\" >nul 2>&1 && echo   OK || echo   SKIP

echo.
echo ========================================
echo   完成! 请检查 _待审阅_可删除 文件夹
echo   确认无误后可删除该文件夹
echo ========================================
pause
