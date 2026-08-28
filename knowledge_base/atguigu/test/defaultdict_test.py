from collections import defaultdict
from typing import List, Dict

# 访问集合时,如果当前key对应的value不存在,则会对value进行自动的初始化操作
# 根据参数初始化成不同的类型
_tasks_running_list: Dict[str, List[str]] = defaultdict(list)
_tasks_result: Dict[str, Dict[str, str]] = defaultdict(dict)

print(_tasks_running_list["task_001"])
print(_tasks_result["task_001"])