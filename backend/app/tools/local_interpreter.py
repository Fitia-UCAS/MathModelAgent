from app.tools.base_interpreter import BaseCodeInterpreter
from app.tools.notebook_serializer import NotebookSerializer
import jupyter_client
from jupyter_client import KernelManager
from app.utils.log_util import logger
import os
from app.services.redis_manager import redis_manager
from app.schemas.response import (
    OutputItem,
    ResultModel,
    StdErrModel,
    SystemMessage,
)


class LocalCodeInterpreter(BaseCodeInterpreter):
    def __init__(
        self,
        task_id: str,
        work_dir: str,
        notebook_serializer: NotebookSerializer,
    ):
        super().__init__(task_id, work_dir, notebook_serializer)
        self.km, self.kc = None, None
        self.interrupt_signal = False

    async def initialize(self):
        # 本地内核一般不需异步上传文件，直接切换目录即可
        # 初始化 Jupyter 内核管理器和客户端
        logger.info("初始化本地内核")

        kernel_name = (os.environ.get("MMA_KERNEL_NAME") or "").strip()
        python_exe = (os.environ.get("MMA_PYTHON_EXE") or "").strip()

        try:
            if kernel_name:
                logger.info(f"使用指定内核名称启动 Jupyter Kernel: {kernel_name}")
                try:
                    self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name=kernel_name)
                except Exception as e:
                    logger.exception(f"通过 kernel_name 启动内核失败，尝试回退到默认 'python3'：{e}")
                    self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name="python3")
            elif python_exe:
                logger.info(f"使用指定 Python 可执行文件启动 ipykernel: {python_exe}")
                km = KernelManager()
                km.kernel_cmd = [python_exe, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
                km.start_kernel()
                kc = km.client()
                kc.start_channels()
                self.km, self.kc = km, kc
            else:
                logger.info("未设置 MMA_KERNEL_NAME / MMA_PYTHON_EXE，使用默认 kernel_name='python3'")
                self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name="python3")
        except Exception:
            logger.exception("启动内核时发生异常，尝试直接用默认 python3 再次启动")
            self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name="python3")

        # 确保工作目录存在并切换
        self._create_work_dir()
        # 使用你指定的简洁预执行代码
        self._pre_execute_code()

    def _pre_execute_code(self):
        init_code = (
            "import os, sys, platform, json\n"
            f"work_dir = {repr(os.path.abspath(self.work_dir))}\n"
            "os.makedirs(work_dir, exist_ok=True)\n"
            "os.chdir(work_dir)\n"
            "print('当前工作目录:', os.getcwd())\n"
            "print('当前 Python 可执行文件:', sys.executable)\n"
            "print('Python 版本:', platform.python_version())\n"
            "\n"
            "def _detect_ques_count():\n"
            "    # 1) 显式指定优先\n"
            "    v = os.environ.get('MMA_QUES_COUNT')\n"
            "    if v and v.isdigit() and int(v) > 0:\n"
            "        return int(v)\n"
            "    # 2) 仅在 task_id 和 日志根 都明确时尝试读取日志（避免“编号日志”误用）\n"
            "    tid = os.environ.get('MMA_TASK_ID')\n"
            "    log_root = os.environ.get('MMA_LOG_ROOT')\n"
            "    if tid and log_root:\n"
            "        p = os.path.join(log_root, f'{tid}.json')\n"
            "        if os.path.exists(p):\n"
            "            try:\n"
            "                with open(p, 'r', encoding='utf-8') as f:\n"
            "                    data = json.load(f)\n"
            "                items = data if isinstance(data, list) else [data]\n"
            "                for m in reversed(items):\n"
            "                    c = m.get('content', {}) if isinstance(m, dict) else {}\n"
            "                    if isinstance(c, dict):\n"
            "                        q = c.get('ques_count')\n"
            "                        if isinstance(q, int) and q > 0:\n"
            "                            return q\n"
            "            except Exception as _e:\n"
            "                print('[初始化] 读取日志失败:', _e)\n"
            "    # 3) 回退默认\n"
            "    return 6\n"
            "\n"
            "_q_count = _detect_ques_count()\n"
            "print('\\n[题目数] 使用题目数 =', _q_count)\n"
            "\n"
            "import matplotlib as mpl\n"
            "mpl.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "try:\n"
            "    import seaborn as sns\n"
            "    sns.set_style('whitegrid')\n"
            "    sns.set_context('paper', font_scale=1.2)\n"
            "except Exception as _e:\n"
            "    print('[初始化] seaborn 不可用，跳过风格设置:', _e)\n"
            "plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']\n"
            "plt.rcParams['axes.unicode_minus'] = False\n"
            "plt.rcParams['font.family'] = 'sans-serif'\n"
            "mpl.rcParams['font.size'] = 12\n"
            "mpl.rcParams['axes.labelsize'] = 12\n"
            "mpl.rcParams['xtick.labelsize'] = 10\n"
            "mpl.rcParams['ytick.labelsize'] = 10\n"
            "\n"
            "_sections = {'eda': ['datasets','figures','reports']}\n"
            "for i in range(1, _q_count + 1):\n"
            "    _sections[f'ques{i}'] = ['datasets','figures','reports']\n"
            "_sections['sensitivity_analysis'] = ['datasets','figures','reports']\n"
            "created = []\n"
            "for sec, subs in _sections.items():\n"
            "    for sub in subs:\n"
            "        p = os.path.join(work_dir, sec, sub)\n"
            "        os.makedirs(p, exist_ok=True)\n"
            "        created.append(os.path.relpath(p, work_dir))\n"
            "print('\\n[目录初始化] 已确保存在以下路径：')\n"
            "for p in created: print(' -', p)\n"
            "print('[目录初始化] 共计:', len(created), '个子目录\\n')\n"
        )
        try:
            self.execute_code_(init_code)
        except Exception:
            logger.exception("预执行初始化代码时发生异常（但继续）")

    async def execute_code(self, code: str) -> tuple[str, bool, str]:
        logger.info(f"执行代码: {code}")
        #  添加代码到notebook
        self.notebook_serializer.add_code_cell_to_notebook(code)

        text_to_gpt: list[str] = []
        content_to_display: list[OutputItem] | None = []
        error_occurred: bool = False
        error_message: str = ""

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="开始执行代码"),
        )
        # 执行 Python 代码
        logger.info("开始在本地执行代码...")
        execution = self.execute_code_(code)
        logger.info("代码执行完成，开始处理结果...")

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="代码执行完成"),
        )

        for mark, out_str in execution:
            if mark in ("stdout", "execute_result_text", "display_text"):
                text_to_gpt.append(self._truncate_text(f"[{mark}]\n{out_str}"))
                #  添加text到notebook
                content_to_display.append(
                    ResultModel(type="result", format="text", msg=out_str)
                )
                self.notebook_serializer.add_code_cell_output_to_notebook(out_str)

            elif mark in (
                "execute_result_png",
                "execute_result_jpeg",
                "display_png",
                "display_jpeg",
            ):
                # TODO: 视觉模型解释图像
                text_to_gpt.append(f"[{mark} 图片已生成，内容为 base64，未展示]")

                #  添加image到notebook
                if "png" in mark:
                    self.notebook_serializer.add_image_to_notebook(out_str, "image/png")
                    content_to_display.append(
                        ResultModel(type="result", format="png", msg=out_str)
                    )
                else:
                    self.notebook_serializer.add_image_to_notebook(
                        out_str, "image/jpeg"
                    )
                    content_to_display.append(
                        ResultModel(type="result", format="jpeg", msg=out_str)
                    )

            elif mark == "error":
                error_occurred = True
                error_message = self.delete_color_control_char(out_str)
                error_message = self._truncate_text(error_message)
                logger.error(f"执行错误: {error_message}")
                text_to_gpt.append(error_message)
                #  添加error到notebook
                self.notebook_serializer.add_code_cell_error_to_notebook(out_str)
                content_to_display.append(StdErrModel(msg=out_str))

        logger.info(f"text_to_gpt: {text_to_gpt}")
        combined_text = "\n".join(text_to_gpt)

        await self._push_to_websocket(content_to_display)

        return (
            combined_text,
            error_occurred,
            error_message,
        )

    def execute_code_(self, code) -> list[tuple[str, str]]:
        msg_id = self.kc.execute(code)
        logger.info(f"执行代码: {code}")
        # Get the output of the code
        msg_list = []
        while True:
            try:
                iopub_msg = self.kc.get_iopub_msg(timeout=1)
                msg_list.append(iopub_msg)
                if (
                    iopub_msg["msg_type"] == "status"
                    and iopub_msg["content"].get("execution_state") == "idle"
                ):
                    break
            except:
                if self.interrupt_signal:
                    try:
                        self.km.interrupt_kernel()
                    except Exception:
                        logger.exception("中断内核时发生错误")
                    self.interrupt_signal = False
                continue

        all_output: list[tuple[str, str]] = []
        for iopub_msg in msg_list:
            if iopub_msg["msg_type"] == "stream":
                if iopub_msg["content"].get("name") == "stdout":
                    output = iopub_msg["content"]["text"]
                    all_output.append(("stdout", output))
            elif iopub_msg["msg_type"] == "execute_result":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("execute_result_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("execute_result_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("execute_result_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("execute_result_jpeg", output))
            elif iopub_msg["msg_type"] == "display_data":
                if "data" in iopub_msg["content"]:
                    if "text/plain" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/plain"]
                        all_output.append(("display_text", output))
                    if "text/html" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["text/html"]
                        all_output.append(("display_html", output))
                    if "image/png" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/png"]
                        all_output.append(("display_png", output))
                    if "image/jpeg" in iopub_msg["content"]["data"]:
                        output = iopub_msg["content"]["data"]["image/jpeg"]
                        all_output.append(("display_jpeg", output))
            elif iopub_msg["msg_type"] == "error":
                # TODO: 正确返回格式
                if "traceback" in iopub_msg["content"]:
                    output = "\n".join(iopub_msg["content"]["traceback"])
                    cleaned_output = self.delete_color_control_char(output)
                    all_output.append(("error", cleaned_output))
        return all_output

    async def get_created_images(self, section: str) -> list[str]:
        """获取新创建的图片列表"""
        current_images = set()
        files = os.listdir(self.work_dir)
        for file in files:
            if file.endswith((".png", ".jpg", ".jpeg")):
                current_images.add(file)

        # 计算新增的图片
        new_images = current_images - self.last_created_images

        # 更新last_created_images为当前的图片集合
        self.last_created_images = current_images

        logger.info(f"新创建的图片列表: {new_images}")
        return list(new_images)  # 最后转换为list返回

    async def cleanup(self):
        # 关闭内核
        try:
            self.kc.shutdown()
        except Exception:
            logger.exception("关闭 kc 时出错（忽略）")
        try:
            logger.info("关闭内核")
            self.km.shutdown_kernel()
        except Exception:
            logger.exception("关闭内核时出错（忽略）")

    def send_interrupt_signal(self):
        self.interrupt_signal = True

    def restart_jupyter_kernel(self):
        """Restart the Jupyter kernel and recreate the work directory."""
        try:
            self.kc.shutdown()
        except Exception:
            logger.exception("重启前关闭 kc 出错（忽略）")

        # 使用与 initialize 相同的策略启动（支持 env）
        kernel_name = (os.environ.get("MMA_KERNEL_NAME") or "").strip()
        python_exe = (os.environ.get("MMA_PYTHON_EXE") or "").strip()

        try:
            if kernel_name:
                logger.info(f"重启：使用指定内核名称启动 Jupyter Kernel: {kernel_name}")
                self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name=kernel_name)
            elif python_exe:
                logger.info(f"重启：使用指定 Python 可执行文件启动 ipykernel: {python_exe}")
                km = KernelManager()
                km.kernel_cmd = [python_exe, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
                km.start_kernel()
                kc = km.client()
                kc.start_channels()
                self.km, self.kc = km, kc
            else:
                logger.info("重启：未设置 MMA_KERNEL_NAME / MMA_PYTHON_EXE，使用默认 kernel_name='python3'")
                self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name="python3")
        except Exception:
            logger.exception("重启内核时发生异常，尝试使用默认 kernel_name='python3'")
            self.km, self.kc = jupyter_client.manager.start_new_kernel(kernel_name="python3")

        self.interrupt_signal = False
        self._create_work_dir()

    def _create_work_dir(self):
        """Ensure the working directory exists after a restart."""
        os.makedirs(self.work_dir, exist_ok=True)
