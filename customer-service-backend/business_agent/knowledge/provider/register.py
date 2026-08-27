from business_agent.knowledge.provider.provider import Provider


class KnowledgeRegister:

  def __init__(self, providers: list[Provider]):

    self._providers: dict[str, Provider] = {provider.provider_id: provider for provider in providers}


  def get_provider_by_id(self, provider_id: str) -> Provider:
    return self._providers[provider_id]