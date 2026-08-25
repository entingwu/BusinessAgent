"""
Define Router
"""

from pydantic import BaseModel
import uuid
from fastapi import APIRouter

from atguigu.api.dependencies import DialogueStateServiceDep
from atguigu.api.schemas import ChatHistoryResponse, ChatRequest, ChatResponse, ChatBotMessage, ChatObject
from atguigu.domain.messages import UserMessage, ProcessedResult, MessageType, FocusedObject

router = APIRouter()

@router.get("/")
def hello_endpoint():
  """
  API response layer: FASTAPI automatically serializes the object returned from API as json string: serialization
  API request handle layer: Deserialize the json string from frontend as defined data model object.
  """
  return {"success": "ok"}

class User(BaseModel):
  name: str
  age: int
  address: str

@router.get("/test", response_model=User)
def test_endpoint():
  """
  response_model:
  1. used for Evaluator
  2. used for Filter
  3. Generated comprehensive API doc.
  Returns:
  """
  return {
    "name": "zs",
    "age": "18",
    "address": "sz",
    "card_no": "xxx"
  }

@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest, service: DialogueStateServiceDep):
  # 1. Convert API data model to domain data model
  user_message = _build_user_message(chat_request)
  
  # 2. Call Service to process domain data model, it returns domain data model
  processed_result = await service.process_message(user_message)
  
  # 3. Convert processed domain data model as API data model
  chat_reponse = _build_chat_response(processed_result)
  return chat_reponse

def _build_user_message(chat_request: ChatRequest) -> UserMessage:
    return UserMessage(
        sender_id=chat_request.sender_id,
        message_id=str(uuid.uuid4()),
        type=MessageType.OBJECT if chat_request.object is not None else MessageType.TEXT,
        text=chat_request.text,
        object=FocusedObject(
            id=chat_request.object.id,
            type=chat_request.object.type,
            title=chat_request.object.title,
            attributes=chat_request.object.attributes
        ) if chat_request.object is not None else None
    )


def _build_chat_response(process_result: ProcessedResult) -> ChatResponse:
    return ChatResponse(
        message_id=process_result.message_id,
        messages=[
            ChatBotMessage(text=bot_message.text,
                           object=ChatObject(
                               id=bot_message.object.id,
                               type=bot_message.object.type,
                               title=bot_message.object.title,
                               attributes=bot_message.object.attributes
                           ) if bot_message.object is not None else None
                           )
            for bot_message in process_result.messages
        ]
    )

@router.get("/api/chat/history", response_model=ChatHistoryResponse)
async def chat_history_endpoint(sender_id: str, 
                                service: DialogueStateServiceDep):
   chat_history_messages = await service.get_chat_history(sender_id)

   return ChatHistoryResponse(sender_id=sender_id, messages=chat_history_messages)
