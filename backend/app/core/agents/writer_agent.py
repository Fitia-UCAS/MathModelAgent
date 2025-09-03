from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import get_writer_prompt
from app.schemas.enums import CompTemplate, FormatOutPut
from app.tools.openalex_scholar import OpenAlexScholar
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, WriterMessage
import json
from app.core.functions import writer_tools
from icecream import ic
from app.schemas.A2A import WriterResponse

# ==== 新增：图片分配与审计（按部分专属目录 + 跨部分禁用 + 单次引用 + 清单持久化） ====
from app.core.agents.agent_utils import WriterImageManager
# ==============================================================================


# 长文本
# TODO: 并行 parallel
# TODO: 获取当前文件下的文件
# TODO: 引用cites tool
class WriterAgent(Agent):  # 同样继承自Agent类
    def __init__(
        self,
        task_id: str,
        model: LLM,
        max_chat_turns: int = 600,  # 添加最大对话轮次限制
        comp_template: CompTemplate = CompTemplate,
        format_output: FormatOutPut = FormatOutPut.Markdown,
        scholar: OpenAlexScholar = None,
        max_memory: int = 25,  # 添加最大记忆轮次
    ) -> None:
        super().__init__(task_id, model, max_chat_turns, max_memory)
        self.format_out_put = format_output
        self.comp_template = comp_template
        self.scholar = scholar
        self.is_first_run = True
        self.system_prompt = get_writer_prompt(format_output)
        self.available_images: list[str] = []

        # ==== 新增：初始化图片管理器（清单：<WORK_BASE>/<task_id>/res_used_image.json） ====
        self.image_mgr = WriterImageManager(task_id=self.task_id)
        # ======================================================================

    async def run(
        self,
        prompt: str,
        available_images: list[str] = None,
        sub_title: str = None,
    ) -> WriterResponse:
        """
        执行写作任务
        Args:
            prompt: 写作提示
            available_images: 可用的图片相对路径列表（如 20250420-173744-9f87792c/编号_分布.png）
            sub_title: 子任务标题
        """
        logger.info(f"subtitle是:{sub_title}")

        if self.is_first_run:
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )

        # ==== 新增（最小改动）：使用现成 available_images，按“部分专属目录 + 跨部分唯一”过滤，并把图片约束拼进 prompt ====
        # 规则：ques1 → 仅 ques1/figures/；ques2 → 仅 ques2/figures/；eda → 仅 eda/figures/；
        #       sensitivity_analysis → 仅 sensitivity_analysis/figures/；且每张图本部分最多引用一次，跨部分禁止复用
        final_prompt, filtered_images = self.image_mgr.pre_run_prepare_prompt(
            section_name=sub_title or "",
            base_prompt=prompt,
            available_images=available_images or [],
        )
        prompt = final_prompt
        # =============================================================================================================

        if filtered_images:
            self.available_images = filtered_images
            # 拼接成完整URL（沿用你的原逻辑，但内容来自“过滤后”的列表，确保只给合法候选）
            image_list = ",".join(filtered_images)
            image_prompt = f"\n可用的图片链接列表：\n{image_list}\n请在写作时适当引用这些图片链接。"
            logger.info(f"image_prompt是:{image_prompt}")
            prompt = prompt + image_prompt
        else:
            self.available_images = []
            logger.info("本部分无可用图片或均已被其他部分使用：禁止插图")

        logger.info(f"{self.__class__.__name__}:开始:执行对话")
        self.current_chat_turns += 1  # 重置对话轮次计数器

        await self.append_chat_history({"role": "user", "content": prompt})

        # 获取历史消息用于本次对话
        response = await self.model.chat(
            history=self.chat_history,
            tools=writer_tools,
            tool_choice="auto",
            agent_name=self.__class__.__name__,
            sub_title=sub_title,
        )

        footnotes = []

        if (
            hasattr(response.choices[0].message, "tool_calls")
            and response.choices[0].message.tool_calls
        ):
            logger.info("检测到工具调用")
            tool_call = response.choices[0].message.tool_calls[0]
            tool_id = tool_call.id
            if tool_call.function.name == "search_papers":
                logger.info("调用工具: search_papers")
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(content=f"写作手调用{tool_call.function.name}工具"),
                )

                query = json.loads(tool_call.function.arguments)["query"]

                await redis_manager.publish_message(
                    self.task_id,
                    WriterMessage(
                        input={"query": query},
                    ),
                )

                # 更新对话历史 - 添加助手的响应
                await self.append_chat_history(response.choices[0].message.model_dump())
                ic(response.choices[0].message.model_dump())

                try:
                    papers = await self.scholar.search_papers(query)
                except Exception as e:
                    error_msg = f"搜索文献失败: {str(e)}"
                    logger.error(error_msg)
                    return WriterResponse(
                        response_content=error_msg, footnotes=footnotes
                    )
                # TODO: pass to frontend
                papers_str = self.scholar.papers_to_str(papers)
                logger.info(f"搜索文献结果\n{papers_str}")
                await self.append_chat_history(
                    {
                        "role": "tool",
                        "content": papers_str,
                        "tool_call_id": tool_id,
                        "name": "search_papers",
                    }
                )
                next_response = await self.model.chat(
                    history=self.chat_history,
                    tools=writer_tools,
                    tool_choice="auto",
                    agent_name=self.__class__.__name__,
                    sub_title=sub_title,
                )
                response_content = next_response.choices[0].message.content
        else:
            response_content = response.choices[0].message.content
        self.chat_history.append({"role": "assistant", "content": response_content})
        logger.info(f"{self.__class__.__name__}:完成:执行对话")

        # ==== 新增（最小改动）：写作后登记与校验（一次性 / 跨部分 / 目录越界），清单写入 <task_id>/res_used_image.json ====
        try:
            audit = self.image_mgr.post_run_validate_and_register(
                section_name=sub_title or "",
                response_text=response_content,
                filtered_images=self.available_images,  # 仅限本部分允许的候选集合
            )
            logger.info(
                f"写作手图片审计结果: used={audit.get('used_images')}, "
                f"violations={audit.get('violations')}, manifest={audit.get('manifest_path')}"
            )
        except Exception as e:
            logger.error(f"写作手图片审计登记失败: {str(e)}")
        # ==============================================================================================================

        return WriterResponse(response_content=response_content, footnotes=footnotes)

    async def summarize(self) -> str:
        """
        总结对话内容
        """
        try:
            await self.append_chat_history(
                {"role": "user", "content": "请简单总结以上完成什么任务取得什么结果:"}
            )
            # 获取历史消息用于本次对话
            response = await self.model.chat(
                history=self.chat_history, agent_name=self.__class__.__name__
            )
            await self.append_chat_history(
                {"role": "assistant", "content": response.choices[0].message.content}
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"总结生成失败: {str(e)}")
            # 返回一个基础总结，避免完全失败
            return "由于网络原因无法生成详细总结，但已完成主要任务处理。"
