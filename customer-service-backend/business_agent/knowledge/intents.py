from  dataclasses  import  dataclass


@dataclass(slots=True)
class KnowledgeIntent:
    id: str
    description: str
    provider_ids: list[str]
    requires_object_type:str  | None =None



# Every knowledge intent the system supports
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
    # This description has to stay narrow. The original Chinese read 「电商通用信息咨询」, and
    # translating it directly to "General e-commerce questions" widened it a notch — measured,
    # "What can you help me with" got pulled onto this intent, went to retrieval, missed, and
    # returned the fallback. A user whose first question is "what can you do" being told "I could
    # not find that in the knowledge base" is the worst possible opening.
    # Questions about the assistant's own capabilities are routed to chitchat by an explicit rule
    # in turn_plan.jinja2.
    "general_ecommerce_info": KnowledgeIntent(
        id="general_ecommerce_info",
        description=("Questions about the merchant's rules or shopping process that do not fit "
                     "the more specific intents above. Not for questions about what this "
                     "assistant itself can do."),
        provider_ids=["faq.default", "rag.default"],
    ),
}