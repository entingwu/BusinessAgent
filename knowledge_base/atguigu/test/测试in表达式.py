from atguigu.utils.milvus_utils import escape_milvus_string

item_names = ["a'b\\c", "b"]

escaped = ', '.join(f'"{escape_milvus_string(name)}"' for name in item_names)
expr = f"item_name in [{escaped}]"

print(expr)

