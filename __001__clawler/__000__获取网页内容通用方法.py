# -*- coding: utf-8 -*-
# 📜 代码文字逻辑解析
# 本文件是 AI 法律助理项目中爬虫模块的通用基础工具层，提供两个核心函数：fetch_web_content 和
# extract_text_from_soup。前者负责向目标 URL 发起 HTTP GET 请求，内置浏览器级别 User-Agent 伪装、
# 自动编码探测、超时控制、以及最多 max_retries 次的指数级随机退避重试机制，能够稳健地应对网络抖动、
# 服务器限流、连接中断等异常情况，最终返回解析好的 BeautifulSoup 对象。后者则基于 CSS 选择器优先
# 定位正文容器，再遍历常见语义标签（h1-h4、p、ul、ol、div、article、section），将 HTML 结构化内容
# 转换为按行组织的纯文本，列表项自动添加 "- " 前缀。文件顶部还针对 Windows 控制台默认 GBK 编码导致
# 中文/特殊符号输出乱码的问题，统一将 stdout/stderr 重配置为 UTF-8。该文件被 __002__crawl_law_database.py
# 等后续爬虫脚本导入复用，是整个数据采集流程的底层依赖。
import os  # 导入 os 模块，用于操作系统相关功能（本文件中实际未直接使用，保留以备扩展）
import sys  # 导入 sys 模块，用于访问解释器变量与标准流重配置
import requests  # 导入 requests 库，用于发起 HTTP 请求
from bs4 import BeautifulSoup  # 从 bs4 导入 BeautifulSoup，用于解析 HTML 文档
import time  # 导入 time 模块，用于重试时的 sleep 休眠
import random  # 导入 random 模块，用于生成随机退避时间，避免请求节奏过于规律

# 统一 stdout 为 UTF-8, 避免 Windows GBK 中文/符号输出错误
# 判断当前标准输出流的编码是否为 utf-8（兼容 "utf-8" 与 "utf8" 两种写法，并忽略大小写与连字符差异）
if sys.stdout.encoding.lower().replace("-", "") != "utf8":
    try:
        # 将标准输出流重新配置为 UTF-8 编码，errors="replace" 表示遇到无法解码的字符用替换符替代
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        # 同步将标准错误流也重配置为 UTF-8，保证异常日志同样不乱码
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # 若重配置失败（例如流不支持 reconfigure），则静默忽略，不阻断后续流程
        pass


def fetch_web_content(url, timeout=30, max_retries=3):
    """
    通用网页内容获取方法，带 UA、重试、错误处理

    【作用】
        向指定 URL 发起 HTTP GET 请求，伪装为 Chrome 浏览器 UA，支持自动编码探测、
        超时控制与最多 max_retries 次重试（重试间隔随次数递增并加入随机抖动），
        成功时返回解析好的 BeautifulSoup 对象，全部失败时静默返回 None。

    【参数】
        url (str): 目标网页地址
        timeout (int): 单次请求超时时间，单位秒，默认 30
        max_retries (int): 最大重试次数（含首次请求），默认 3

    【返回值】
        BeautifulSoup: 请求成功且状态码为 200 时返回基于 html.parser 的解析对象
        None: 所有重试均失败时返回 None，调用方需做空值判断

    【可迁移性说明】
        该函数与业务无关，是纯通用的 HTTP 抓取工具，可直接复用于任何需要抓取网页的
        Python 项目。迁移时仅需保证 requests、beautifulsoup4 已安装；若目标站点需要
        Cookie 或代理，可在 requests.get 中额外补充参数。
    """
    # 构造浏览器级别的请求头，伪装为 Chrome 120，避免被站点反爬策略拦截
    headers = {
        # User-Agent 声明为 Windows 平台 Chrome 120，是最常见的桌面浏览器标识
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Accept 声明客户端可接受的内容类型及优先级，HTML 优先
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        # Accept-Language 声明语言偏好，中文优先，兼容英文
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        # Accept-Encoding 声明支持的压缩算法，requests 会自动解压
        "Accept-Encoding": "gzip, deflate, br",
        # Connection 保持长连接，降低握手开销
        "Connection": "keep-alive",
        # Cache-Control 设为 max-age=0，强制获取最新内容
        "Cache-Control": "max-age=0",
    }

    # 重试循环：attempt 从 1 到 max_retries，包含首次请求
    for attempt in range(1, max_retries + 1):
        try:
            # 发起 GET 请求，传入请求头与超时时间
            response = requests.get(url, headers=headers, timeout=timeout)
            # 使用 apparent_encoding 自动探测响应真实编码（如 gb2312），探测失败时回退 utf-8
            response.encoding = response.apparent_encoding or "utf-8"
            # 判断状态码是否为 200（成功）
            if response.status_code == 200:
                # 成功：用 html.parser 解析响应文本并返回 BeautifulSoup 对象
                return BeautifulSoup(response.text, "html.parser")
            else:
                # 状态码非 200：打印警告，包含当前重试次数、状态码与 URL，便于排查
                print(f"[警告] 第 {attempt} 次请求状态码异常：{response.status_code} - {url}")
        except requests.exceptions.Timeout:
            # 捕获超时异常：服务器在 timeout 内未响应
            print(f"[警告] 第 {attempt} 次请求超时：{url}")
        except requests.exceptions.ConnectionError:
            # 捕获连接错误：DNS 解析失败、拒绝连接、网络中断等
            print(f"[警告] 第 {attempt} 次连接失败：{url}")
        except requests.exceptions.RequestException as e:
            # 捕获 requests 库的其他请求异常（如重定向过多、SSL 错误等）
            print(f"[警告] 第 {attempt} 次请求异常：{e} - {url}")
        except Exception as e:
            # 兜底捕获上述未覆盖的未知异常，防止程序因意外错误崩溃
            print(f"[警告] 第 {attempt} 次未知异常：{e} - {url}")

        # 若当前并非最后一次重试，则休眠一段时间后继续下一次尝试
        if attempt < max_retries:
            # 退避时间 = 1~3 秒随机值 × 当前重试次数，实现随次数递增的指数退避，缓解服务器压力
            sleep_time = random.uniform(1, 3) * attempt
            # 休眠 sleep_time 秒
            time.sleep(sleep_time)

    # 所有重试均失败：打印失败日志并静默返回 None，交由调用方处理
    print(f"[失败] 多次重试失败，静默跳过：{url}")
    return None


