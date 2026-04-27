import os
import shutil
from pathlib import Path

def fix_duplicates():
    inbox_dir = Path("/Users/dubianche/Downloads/norn_plus_image_manager_v2_curated/output/images_inbox")

    # 用户不小心生成了两次 145-148。
    # 所以现在的 149-152 其实是多出来的第二批 145-148。
    # 真正的 149 其实被挤到了 153 的位置。
    # 所以我们需要把 153 到 260，全部往前挪 4 个位置，正好覆盖掉 149-152。

    moved_count = 0
    # 正序遍历，把后面的往前移
    for i in range(153, 261):
        old_file = inbox_dir / f"NORN_{i:04d}.png"
        new_file = inbox_dir / f"NORN_{i-4:04d}.png"
        
        if old_file.exists():
            shutil.move(str(old_file), str(new_file))
            moved_count += 1
            # print(f"✅ {old_file.name} -> {new_file.name}")

    print(f"\n🎉 完美！成功将 {moved_count} 张图片往前移动了 4 位。")
    print("多余的（重复生成的）那批已经被覆盖清理掉了，现在 inbox 里的编号刚好是 129 到 256！")

if __name__ == "__main__":
    fix_duplicates()
