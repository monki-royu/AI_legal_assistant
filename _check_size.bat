@echo off
set F=e:\to_github_project\AI_legal_assistant\docs\flowcharts\节点式流程图.html
for %%I in ("%F%") do echo SIZE_BYTES=%%~zI
find /c /v "" "%F%"
echo OK
