"""
Define Router
"""

import uuid
from fastapi import APIRouter

from business_agent.api.dependencies import DialogueStateServiceDep
from business_agent.api.schemas import (ChatHistoryResponse, ChatRequest, ChatResponse, ChatBotMessage,
                                        ChatObject, HandoffRequest, SessionStateResponse)
from business_agent.domain.messages import UserMessage, ProcessedResult, MessageType, FocusedObject

router = APIRouter()

@router.get("/")
def hello_endpoint():
  """
  API response layer: FASTAPI automatically serializes the object returned from API as json string: serialization
  API request handle layer: Deserialize the json string from frontend as defined data model object.
  """
  return {"success": "ok"}

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


def _to_chat_object(focused_object: FocusedObject) -> ChatObject:
    """
    Goal: 领域模型的业务对象转成 API 模型。object 与 cards 两条路径共用，避免写两份转换
    """
    return ChatObject(
        id=focused_object.id,
        type=focused_object.type,
        title=focused_object.title,
        attributes=focused_object.attributes,
    )


def _build_chat_response(process_result: ProcessedResult) -> ChatResponse:
    """
    Goal: 领域模型转 API 模型。协议见 meta-business-agent.md 附录 E
    """
    return ChatResponse(
        message_id=process_result.message_id,
        control_owner=process_result.control_owner,
        messages=[
            ChatBotMessage(
                text=bot_message.text,
                object=_to_chat_object(bot_message.object) if bot_message.object is not None else None,
                cards=[_to_chat_object(card) for card in bot_message.cards],
                suggestions=list(bot_message.suggestions),
            )
            for bot_message in process_result.messages
        ]
    )

@router.get("/api/chat/history", response_model=ChatHistoryResponse)
async def chat_history_endpoint(sender_id: str, 
                                service: DialogueStateServiceDep):
   chat_history_messages = await service.get_chat_history(sender_id)

   state = await service.get_session_state(sender_id)
   return ChatHistoryResponse(sender_id=sender_id,
                              control_owner=state.control_owner.value,
                              messages=chat_history_messages)


def _build_session_state_response(sender_id: str, state) -> SessionStateResponse:
    """
    Goal: 领域状态转 API 模型。当前流程可能是业务流程，也可能是系统流程
    """
    task = state.current_task()
    return SessionStateResponse(
        sender_id=sender_id,
        control_owner=state.control_owner.value,
        handoff_trigger=state.handoff_trigger.value if state.handoff_trigger is not None else None,
        handoff_reason=state.handoff_reason,
        active_flow=task.flow_id if task is not None else None,
        active_step=task.step_id if task is not None else None,
        slots=dict(state.active_task.slots) if state.active_task is not None else {},
    )


@router.get("/api/session/state", response_model=SessionStateResponse)
async def session_state_endpoint(sender_id: str, service: DialogueStateServiceDep):
    """当前流程、步骤、槽位与控制权归属（规范 4.2）"""
    state = await service.get_session_state(sender_id)
    return _build_session_state_response(sender_id, state)


@router.post("/api/handoff", response_model=SessionStateResponse)
async def handoff_endpoint(body: HandoffRequest, service: DialogueStateServiceDep):
    """
    坐席接管（claim）或交还 Agent（release）。
    第一档只翻转控制权；移交包属于第二档。
    """
    state = await service.set_control_owner(body.sender_id, body.action, body.reason)
    return _build_session_state_response(body.sender_id, state)
