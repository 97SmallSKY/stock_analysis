import base64
import os

file_path = "/Users/xiaotian/Downloads/69c24f2e001c736bba67.jpg"

with open(file_path, 'rb') as f:
    raw = f.read()

print("=" * 50)
print("📄 原始文件信息")
print("=" * 50)
print(f"文件大小:     {len(raw)} bytes")
print(f"前20字符:     {raw[:20]}")
print(f"末尾20字符:   {raw[-20:]}")
print(f"是否有换行符: {'有' if b'\\n' in raw else '没有'}")

# Base64 解码
print("\n" + "=" * 50)
print("🔍 Base64 解码后分析")
print("=" * 50)
decoded = base64.b64decode(raw)
print(f"解码后大小:       {len(decoded)} bytes")
print(f"解码后前16字节:   {decoded[:16].hex()}")
print(f"解码后前16字节值: {list(decoded[:16])}")

# 判断是否是已知格式
print("\n" + "=" * 50)
print("🔍 尝试识别格式")
print("=" * 50)
signatures = {
    'JPEG':  (b'\xff\xd8\xff',          3),
    'PNG':   (b'\x89PNG\r\n\x1a\n',     8),
    'GIF':   (b'GIF8',                  4),
    'WEBP':  (b'RIFF',                  4),
    'BMP':   (b'BM',                    2),
    'PDF':   (b'%PDF',                  4),
    'ZIP':   (b'PK\x03\x04',            4),
    'JSON':  (b'{',                     1),
    'XML':   (b'<?xml',                 5),
}

matched = False
for fmt, (sig, length) in signatures.items():
    if decoded[:length] == sig:
        print(f"✅ 识别为: {fmt}")
        matched = True
        break

if not matched:
    print(f"❌ 未识别格式，前8字节: {decoded[:8].hex()}")

    # 尝试再次Base64解码（双重编码）
    print("\n" + "=" * 50)
    print("🔍 尝试二次 Base64 解码")
    print("=" * 50)
    try:
        decoded2 = base64.b64decode(decoded)
        print(f"二次解码后前16字节: {decoded2[:16].hex()}")
        for fmt, (sig, length) in signatures.items():
            if decoded2[:length] == sig:
                print(f"✅ 二次解码后识别为: {fmt}")
                # 保存文件
                with open("restored_image.jpg", 'wb') as f:
                    f.write(decoded2)
                print("✅ 已保存为 restored_image.jpg")
                break
    except Exception as e:
        print(f"二次解码失败: {e}")

    # 检查是否是AES加密（通常以固定字节开头）
    print("\n" + "=" * 50)
    print("🔍 字节分布分析（判断是否加密）")
    print("=" * 50)
    byte_set = len(set(decoded[:256]))
    print(f"前256字节中不同字节数: {byte_set}/256")
    if byte_set > 200:
        print("⚠️  字节分布均匀，高度怀疑是 AES 或其他方式加密")
        print("👉 需要找到上传代码，确认是否有加密逻辑")
    else:
        print("字节分布不均匀，可能是其他编码")
