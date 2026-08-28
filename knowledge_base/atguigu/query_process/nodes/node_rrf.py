# atguigu/query_process/nodes/node_rrf.py
from typing import List

from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.logger import logger
from atguigu.utils.mongo_history_utils import format_json


class NodeRrf(NodeBase):
    """
    节点功能：Reciprocal Rank Fusion
    将多路召回的结果（向量、HyDE、Web）进行加权融合排序。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_rrf"

    def process(self, state: QueryGraphState) :
        #1. 获取各路搜索结果（向量、假设性向量）
        embedding_chunks = state.get("embedding_chunks")
        embedding_search_list = [doc.get("entity") for doc in (embedding_chunks or []) if isinstance(doc, dict) ]

        hyde_embedding_chunks = state.get("hyde_embedding_chunks")
        hyde_embedding_search_list = [doc.get("entity") for doc in (hyde_embedding_chunks or []) if isinstance(doc, dict)]

        # 2. 定义不同搜索路的权重
        rrf_inputs = [
            (embedding_search_list, 1.0),
            (hyde_embedding_search_list, 0.8),
        ]

        # 3. 利用RRF融合重排序算法对所有搜索路上的文档进行初步排序
        rrf_merge_results = self._rrf_merge(rrf_inputs, max_results = 5)

        # 4. 获取最终的排序结果:只要文档，不要分数
        rrf_chunks = [doc for doc, _ in rrf_merge_results]

        return {
            "rrf_chunks": rrf_chunks
        }

    def _rrf_merge(self, rrf_inputs, k: int = 60, max_results: int = None) -> List:
        """
        :param rrf_inputs: 待融合排序的数据列表
        :param k:           平滑常数
        :param max_results: 合并后保留的文档数量，默认：全部保留
        :return: 合并且已排序之后的文档列表（按照得分降序）
        """

        # 1.存放分数的集合： key:chunk_id，value:所有搜索路的分数综合
        chunk_scores = {}

        # 2. 存放数据的集合： key:chunk_id，value:当前文档
        chunk_data = {}

        # 3. 遍历所有搜索路
        for rrf_input, weight in rrf_inputs:
            for rank, doc in enumerate(rrf_input, start=1):
                chunk_id = doc.get("chunk_id")
                chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) +  weight / (k + rank)

                # 只有首次设置会成功
                chunk_data.setdefault(chunk_id, doc)

        # 4. 按照得分降序对数据进行排序
        # print(chunk_scores)
        # 组装结果列表：[(文档1, score),(文档2, score)，(文档3, score)...]
        unsorted_results = [(chunk_data[cid], score) for cid, score in chunk_scores.items()]
        sorted_results = sorted(unsorted_results, key=lambda x: x[1], reverse=True)

        # 5. 取前max_results个数据返回
        return sorted_results[:max_results] if max_results else sorted_results

if __name__ == '__main__':

    # 模拟两路检索结果
    # mock_state_1= {
    #     "embedding_chunks": [
    #         {
    #             "entity": {
    #                 "chunk_id": "chunk_1",
    #                 "item_name": "主体名称",
    #                 "content": "向量搜索结果#1"
    #             }
    #         },
    #         {"entity": {"chunk_id": "chunk_2", "content": "向量搜索结果#2"}},
    #         {"entity": {"chunk_id": "chunk_3", "content": "向量搜索结果#3"}},
    #         {"entity": {"chunk_id": "chunk_4", "content": "向量搜索结果#4"}},
    #         {"entity": {"chunk_id": "chunk_5", "content": "向量搜索结果#5"}},
    #         {"entity": {"chunk_id": "chunk_6", "content": "向量搜索结果#6"}},
    #         {"entity": {"chunk_id": "chunk_7", "content": "向量搜索结果#7"}},
    #         {"entity": {"chunk_id": "chunk_8", "content": "向量搜索结果#8"}},
    #         {"entity": {"chunk_id": "chunk_9", "content": "向量搜索结果#9"}},
    #         {"entity": {"chunk_id": "chunk_10", "content": "向量搜索结果#10"}},
    #     ],
    #     "hyde_embedding_chunks": [
    #         {"entity": {"chunk_id": "chunk_1", "content": "HyDE搜索结果#1"}},
    #         {"entity": {"chunk_id": "chunk_4", "content": "HyDE搜索结果#4"}},
    #         {"entity": {"chunk_id": "chunk_2", "content": "HyDE搜索结果#2"}},
    #         {"entity": {"chunk_id": "chunk_10", "content": "HyDE搜索结果#10"}},
    #         {"entity": {"chunk_id": "chunk_12", "content": "HyDE搜索结果#12"}},
    #         {"entity": {"chunk_id": "chunk_20", "content": "HyDE搜索结果#20"}},
    #         {"entity": {"chunk_id": "chunk_6", "content": "HyDE搜索结果#6"}},
    #         {"entity": {"chunk_id": "chunk_3", "content": "HyDE搜索结果#3"}},
    #         {"entity": {"chunk_id": "chunk_8", "content": "HyDE搜索结果#8"}},
    #         {"entity": {"chunk_id": "chunk_7", "content": "HyDE搜索结果#7"}},
    #     ]
    # }

    mock_state = {
        "embedding_chunks": [
        {
            "chunk_id": 467526165977636593,
            "distance": 0.8441392779350281,
            "entity": {
                "chunk_id": 467526165977636593,
                "content": "## HAK 180 烫金机\n\n产品安全手册（简体中文）\n\n感谢您购买 HAK 180 烫金机。\n\n在使用本设备之前，请先阅读本手册，包括所有预防措施。阅读本手册后，请妥善保管。\n\n有关使用本设备的更多信息，请参阅使用说明书，其可在兄弟 (中国)商业有限公司技术服务支持网站 http://www.95105369.com/Web/Manuals.aspx 上找到。建议您先通读使用说明书，再使用本设备。\n\n如需获得常见问题解答、故障排除和说明书， 请访问\n\nhttp://www.95105369.com。\n\n对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t对于保养、调整或维修事宜，请联系 Brother 呼叫中心或您当地的Brother 经销商。\n\n•\t如果本设备工作不正常或发生任何错误，请关闭本设备，拔下所有电缆，然后联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t本文档中提供的信息可能会随时更改，恕不另行通知。\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636596,
            "distance": 0.8376867771148682,
            "entity": {
                "chunk_id": 467526165977636596,
                "content": "## 设备\n\n•\t请先阅读这本手册，再尝试操作本设备或尝试进行任何维护。不按照这些说明操作可能会提高发生人员受伤或财产损坏（包括火灾、触电、烧伤或窒息所致）的风险。对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t请勿在未去除所有包装材料的情况下使用本设备，包括本设备内部的任何附加的包装材料。否则可能会产生火灾的风险。\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。\n\n•\t请勿尝试自行维修本设备。打开或拆下盖子可能使您接触到危险电压点以及带来其他风险，并且可能使您的保修失效。对于所有维修事宜，请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t请在以下环境使用本设备：温度保持在 10 °C 和 32 °C 之间，湿度保持在 20% 和 80% 之间，无冷凝。\n\n•\t请勿使本设备受到阳光直射、过热、接触明火、腐蚀性气体、湿气或灰尘。否则可能产生触电、短路或火灾的风险，从而导致损坏设备和/或导致设备无法运行。\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636592,
            "distance": 0.8372341394424438,
            "entity": {
                "chunk_id": 467526165977636592,
                "content": "![条形码（用于产品标识，型号：D01WD7001-00）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/677a08ee041965bbbdb6b483d6c17d5aaa36a26b6dc96870a2019f0307b8616f.jpg)\nD01WD7001-00\n\nSCHN\n",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636599,
            "distance": 0.8354134559631348,
            "entity": {
                "chunk_id": 467526165977636599,
                "content": "## 设备\n\n![⚠️ 设备内部零件高温警示：使用后勿立即触碰灰色标记区域，需等待冷却至170°C（338°F）以下再操作，以防烫伤](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/f3349cded08d6686a93d0a81b9a64ec1e50d9a82cbb88541b37027f085813a15.jpg)\n儎⑟ഴḽ䆜઀ᛞ࠽व䀜᪮儎⑟Ⲻ䇴༽䜞ԬȾ\n\n![**图：设备内部结构示意图——手部操作部件（电源线安全警示前页）**](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/501bb8d2d681e4502d87badb15a68939eadfa086d309c3599f1c36b0bc559177.jpg)",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636606,
            "distance": 0.8275882601737976,
            "entity": {
                "chunk_id": 467526165977636606,
                "content": "## 设备\n\n如果遵守了操作说明进行操作，但是设备不能正确运行，请仅调整操作说明中涵盖的控制。错误调整其他控制可能导致损坏并且通常需要合格技术进行全面工作以将本设备恢复到正常操作。Brother不建议使用 Brother 正品烫金膜盒以外的其他品牌烫金膜盒。如果使用与本设备不兼容的耗材导致损坏本设备的任何零件，由此导致的任何维修可能不在保修范围内。\n",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636594,
            "distance": 0.8254126310348511,
            "entity": {
                "chunk_id": 467526165977636594,
                "content": "## HAK 180 烫金机\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。\n\n•\t请注意，对于使用通过本设备制作的产品造成的任何损坏或利润损失，或者故障、维修导致的数据消失或更改，或者第三方提出的任何索赔，我们不承担任何责任。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636598,
            "distance": 0.8222229480743408,
            "entity": {
                "chunk_id": 467526165977636598,
                "content": "## 设备\n\n•\t请勿将本设备放在化学品附近，或者将本设备放置在可能会泼溅到化学品的位置。万一化学品接触本设备，则存在火灾或触电的风险。特别是有机溶剂或液体（如苯、油漆稀释剂、抛光剂或除臭剂）可能导致塑料盖和/或电缆溶解或分解，从而产生火灾或触电的风险。这些化学品或其他化学品可能导致本设备故障或褪色。\n\n•\t本设备的包装中使用了塑料袋。塑料袋并不是玩具。为避免窒息的危险，请将这些塑料袋远离婴儿和儿童，并正确弃置这些塑料袋。\n\n•\t对于使用起搏器的用户：\n\n本设备可能会产生弱磁场。如果您在本设备附近感觉到起搏器工作不正常，请远离本设备，并立即咨询医生。\n\n•\t使用本设备之后短时间内，本设备的一些内部零件仍然处于极热状态。打开前盖时，请勿触摸以灰色标记的区域。存在烧伤的风险。先等待设备冷却下来，再触摸设备的内部零件。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636604,
            "distance": 0.8156691789627075,
            "entity": {
                "chunk_id": 467526165977636604,
                "content": "## 为设备选择一个安全的位置\n\n•\t提起本设备时，请使用双手抓稳本设备的两侧。如果抓住的是进纸托板和出纸盒，它们可能会掉下来。必须通过将双手放在本设备下面来搬运本设备。\n\n![**正确与错误的设备搬运方式：避免抓握进纸托板/出纸盒，应双手托住设备底部以防止跌落或部件脱落**](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/cc5ee1ac24ebb2707d40dc7a234a8b243f55f5bf08fabc683859be6fdf096ffa.jpg)\n确保本设备的任何部位均未伸出设备所在的桌面或支架。特别是当本设备位于桌面、支架等边缘时，请勿让出纸盒打开。确保本设备位于平整、水平且稳定的表面上，避免震动。不遵守这些预防措施可能导致设备跌落，从而导致用户的人身伤害以及设备严重损坏。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636602,
            "distance": 0.7093319296836853,
            "entity": {
                "chunk_id": 467526165977636602,
                "content": "## 设备\n\n•\t将本设备放置在平整、水平且稳定的表面上（如桌面），避免震动和冲击。\n\n•\t将本设备放置在通风良好的环境中。\n\n•\t为了防止人员受伤，请谨慎操作，避免将手指放置在图中所示的区域中。\n\n![禁止将手指伸入设备内部齿轮/传动机构区域（图中放大示意）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/c61a7f4e923881679f747508ae309c39dc221685344b068009256b1b3a40cc00.jpg)\n\n![禁止将手指伸入设备顶部开口区域（如进纸/出纸口）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/5067b2891ca4f761e2874921e0eb433aa742afbf38ca8dc509afecbf0aa6a6b5.jpg)\n",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        },
        {
            "chunk_id": 467526165977636597,
            "distance": 0.6980783343315125,
            "entity": {
                "chunk_id": 467526165977636597,
                "content": "## 设备\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。\n\n否则当水（包括加热 空调 通风设备所产生的冷凝水）接触本设备时可能产生短路或火灾的风险。\n\n•\t如果设备变得异常高温、冒烟、产生任何强烈味道，或者如果您意外在设备上倒入任何液体，请立即从电源插座拔掉设备的插头。请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n如果设备跌落或者已损坏，则有触电的可能性。请从电源插座中拔掉设备的插头，然后联系 呼叫中心或您当地的 经销商。\n\n•\t如果水、其他液体或金属物体进入设备内部，请立即从电源插座中拔掉设备的插头，然后联系 Brother 呼叫中心或您当地的 Brother经销商。\n\n•\t请勿在卡纸或有纸张散落在设备内部的情况下尝试使用本设备。纸张与定影单元长时间接触可能导致火灾。\n\n请勿使用任何易燃物品、任何类型的喷雾剂包含酒精或氨水的有机溶剂/液体来清洁本设备的内部或外部。否则可能导致火灾。请改用无绒干抹布。有关如何清洁本设备的说明，请参阅 。",
                "item_name": "BrotherHAK180烫金机D01WD7001-00"
            }
        }
    ],
        "hyde_embedding_chunks": [
            {
                "chunk_id": 467526165977636599,
                "distance": 0.8704971075057983,
                "entity": {
                    "chunk_id": 467526165977636599,
                    "content": "## 设备\n\n![⚠️ 设备内部零件高温警示：使用后勿立即触碰灰色标记区域，需等待冷却至170°C（338°F）以下再操作，以防烫伤](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/f3349cded08d6686a93d0a81b9a64ec1e50d9a82cbb88541b37027f085813a15.jpg)\n儎⑟ഴḽ䆜઀ᛞ࠽व䀜᪮儎⑟Ⲻ䇴༽䜞ԬȾ\n\n![**图：设备内部结构示意图——手部操作部件（电源线安全警示前页）**](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/501bb8d2d681e4502d87badb15a68939eadfa086d309c3599f1c36b0bc559177.jpg)",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636593,
                "distance": 0.8703023195266724,
                "entity": {
                    "chunk_id": 467526165977636593,
                    "content": "## HAK 180 烫金机\n\n产品安全手册（简体中文）\n\n感谢您购买 HAK 180 烫金机。\n\n在使用本设备之前，请先阅读本手册，包括所有预防措施。阅读本手册后，请妥善保管。\n\n有关使用本设备的更多信息，请参阅使用说明书，其可在兄弟 (中国)商业有限公司技术服务支持网站 http://www.95105369.com/Web/Manuals.aspx 上找到。建议您先通读使用说明书，再使用本设备。\n\n如需获得常见问题解答、故障排除和说明书， 请访问\n\nhttp://www.95105369.com。\n\n对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t对于保养、调整或维修事宜，请联系 Brother 呼叫中心或您当地的Brother 经销商。\n\n•\t如果本设备工作不正常或发生任何错误，请关闭本设备，拔下所有电缆，然后联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t本文档中提供的信息可能会随时更改，恕不另行通知。\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636592,
                "distance": 0.8628888130187988,
                "entity": {
                    "chunk_id": 467526165977636592,
                    "content": "![条形码（用于产品标识，型号：D01WD7001-00）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/677a08ee041965bbbdb6b483d6c17d5aaa36a26b6dc96870a2019f0307b8616f.jpg)\nD01WD7001-00\n\nSCHN\n",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636596,
                "distance": 0.8584069013595581,
                "entity": {
                    "chunk_id": 467526165977636596,
                    "content": "## 设备\n\n•\t请先阅读这本手册，再尝试操作本设备或尝试进行任何维护。不按照这些说明操作可能会提高发生人员受伤或财产损坏（包括火灾、触电、烧伤或窒息所致）的风险。对于本设备所有者不遵守本指南中规定的说明操作而导致的损害，Brother 不承担任何责任。\n\n•\t请勿在未去除所有包装材料的情况下使用本设备，包括本设备内部的任何附加的包装材料。否则可能会产生火灾的风险。\n\n•\t请勿拆解本设备。拆解本设备可能会导致火灾或触电。\n\n•\t请勿尝试自行维修本设备。打开或拆下盖子可能使您接触到危险电压点以及带来其他风险，并且可能使您的保修失效。对于所有维修事宜，请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n•\t请在以下环境使用本设备：温度保持在 10 °C 和 32 °C 之间，湿度保持在 20% 和 80% 之间，无冷凝。\n\n•\t请勿使本设备受到阳光直射、过热、接触明火、腐蚀性气体、湿气或灰尘。否则可能产生触电、短路或火灾的风险，从而导致损坏设备和/或导致设备无法运行。\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636606,
                "distance": 0.847031831741333,
                "entity": {
                    "chunk_id": 467526165977636606,
                    "content": "## 设备\n\n如果遵守了操作说明进行操作，但是设备不能正确运行，请仅调整操作说明中涵盖的控制。错误调整其他控制可能导致损坏并且通常需要合格技术进行全面工作以将本设备恢复到正常操作。Brother不建议使用 Brother 正品烫金膜盒以外的其他品牌烫金膜盒。如果使用与本设备不兼容的耗材导致损坏本设备的任何零件，由此导致的任何维修可能不在保修范围内。\n",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636602,
                "distance": 0.8467141389846802,
                "entity": {
                    "chunk_id": 467526165977636602,
                    "content": "## 设备\n\n•\t将本设备放置在平整、水平且稳定的表面上（如桌面），避免震动和冲击。\n\n•\t将本设备放置在通风良好的环境中。\n\n•\t为了防止人员受伤，请谨慎操作，避免将手指放置在图中所示的区域中。\n\n![禁止将手指伸入设备内部齿轮/传动机构区域（图中放大示意）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/c61a7f4e923881679f747508ae309c39dc221685344b068009256b1b3a40cc00.jpg)\n\n![禁止将手指伸入设备顶部开口区域（如进纸/出纸口）](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/5067b2891ca4f761e2874921e0eb433aa742afbf38ca8dc509afecbf0aa6a6b5.jpg)\n",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636597,
                "distance": 0.837287962436676,
                "entity": {
                    "chunk_id": 467526165977636597,
                    "content": "## 设备\n\n•\t请勿将设备放在加热器、空调、电风扇或水附近。\n\n否则当水（包括加热 空调 通风设备所产生的冷凝水）接触本设备时可能产生短路或火灾的风险。\n\n•\t如果设备变得异常高温、冒烟、产生任何强烈味道，或者如果您意外在设备上倒入任何液体，请立即从电源插座拔掉设备的插头。请联系 Brother 呼叫中心或您当地的 Brother 经销商。\n\n如果设备跌落或者已损坏，则有触电的可能性。请从电源插座中拔掉设备的插头，然后联系 呼叫中心或您当地的 经销商。\n\n•\t如果水、其他液体或金属物体进入设备内部，请立即从电源插座中拔掉设备的插头，然后联系 Brother 呼叫中心或您当地的 Brother经销商。\n\n•\t请勿在卡纸或有纸张散落在设备内部的情况下尝试使用本设备。纸张与定影单元长时间接触可能导致火灾。\n\n请勿使用任何易燃物品、任何类型的喷雾剂包含酒精或氨水的有机溶剂/液体来清洁本设备的内部或外部。否则可能导致火灾。请改用无绒干抹布。有关如何清洁本设备的说明，请参阅 。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636598,
                "distance": 0.8342119455337524,
                "entity": {
                    "chunk_id": 467526165977636598,
                    "content": "## 设备\n\n•\t请勿将本设备放在化学品附近，或者将本设备放置在可能会泼溅到化学品的位置。万一化学品接触本设备，则存在火灾或触电的风险。特别是有机溶剂或液体（如苯、油漆稀释剂、抛光剂或除臭剂）可能导致塑料盖和/或电缆溶解或分解，从而产生火灾或触电的风险。这些化学品或其他化学品可能导致本设备故障或褪色。\n\n•\t本设备的包装中使用了塑料袋。塑料袋并不是玩具。为避免窒息的危险，请将这些塑料袋远离婴儿和儿童，并正确弃置这些塑料袋。\n\n•\t对于使用起搏器的用户：\n\n本设备可能会产生弱磁场。如果您在本设备附近感觉到起搏器工作不正常，请远离本设备，并立即咨询医生。\n\n•\t使用本设备之后短时间内，本设备的一些内部零件仍然处于极热状态。打开前盖时，请勿触摸以灰色标记的区域。存在烧伤的风险。先等待设备冷却下来，再触摸设备的内部零件。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636594,
                "distance": 0.7207121253013611,
                "entity": {
                    "chunk_id": 467526165977636594,
                    "content": "## HAK 180 烫金机\n\n•\t严禁未经授权擅自复制或重制本文档的任何部分或全部内容。\n\n•\t请注意，对于使用通过本设备制作的产品造成的任何损坏或利润损失，或者故障、维修导致的数据消失或更改，或者第三方提出的任何索赔，我们不承担任何责任。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            },
            {
                "chunk_id": 467526165977636604,
                "distance": 0.7149743437767029,
                "entity": {
                    "chunk_id": 467526165977636604,
                    "content": "## 为设备选择一个安全的位置\n\n•\t提起本设备时，请使用双手抓稳本设备的两侧。如果抓住的是进纸托板和出纸盒，它们可能会掉下来。必须通过将双手放在本设备下面来搬运本设备。\n\n![**正确与错误的设备搬运方式：避免抓握进纸托板/出纸盒，应双手托住设备底部以防止跌落或部件脱落**](http://192.168.100.100:9000/knowledge-base/upload-images/hak180产品安全手册/cc5ee1ac24ebb2707d40dc7a234a8b243f55f5bf08fabc683859be6fdf096ffa.jpg)\n确保本设备的任何部位均未伸出设备所在的桌面或支架。特别是当本设备位于桌面、支架等边缘时，请勿让出纸盒打开。确保本设备位于平整、水平且稳定的表面上，避免震动。不遵守这些预防措施可能导致设备跌落，从而导致用户的人身伤害以及设备严重损坏。",
                    "item_name": "BrotherHAK180烫金机D01WD7001-00"
                }
            }
        ]
    }

    node_rrf = NodeRrf()
    result = node_rrf(mock_state)
    logger.info(format_json(result))

