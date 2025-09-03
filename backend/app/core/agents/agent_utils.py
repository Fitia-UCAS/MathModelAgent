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
