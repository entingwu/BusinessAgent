import re


title_pattern = r'^\s*#{1,6}\s+.+'
in_code_block = False
stripped_line = "# 标题1"
a = "hello"
print(re.match(title_pattern, stripped_line)) # None
is_valid_title =  (result := re.match(title_pattern, stripped_line)) and not in_code_block
print(is_valid_title)
print(result)