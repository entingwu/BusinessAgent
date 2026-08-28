def func_a():
    raise ValueError("原始错误")

def func_b():
    try:
        func_a()
    except ValueError as e:
        raise e      # ❌ 截断了异常链

def func_c():
    try:
        func_a()
    except ValueError as e:
        raise         # ✅ 保留了异常链


def func_d():
    try:
        func_a()
    except ValueError as e:
        raise RuntimeError(f"包装错误:{e}")



func_d()