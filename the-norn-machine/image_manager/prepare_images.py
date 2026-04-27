#!/usr/bin/env python3
import argparse
import os
import shutil
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="将 Downloads 文件夹中最新下载的 ChatGPT 图片重命名并移入 inbox")
    parser.add_argument("--start", type=int, required=True, help="起始 ID，例如 129")
    parser.add_argument("--end", type=int, required=True, help="结束 ID，例如 256")
    args = parser.parse_args()

    start_id = args.start
    end_id = args.end
    count = end_id - start_id + 1

    if count <= 0:
        print("结束 ID 必须大于等于起始 ID")
        return

    downloads_dir = Path.home() / "Downloads"
    inbox_dir = Path(__file__).resolve().parent / "output" / "images_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取所有的 ChatGPT 导出的图片 (包括中英文命名)
    files = list(downloads_dir.glob("ChatGPT Image*.png"))
    
    # 按照文件的创建/修改时间排序 (最早下载的排前面，最晚下载的排后面)
    files.sort(key=lambda p: p.stat().st_birthtime)
    
    if len(files) < count:
        print(f"❌ 错误：在 Downloads 中只找到了 {len(files)} 张 ChatGPT 图片，但你需要提取 {count} 张。")
        return
        
    # 截取最新下载的 count 张图片
    target_files = files[-count:]
    
    print(f"📦 找到 {len(files)} 张图片，截取最新下载的 {count} 张。")
    print(f"准备移动并重命名为 NORN_{start_id:04d}.png 到 NORN_{end_id:04d}.png ...\n")
    
    current_id = start_id
    success_count = 0
    for p in target_files:
        new_name = f"NORN_{current_id:04d}.png"
        dest = inbox_dir / new_name
        shutil.move(str(p), str(dest))
        print(f"✅ {p.name}  ->  {new_name}")
        current_id += 1
        success_count += 1

    print(f"\n🎉 成功处理了 {success_count} 张图片！")
    print("👉 下一步：请运行 `python3 norn_plus_manager_v2.py ingest` 正式导入它们。")

if __name__ == "__main__":
    main()
