from atguigu.tool.logger import logger


def a():
    print("a")
    b()


def b():
    print("b")
    i = 9 / 0

def main():
    print("main")
    a()

if __name__ == '__main__':


    try:

        main()

    except Exception as e:
        # logger.error(f"程序异常: {e}") # 输出错误原因
        # logger.exception(f"程序异常: {e}") # 输出错误跟踪站
        # logger.error(f"程序异常: {e}", exc_info=True) # 输出错误跟踪站
        logger.error("报错了")
        # raise  e
