import re

md_content = "一些文本![图片描述](images/img.png)一些文本"
image_file = "img.png"
summary = r"图\s片摘要"
url = "http://abc.com/xyz/123.png"
# 1、定义正则表达式
# ![描述](xxx文件名.扩展名)
pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r"\)")

# 2、替换
# md_content = pattern.sub(f"![{summary}]({url})", md_content)
md_content = pattern.sub(lambda _:f"![{summary}]({url})", md_content)

# "一些文本![图片摘要](http://abc.com/xyz/123.png)一些文本"
print(md_content)