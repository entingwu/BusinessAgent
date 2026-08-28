from  dataclasses  import  dataclass


@dataclass(slots=True)
class KnowledgeIntent:
    id: str
    description: str
    provider_ids: list[str]
    requires_object_type:str  | None =None



# 系统支持的所有知识意图
KNOWLEDGE_INTENTS: dict[str, KnowledgeIntent] = {
    "product_info": KnowledgeIntent(
        id="product_info", description="Questions about product information",
        provider_ids=["api.product"], requires_object_type="product",
    ),
    "order_info": KnowledgeIntent(
        id="order_info", description="Questions about order information",
        provider_ids=["api.order"], requires_object_type="order",
    ),
    "refund_policy": KnowledgeIntent(
        id="refund_policy", description="Questions about the refund policy",
        provider_ids=["faq.default", "rag.default"],
    ),
    "return_policy": KnowledgeIntent(
        id="return_policy", description="Questions about the return policy",
        provider_ids=["faq.default", "rag.default"],
    ),
    "shipping_policy": KnowledgeIntent(
        id="shipping_policy", description="Questions about the shipping policy",
        provider_ids=["faq.default", "rag.default"],
    ),
    "platform_rule": KnowledgeIntent(
        id="platform_rule", description="Questions about platform rules",
        provider_ids=["rag.default"],
    ),
    # 描述必须写窄。中文原文是「电商通用信息咨询」，直译成 "General e-commerce
    # questions" 之后范围被放大了一圈——实测「What can you help me with」
    # 会被吸到这个意图上，然后走知识检索、未命中、返回兜底。用户第一句话问
    # 「你能做什么」就被告知「知识库里查不到」，是最糟糕的开场。
    # 这类问题该走 onboarding 流程（它的 description 就写着介绍可办业务）。
    "general_ecommerce_info": KnowledgeIntent(
        id="general_ecommerce_info",
        description=("Questions about the merchant's rules or shopping process that do not fit "
                     "the more specific intents above. Not for questions about what this "
                     "assistant itself can do."),
        provider_ids=["faq.default", "rag.default"],
    ),
}