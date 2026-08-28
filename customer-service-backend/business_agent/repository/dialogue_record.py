from sqlalchemy.orm import  Mapped,mapped_column
from sqlalchemy import  TEXT

from business_agent.repository.base import  Base

class DialogueRecord(Base):
  __tablename__ = "dialogue_states"

  sender_id: Mapped[str] = mapped_column(primary_key=True)  # primary key; Mapped[str] infers a varchar column and gives the IDE type hints
  state_json: Mapped[str]=mapped_column(TEXT, nullable=False,default="{}")