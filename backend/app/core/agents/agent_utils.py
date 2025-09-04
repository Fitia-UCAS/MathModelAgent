# app/core/agents/agent_utils.py

# 1 导入依赖
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import List, Tuple, Set, Dict

# 1.1 复用 common_utils 能力
from app.utils.common_utils import get_work_dir, get_current_files


# 2 工具类：写作手图片分配与审计（专用于 writer_agent.py）
class WriterImageManager:
    # 2.1 初始化（复用 get_work_dir，清单放在 <work_dir>/res_used_image.json）
    def __init__(self, task_id: str) -> None:
        self.task_id = str(task_id)
        self.work_dir: Path = Path(get_work_dir(self.task_id)).resolve()
        self.manifest_path: Path = self.work_dir / "res_used_image.json"
        self.MD_IMG_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

        # 2.1.1 确保任务目录存在
        self.work_dir.mkdir(parents=True, exist_ok=True)

    # 3 清单读写
    # 3.1 读取清单
    def _load_manifest(self) -> Dict:
        if not self.manifest_path.exists():
            return {"by_image": {}, "by_section": {}, "updated_at": 0}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {"by_image": {}, "by_section": {}, "updated_at": 0}

    # 3.2 写入清单
    def _save_manifest(self, data: Dict) -> Path:
        data["updated_at"] = int(time.time())
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.manifest_path

    # 3.3 路径规范化与工具
    # 3.3.1 统一分隔符、去空白
    def _norm(self, p: str) -> str:
        if p is None:
            return ""
        return str(p).strip().replace("\\", "/")

    # 3.3.2 去掉 "<task_id>/" 前缀（若存在），统一为任务内相对路径
    def _strip_task_prefix(self, p: str) -> str:
        p = self._norm(p)
        prefix = f"{self.task_id.strip().rstrip('/')}/"
        if p.startswith(prefix):
            return p[len(prefix):]
        return p

    # 3.3.3 本部分允许目录：eda/figures/；sensitivity_analysis/figures/；quesN/figures/
    def _allowed_dir_for_section(self, section_name: str) -> str:
        name = self._norm(section_name).lower()
        if name == "eda":
            return "eda/figures/"
        if name == "sensitivity_analysis":
            return "sensitivity_analysis/figures/"
        m = re.match(r"^ques(\d+)$", name)
        if m:
            return f"ques{m.group(1)}/figures/"
        return ""

    # 3.3.4 路径是否处于允许目录
    def _in_allowed_dir(self, rel_path: str, allowed_dir: str) -> bool:
        rp = self._norm(rel_path)
        ad = self._norm(allowed_dir)
        return ad != "" and rp.startswith(ad)

    # 4 写作前：过滤图片（不注入提示词，仅返回过滤后的列表）
    # 4.1 输入：
    #   - section_name：本部分标识（eda / quesN / sensitivity_analysis）
    #   - base_prompt：原始写作提示（保持原样返回）
    #   - available_images：候选图片（为空时自动从本部分目录枚举）
    # 4.2 输出：
    #   - final_prompt：base_prompt 原样
    #   - filtered_images：仅限本部分目录且未被其他部分登记使用
    def pre_run_prepare_prompt(
        self,
        section_name: str,
        base_prompt: str,
        available_images: List[str] | None,
    ) -> Tuple[str, List[str]]:
        manifest = self._load_manifest()
        used_by_image: Dict[str, str] = manifest.get("by_image", {})
        allowed_dir = self._allowed_dir_for_section(section_name)

        # 4.2.1 若未提供候选图，自动从本部分目录枚举（复用 get_current_files）
        if not available_images:
            if allowed_dir:  # 仅当识别出合法部分时才扫描
                section_dir = (self.work_dir / allowed_dir).resolve()
                if section_dir.exists():
                    names = get_current_files(str(section_dir), type="image") or []
                    # 枚举出的文件名需回到“任务内相对路径”：allowed_dir + filename
                    available_images = [f"{allowed_dir}{name}" for name in names]
                else:
                    available_images = []
            else:
                available_images = []

        # 4.2.2 规范化并仅保留允许目录内
        candidates_all = [
            self._strip_task_prefix(p)
            for p in (available_images or [])
            if self._norm(p)
        ]
        candidates = [p for p in candidates_all if self._in_allowed_dir(p, allowed_dir)]

        # 4.2.3 过滤掉跨部分已使用的图片
        filtered = [p for p in candidates if p not in used_by_image]

        # 4.2.4 返回：不追加任何提示词（避免与 writer_agent 的拼接重复）
        final_prompt = base_prompt
        return final_prompt, filtered

    # 5 写作后：解析正文图片引用并登记（一次性 + 跨部分禁用 + 目录合法性）
    # 5.1 返回：
    #   - used_images：本部分实际使用（且在 filtered 内）的图片列表
    #   - violations：违规项（not_in_allowed / duplicate_within_section / cross_section_conflict / wrong_section_dir）
    #   - manifest_path：清单绝对路径
    def post_run_validate_and_register(
        self,
        section_name: str,
        response_text: str,
        filtered_images: List[str],
    ) -> Dict:
        allowed_dir = self._allowed_dir_for_section(section_name)
        allowed_set: Set[str] = set(self._norm(p) for p in (filtered_images or []))
        content = response_text or ""

        # 5.1.1 抽取 Markdown 图片路径并去掉 <task_id>/ 前缀
        md_paths = set(self._strip_task_prefix(p) for p in self.MD_IMG_PATTERN.findall(content))

        # 5.1.2 兜底：正文中直接出现的路径片段
        raw_hits: Set[str] = set()
        for p in allowed_set:
            if p and p in content:
                raw_hits.add(p)

        # 5.1.3 实际使用（限 allowed_set）
        used_allowed = (md_paths | raw_hits) & allowed_set

        # 5.1.4 本部分重复使用检测
        dup_within_section: List[str] = []
        for p in used_allowed:
            cnt = len(re.findall(re.escape(p), content))
            if cnt > 1:
                dup_within_section.append(p)

        # 5.1.5 越权引用（Markdown 中出现但不在 allowed_set）
        not_in_allowed = [p for p in md_paths if p not in allowed_set]

        # 5.1.6 目录越界（即使在 allowed_set，也需确认目录前缀）
        wrong_section_dir = [p for p in used_allowed if not self._in_allowed_dir(p, allowed_dir)]

        # 5.1.7 读写清单并登记
        manifest = self._load_manifest()
        by_image: Dict[str, str] = manifest.get("by_image", {})
        by_section: Dict[str, List[str]] = manifest.get("by_section", {})

        cross_conflict: List[str] = []
        for p in used_allowed:
            prev = by_image.get(p)
            if prev and prev != section_name:
                cross_conflict.append(p)
            else:
                by_image[p] = section_name

        sec_list = set(by_section.get(section_name, []))
        sec_list.update(list(used_allowed))
        by_section[section_name] = sorted(sec_list)

        manifest["by_image"] = by_image
        manifest["by_section"] = by_section
        path = self._save_manifest(manifest)

        return {
            "used_images": sorted(list(used_allowed)),
            "violations": {
                "not_in_allowed": sorted(not_in_allowed),
                "duplicate_within_section": sorted(dup_within_section),
                "cross_section_conflict": sorted(cross_conflict),
                "wrong_section_dir": sorted(wrong_section_dir),
            },
            "manifest_path": str(path),
        }

    # 6 审计：检查是否存在跨部分复用或目录越界
    def audit(self) -> Dict:
        data = self._load_manifest()
        by_image: Dict[str, str] = data.get("by_image", {})
        by_section: Dict[str, List[str]] = data.get("by_section", {})

        # 6.1 统计是否同一图片出现在多个部分
        counts: Dict[str, int] = {}
        for sec, paths in by_section.items():
            for p in paths:
                counts[p] = counts.get(p, 0) + 1
        multi_used = [p for p, c in counts.items() if c > 1]

        # 6.2 目录越界审计
        wrong_dir: List[Tuple[str, str]] = []
        for p, sec in by_image.items():
            allow_dir = self._allowed_dir_for_section(sec)
            if not self._in_allowed_dir(p, allow_dir):
                wrong_dir.append((p, sec))

        return {
            "by_image": by_image,
            "by_section": by_section,
            "multi_used_images": sorted(multi_used),
            "wrong_section_dir_pairs": sorted(wrong_dir),
            "ok": len(multi_used) == 0 and len(wrong_dir) == 0,
            "manifest_path": str(self.manifest_path),
            "work_dir": str(self.work_dir),
        }

    # 7 维护：清空任务清单（删除 <task_id>/res_used_image.json）
    def clear_manifest(self) -> str:
        if self.manifest_path.exists():
            self.manifest_path.unlink()
        return str(self.manifest_path)
