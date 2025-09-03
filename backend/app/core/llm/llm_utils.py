# app/core/llm/llm_utils.py

# 只做“出站消息序列的最小化规范化”：
#   1. 合并相邻同角色纯文本（尤其 user→user）
#   * 不做：首条非 system→user、末尾 tool 处理、function→tool、tool/assistant.tool_calls 结构化

# 1 导入依赖
import json
from typing import Any, List, Dict, Optional, Set
from app.utils.log_util import logger as default_logger

# 2 常量
ALLOWED_ROLES_DEFAULT: Set[str] = {"system", "user", "assistant", "tool"}


# 3 类：消息清洗与预览
class MessageSanitizer:
    # 3.1 初始化
    # 3.1.1 说明：允许自定义角色集合与 logger；默认使用项目 logger 与标准角色集合
    def __init__(self, allowed_roles: Optional[Set[str]] = None, logger=default_logger):
        self.allowed_roles: Set[str] = allowed_roles or set(ALLOWED_ROLES_DEFAULT)
        self.logger = logger

    # 3.2 简易判空
    def _nonempty_str(self, x: Any) -> bool:
        return isinstance(x, str) and x.strip() != ""

    # 3.3 预览 messages（仅日志友好）
    def pretty_preview_messages(self, msgs: List[Dict[str, Any]], max_len: int = 2000) -> str:
        lines = []
        for i, m in enumerate(msgs or []):
            if not isinstance(m, dict):
                lines.append(f"[{i}] <non-dict> {type(m).__name__}")
                continue
            role = m.get("role")
            info = []
            if role == "assistant" and isinstance(m.get("tool_calls"), list) and m["tool_calls"]:
                brief = []
                for t in m["tool_calls"]:
                    if not isinstance(t, dict):
                        continue
                    fid = t.get("id")
                    fn = (t.get("function") or {}).get("name")
                    args = (t.get("function") or {}).get("arguments")
                    alen = len(args) if isinstance(args, str) else -1
                    brief.append(f"(id={fid}, fn={fn}, args_len={alen})")
                info.append(f"tool_calls={brief}")
            if role == "tool":
                tcid = m.get("tool_call_id")
                if tcid:
                    info.append(f"tool_call_id={tcid}")
            content = m.get("content")
            if isinstance(content, (dict, list)):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)
            cprev = content or ""
            if isinstance(cprev, str) and len(cprev) > max_len:
                cprev = cprev[:max_len] + "…"
            lines.append(f"[{i}] role={role}{(' ' + ' '.join(info)) if info else ''} | {repr(cprev)}")
        return "\n".join(lines)

    # 4 主函数：仅合并相邻同角色纯文本
    # 4.1 说明：
    #   4.1.1 仅当相邻两条为 user/assistant 且双方都不含 tool_calls，才合并 content（字符串）
    #   4.1.2 content 为 dict/list 的，不做合并（避免误合 JSON/结构化内容）
    #   4.1.3 其他一律原样保留，不增不减
    def sanitize_messages(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if not isinstance(messages, list):
            return messages

        merged: List[Dict[str, Any]] = []
        for m in messages:
            if not isinstance(m, dict):
                merged.append(m)
                continue

            role = m.get("role")
            content = m.get("content")

            can_merge = (
                len(merged) > 0
                and isinstance(merged[-1], dict)
                and merged[-1].get("role") in ("user", "assistant")
                and role in ("user", "assistant")
                and "tool_calls" not in merged[-1]
                and "tool_calls" not in m
                and isinstance(merged[-1].get("content"), str)
                and isinstance(content, str)
            )

            if can_merge:
                a = (merged[-1].get("content") or "").strip()
                b = (content or "").strip()
                s = (a + ("\n\n" if a and b else "") + b).strip()
                merged[-1]["content"] = s
            else:
                merged.append(m)

        # 仅日志预览（不修改序列）
        self.logger.info("🧹 sanitize_messages[min] =>\n" + self.pretty_preview_messages(merged))
        return merged


# 5 便捷函数（如已有大量调用点，也可继续使用这些函数名）
_default_sanitizer = MessageSanitizer()

def sanitize_messages(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    return _default_sanitizer.sanitize_messages(messages, tools)

def pretty_preview_messages(msgs: List[Dict[str, Any]], max_len: int = 5000) -> str:
    return _default_sanitizer.pretty_preview_messages(msgs, max_len)
