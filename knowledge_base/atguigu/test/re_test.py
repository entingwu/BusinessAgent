import re

image_file = "华为显示器 B3-241H 用户指南-(SSN-24BZ,VGA,04,zh-cn).md"

print(re.escape(image_file))
print(image_file)


# path = "D:\output\华为显示器 B3-241H 用户指南-(SSN-24BZ,VGA,04,zh-cn)" # 报错
# path = r"D:\output\华为显示器 B3-241H 用户指南-(SSN-24BZ,VGA,04,zh-cn)" # 正常
path = "D:\\output\\华为显示器 B3-241H 用户指南-(SSN-24BZ,VGA,04,zh-cn)"
print(path)