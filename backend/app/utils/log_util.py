# app/utils/log_util.py

# 1 导入依赖
from pathlib import Path
from loguru import logger as _logger
import sys
import re

# 2 Logger 初始化类
class LoggerInitializer:
    def __init__(self):
        # 2.1 backend 根目录：.../backend
        backend_dir = Path(__file__).resolve().parents[2]
        self.logs_root = backend_dir / "logs"
        self.logs_root.mkdir(parents=True, exist_ok=True)

        # 2.2 messages 目录（保留，供其它模块使用时保证存在）
        (self.logs_root / "messages").mkdir(parents=True, exist_ok=True)

        # 2.3 不创建 errors 目录（按用户要求）
        self.errors_dir = None  # 保留属性以防外部引用，但不创建目录

        # 2.4 启动时确保 launcher 子目录存在（用于编号日志）
        self.launcher_dir = self.logs_root / "launcher"
        self.launcher_dir.mkdir(parents=True, exist_ok=True)

    # 3 序号生成工具
    def _next_seq(self, folder: Path, pattern: str, pad: int = 3) -> str:
        """
        3.1 计算下一个序号（001、002、...）。
        3.2 pattern 示例： r"(\\d{3})\\.log$"
        3.3 若目录不可访问或无匹配文件，则返回 "001"（默认）。
        """
        max_n = 0
        try:
            for p in folder.iterdir():
                m = re.search(pattern, p.name)
                if m:
                    try:
                        n = int(m.group(1))
                        if n > max_n:
                            max_n = n
                    except Exception:
                        # 忽略无法解析的项
                        pass
        except Exception:
            # 如果 folder 为 None 或不存在，则直接返回 001
            return f"{1:0{pad}d}"
        return f"{max_n + 1:0{pad}d}"

    # 4 保留但不写文件的错误 sink（no-op）
    def __error_sink(self, message):
        """
        4.1 变更说明：原实现会把每条 ERROR 写入 logs/errors/errorNNN.txt。
        4.2 按要求，我们取消该行为（no-op），ERROR 仍会写入主日志文件。
        """
        return

    # 5 初始化并返回 logger
    def init_log(self):
        logger = _logger
        logger.remove()

        # 5.1 控制台 sink（不变）
        logger.add(
            sys.stdout,
            level="INFO",
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )

        # 5.2 主日志文件：按三位编号输出到 logs/launcher/{NNN}.log（轮转 + 压缩）
        seq = self._next_seq(self.launcher_dir, r"(\d{3})\.log$")
        numbered_log = self.launcher_dir / f"{seq}.log"
        logger.add(
            str(numbered_log),
            rotation="50 MB",
            encoding="utf-8",
            enqueue=False,
            backtrace=False,
            diagnose=False,
            compression="zip",
        )

        # 5.3 兼容之前行为：保留一个固定名的 app.log（可选），以便外部脚本仍可读取固定路径
        #      如果你不希望有 app.log 可以注释下面两行。
        app_log = self.logs_root / "app.log"
        logger.add(
            str(app_log),
            rotation="50 MB",
            encoding="utf-8",
            enqueue=False,
            backtrace=False,
            diagnose=False,
            compression="zip",
        )

        # 5.4 不再注册单独的 ERROR sink（不创建 errors/ 也不写 errorNNN.txt）。
        #      ERROR 级别仍会写入上面的编号日志与 app.log。

        return logger


# 6 全局 logger 实例（模块导入时初始化）
log_initializer = LoggerInitializer()
logger = log_initializer.init_log()
