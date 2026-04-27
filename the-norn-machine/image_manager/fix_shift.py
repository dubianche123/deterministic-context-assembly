import os
import shutil
from pathlib import Path

def fix_shift():
    inbox_dir = Path("/Users/dubianche/Downloads/norn_plus_image_manager_v2_curated/output/images_inbox")
    downloads_dir = Path.home() / "Downloads"

    # 1. 把 inbox 里的 129~256 整体往后挪 4 个位子，变成 133~260
    # 倒序遍历防止覆盖
    for i in range(256, 128, -1):
        old_file = inbox_dir / f"NORN_{i:04d}.png"
        new_file = inbox_dir / f"NORN_{i+4:04d}.png"
        if old_file.exists():
            shutil.move(str(old_file), str(new_file))

    # 2. 把遗漏的最早那 4 张图拿过来填补 129~132
    skipped_files = [
        downloads_dir / "ChatGPT Image 2026年4月27日 04_19_11 (1).png",
        downloads_dir / "ChatGPT Image 2026年4月27日 04_19_11 (2).png",
        downloads_dir / "ChatGPT Image 2026年4月27日 04_19_13 (3).png",
        downloads_dir / "ChatGPT Image 2026年4月27日 04_19_14 (4).png",
    ]

    for idx, p in enumerate(skipped_files):
        if p.exists():
            dest = inbox_dir / f"NORN_{129 + idx:04d}.png"
            shutil.move(str(p), str(dest))
            print(f"✅ 已找回并重命名: {p.name} -> {dest.name}")
        else:
            print(f"❌ 未找到: {p.name}")

    print("\n🎉 修复完成！现在的 NORN_0129 是真正的第一张图。")
    print("注：因为你实际上今天下载了 132 张图（多出了一批），多出来的 4 张现在变成了 257~260 号。")

if __name__ == "__main__":
    fix_shift()