def extract_text_from_soup(soup, selectors=None):
    """
    从 BeautifulSoup 对象中提取结构化纯文本

    【作用】
        将 BeautifulSoup 解析后的 HTML 树转换为可读的纯文本。若提供 selectors，则优先按
        CSS 选择器定位正文容器；否则对整棵树遍历。遍历时只处理 h1-h4、p、ul、ol、div、
        article、section 等语义标签，列表项以 "- " 前缀输出，最终用换行符拼接为字符串。

    【参数】
        soup (BeautifulSoup): 已解析的 BeautifulSoup 对象，可为 None
        selectors (list[str] | None): CSS 选择器列表，按顺序尝试，首个命中的容器作为正文区域

    【返回值】
        str: 提取的纯文本，多个内容块以换行分隔；若 soup 为 None 则返回空字符串

    【可迁移性说明】
        该函数与法律业务无关，是通用的 HTML 正文抽取工具。迁移到其他爬虫项目时，只需调整
        selectors 列表以适配目标站点的 DOM 结构即可。对结构特别复杂的页面，可扩展对 blockquote、
        table 等标签的处理逻辑。
    """
    # 若传入的 soup 为 None（抓取失败），直接返回空字符串
    if soup is None:
        return ""

    # 初始化正文容器变量，用于存放 selectors 命中的节点
    main_div = None
    # 仅在提供了 selectors 时才尝试定位正文容器
    if selectors:
        # 遍历每个选择器，按优先级依次尝试
        for selector in selectors:
            # 使用 select_one 选取首个匹配节点
            main_div = soup.select_one(selector)
            # 一旦命中即跳出循环，采用该容器作为正文区域
            if main_div:
                break

    # 若成功定位到正文容器则使用它，否则回退到整棵 soup 树
    target = main_div if main_div else soup

    # 初始化内容行列表，用于收集各标签提取的文本
    content_lines = []
    # 在目标节点下查找所有语义标签（按文档顺序），逐一处理
    for tag in target.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "div", "article", "section"]):
        # 对于标题与段落标签，直接提取其内部文本
        if tag.name in ["h1", "h2", "h3", "h4", "p"]:
            # get_text 以空字符串作为分隔符拼接子节点文本，strip=True 去除首尾空白
            text = tag.get_text(separator="", strip=True)
            # 仅当文本非空时才加入结果列表，避免空行
            if text:
                content_lines.append(text)
        # 对于列表标签，逐条提取直接子级 li 的文本并加 "- " 前缀
        elif tag.name in ["ul", "ol"]:
            # recursive=False 表示只取直接子级 li，避免嵌套列表重复提取
            for li in tag.find_all("li", recursive=False):
                # 提取单个 li 的纯文本
                li_text = li.get_text(separator="", strip=True)
                # 非空则加入列表项前缀 "- "
                if li_text:
                    content_lines.append(f"- {li_text}")

    # 二次过滤：去除可能残留的空白行（strip 后为空的行）
    content_lines = [line for line in content_lines if line.strip()]
    # 以换行符拼接所有行，返回最终的纯文本
    return "\n".join(content_lines)


if __name__ == "__main__":
    # 模块作为主程序运行时暂无独立逻辑，保留 pass 便于直接执行测试
    pass
