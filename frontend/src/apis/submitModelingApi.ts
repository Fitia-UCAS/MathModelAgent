// ... existing code ...
import request from "@/utils/request";

export function submitModelingTask(
	problem: {
		ques_all: string;
		comp_template?: string;
		format_output?: string;
	},
	files?: File[],
) {
	const formData = new FormData();
	// 添加问题数据
	formData.append("ques_all", problem.ques_all);
	formData.append("comp_template", 'CHINA');
	formData.append("format_output", problem.format_output || "Markdown");

	if (files) {
		// file 是文件对象

		// 添加文件
		if (files) {
			for (const file of files) {
				formData.append("files", file);
			}
		}

		return request.post<{
			task_id: string;
			status: string;
		}>("/modeling/", formData, {
			headers: {
				"Content-Type": "multipart/form-data",
			},
			timeout: 30000, // 添加超时设置
		});
	}
}
