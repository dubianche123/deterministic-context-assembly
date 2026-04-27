#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

BUCKET_NAME = "leo-norn-machine-test-picture-726725835094-ap-northeast-1-an"
REGION = "ap-northeast-1"
S3_PREFIX = "images"  # 图片在 S3 上的文件夹名

def main():
    root_dir = Path(__file__).resolve().parent
    final_dir = root_dir / "output" / "images_final"
    db_path = root_dir / "data" / "norn_metadata.jsonl"
    
    if not final_dir.exists():
        print(f"❌ 找不到 {final_dir}，请确保图片已经存放在这里。")
        return

    # 1. 使用 AWS CLI 同步图片到 S3（速度极快，自带断点续传）
    s3_uri = f"s3://{BUCKET_NAME}/{S3_PREFIX}/"
    print(f"🚀 [1/3] 开始将图片批量上传至 S3: {s3_uri} ...")
    try:
        subprocess.run(["aws", "s3", "sync", str(final_dir), s3_uri, "--region", REGION], check=True)
    except subprocess.CalledProcessError:
        print("❌ AWS 上传失败，请检查网络或 AWS 权限配置。")
        return

    print("✅ 上传成功！\n")

    # 2. 更新本地数据库（核心步骤，防止以后运行 export 时 URL 又丢了）
    print("📝 [2/3] 正在将 S3 链接写入本地核心数据库 (norn_metadata.jsonl) ...")
    if not db_path.exists():
        print(f"❌ 找不到 {db_path}！")
        return

    items = []
    updated_count = 0
    with open(db_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            
            # 如果图片已经 generated，我们就拼装它的 S3 URL
            if item.get("status") == "generated" and item.get("local_path"):
                filename = Path(item["local_path"]).name
                # 拼装标准的 S3 访问链接
                url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{S3_PREFIX}/{filename}"
                
                if item.get("image_url") != url:
                    item["image_url"] = url
                    updated_count += 1
            
            items.append(item)

    # 覆写数据库
    tmp_path = db_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp_path.replace(db_path)
    print(f"✅ 成功更新了 {updated_count} 张图片的 URL！\n")

    # 3. 重新调用 export 生成最终的 JSON 文件
    print("📦 [3/3] 正在重新打包并生成最终版的 metadata JSON 文件...")
    subprocess.run(
        ["python3", "norn_plus_manager_v2.py", "export", "--format", "json", "--only-generated"], 
        check=True
    )
    
    print("\n🎉 全部大功告成！")
    print("最新生成的导出文件已经包含全部 S3 链接，去 output/exports/ 文件夹里拿来直接用就行！")

if __name__ == "__main__":
    main()