# 8 JSON 解码与修复：JsonDecoder（纯本地）
# 8.1 说明：
#   - sanitize(raw) -> str：返回“可被 json.loads 成功解析”的合法 JSON 文本；失败抛 JSONDecodeError
#   - decode(raw)   -> dict：等价于 json.loads(sanitize(raw))；根必须为对象
#   - 流程：清理围栏/控制字符 → 提取首个 JSON → 修复非法反斜杠/行末续行/字符串裸换行 → 校验
import json
import re
from typing import Optional, Any

class JsonDecoder:
    # 8.2 统一入口：返回“已修复的合法 JSON 文本”
    @classmethod
    async def sanitize(cls, raw: str) -> str:
        # 8.2.1 初步清洗与提取
        content = cls._strip_fences_outer_or_all(cls._clean_control_chars(raw, keep_whitespace=True))
        json_str = cls._extract_first_json_block(content) or (content or "")
        if not json_str.strip():
            raise json.JSONDecodeError("empty json content", json_str, 0)

        # 8.2.2 第一轮：最小必要修复
        s1 = cls._fix_invalid_json_escapes(json_str)
        s1 = cls._normalize_backslash_newline(s1)
        s1 = cls._escape_raw_newlines_in_json_strings(s1)
        if cls._is_valid_json_object(s1):
            return s1

        # 8.2.3 第二轮：宽松回退
        s2 = cls._fallback_relax(s1)
        if cls._is_valid_json_object(s2):
            return s2

        # 8.2.4 失败即抛（与 json.loads 异常类型对齐）
        raise json.JSONDecodeError("unparseable json after local fixes", json_str, 0)

    # 8.3 便捷入口：直接返回 dict（内部就是 sanitize + json.loads）
    @classmethod
    async def decode(cls, raw: str) -> dict:
        fixed = await cls.sanitize(raw)
        obj = json.loads(fixed)
        if not isinstance(obj, dict):
            # 根必须为对象，方便上层直接取 'ques_count'
            raise json.JSONDecodeError("root must be a JSON object", fixed, 0)
        return obj

    # 8.4 判定“JSON 文本是否合法且根为对象”
    @staticmethod
    def _is_valid_json_object(s: str) -> bool:
        try:
            v = json.loads(s)
            return isinstance(v, dict)
        except Exception:
            return False

    # 8.5 清理控制字符（保留 \n \r \t）
    @staticmethod
    def _clean_control_chars(s: str, keep_whitespace: bool = True) -> str:
        if not s:
            return ""
        if s and s[0] == "\ufeff":  # 去 BOM
            s = s[1:]
        if keep_whitespace:
            return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", s)
        return re.sub(r"[\x00-\x1F\x7F]", "", s)

    # 8.6 去掉 ```json ... ``` 或 ``` ... ``` 围栏（若存在）
    @staticmethod
    def _strip_fences_outer_or_all(s: str) -> str:
        if not s:
            return ""
        s = s.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\s*```$", "", s)
        return s.strip()

    # 8.7 提取首个配平的 JSON 对象（栈法，跳过字符串与转义）
    @staticmethod
    def _extract_first_json_block(s: str) -> Optional[str]:
        if not s:
            return None
        start = s.find("{")
        if start == -1:
            return None
        i = start
        depth = 0
        in_str = False
        esc = False
        while i < len(s):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return s[start : i + 1]
            i += 1
        return None

    # 8.8 把非法“单反斜杠转义”改为“合法双反斜杠”
    @staticmethod
    def _fix_invalid_json_escapes(s: str) -> str:
        # 合法转义首字符：" \/ b f n r t u
        return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", s)

    # 8.9 处理反斜杠续行（反斜杠 + 真实换行 → \\n）
    @staticmethod
    def _normalize_backslash_newline(s: str) -> str:
        return re.sub(r"\\\r?\n", r"\\n", s)

    # 8.10 JSON 字符串字面量内部的真实换行/回车 → \\n（状态机）
    @staticmethod
    def _escape_raw_newlines_in_json_strings(s: str) -> str:
        out = []
        in_str = False
        pending_backslash = False
        i = 0
        L = len(s)
        while i < L:
            ch = s[i]
            if not in_str:
                out.append(ch)
                if ch == '"':
                    in_str = True
                    pending_backslash = False
                i += 1
                continue

            if pending_backslash:
                if ch in ['"', "\\", "/", "b", "f", "n", "r", "t"]:
                    out.append("\\" + ch)
                elif ch == "u" and i + 4 < L and re.match(r"[0-9a-fA-F]{4}", s[i + 1 : i + 5]):
                    out.append("\\u" + s[i + 1 : i + 5])
                    i += 4
                elif ch in ("\n", "\r"):
                    out.append("\\n")
                else:
                    out.append("\\\\")
                    out.append(ch)
                pending_backslash = False
                i += 1
                continue

            if ch == "\\":
                pending_backslash = True
                i += 1
                continue

            if ch == '"':
                out.append(ch)
                in_str = False
                i += 1
                continue

            if ch in ("\n", "\r"):
                out.append("\\n")
                i += 1
                continue

            out.append(ch)
            i += 1

        if pending_backslash:
            out.append("\\\\")
        return "".join(out)

    # 8.11 宽松回退：去尾逗号、单引号→双引号、再次修复非法反斜杠
    @classmethod
    def _fallback_relax(cls, s: str) -> str:
        t = re.sub(r",\s*}", "}", s)
        t = re.sub(r",\s*]", "]", t)
        t = t.replace("'", '"')
        t = cls._fix_invalid_json_escapes(t)
        return t

# 9 数据集管理：CoderDatasetManager（供 coder_agent 使用）
# 9.1 说明：
#   - 目的：统一枚举 <task_id> 工作目录下 {eda/, quesN/, sensitivity_analysis/}/datasets/ 内所有数据文件
#   - 输出：任务内相对路径（正斜杠），可按分节分组或扁平列表
#   - 规则：仅允许顶层目录 eda/、sensitivity_analysis/、quesN（N 为正整数）；仅扫描其下二级目录 datasets/
#   - 扩展名：默认 { .csv, .xlsx, .xls, .parquet, .json }
class CoderDatasetManager:
    # 9.2 初始化
    def __init__(self, task_id: str, exts: Set[str] | None = None) -> None:
        self.task_id: str = str(task_id)
        self.work_dir: Path = Path(get_work_dir(self.task_id)).resolve()
        self.exts: Set[str] = set(exts) if exts else {
            ".csv", ".xlsx", ".xls", ".parquet", ".json"
        }

    # 9.3 工具：规范化/剥离前缀/校验目录
    # 9.3.1 统一分隔符、去空白
    def _norm(self, p: str) -> str:
        if p is None:
            return ""
        return str(p).strip().replace("\\", "/")

    # 9.3.2 去掉 "<task_id>/" 前缀（若存在），统一为任务内相对路径
    def _strip_task_prefix(self, p: str) -> str:
        p = self._norm(p)
        prefix = f"{self.task_id.strip().rstrip('/')}/"
        if p.startswith(prefix):
            return p[len(prefix):]
        return p

    # 9.3.3 顶层目录是否允许（返回规范化后的节名；不允许则返回空串）
    def _as_allowed_section(self, name: str) -> str:
        n = self._norm(name).lower().rstrip("/")
        if n == "eda":
            return "eda"
        if n == "sensitivity_analysis":
            return "sensitivity_analysis"
        m = re.match(r"^ques(\d+)$", n)
        if m:
            return f"ques{m.group(1)}"
        return ""

    # 9.3.4 是否允许的数据扩展名
    def _is_allowed_ext(self, path: Path) -> bool:
        return path.suffix.lower() in self.exts

    # 9.4 核心：按分节枚举
    # 9.4.1 返回 dict：{section_name: [ "section/datasets/xxx.csv", ... ], ...}
    def list_grouped_by_section(self) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}

        if not self.work_dir.exists():
            return grouped

        # 9.4.1.1 遍历顶层目录，仅限允许的分节
        for child in self.work_dir.iterdir():
            if not child.is_dir():
                continue
            sec = self._as_allowed_section(child.name)
            if not sec:
                continue

            ds_dir = child / "datasets"
            if not (ds_dir.exists() and ds_dir.is_dir()):
                continue

            # 9.4.1.2 深度递归枚举 datasets 下所有文件
            paths: List[str] = []
            for p in ds_dir.rglob("*"):
                if p.is_file() and self._is_allowed_ext(p):
                    rel = str(p.relative_to(self.work_dir)).replace("\\", "/")
                    paths.append(rel)

            if paths:
                grouped[sec] = sorted(paths)

        return grouped

    # 9.5 扁平列表（不分组）
    def list_all_dataset_paths(self) -> List[str]:
        res: List[str] = []
        grouped = self.list_grouped_by_section()
        for _, arr in grouped.items():
            res.extend(arr)
        return sorted(res)

    # 9.6 限定某一分节（eda / sensitivity_analysis / quesN）
    def list_by_section(self, section_name: str) -> List[str]:
        sec = self._as_allowed_section(section_name)
        if not sec:
            return []
        return self.list_grouped_by_section().get(sec, [])

    # 9.7 输出统一清单字典（便于直接注入到对话）
    # 9.7.1 结构：
    #   {
    #     "tip": "系统自动枚举的 datasets 清单（相对路径）",
    #     "paths": [...],
    #     "by_section": {"eda": [...], "ques1": [...], ...},
    #     "root": "<绝对工作目录>",
    #     "exts": [".csv", ".xlsx", ...],
    #     "updated_at": 1690000000
    #   }
    def build_manifest_dict(self) -> Dict:
        grouped = self.list_grouped_by_section()
        flat = []
        for arr in grouped.values():
            flat.extend(arr)
        return {
            "tip": "系统自动枚举的 datasets 清单（相对路径）",
            "paths": sorted(set(flat)),
            "by_section": {k: v for k, v in sorted(grouped.items())},
            "root": self._norm(str(self.work_dir)),
            "exts": sorted(self.exts),
            "updated_at": int(time.time()),
        }

    # 9.8 便捷：JSON 字符串（ensure_ascii=False）
    def build_manifest_json(self) -> str:
        return json.dumps(self.build_manifest_dict(), ensure_ascii=False)
