# clean_taskid.py

# 1 导入依赖
import os
import re
import shutil

# 2 根目录（按实际情况调整）
BASE_DIR = os.path.abspath("backend")

# 3 目标路径
logs_dir = os.path.join(BASE_DIR, "logs")  # 3.1 logs 根目录
launcher_dir = os.path.join(logs_dir, "launcher")
launcher_debug_dir = os.path.join(logs_dir, "launcher_debug")
messages_dir = os.path.join(BASE_DIR, "logs", "messages")
work_dir = os.path.join(BASE_DIR, "project", "work_dir")

# 4 正则匹配模式
# 4.1 {数字}.log
log_pattern = re.compile(r"^\d+\.log$")
# 4.2 源码快照_{数字}.txt
txt_pattern = re.compile(r"^源码快照_\d+\.txt$")
# 4.3 {数字}.json
json_pattern = re.compile(r"^\d+\.json$")
# 4.4 work_dir/{数字}
workdir_pattern = re.compile(r"^\d+$")
# 4.5 指定文件名（删除 logs/ 下的 app.log）
app_log_filename = "app.log"

# 5 工具函数
# 5.1 删除匹配文件
def remove_files_in_dir(path, pattern):
    if not os.path.exists(path):
        return
    for fname in os.listdir(path):
        if pattern.match(fname):
            fpath = os.path.join(path, fname)
            try:
                if os.path.isfile(fpath):
                    print(f"删除文件: {fpath}")
                    os.remove(fpath)
            except Exception as e:
                print(f"删除文件失败: {fpath} -> {e}")

# 5.2 删除匹配目录
def remove_dirs_in_dir(path, pattern):
    if not os.path.exists(path):
        return
    for dname in os.listdir(path):
        if pattern.match(dname):
            dpath = os.path.join(path, dname)
            try:
                if os.path.isdir(dpath):
                    print(f"删除目录: {dpath}")
                    shutil.rmtree(dpath)
            except Exception as e:
                print(f"删除目录失败: {dpath} -> {e}")

# 5.3 删除指定文件（在指定目录下删除特定文件名）
def remove_specific_file(dir_path, filename):
    if not os.path.exists(dir_path):
        return
    fpath = os.path.join(dir_path, filename)
    try:
        if os.path.isfile(fpath):
            print(f"删除指定文件: {fpath}")
            os.remove(fpath)
    except Exception as e:
        print(f"删除指定文件失败: {fpath} -> {e}")

# 6 主程序
if __name__ == "__main__":
    # 6.1 删除 launcher 下的 .log
    remove_files_in_dir(launcher_dir, log_pattern)

    # 6.2 删除 launcher_debug 下的 .log
    remove_files_in_dir(launcher_debug_dir, log_pattern)

    # 6.3 删除 launcher 下的 源码快照_{数字}.txt
    remove_files_in_dir(launcher_dir, txt_pattern)

    # 6.4 删除 launcher_debug 下的 源码快照_{数字}.txt
    remove_files_in_dir(launcher_debug_dir, txt_pattern)

    # 6.5 删除 messages 下的 .json
    remove_files_in_dir(messages_dir, json_pattern)

    # 6.6 删除 work_dir/{数字}
    remove_dirs_in_dir(work_dir, workdir_pattern)

    # 6.7 删除 logs/ 下的 app.log（新增）
    remove_specific_file(logs_dir, app_log_filename)

    print("清理完成 ✅")
