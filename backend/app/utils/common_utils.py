# 1 导入依赖
import os
import datetime
import hashlib
import tomllib
import re
from pathlib import Path
from app.schemas.enums import CompTemplate
from app.utils.log_util import logger
import pypandoc
from app.config.setting import settings
from icecream import ic

# 2 辅助常量
# 2.1 工作目录基路径（相对于项目根）
WORK_BASE = Path(__file__).resolve().parents[2] / "project" / "work_dir"
WORK_BASE.mkdir(parents=True, exist_ok=True)


# 3 工具函数
# 3.1 生成任务ID
# 3.1.1 说明：改为顺序编号 001、002、003...，逻辑：
#   1) 扫描 WORK_BASE 下已有的子目录名
#   2) 目录名为纯数字的视为有效，取最大值 + 1
#   3) 起始值为 001
def create_task_id() -> str:
    """生成顺序三位编号任务ID（001、002...）"""
    work_base: Path = WORK_BASE
    work_base.mkdir(parents=True, exist_ok=True)

    max_n = 0
    for p in work_base.iterdir():
        if p.is_dir():
            m = re.fullmatch(r"(\d{1,})", p.name)
            if m:
                try:
                    n = int(m.group(1))
                    if n > max_n:
                        max_n = n
                except ValueError:
                    # 非数字目录忽略
                    pass

    next_id = f"{max_n + 1:03d}"  # 三位补零，如 001
    return next_id


# 3.2 创建工作目录
def create_work_dir(task_id: str) -> str:
    # 设置主工作目录和子目录
    work_dir = os.path.join("project", "work_dir", task_id)

    try:
        # 创建目录，如果目录已存在也不会报错
        os.makedirs(work_dir, exist_ok=True)
        return work_dir
    except Exception as e:
        # 捕获并记录创建目录时的异常
        logger.error(f"创建工作目录失败: {str(e)}")
        raise


# 3.3 获取工作目录
def get_work_dir(task_id: str) -> str:
    work_dir = os.path.join("project", "work_dir", task_id)
    if os.path.exists(work_dir):
        return work_dir
    else:
        logger.error(f"工作目录不存在: {work_dir}")
        raise FileNotFoundError(f"工作目录不存在: {work_dir}")


# 3.4 获取配置模板
# 3.4.1 TODO: 是不是应该将 Prompt 写成一个 class
def get_config_template(comp_template: CompTemplate = CompTemplate.CHINA) -> dict:
    if comp_template == CompTemplate.CHINA:
        return load_toml(os.path.join("app", "config", "md_template.toml"))


# 3.5 加载 TOML
def load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


# 3.6 加载 Markdown
def load_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# 3.7 列出当前目录下文件
def get_current_files(folder_path: str, type: str = "all") -> list[str]:
    files = os.listdir(folder_path)
    if type == "all":
        return files
    elif type == "md":
        return [file for file in files if file.endswith(".md")]
    elif type == "ipynb":
        return [file for file in files if file.endswith(".ipynb")]
    elif type == "data":
        return [
            file for file in files if file.endswith(".xlsx") or file.endswith(".csv")
        ]
    elif type == "image":
        return [
            file for file in files if file.endswith(".png") or file.endswith(".jpg")
        ]


# 3.8 转换图片链接为静态 URL
# 3.8.1 判断 content 是否包含图片 xx.png, 对其处理为
#    ![filename](http://localhost:8000/static/{task_id}/filename.jpg)
def transform_link(task_id: str, content: str):
    content = re.sub(
        r"!\[(.*?)\]\((.*?\.(?:png|jpg|jpeg|gif|bmp|webp))\)",
        lambda match: f"![{match.group(1)}]({settings.SERVER_HOST}/static/{task_id}/{match.group(2)})",
        content,
    )
    return content


# 3.9 将 Markdown 转为 DOCX
# 3.9.1 TODO: fix 公式显示
def md_2_docx(task_id: str):
    work_dir = get_work_dir(task_id)
    md_path = os.path.join(work_dir, "res.md")
    docx_path = os.path.join(work_dir, "res.docx")

    extra_args = [
        "--resource-path",
        str(work_dir),
        "--mathml",  # MathML 格式公式
        "--standalone",
    ]

    pypandoc.convert_file(
        source_file=md_path,
        to="docx",
        outputfile=docx_path,
        format="markdown+tex_math_dollars",
        extra_args=extra_args,
    )
    print(f"转换完成: {docx_path}")
    logger.info(f"转换完成: {docx_path}")


# 3.10 拆分正文与脚注
def split_footnotes(text: str) -> tuple[str, list[tuple[str, str]]]:
    main_text = re.sub(
        r"\n\[\^\d+\]:.*?(?=\n\[\^|\n\n|\Z)", "", text, flags=re.DOTALL
    ).strip()

    # 匹配脚注定义
    footnotes = re.findall(r"\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\n\n|\Z)", text, re.DOTALL)
    logger.info(f"main_text:{main_text} \n footnotes:{footnotes}")
    return main_text, footnotes
