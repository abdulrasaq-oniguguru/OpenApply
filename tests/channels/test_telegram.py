from pathlib import Path

from openapply.channels.telegram import TelegramChannel
from openapply.storage.database import Database


def update(update_id: int, text: str, *, chat_type: str = "private") -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {"id": 123},
            "chat": {"id": 456, "type": chat_type},
        },
    }


def test_pairing_is_private_single_use_and_unbound_users_are_rejected(tmp_path: Path) -> None:
    channel = TelegramChannel("test-token", Database(tmp_path / "agent.db"))
    code = channel.create_pairing_code()

    assert channel.handle_update(update(1, f"/start {code}")) == (
        "456",
        "OpenApply is paired. Ask for today's report, application details, or start your "
        "interview. Application approval stays in your private OpenApply app.",
    )
    assert channel.handle_update(update(2, f"/start {code}")) == (
        "456",
        "Pairing failed or expired. Create a new code in OpenApply.",
    )
    assert channel.handle_update(update(3, "today's report")) is not None
    assert channel.handle_update(update(4, "hello", chat_type="group")) is None

    assert channel.disconnect() == 1
    assert channel.handle_update(update(5, "today's report")) == (
        "456",
        "This chat is not paired with OpenApply.",
    )
