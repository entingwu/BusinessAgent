# atguigu/query_process/nodes/node_rerank.py
from typing import Any, Dict, List

from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.logger import logger
from atguigu.utils.mongo_history_utils import format_json
from atguigu.utils.reranker_http_utils import rerank_documents


class NodeRerank(NodeBase):
    """
    节点功能：使用 Cross-Encoder 模型对 RRF 后的结果进行精确打分重排。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_rerank"

    # -----------------------------
    # Rerank / TopK 全局常量（不从 state 读取）
    # -----------------------------
    # 动态 TopK 硬上限：最多取前 N 条（<=10）
    RERANK_MAX_TOPK: int = 10
    # 最小 TopK：至少保留前 N 条（>=1，且 <= RERANK_MAX_TOPK）
    RERANK_MIN_TOPK: int = 2  # 总数最少条数

    # 断崖阈值（相对 - 一般针对低分文档）
    RERANK_GAP_RATIO: float = 0.25
    # 断崖阈值（绝对 - 一般针对高分文档）
    RERANK_GAP_ABS: float = 0.10

    # 最低入选标准
    SCORE_MIN: float = 0.8

    def process(self, state: QueryGraphState):

        # 1. 合并多数据源的文档(rrf 和 mcp 组装)
        merged_multi_docs: List[Dict[str, Any]] = self._step1_merge_multi_source_docs(state)

        # 2. Rerank精排
        # 调用reranker_http_utils实现精排获取分数列表
        # 将分数列表和原始文档对应后降序排列
        reranked_docs: List[Dict[str, Any]] = self._step2_rerank_merged_docs(state, merged_multi_docs)

        # 3. 动态topk截断（断崖检测）
        cutoff_docs = self._step3_cliff_cutoff(reranked_docs)

        # 4. 返回state结果
        return {
            "reranked_docs": cutoff_docs
        }

    def _step1_merge_multi_source_docs(self, state):

        # {
        # "title":"", b
        # "content":"",
        # "chunk_id":"",
        # "url":"",
        # "source":"",
        # }

        # 1. 定义结果集合
        merged_multi_docs = []

        # 2. 获取本地rrf结果文档列表，并组织数据
        for rrf_doc in state.get("rrf_chunks"):
            format_rrf_doc = {
                "title": rrf_doc.get("item_name"),
                "content": rrf_doc.get("content"),
                "chunk_id": rrf_doc.get("chunk_id"),
                "url": None,
                "source": "local",
            }
            merged_multi_docs.append(format_rrf_doc)

        # 3. 获取网搜结果列表，并组织数据
        for web_doc in state.get("web_search_docs"):
            format_web_doc = {
                "title": web_doc.get("title"),
                "content": web_doc.get("snippet"),
                "chunk_id": None,
                "url": web_doc.get("url"),
                "source": "web",
            }
            merged_multi_docs.append(format_web_doc)

        # 4. 返回结果
        return merged_multi_docs

    def _step2_rerank_merged_docs(self, state, merged_multi_docs):

        # 1. 获取改写后的用户问题
        user_query = state.get("rewritten_query")

        # 2. 获取文档的内容列表
        contents = [doc.get("content") for doc in merged_multi_docs]

        # 3. 调用reranker_http_utils实现精排
        rerank_scores = rerank_documents(user_query, contents)

        # 4. 组装文档和分数
        reranked_docs = [{"score": score, **doc} for doc, score in zip(merged_multi_docs, rerank_scores)]

        # 5. 按照得分降序对数据进行排序
        sorted_score_docs = sorted(reranked_docs, key=lambda x: x.get("score"), reverse=True)

        # 6. 返回结果
        return sorted_score_docs

    def _step3_cliff_cutoff(self, ranked_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

        """断崖检测截断：相邻得分差距超过阈值时截断。"""
        if not ranked_docs:
            return []

        # 0. 如果第一条数据没有达到最低分数标准，则返回空列表
        if ranked_docs[0].get("score") < self.SCORE_MIN:
            return []

        # 1. 计算断崖文档数量硬上限
        upper_bound = min(self.RERANK_MAX_TOPK, len(ranked_docs))
        # 2. 计算断崖文档数量硬下限
        lower_bound = min(self.RERANK_MIN_TOPK, upper_bound)

        # 3. 默认：取满硬上限
        cutoff_pos = upper_bound

        # 4. 遍历文档
        # 起点：从硬下限和其后的文档之间进行比较
        # 终点：硬上限和他的前一条之间进行比较
        for index in range(lower_bound - 1, upper_bound - 1):
            current_score = ranked_docs[index].get("score")
            next_score = ranked_docs[index + 1].get("score")

            # 两条记录的绝对差值
            abs_gap = current_score - next_score

            # 两条记录的相对差值
            rel_gap = abs_gap / (abs(current_score) + 1e-6)

            # 满足任意条件及达到断崖阈值
            # 两条记录的分数差的绝对值 >= 断崖绝对阈值
            # 两条记录的分数差的相对值 >= 断崖相对阈值
            if abs_gap >= self.RERANK_GAP_ABS or rel_gap >= self.RERANK_GAP_RATIO:
                cutoff_pos = index + 1
                logger.info(f"断崖位置：{cutoff_pos}")
                break

        return ranked_docs[:cutoff_pos]


if __name__ == '__main__':
    mock_state = {
        "rewritten_query": "怎么测这块主板的短路问题？",
        "rrf_chunks": [
            {
                "chunk_id": "local_1",
                "item_name": "主板维修手册",
                "content": "主板短路通常表现为通电后风扇转一下就停，可以使用万用表的蜂鸣档测量。"
            },
            {
                "chunk_id": "local_2",
                "item_name": "闲聊",
                "content": "今天中午去吃猪脚饭吧，这块主板外观很漂亮。"
            },
        ],
        "web_search_docs": [
            {
                "url": "https://example.com/repair",
                "title": "短路查修指南",
                "snippet": "主板通电前先打各主供电电感对地阻值，阻值偏低就是短路。"
            },
            {
                "url": "https://example.com/news",
                "title": "科技新闻",
                "snippet": "苹果发布新款手机，A系列芯片性能提升20%。"
            },
        ],
    }

    node_rerank = NodeRerank()
    result = node_rerank(mock_state)
    logger.info(format_json(result))

    # mock_state = {
    #     "rewritten_query": "BrotherHAK180烫金机D01WD7001-00如何使用？",
    #     "web_search_docs": [
    #         {
    #             "title": "Brother兄弟烫金机HAK180快速设置指导手册.pdf 1页",
    #             "snippet": "brother兄弟烫金机hak180快速设置指导手册用户手册产品说明书使用说明文档安装使用手册 快速安装指南 d01wd7001-00 hak 180 请先阅读产品安全手册 ,然后阅读此指南获取正确的安装步骤. 请将本指南放在设备旁边,以便快速查阅 移去白色保护材料 1打开设备包装并检查组件 背面进纸托板 操作视频烫金膜盒* 产品安全手册 快速安装指南 安装使用烫金膜盒支架 (预安装) 视频烫金机 移去蓝色固定胶带 电源线 下载文档",
    #             "url": "https://m.book118.com/html/2022/0717/8000005106004120.shtm"
    #         },
    #         {
    #             "title": "HAK180",
    #             "snippet": "HAK180 烫金机 零售价 面议 最大15PPM烫金速度  可选7PPM烫金速度  无版烫印  配备最大44页标准ADF进纸器  支持省膜模式  10字符x2行LCD液晶屏  HAK180烫金机,凭借其高速、高品质、以及出色的细节小字烫印效果,成为定制化专属机型。可烫印90g/m²~350g/m²的A4各类型纸张,支持各类广泛的应用领域。 高效、稳定的进纸结构 配备44页标准ADF进纸器,支持90g/m²~350g/m²的各类纸张(普通纸、薄纸、再生纸、厚纸等),进纸通道结构稳定可靠,支持连续烫印。 * 350g/m²支持12页自动进纸 * 最大支持44页进纸容量(90g/m²)烫印面朝下 高速连续烫金 HAK180针对不同厚度、介质的纸张提供两种可选烫金速度。15ppm满足普通规格纸张的高效烫金需求,7ppm适合稍厚纸张的烫金。 10字符×2行LCD液晶屏 10字符×2行LCD液晶屏,2个自定义按键,操作直观,方便快捷。 产品规格  一般参数  正常工作环境(温度): 10 ~ 32 摄氏度(50 ~ 90 华氏度) 正常工作环境(相对湿度): 20 % ~ 80 % 机器尺寸: W 384.2mm×D 330.2mm×H 356.2mm 重量(含包装箱): 16.9kg 电源: 220~240 V 消费电力(烫印中): 少于340W 消费电力(待机中): 少于7W 消费电力(关机): 少于0.04W LCD液晶屏尺寸: 48.0mm×10.9mm 节省烫金膜功能: 支持(在省膜模式中“跳过”和“中间”功能, 仅适用全幅烫金膜盒) 烫印参数  最大烫印速度 (A4): 最高达15 ppm 可选烫印速度(A4): 7 ppm 烫金机-HAK180-烫印速度调整-7PPM 烫金机-HAK180-安装耗材 烫金机-HAK180-更换耗材",
    #             "url": "https://www.brother.cn/hak/hak180"
    #         },
    #         {
    #             "title": "无版烫金+连续烫印?论一台优秀烫金机的自我修养!兄弟HAK180烫金机评测",
    #             "snippet": "作为高端文印设备,烫金机并没有太高的“知名度”,大部分人可能从未听过,而且它售价较为昂贵,一般只会在高端文印店(或工厂)才能见到。不过,由烫金机实现的作品,相信大家都接触过,甚至是“得到过”,比如入户门上的金色福字、商务会议的邀请函、礼品店/花店的祝福贺卡、高档酒店/餐厅的菜单酒单,以及代表荣誉和认可的获奖奖状等等。 去年底,Brother在进博会发布HAK180烫金机,作为Brother旗下新品类,烫金机是其在打印机、一体机、标签机、条码机、扫描仪等之后,布局的又一办公文印设备品。作为一款主要针对高端文印店推出的产品,HAK180的问世,令烫金品在文印店中即可完成,无需再像以前跑到制作工厂去定制,简化流程提升效率;对于烫金需求方而言,也就是企业、学校、花店等,无需频繁的确认,减少了制作流程,向文印店提出需求后,在文印店中就可完成,简单的烫金需求甚至可以做到“立等可取”,一改了传统需要在“需求方,供应商,制作工厂”间频繁沟通、确认、修改的流程,HAK180让烫金流程更省时、更省力、更省沟通。那么,烫金机究竟如何工作,长相又如何,且随着笔者一同去认识这款产品! 我们先观看一段视频,了解下烫金机的用途 细分市场需求,灵巧机身,任性安置 近年来,随着文印市场逐渐呈现精细化发展趋势,高端文印的需求逐渐增加,大势之下兄弟HAK180烫金机应运而生。烫金机,顾名思义,可以简单理解为,在纸张表面烫印一层金色,当然,此“金”非彼“金”,就像上面提到的奖状、春联,只是在技术上有些特殊。 第一眼看到兄弟HAK180烫金机,如非提前知晓这是一台烫金机,可能会让人误以为是一台馈纸式扫描仪,毕竟从外观来看,兄弟HAK180烫金机与扫描仪有着相似的外观,尤其是进纸、出纸托盘的设计,都有着一定相似度。 机身顶部的进纸托盘可以存放大量用于烫印的纸张,HAK180支持多种纸张质量规格,像办公常用70g/m²的A4纸张,以及更厚更重350g/m²的A4纸张都是可以正常实现烫印的,其中90g/m²纸张可以同时存放44张,350g/m²纸张可以同时存放12张,并可实现纸张自动、连续进纸烫金(如文章起始视频所示),这得益于其采用的“多页连续烫金”技术,可以处理批量烫印任务。 兄弟HAK180烫金机还支持“无版烫金”,整个烫印过程无需提前制版。",
    #             "url": "https://baijiahao.baidu.com/s?id=1737712132039151978&wfr=spider&for=pc"
    #         },
    #         {
    #             "title": "高速高品质 定制化专属,兄弟HAK180烫金机让你的文印店抢占先机",
    #             "snippet": "Brother兄弟(以下简称“兄弟”)推出的HAK180烫金机凭借其高速、高品质、以及出色的细节小字烫印效果,成为定制化专属机型,专业实力为邀请函、贺卡、请柬等个性化定制需求提供了更多的便利,最终帮助用户实现产业升级、促进文印服务往高端化发展。同时,无版烫印、支持省膜模式,大幅降低运营成本,使用效率更高,免去使用者的顾虑,为业务保驾护航。   紧凑体积,简约外观 外观方面,这款HAK180烫金机产品给人以沉稳扎实的感觉。产品颜色为黑色,磨砂的质感使得产品在使用时不易留下指纹,更具耐磨性。一体机整体观感棱角分明,但机身边角处均采用了圆润的设计,很大程度避免了用户在使用时发生不必要的磕碰。烫金机正面采用斜面设计,使得操作更加便捷舒适,摁键设置不用半蹲操作。并且外观还获得了2021年的日本GOOD DESIGN奖。       操作面板采用经济性和操作性适中的10字符*2行LCD液晶屏+按键的方式,操作直观,方便快捷。对于打印店快速、效率的工作环境来说,简洁明了的直观显示非常友好。       在体积方面,兄弟HAK180体积大小为384.2mm*330.2mm*356.2mm,作为一台无版烫金机,这样的机身体积可以摆放在室内桌子上的任何位置。",
    #             "url": "https://www.163.com/dy/article/HC5ISR9H05119GO7.html"
    #         },
    #         {
    #             "title": "使用说明书",
    #             "snippet": "使用说明书 联系我们 访问 www.brother.cn 语言 标题 说明 发布日期 (版本) 文件 (大小) 快速设置指导手册 有关产品安装. 2021-08-31 (0) 下载 (1.50mb) 使用说明书 有关产品的基本信息. 2023-06-09 (d) 下载 (1.24mb) 产品安全手册 在尝试操​​作本产品之前或者尝试任何维护之前,需要阅读安全指示. 2021-08-31 (0) 下载 (0.61mb) 下载并查看pdf格式的使用说明书.阅读pdf文档要求安装adobe® acrobat reader dc®软件.如果您没有adobe® acrobat reader dc®软件,请点击\"adobe® acrobat reader dc®\"链接下载此软件. 中国(中文) 更改国家或地区 (语言)",
    #             "url": "https://support.brother.com/g/b/manuallist.aspx?c=cn&lang=zh&prod=hak180_cn&type2=5"
    #         }
    #     ],
    #
    #     "rrf_chunks": [
    #         {
    #             "chunk_id": 467526165977636593,
    #             "content": "## HAK 180 烫金机\n\n产品安全手册（简体中文）\n\n感谢您购买 HAK 180 烫金机。\n\n在使用本设备之前，请先阅读本手册，包括所有预防措施。阅读本手册后，请妥善保管。\n\n有关使用本设备的更多信息，请参阅使用说明书，其可在兄弟 (中国)商业有限公司技术服务支持网站 http://www.95105369.com/Web/Manuals.aspx 上找到。建议您先通读使用说明书，再使用本设备。\n\n如需获得常见问题解答、故障排除和说明书， 请访问\n\nhttp://www.95105369.com。\n\n对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t对于保养、调整或维修事宜，请联系 Brother 呼叫中心或您当地的Brother 经销商。\n\n•\t如果本设备工作不正常或发生任何错误，请关闭本设备，拔下所有电缆，然后联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t本文档中提供的信息可能会随时更改，恕不另行通知。\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。",
    #             "item_name": "BrotherHAK180烫金机D01WD7001-00"
    #         },
    #         {
    #             "chunk_id": 467526165977636599,
    #             "content": "## 设备\n\n![⚠️ 设备内部零件高温警示：使用后勿立即触碰灰色标记区域，需等待冷却至170°C（338°F）以下再操作，以防烫伤](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/f3349cded08d6686a93d0a81b9a64ec1e50d9a82cbb88541b37027f085813a15.jpg)\n儎⑟ഴḽ䆜઀ᛞ࠽व䀜᪮儎⑟Ⲻ䇴༽䜞ԬȾ\n\n![**图：设备内部结构示意图——手部操作部件（电源线安全警示前页）**](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/501bb8d2d681e4502d87badb15a68939eadfa086d309c3599f1c36b0bc559177.jpg)",
    #             "item_name": "BrotherHAK180烫金机D01WD7001-00"
    #         },
    #         {
    #             "chunk_id": 467526165977636596,
    #             "content": "## 设备\n\n•\t请先阅读这本手册，再尝试操作本设备或尝试进行任何维护。不按照这些说明操作可能会提高发生人员受伤或财产损坏（包括火灾、触电、烧伤或窒息所致）的风险。对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t请勿在未去除所有包装材料的情况下使用本设备，包括本设备内部的任何附加的包装材料。否则可能会产生火灾的风险。\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。\n\n•\t请勿尝试自行维修本设备。打开或拆下盖子可能使您接触到危险电压点以及带来其他风险，并且可能使您的保修失效。对于所有维修事宜，请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t请在以下环境使用本设备：温度保持在 10 °C 和 32 °C 之间，湿度保持在 20% 和 80% 之间，无冷凝。\n\n•\t请勿使本设备受到阳光直射、过热、接触明火、腐蚀性气体、湿气或灰尘。否则可能产生触电、短路或火灾的风险，从而导致损坏设备和/或导致设备无法运行。\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。",
    #             "item_name": "BrotherHAK180烫金机D01WD7001-00"
    #         },
    #         {
    #             "chunk_id": 467526165977636592,
    #             "content": "![条形码（用于产品标识，型号：D01WD7001-00）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/677a08ee041965bbbdb6b483d6c17d5aaa36a26b6dc96870a2019f0307b8616f.jpg)\nD01WD7001-00\n\nSCHN\n",
    #             "item_name": "BrotherHAK180烫金机D01WD7001-00"
    #         },
    #         {
    #             "chunk_id": 467526165977636606,
    #             "content": "## 设备\n\n如果遵守了操作说明进行操作，但是设备不能正确运行，请仅调整操作说明中涵盖的控制。错误调整其他控制可能导致损坏并且通常需要合格技术进行全面工作以将本设备恢复到正常操作。Brother不建议使用 Brother 正品烫金膜盒以外的其他品牌烫金膜盒。如果使用与本设备不兼容的耗材导致损坏本设备的任何零件，由此导致的任何维修可能不在保修范围内。\n",
    #             "item_name": "BrotherHAK180烫金机D01WD7001-00"
    #         }
    #     ]
    # }
