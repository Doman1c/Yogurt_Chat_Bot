from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.identifiers import UserId
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.ports.state_store import CoreStateStore


class ConversationAuthority:
    """Makes conversation ownership a checked authority boundary, not an ID secret."""

    def __init__(self, store: CoreStateStore) -> None:
        self._store = store

    async def create(self, token: CoreAuthorityToken, conversation_ref: ConversationRef) -> None:
        await self._store.create_conversation(token, conversation_ref)

    async def resolve(self, conversation_ref: ConversationRef) -> ConversationRef:
        return await self._store.resolve_conversation(conversation_ref)

    async def validate_owner(self, conversation_ref: ConversationRef, user_id: UserId) -> bool:
        return await self._store.validate_conversation_owner(conversation_ref, user_id)
